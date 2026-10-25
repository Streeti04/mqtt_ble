import socket
import json
import time
import threading
import sys
import paho.mqtt.client as mqtt

from mqtt_config import load_mqtt_config_or_exit

# ==========================================
# KONFIGURATION (Bitte anpassen!)
# ==========================================
# MQTT-Zugangsdaten kommen aus mqtt.env bzw. Umgebungsvariablen (mqtt_config.py).
# Geladen wird beim Start im Hauptprogramm; fehlende Werte -> Abbruch mit Meldung.

DEVICE_NAME_PREFIX = "Govee"

# Optional: Eigene Namen pro MAC vergeben (MAC ohne Doppelpunkte, gross geschrieben)
# Beispiel:
# CUSTOM_DEVICE_NAMES = {
#     "78C3D7303837235E": "Floorlamp Basic",
# }
CUSTOM_DEVICE_NAMES = {}

# Optional: Nur bestimmte Geraete bridgen (leer = alle gefundenen)
ALLOWED_MACS = []

# Govee LAN Discovery
SCAN_TARGET_IP = "239.255.255.250"
SCAN_TARGET_PORT = 4001
SCAN_RESPONSE_PORT = 4002
GOVEE_CONTROL_PORT = 4003
DISCOVERY_TIMEOUT_SECONDS = 4
DISCOVERY_RETRIES = 3
STATUS_POLL_SECONDS = 5

# MQTT Wildcard Topic fuer Befehle aller Govee-Lampen
TOPIC_SET_WILDCARD = "govee/+/set"

# Wird zur Laufzeit automatisch ermittelt
devices_by_mac = {}
ip_to_mac = {}
devices_lock = threading.Lock()

# ==========================================
# FUNKTIONEN
# ==========================================

# UDP Socket vorbereiten
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# Wir binden Port 4002, um die Antworten der Lampe zu empfangen
udp_sock.bind(("0.0.0.0", SCAN_RESPONSE_PORT))


def normalize_mac(mac_address):
    """Konvertiert MAC in Home-Assistant-freundliches Format ohne Doppelpunkte."""
    return mac_address.replace(":", "").upper()


def build_topics(mac_address):
    """Erzeugt MQTT Topics pro Geraet basierend auf der MAC-Adresse."""
    return {
        "state": f"govee/{mac_address}/state",
        "set": f"govee/{mac_address}/set",
        "discovery": f"homeassistant/light/govee_{mac_address}/config"
    }


def build_device_name(mac_address, scan_data):
    """Erzeugt einen sinnvollen Namen pro gefundenem Govee-Geraet."""
    if mac_address in CUSTOM_DEVICE_NAMES:
        return CUSTOM_DEVICE_NAMES[mac_address]

    candidate = (
        scan_data.get("deviceName")
        or scan_data.get("devName")
        or scan_data.get("name")
    )
    if candidate:
        return str(candidate)

    sku = scan_data.get("sku")
    if sku:
        return f"{DEVICE_NAME_PREFIX} {sku} {mac_address[-4:]}"

    return f"{DEVICE_NAME_PREFIX} {mac_address[-4:]}"


def should_include_device(mac_address):
    """Filtert optional auf erlaubte MACs."""
    if not ALLOWED_MACS:
        return True

    normalized = {normalize_mac(m) for m in ALLOWED_MACS}
    return mac_address in normalized


def register_device(scan_data, fallback_ip):
    """Registriert oder aktualisiert ein gefundenes Geraet in den Laufzeit-Maps."""
    raw_mac = scan_data.get("device")
    if not raw_mac:
        return None

    mac_address = normalize_mac(raw_mac)
    if not should_include_device(mac_address):
        return None

    discovered_ip = scan_data.get("ip") or fallback_ip
    if not discovered_ip:
        return None

    with devices_lock:
        existing = devices_by_mac.get(mac_address, {})
        device_name = existing.get("name") or build_device_name(mac_address, scan_data)
        topics = existing.get("topics") or build_topics(mac_address)

        device_info = {
            "mac": mac_address,
            "ip": discovered_ip,
            "sku": scan_data.get("sku"),
            "name": device_name,
            "topics": topics,
        }

        devices_by_mac[mac_address] = device_info
        ip_to_mac[discovered_ip] = mac_address

    return device_info


