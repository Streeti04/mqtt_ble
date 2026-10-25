"""
Govee Bluetooth Low Energy (BLE) Sensor Scanner mit BLEAK
==========================================================
Modernere Version mit der bleak-Bibliothek für bessere Windows-BLE-Unterstützung.
Scannt nach Govee BLE Temperatur- und Feuchtigkeitssensoren und veröffentlicht
die Daten über MQTT.

Unterstützte Govee-Modelle:
- GVH5075
- GVH5177
- GVH600C
- Andere GVH-Modelle

Vorteile von bleak gegenüber bleson:
- Bessere Windows-BLE-Unterstützung
- Aktivere Entwicklung und Wartung
- Zuverlässigerer Zugriff auf Manufacturer Data
"""

import asyncio
import logging
import json
import os
import sys
import time
import re
import threading
from pathlib import Path
from basic_mqtt import basic_mqtt
from learn_mode import (
    Allowlist,
    LearnWindow,
    OnceLogger,
    DEFAULT_LEARN_SECONDS,
    DECISION_ALLOW,
    DECISION_LEARN,
    decide,
    parse_switch_payload,
)
from govee_led_wez.govee import GoveeController, GoveeDevice
import bleak_autorelease
from scanner_cache import clear_scanner_cache

# Bleak-Imports für BLE-Scanning
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# ==========================================
# Logging-Konfiguration
# ==========================================
# Reduziertes Logging für Langzeitbetrieb (Wochen/Monate)
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.WARNING)  # Nur Warnungen und Fehler

# Reduziere Bleak-Logging für sauberere Ausgabe
logging.getLogger("bleak").setLevel(logging.ERROR)

# ==========================================
# MQTT-Client Initialisierung
# ==========================================
mqtt = basic_mqtt(client_id="govee_scanner", silent=True)  # Silent-Modus für Langzeitbetrieb

# ==========================================
# Globale Variablen
# ==========================================
# Dictionary zum Speichern der zuletzt empfangenen Werte pro Gerät
# Verhindert doppelte Veröffentlichungen identischer Daten
last = {}

# Set zum Tracken welche Geräte bereits für Home Assistant Discovery registriert wurden
discovered_devices = set()

# Set zum Tracken welche BLE-Lichter bereits für Home Assistant Discovery registriert wurden
discovered_light_devices = set()

# Dictionary zum Speichern des letzten MQTT-Publish-Zeitpunkts pro Gerät (für 60s Throttling)
last_publish_time = {}
last_light_publish_time = {}

# MQTT Publish-Intervall in Sekunden (schont Netzwerk und Home Assistant)
PUBLISH_INTERVAL = 60
LIGHT_PUBLISH_INTERVAL = 30

LIGHT_COMMAND_TOPIC_WILDCARD = "govee_ble/light/+/set"

# ==========================================
# Lernmodus / Allowlist
# ==========================================
# Im Normalbetrieb werden nur Geraete aus der Allowlist veroeffentlicht.
# Neue Geraete kommen nur hinzu, solange der HA-Schalter
# "BLE neue Geraete erlauben" an ist (automatisch AUS nach LEARN_SECONDS).
STATE_DIR = Path(os.environ.get("MQTT_BLE_STATE_DIR") or Path(__file__).resolve().parent / "state")
ALLOWLIST_PATH = STATE_DIR / "known_devices.json"
LEARN_SECONDS = int(os.environ.get("MQTT_BLE_LEARN_SECONDS") or DEFAULT_LEARN_SECONDS)

LEARN_COMMAND_TOPIC = "mqtt_ble/observe/learn/set"
LEARN_STATE_TOPIC = "mqtt_ble/observe/learn/state"
LEARN_DISCOVERY_TOPIC = "homeassistant/switch/mqtt_ble_observe_learn/config"
OBSERVER_STATUS_TOPIC = "mqtt_ble/observe/status"  # online/offline (LWT)

try:
    allowlist = Allowlist.load(ALLOWLIST_PATH)
except Exception as exc:  # defekte Datei nicht still ueberschreiben
    print(f"[FATAL] Allowlist {ALLOWLIST_PATH} nicht lesbar: {exc}", file=sys.stderr, flush=True)
    sys.exit(2)

learn_window = LearnWindow(LEARN_SECONDS)  # startet immer AUS
ignored_log = OnceLogger()

