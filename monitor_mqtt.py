"""
MQTT Monitor für Govee-Sensordaten
===================================
Dieses Skript abonniert alle Govee-Topics und zeigt die eingehenden
Temperatur-, Luftfeuchtigkeits- und Batteriedaten an.

Verwendung: python monitor_mqtt.py
Beenden: Strg+C
"""

import json
import logging
from basic_mqtt import basic_mqtt
from datetime import datetime

# Logging-Konfiguration - nur Fehler anzeigen
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.ERROR)  # Nur Fehler, keine Info-Meldungen

# MQTT-Logger ebenfalls auf ERROR setzen
logging.getLogger("paho").setLevel(logging.ERROR)

class GoveeMonitor:
    """
    MQTT-Monitor für Govee-Sensordaten
    
    Abonniert alle govee/* Topics und zeigt eingehende Daten formatiert an.
    """
    
    def __init__(self):
        """Initialisiert den Monitor"""
        self.mqtt = basic_mqtt(client_id="govee_monitor", silent=True)  # Stiller Modus
        self.message_count = 0
        
    def on_message_received(self, client, userdata, msg):
        """
        Callback für eingehende MQTT-Nachrichten
        
        Zeigt nur Temperatur, Luftfeuchtigkeit und Batterie formatiert an.
        Alle Debug-Meldungen sind deaktiviert.
        """
        try:
            # Dekodiere JSON-Payload
            payload = json.loads(msg.payload.decode())
            
            # temp_c ist das aktuelle Feld; temp_f wird als Fallback akzeptiert
            temp = payload.get('temp_c', payload.get('temp_f'))
            if temp is None or 'hum' not in payload:
                return  # Überspringe unvollständige Nachrichten
            
            self.message_count += 1
            
            # Extrahiere MAC-Adresse aus Topic (govee/A4:C1:38:D7:25:C3)
            mac = msg.topic.split('/')[-1]
            
            # Kompakte Ausgabe
            zeit = datetime.now().strftime('%H:%M:%S')
            hum = payload.get('hum', 0)
            batt = payload.get('batt', 0)
            
            print(f"[{zeit}] {mac:20s} | {temp:6.2f}°C | {hum:5.1f}% | Batt: {batt:3d}%")
            
        except json.JSONDecodeError:
            pass  # Ignoriere fehlerhafte Nachrichten
        except Exception:
            pass  # Ignoriere andere Fehler
    
    def _on_connect(self, client, userdata, flags, rc):
        """Erneuert das Abo bei jeder (Wieder-)Verbindung — sonst bleibt der
        Monitor nach einem Broker-Neustart dauerhaft stumm."""
        if rc == 0:
            client.subscribe("govee/#")

    def start(self):
        """Startet den Monitor"""
        print("\n" + "="*80)
        print("  GOVEE MQTT MONITOR - Nur Sensordaten")
        print("="*80)
        
        # Keine zweite Hintergrund-Loop starten, da unten loop_forever() läuft.
        self.mqtt.connect(start_loop=False)
        # WICHTIG: basic_mqtt.connect() überschreibt alle Callbacks mit seinen
        # Silent-Handlern — unsere Handler daher erst NACH connect() setzen.
        # (Fix 29.08.2026: vorher landete jede Nachricht im Silent-Handler
        # und der Monitor druckte nie Sensorzeilen.)
        self.mqtt.client.on_message = self.on_message_received
        self.mqtt.client.on_connect = self._on_connect
        # Erstes Abo direkt nach connect(); Re-Subscribes übernimmt _on_connect
        self.mqtt.client.subscribe("govee/#")
        
        print("\n  Zeit     | Sensor (MAC)         | Temp   | Feuchte | Batterie")
        print("-" * 80)
        
        try:
            # Blockierende Loop - hält das Programm am Laufen
            self.mqtt.loop()
        except KeyboardInterrupt:
            print("\n" + "="*80)
            print(f"  Monitor beendet - {self.message_count} Nachrichten empfangen")
            print("="*80 + "\n")
            self.mqtt.disconnect()

if __name__ == "__main__":
    monitor = GoveeMonitor()
    monitor.start()