def discover_govee_devices(timeout_seconds=DISCOVERY_TIMEOUT_SECONDS, retries=DISCOVERY_RETRIES):
    """Findet alle erreichbaren Govee-Lampen im LAN per UDP-Scan."""
    scan_payload = {
        "msg": {
            "cmd": "scan",
            "data": {"account_topic": "reserve"}
        }
    }

    discovered_count = 0

    for attempt in range(1, retries + 1):
        try:
            print(f"Starte Govee Discovery (Versuch {attempt}/{retries})...")
            udp_sock.sendto(
                json.dumps(scan_payload).encode("utf-8"),
                (SCAN_TARGET_IP, SCAN_TARGET_PORT)
            )

            start_time = time.time()
            while (time.time() - start_time) < timeout_seconds:
                remaining = timeout_seconds - (time.time() - start_time)
                udp_sock.settimeout(max(0.1, remaining))

                try:
                    data, addr = udp_sock.recvfrom(1024)
                except socket.timeout:
                    break

                try:
                    response = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                msg = response.get("msg", {})
                if msg.get("cmd") != "scan":
                    continue

                scan_data = msg.get("data", {})
                device_info = register_device(scan_data, addr[0])
                if not device_info:
                    continue

                discovered_count = len(devices_by_mac)
                print(
                    f"  -> gefunden: {device_info['name']} "
                    f"(MAC={device_info['mac']}, IP={device_info['ip']})"
                )

        except Exception as e:
            print(f"Discovery-Fehler: {e}")

    return discovered_count

def send_udp_cmd(target_ip, cmd, value=None):
    """Sendet einen Befehl an Port 4003 einer bestimmten Lampe."""
    if not target_ip:
        print("Keine Ziel-IP vorhanden. Befehl wird verworfen.")
        return

    payload = {"msg": {"cmd": cmd, "data": {}}}
    if value is not None:
        payload["msg"]["data"]["value"] = value
        
    try:
        message = json.dumps(payload).encode('utf-8')
        udp_sock.sendto(message, (target_ip, GOVEE_CONTROL_PORT))
    except Exception as e:
        print(f"Fehler beim Senden über UDP: {e}")


def send_discovery_for_device(client, device_info):
    """Sende Home Assistant Discovery fuer genau ein Govee-Geraet."""
    discovery_payload = {
        "name": device_info["name"],
        "unique_id": f"govee_light_{device_info['mac']}",
        "command_topic": device_info["topics"]["set"],
        "state_topic": device_info["topics"]["state"],
        "schema": "json",
        "brightness": True,
        "device": {
            "identifiers": [device_info["mac"]],
            "name": device_info["name"],
            "manufacturer": "Govee",
            "model": device_info.get("sku") or "LAN Light"
        }
    }
    client.publish(device_info["topics"]["discovery"], json.dumps(discovery_payload), retain=True)

def on_mqtt_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"MQTT Verbindung fehlgeschlagen: {reason_code}")
        return

    print("Verbunden mit MQTT Broker!")

    with devices_lock:
        devices = list(devices_by_mac.values())

    for device_info in devices:
        send_discovery_for_device(client, device_info)
        # Sofort Status abrufen, damit HA schnell den Initialzustand bekommt.
        send_udp_cmd(device_info["ip"], "devStatus")

    client.subscribe(TOPIC_SET_WILDCARD)
    print(f"MQTT Subscription aktiv: {TOPIC_SET_WILDCARD}")