# Unterstützte Govee-Lichtmodelle für BLE-Discovery
SUPPORTED_LIGHT_MODELS = {
    "H6099",  # Beispiel aus User-Anforderung
    "H60",
    "H61",
    "H70",
    "H71",
}

# Statistik-Zähler
stats = {
    'total_advertisements': 0,
    'govee_found': 0,
    'lights_found': 0,
    'light_updates': 0,
    'data_published': 0,
    'devices': set()
}

# BLE-Lichtsteuerung
ble_controller = GoveeController()
ble_light_devices = {}
ble_light_state = {}
ble_light_lock = threading.Lock()
scanner_event_loop = None

# ==========================================
# Hilfsfunktionen
# ==========================================

def sensor_device_id(mac_address):
    """Geraete-ID eines Sensors, wie sie im HA-Discovery-Topic steht."""
    return f"govee_{mac_address.lower()}"


def light_device_id(mac_address):
    """Geraete-ID eines BLE-Lichts, wie sie im HA-Discovery-Topic steht."""
    return f"govee_ble_light_{mac_address.lower()}"


def admit_device(device_id, device_name):
    """Allowlist-/Lernmodus-Pruefung. True = veroeffentlichen."""
    decision = decide(device_id, allowlist, learn_window.is_active())
    if decision == DECISION_ALLOW:
        return True
    if decision == DECISION_LEARN:
        if allowlist.add(device_id, name=device_name, source="learn"):
            print(f"[LEARN] {device_name} ({device_id}) in Allowlist aufgenommen")
        return True
    if ignored_log.first_time(device_id):
        print(f"[IGNORE] {device_name} ({device_id}) nicht in Allowlist - wird nicht veroeffentlicht")
    return False


def publish_learn_state():
    """Publiziert den Lernmodus-Zustand (retained)."""
    state = "ON" if learn_window.is_active() else "OFF"
    mqtt.publish(LEARN_STATE_TOPIC, state, retain=True)


def publish_learn_switch_discovery():
    """HA-Discovery fuer den Schalter 'BLE neue Geraete erlauben' (retained)."""
    config = {
        "name": "BLE neue Geräte erlauben",
        "unique_id": "mqtt_ble_observe_learn",
        "command_topic": LEARN_COMMAND_TOPIC,
        "state_topic": LEARN_STATE_TOPIC,
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
        "retain": False,
        "optimistic": False,
        "availability_topic": OBSERVER_STATUS_TOPIC,
        "icon": "mdi:bluetooth-settings",
    }
    mqtt.publish(LEARN_DISCOVERY_TOPIC, json.dumps(config, ensure_ascii=False), retain=True)


def handle_learn_command(payload, retained=False):
    """Verarbeitet ON/OFF fuer den Lernmodus."""
    if retained:
        # Retained-Befehle ignorieren: nach Neustart ist der Lernmodus immer AUS.
        return
    wanted = parse_switch_payload(payload)
    if wanted is None:
        print("[LEARN] Ungueltige Payload fuer Lernmodus ignoriert")
        return
    if wanted:
        learn_window.enable()
        print(f"[LEARN] Lernmodus AN fuer {LEARN_SECONDS // 60} min")
    else:
        if learn_window.disable():
            print("[LEARN] Lernmodus AUS")
    publish_learn_state()


def publish_homeassistant_discovery(device_name, mac_address):
    """
    Sendet Home Assistant MQTT Discovery Konfiguration
    
    Registriert einen Govee-Sensor automatisch in Home Assistant über
    MQTT Discovery. Erstellt drei Entities: Temperatur, Luftfeuchtigkeit, Batterie.
    
    Args:
        device_name (str): Name des Geräts (z.B. "GVH5075_25C3")
        mac_address (str): MAC-Adresse ohne Doppelpunkte (z.B. "A4C13825C3DA")
    """
    # Eindeutige Device-ID basierend auf MAC
    device_id = sensor_device_id(mac_address)
    base_topic = f"govee/{mac_address}"
    
    # Extrahiere Modellnummer und kurze ID aus dem Namen
    # z.B. "GVH5075_25C3" -> Modell: GVH5075, ID: 25C3
    model = device_name.split('_')[0] if '_' in device_name else "GVH"
    short_id = device_name.split('_')[1] if '_' in device_name else mac_address[-4:]
    
    # Device-Info (wird von allen Entities geteilt)
    device_info = {
        "identifiers": [device_id],
        "name": f"Govee {short_id}",
        "model": model,
        "manufacturer": "Govee"
    }
    
    # Temperatur-Sensor Discovery
    temp_config = {
        "name": f"Govee {short_id} Temperatur",
        "unique_id": f"{device_id}_temperature",
        "state_topic": base_topic,
        "value_template": "{{ value_json.temp_c }}",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "device": device_info
    }
    mqtt.publish(f"homeassistant/sensor/{device_id}/temperature/config", 
                 json.dumps(temp_config), retain=True)
    
    # Luftfeuchtigkeits-Sensor Discovery
    hum_config = {
        "name": f"Govee {short_id} Luftfeuchtigkeit",
        "unique_id": f"{device_id}_humidity",
        "state_topic": base_topic,
        "value_template": "{{ value_json.hum }}",
        "unit_of_measurement": "%",
        "device_class": "humidity",
        "state_class": "measurement",
        "device": device_info
    }
    mqtt.publish(f"homeassistant/sensor/{device_id}/humidity/config", 
                 json.dumps(hum_config), retain=True)
    
    # Batterie-Sensor Discovery
    batt_config = {
        "name": f"Govee {short_id} Batterie",
        "unique_id": f"{device_id}_battery",
        "state_topic": base_topic,
        "value_template": "{{ value_json.batt }}",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "device": device_info
    }
    mqtt.publish(f"homeassistant/sensor/{device_id}/battery/config", 
                 json.dumps(batt_config), retain=True)
    
    # Keine Logging-Ausgabe - wird als print in detection_callback gemacht


def normalize_mac(mac_address):
    """Konvertiert BLE-MAC in ein einheitliches Format ohne Doppelpunkte."""
    return mac_address.replace(':', '').replace('-', '').upper()


def extract_light_model(device_name):
    """Extrahiert eine Govee-Modellkennung aus dem Device-Namen (z.B. H6099)."""
    if not device_name:
        return "H6099"

    model_match = re.search(r"H\d{4}", device_name.upper())
    if model_match:
        return model_match.group(0)

    # Fallback: Modellfamilie H6/H7 funktioniert in der Regel mit H6099-Command-Set.
    return "H6099"


def ha_brightness_to_pct(ha_brightness):
    """Skaliert Home Assistant Helligkeit 0-255 auf Prozent 0-100."""
    return max(0, min(100, int(round((int(ha_brightness) / 255.0) * 100))))


def pct_to_ha_brightness(brightness_pct):
    """Skaliert Prozent 0-100 auf Home Assistant Helligkeit 0-255."""
    return max(0, min(255, int(round((int(brightness_pct) / 100.0) * 255))))


def publish_light_control_state(light_id):
    """Publiziert den aktuellen Light-Zustand auf das HA-State-Topic."""
    with ble_light_lock:
        state = dict(ble_light_state.get(light_id, {"state": "OFF", "brightness": 255}))

    mqtt.publish(f"govee_ble/light/{light_id}/state", json.dumps(state))


def register_ble_light_for_control(device, light_id):
    """Registriert ein BLE-Licht für Steuerbefehle über govee-led-wez."""
    model = extract_light_model(device.name)

    with ble_light_lock:
        control_device = ble_light_devices.get(light_id)
        if not control_device:
            control_device = GoveeDevice(device_id=f"ble_{light_id}", model=model)
            ble_light_devices[light_id] = control_device
            ble_light_state[light_id] = {"state": "OFF", "brightness": 255}

        control_device.model = model
        control_device.ble_device = device

        ble_controller.devices[control_device.device_id] = control_device
        ble_controller.register_ble_device(device)


async def handle_light_command(light_id, payload):
    """Führt einen Home-Assistant-Lichtbefehl für ein BLE-Gerät aus."""
    with ble_light_lock:
        control_device = ble_light_devices.get(light_id)
        cached_state = dict(ble_light_state.get(light_id, {"state": "OFF", "brightness": 255}))

    if not control_device:
        print(f"[BLE CONTROL] Unbekanntes Licht: {light_id}")
        return

    requested_state = payload.get("state")
    requested_brightness = payload.get("brightness")

    try:
        if requested_state == "OFF":
            await ble_controller.set_power_state(control_device, False)
            cached_state["state"] = "OFF"

        else:
            if requested_state == "ON":
                await ble_controller.set_power_state(control_device, True)
                cached_state["state"] = "ON"

            if requested_brightness is not None:
                brightness_pct = ha_brightness_to_pct(requested_brightness)
                await ble_controller.set_brightness(control_device, brightness_pct)
                cached_state["brightness"] = pct_to_ha_brightness(brightness_pct)
                cached_state["state"] = "ON"

        with ble_light_lock:
            ble_light_state[light_id] = cached_state

        publish_light_control_state(light_id)

    except Exception as e:
        print(f"[BLE CONTROL] Fehler bei {light_id}: {e}")