def on_mqtt_message(client, userdata, msg):
    """Verarbeitet Home Assistant Befehle fuer mehrere Govee-Geraete."""
    try:
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[0] != "govee" or parts[2] != "set":
            return

        target_mac = normalize_mac(parts[1])
        with devices_lock:
            target_device = devices_by_mac.get(target_mac)

        if not target_device:
            print(f"Unbekanntes Zielgeraet fuer Topic {msg.topic}")
            return

        payload = json.loads(msg.payload.decode('utf-8'))
        print(f"Befehl von HA empfangen fuer {target_device['name']}: {payload}")
        
        # An/Aus verarbeiten
        if "state" in payload:
            val = 1 if payload["state"] == "ON" else 0
            send_udp_cmd(target_device["ip"], "turn", val)
            time.sleep(0.1) # Kurze Pause, damit die Lampe nicht verschluckt
            
        # Helligkeit verarbeiten (HA sendet 0-255, Govee braucht 1-100)
        if "brightness" in payload:
            ha_bright = payload["brightness"]
            govee_bright = max(1, int((ha_bright / 255.0) * 100))
            send_udp_cmd(target_device["ip"], "brightness", govee_bright)
            
        # Nach dem Ändern direkt den Status neu abfragen!
        send_udp_cmd(target_device["ip"], "devStatus")
        
    except Exception as e:
        print(f"Fehler beim Verarbeiten der MQTT-Nachricht: {e}")

def udp_listener(mqtt_client):
    """Lauscht dauerhaft auf Antworten aller Lampen (Port 4002)."""
    while True:
        try:
            udp_sock.settimeout(None)
            data, addr = udp_sock.recvfrom(1024)
            response = json.loads(data.decode('utf-8'))

            msg = response.get("msg", {})
            if msg.get("cmd") != "devStatus":
                continue

            status_data = msg.get("data", {})
            status_mac_raw = status_data.get("device")
            status_mac = normalize_mac(status_mac_raw) if status_mac_raw else None

            with devices_lock:
                if not status_mac:
                    status_mac = ip_to_mac.get(addr[0])

                if not status_mac:
                    continue

                target_device = devices_by_mac.get(status_mac)
                if not target_device:
                    continue

                # Falls sich die IP ändert (z.B. DHCP), aktualisieren wir sie dynamisch.
                if target_device["ip"] != addr[0]:
                    target_device["ip"] = addr[0]
                    ip_to_mac[addr[0]] = status_mac

                topic_state = target_device["topics"]["state"]

            # Govee-Status in Home Assistant (JSON Schema) Format übersetzen
            is_on = status_data.get("onOff", 0)
            govee_bright = status_data.get("brightness", 100)

            # Govee 1-100 -> HA 0-255
            ha_bright = int((govee_bright / 100.0) * 255)

            ha_payload = {
                "state": "ON" if is_on == 1 else "OFF",
                "brightness": ha_bright
            }

            # An Home Assistant (MQTT) senden
            mqtt_client.publish(topic_state, json.dumps(ha_payload))
                
        except Exception as e:
            print(f"UDP Listener Fehler: {e}")

def status_poller():
    """Fragt alle gefundenen Lampen periodisch nach ihrem Status."""
    while True:
        with devices_lock:
            target_ips = [d["ip"] for d in devices_by_mac.values()]

        for target_ip in target_ips:
            send_udp_cmd(target_ip, "devStatus")
            time.sleep(0.05)

        time.sleep(STATUS_POLL_SECONDS)

# ==========================================
# HAUPTPROGRAMM STARTEN
# ==========================================
if __name__ == "__main__":
    mqtt_config = load_mqtt_config_or_exit()

    found_count = discover_govee_devices()
    if found_count == 0:
        print("Keine Govee-Lampe im LAN gefunden. Bitte LAN Control in der Govee App aktivieren.")
        sys.exit(1)

    print(f"Discovery abgeschlossen: {found_count} Govee-Geraet(e) eingebunden")
    with devices_lock:
        for d in devices_by_mac.values():
            print(f" - {d['name']}: MAC={d['mac']} IP={d['ip']} SKU={d.get('sku')}")

    # MQTT Setup
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(mqtt_config.user, mqtt_config.password)
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message
    
    client.connect(mqtt_config.host, mqtt_config.port, 60)

    # Threads für UDP-Listener und Poller starten
    threading.Thread(target=udp_listener, args=(client,), daemon=True).start()
    threading.Thread(target=status_poller, daemon=True).start()

    print("Govee MQTT Bridge gestartet (Multi-Device)...")
    
    # MQTT Loop blockierend starten
    client.loop_forever()