def on_mqtt_message(client, userdata, msg):
    """Empfängt HA-Lichtbefehle und leitet sie in den Async-BLE-Handler."""
    if msg.topic == LEARN_COMMAND_TOPIC:
        handle_learn_command(msg.payload, retained=bool(getattr(msg, "retain", False)))
        return

    if not msg.topic.startswith("govee_ble/light/") or not msg.topic.endswith("/set"):
        return

    parts = msg.topic.split("/")
    if len(parts) != 4:
        return

    light_id = parts[2]

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        print(f"[BLE CONTROL] Ungültige Payload auf {msg.topic}")
        return

    if scanner_event_loop is None:
        print("[BLE CONTROL] Event-Loop noch nicht bereit")
        return

    future = asyncio.run_coroutine_threadsafe(
        handle_light_command(light_id, payload),
        scanner_event_loop,
    )

    def _done_callback(fut):
        exc = fut.exception()
        if exc:
            print(f"[BLE CONTROL] Async-Fehler: {exc}")

    future.add_done_callback(_done_callback)


def on_mqtt_connect(client, userdata, flags, rc):
    """Abonniert nach Verbindungsaufbau alle BLE-Lichtbefehle."""
    if rc == 0:
        client.subscribe(LIGHT_COMMAND_TOPIC_WILDCARD)
        client.subscribe(LEARN_COMMAND_TOPIC)
        print(f"[MQTT] Subscribed: {LIGHT_COMMAND_TOPIC_WILDCARD}, {LEARN_COMMAND_TOPIC}")
        mqtt.publish(OBSERVER_STATUS_TOPIC, "online", retain=True)
        publish_learn_switch_discovery()
        publish_learn_state()


def is_supported_govee_light(device_name):
    """Erkennt Govee-Lichter (z.B. H6099) anhand ihres BLE-Namens."""
    if not device_name:
        return False

    upper_name = device_name.upper()
    if "GOVEE" not in upper_name and "H" not in upper_name:
        return False

    # Explizite Modellsuche wie H6099
    model_match = re.search(r"H\d{4}", upper_name)
    if model_match:
        model = model_match.group(0)
        if model in SUPPORTED_LIGHT_MODELS or model.startswith(("H60", "H61", "H70", "H71")):
            return True

    # Fallback für häufige Namen von Govee-Lights
    if upper_name.startswith("H6") or upper_name.startswith("H7"):
        return True

    return False


def publish_light_homeassistant_discovery(device_name, mac_address):
    """Registriert BLE-Licht als steuerbares Light + Diagnose-Entitäten."""
    device_id = light_device_id(mac_address)
    base_topic = f"govee_ble/light/{mac_address}"

    model_match = re.search(r"H\d{4}", (device_name or "").upper())
    model = model_match.group(0) if model_match else "Govee BLE Light"

    device_info = {
        "identifiers": [device_id],
        "name": device_name or f"Govee Light {mac_address[-4:]}",
        "model": model,
        "manufacturer": "Govee"
    }

    rssi_config = {
        "name": f"{device_info['name']} RSSI",
        "unique_id": f"{device_id}_rssi",
        "state_topic": base_topic,
        "value_template": "{{ value_json.rssi }}",
        "unit_of_measurement": "dBm",
        "state_class": "measurement",
        "icon": "mdi:wifi",
        "device": device_info
    }
    mqtt.publish(
        f"homeassistant/sensor/{device_id}/rssi/config",
        json.dumps(rssi_config),
        retain=True
    )

    online_config = {
        "name": f"{device_info['name']} Online",
        "unique_id": f"{device_id}_online",
        "state_topic": base_topic,
        "value_template": "{{ value_json.online }}",
        "payload_on": "True",
        "payload_off": "False",
        "device_class": "connectivity",
        "device": device_info
    }
    mqtt.publish(
        f"homeassistant/binary_sensor/{device_id}/online/config",
        json.dumps(online_config),
        retain=True
    )

    light_config = {
        "name": device_info["name"],
        "unique_id": f"{device_id}_light",
        "schema": "json",
        "command_topic": f"{base_topic}/set",
        "state_topic": f"{base_topic}/state",
        "brightness": True,
        "device": device_info
    }
    mqtt.publish(
        f"homeassistant/light/{device_id}/config",
        json.dumps(light_config),
        retain=True
    )


def publish_light_ble_state(device_name, mac_address, rssi):
    """Publiziert Presence/Signal-Status für ein BLE-Licht."""
    msg = {
        "name": device_name,
        "online": True,
        "rssi": rssi,
        "last_seen": int(time.time())
    }
    mqtt.publish(f"govee_ble/light/{mac_address}", json.dumps(msg))

def decode_temp_hum(data_bytes, battery, address):
    """
    Dekodiert Temperatur- und Feuchtigkeitsdaten aus BLE-Rohdaten
    
    Wandelt die von Govee-Sensoren gesendeten Rohdaten in lesbare
    Temperatur- und Feuchtigkeitswerte um.
    
    Args:
        data_bytes (bytes): Byte-Array mit kodierten Sensor-Werten (3 Bytes)
        battery (int): Batteriestand in Prozent
        address (str): Bluetooth-MAC-Adresse des Sensors
    
    Returns:
        tuple: (temperature, humidity, battery) oder None bei Duplikat
    """
    global last
    
    # Konvertiere Byte-Array zu Integer (Big Endian)
    values = int.from_bytes(data_bytes, 'big')
    
    # Prüfe ob dieser Wert für diese Adresse schon verarbeitet wurde
    if address not in last or last[address] != values:
        last[address] = values
        
        # Dekodierung der Temperatur: Gesamtwert / 10000
        temp = float(values / 10000)
        
        # Dekodierung der Luftfeuchtigkeit: Letzte 3 Stellen / 10
        hum = float((values % 1000) / 10)
        
        # Keine Logging-Ausgabe mehr (reduziert Output massiv)
        
        return temp, hum, battery
    
    return None

def detection_callback(device: BLEDevice, advertisement_data: AdvertisementData):
    """
    Callback-Funktion für BLE-Advertisement-Pakete (bleak Version)
    
    Wird bei jedem empfangenen BLE-Advertisement aufgerufen. Filtert nach
    Govee-Geräten und extrahiert Sensor-Daten aus den Manufacturer-Daten.
    
    Args:
        device (BLEDevice): BLE-Geräteobjekt mit Name und Adresse
        advertisement_data (AdvertisementData): Advertisement-Daten inkl. Manufacturer Data
    """
    global stats, discovered_devices
    
    stats['total_advertisements'] += 1
    
    # ==========================================
    # BLE-Licht Discovery (z.B. H6099)
    # ==========================================
    if is_supported_govee_light(device.name) and admit_device(
            light_device_id(normalize_mac(device.address)), device.name):
        stats['lights_found'] += 1
        mac_clean = normalize_mac(device.address)
        register_ble_light_for_control(device, mac_clean)

        if mac_clean not in discovered_light_devices:
            publish_light_homeassistant_discovery(device.name, mac_clean)
            discovered_light_devices.add(mac_clean)
            print(f"[LIGHT DISCOVERY] {device.name} ({mac_clean}) in Home Assistant registriert")
            publish_light_control_state(mac_clean)

        current_time = time.time()
        if (device.address not in last_light_publish_time or
            current_time - last_light_publish_time[device.address] >= LIGHT_PUBLISH_INTERVAL):
            publish_light_ble_state(device.name, mac_clean, advertisement_data.rssi)
            last_light_publish_time[device.address] = current_time
            stats['light_updates'] += 1

    # Filtere nach Govee-Sensoren (GVH5xxx Modelle)
    if (device.name and device.name.startswith('GVH5')
            and admit_device(sensor_device_id(device.address.replace(':', '')), device.name)):
        stats['govee_found'] += 1
        stats['devices'].add(device.address)
        
        # ==========================================
        # Home Assistant Auto-Discovery
        # ==========================================
        # Sende Discovery-Konfiguration nur einmal pro Gerät
        mac_clean = device.address.replace(':', '')
        if mac_clean not in discovered_devices:
            publish_homeassistant_discovery(device.name, mac_clean)
            discovered_devices.add(mac_clean)
            print(f"[DISCOVERY] {device.name} in Home Assistant registriert")
        
        # Keine Debug-Ausgabe für jeden empfangenen Advertisement (zu viel Output)
        # Nur bei tatsächlichem MQTT-Publish wird etwas ausgegeben
        
        # ==========================================
        # Manufacturer Data auslesen
        # ==========================================
        # Bleak speichert Manufacturer Data als Dictionary: {company_id: bytes}
        # Govee verwendet verschiedene Company IDs
        if advertisement_data.manufacturer_data:
            # Keine Ausgabe für jeden Advertisement (zu viel Output)
            
            # Durchlaufe alle Manufacturer Data Einträge
            for company_id, data in advertisement_data.manufacturer_data.items():
                # Keine Debug-Ausgabe mehr
                
                # ==========================================
                # Datenextraktion basierend auf Gerätemodell
                # ==========================================
                try:
                    # Govee verwendet Company ID 0xEC88
                    # Nur diese Daten verarbeiten, andere ignorieren
                    if company_id != 0xEC88:
                        # Keine Ausgabe für übersprungene Company IDs
                        continue
                    
                    # Govee-Datenformat: [0x00, byte1, byte2, byte3, battery, 0x00]
                    # Bytes 1-3 enthalten Temperatur und Luftfeuchtigkeit
                    if len(data) >= 5:
                        result = decode_temp_hum(data[1:4], data[4], device.address)
                        
                        if result:
                            temp, hum, battery = result
                            
                            # ==========================================
                            # Plausibilitätsprüfung
                            # ==========================================
                            if -30.0 <= temp <= 180.0 and 0 <= hum <= 100:
                                # ==========================================
                                # Throttling: Nur alle 60 Sekunden publishen
                                # ==========================================
                                current_time = time.time()
                                mac_clean = device.address.replace(':', '')
                                
                                # Prüfe ob 60 Sekunden seit letztem Publish vergangen sind
                                if (device.address not in last_publish_time or 
                                    current_time - last_publish_time[device.address] >= PUBLISH_INTERVAL):
                                    
                                    # ==========================================
                                    # MQTT-Nachricht erstellen und senden
                                    # ==========================================
                                    msg = {
                                        'temp_c': temp,      # Temperatur in Celsius
                                        'hum': hum,          # Luftfeuchtigkeit in %
                                        'batt': battery      # Batterie in %
                                    }
                                    
                                    # Kompakte Ausgabe: Nur bei tatsächlichem Publish
                                    print(f"[{time.strftime('%H:%M:%S')}] {device.name}: {temp:.1f}°C, {hum:.1f}%, Batt {battery}%")
                                    
                                    # Konvertiere zu JSON
                                    json_string = json.dumps(msg, indent=4)
                                    
                                    # Veröffentliche zu geräte-spezifischem Topic
                                    # Verwende MAC-Adresse OHNE Doppelpunkte (muss mit Discovery-Config übereinstimmen!)
                                    mqtt.publish(f"govee/{mac_clean}", json_string)
                                    
                                    # Aktualisiere letzten Publish-Zeitpunkt
                                    last_publish_time[device.address] = current_time
                                    
                                    stats['data_published'] += 1
                                    
                                # Sonst: Daten empfangen, aber noch nicht publishen (Throttling aktiv, keine Ausgabe)
                            else:
                                # Keine Warnung mehr - nur bei sehr unplausiblen Werten
                                if temp < -50 or temp > 100 or hum < 0 or hum > 100:
                                    print(f"[WARN] Extreme Werte: {device.name} Temp={temp:.1f}°C, Hum={hum:.1f}%")
                                
                except Exception as e:
                    print(f"[ERROR] {device.name}: {e}")
        # Keine Ausgabe wenn keine Manufacturer Data vorhanden

async def scan_loop():
    """
    Asynchrone Hauptschleife für kontinuierliches BLE-Scanning
    
    Verwendet bleak's BleakScanner für kontinuierliches Scanning
    nach Govee BLE-Geräten.
    """
    print("\n" + "="*80)
    print(">>> GOVEE BLE SCANNER - Langzeitbetrieb")
    print("="*80)
    print(">>> Suche nach GVH5xxx Sensoren und BLE-Lichtern (z.B. H6099)")
    print(">>> Minimale Ausgabe für Langzeitbetrieb")
    print(f">>> Allowlist: {len(allowlist)} Geräte ({ALLOWLIST_PATH}), Lernmodus AUS")
    print(">>> Drücke Strg+C zum Beenden")
    print("="*80 + "\n")
    
    global scanner_event_loop
    scanner_event_loop = asyncio.get_running_loop()

    # Erstelle Scanner mit unserem Callback
    # Hauptursache des Speicherwachstums: ohne Pool sammelt der Main-Thread
    # pro Advertisement autoreleased ObjC-Objekte (siehe bleak_autorelease.py).
    if not bleak_autorelease.install():
        print("[WARN] Autorelease-Pool-Patch nicht installiert - Speicher-Leak moeglich")

    scanner = BleakScanner(detection_callback=detection_callback)

    print(f"[{time.strftime('%H:%M:%S')}] Scanner gestartet - warte auf Sensordaten...\n")
    
    try:
        # Starte kontinuierliches Scanning
        await scanner.start()
        
        # Laufe endlos (bis Strg+C)
        last_clear = time.time()
        while True:
            await asyncio.sleep(1)

            # Lernmodus nach Ablauf automatisch beenden und HA melden
            if learn_window.poll_expired():
                print("[LEARN] Lernmodus abgelaufen - AUS")
                publish_learn_state()

            # bleak haelt alle gesehenen BLE-Geraete in scanner._backend.seen_devices,
            # das nur in start() geleert wird. Bei Geraeten mit wechselnden
            # (Resolvable Private) Adressen waechst der Cache ueber Wochen
            # unbegrenzt und bindet GB von Speicher. Hier periodisch leeren.
            # Achtung: scanner.discovered_devices_and_advertisement_data ist in
            # bleak>=0.19 nur eine Kopie — clear() darauf war wirkungslos
            # (Leak 4,6 GB nach 8 Tagen trotz "Fix" vom 29.08.2026).
            if time.time() - last_clear > 3600:
                try:
                    if clear_scanner_cache(scanner) is None:
                        print("[WARN] bleak-Cache nicht gefunden (_backend.seen_devices) - Leak-Schutz inaktiv")
                except Exception as exc:
                    print(f"[WARN] Cache-Leeren fehlgeschlagen: {exc}")
                last_clear = time.time()
            
    except KeyboardInterrupt:
        print("\n\n>> Scanner wird beendet...")
    finally:
        await scanner.stop()
        print(f"\n[STATS] Finale Statistik:")
        print(f"   Gesamt Advertisements: {stats['total_advertisements']}")
        print(f"   Govee-Geräte gefunden: {stats['govee_found']}")
        print(f"   BLE-Licht Advertisements: {stats['lights_found']}")
        print(f"   BLE-Licht Updates: {stats['light_updates']}")
        print(f"   Verschiedene Geräte:   {len(stats['devices'])}")
        print(f"   Daten publiziert:      {stats['data_published']}")
        if stats['devices']:
            print(f"\n   Gefundene Geräte:")
            for device in sorted(stats['devices']):
                print(f"      • {device}")

# ==========================================
# Hauptprogramm
# ==========================================
if __name__ == "__main__":
    try:
        # LWT: faellt der Observer aus, wird der Lernschalter in HA 'unavailable'
        mqtt.client.will_set(OBSERVER_STATUS_TOPIC, "offline", retain=True)
        mqtt.connect()
        # WICHTIG: basic_mqtt.connect() überschreibt alle Callbacks mit seinen
        # Silent-Handlern — unsere Handler daher erst NACH connect() setzen.
        # (Fix 29.08.2026: vorher lief on_mqtt_connect nie, das Abo für
        # BLE-Lichtbefehle wurde also nie registriert.)
        mqtt.client.on_connect = on_mqtt_connect
        mqtt.client.on_message = on_mqtt_message
        # Falls die Verbindung schon vor dem Setzen der Handler stand,
        # Abos/Discovery jetzt nachholen (Mehrfach-Subscribe ist harmlos).
        if mqtt.client.is_connected():
            on_mqtt_connect(mqtt.client, None, None, 0)

        # Starte die asynchrone Scanning-Schleife
        asyncio.run(scan_loop())
    except KeyboardInterrupt:
        print("\n>> Programm beendet!")
    finally:
        mqtt.disconnect()
        print("MQTT-Verbindung getrennt. Auf Wiedersehen!\n")
