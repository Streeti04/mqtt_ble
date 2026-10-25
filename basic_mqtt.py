"""
MQTT Helper-Klasse für grundlegende MQTT-Operationen
======================================================
Diese Hilfsklasse vereinfacht die Verwendung von MQTT (Message Queuing Telemetry Transport)
für das Veröffentlichen und Abonnieren von Nachrichten.

Ursprüngliche Quelle: https://gist.github.com/fisherds/f302b253cf7a11c2a0d814acd424b9bb
Modifiziert von: Austin of austinsnerdythings.com (2021-12-27)
"""

from paho.mqtt import client as mqtt_client
import logging
import datetime

from mqtt_config import load_mqtt_config_or_exit

# Konfiguration des Logging-Systems
# Zeigt Zeitstempel, Logger-Name, Level und Nachricht an
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO)

# ==========================================
# MQTT-Broker Konfigurationsparameter
# ==========================================
# Zugangsdaten (Host, Port, User, Passwort) kommen aus mqtt.env bzw. den
# Umgebungsvariablen MQTT_HOST/MQTT_PORT/MQTT_USER/MQTT_PASSWORD -> mqtt_config.py.
# Fehlen sie, bricht der Dienst mit klarer Fehlermeldung ab.
test_topic = "mqtt_test_topic"        # Topic für Testnachrichten

class basic_mqtt:
    """
    Einfache MQTT-Client-Wrapper-Klasse
    
    Diese Klasse kapselt die Funktionalität des paho-mqtt Clients und bietet
    eine vereinfachte Schnittstelle für grundlegende MQTT-Operationen.
    """
    
    def __init__(self, client_id=None, silent=False):
        """
        Initialisiert eine neue MQTT-Client-Instanz
        
        Erstellt einen neuen paho-mqtt Client und initialisiert die
        internen Variablen für Topics und Callbacks.
        
        Args:
            client_id (str): Eindeutige Client-ID (optional, wird auto-generiert wenn None)
            silent (bool): Wenn True, werden Verbindungs-Meldungen unterdrückt
        """
        import uuid
        # Generiere eindeutige Client-ID falls nicht angegeben
        if client_id is None:
            client_id = f"govee_client_{uuid.uuid4().hex[:8]}"
        
        self.client = mqtt_client.Client(client_id=client_id)  # Paho MQTT Client-Instanz mit ID
        self.subscription_topic_name = None         # Gespeicherter Abonnement-Topic
        self.publish_topic_name = None              # Gespeicherter Veröffentlichungs-Topic
        self.callback = None                        # Benutzerdefinierte Callback-Funktion
        self.config = load_mqtt_config_or_exit()    # Bricht bei fehlender Konfiguration ab
        self.host = self.config.host                # MQTT-Broker Host-Adresse
        self.silent = silent                        # Stiller Modus (keine Debug-Ausgaben)
    
    def connect(self, start_loop=True):
        """
        Stellt Verbindung zum MQTT-Broker her
        
        Konfiguriert die Callback-Funktionen, setzt Anmeldedaten und
        verbindet sich mit dem MQTT-Broker. Startet anschließend die
        Client-Loop im Hintergrund für asynchrone Nachrichtenverarbeitung.
        """
        # Registrierung der Callback-Funktionen für verschiedene Ereignisse
        self.client.on_connect = self.on_connect       # Wird bei erfolgreicher Verbindung aufgerufen
        self.client.on_subscribe = self.on_subscribe   # Wird bei erfolgreicher Abonnierung aufgerufen
        self.client.on_message = self.on_message       # Wird bei eingehender Nachricht aufgerufen
        
        # Authentifizierung mit Benutzername und Passwort
        self.client.username_pw_set(self.config.user, self.config.password)
        
        logging.info(f"connecting to MQTT broker at {self.config.host}:{self.config.port}")
        
        # Verbindungsaufbau zum MQTT-Broker
        # keepalive=30: Sendet alle 30 Sekunden ein Ping, um die Verbindung aktiv zu halten
        self.client.connect(host=self.config.host, port=self.config.port, keepalive=30)
        
        # Optional: Netzwerk-Loop in separatem Thread starten.
        # start_loop=False verwenden, wenn der Aufrufer loop_forever() nutzt.
        if start_loop:
            self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        """
        Callback-Funktion für erfolgreiche Verbindung
        
        Wird automatisch aufgerufen, wenn der Client sich erfolgreich mit
        dem MQTT-Broker verbunden hat.
        
        Args:
            client: Die MQTT-Client-Instanz
            userdata: Private Benutzerdaten (falls gesetzt)
            flags: Response-Flags vom Broker
            rc: Return Code (0 = Erfolg)
        """
        if not self.silent:
            print("Connected with result code "+str(rc))
        
        # Hinweis: Abonnements in on_connect() zu platzieren stellt sicher,
        # dass sie bei Verbindungsabbruch und Wiederverbindung erneuert werden
        # Beispiel: self.client.subscribe("$SYS/#")

    def on_message(self, client, userdata, msg):
        """
        Callback-Funktion für eingehende Nachrichten
        
        Wird aufgerufen, wenn eine Nachricht zu einem abonnierten Topic empfangen wird.
        
        Args:
            client: Die MQTT-Client-Instanz
            userdata: Private Benutzerdaten (falls gesetzt)
            msg: Die empfangene Nachricht (enthält topic und payload)
        """
        if not self.silent:
            print(f"got message of topic {msg.topic} with payload {msg.payload}")

    def on_subscribe(self, client, userdata, mid, granted_qos):
        """
        Callback-Funktion für erfolgreiche Abonnierung
        
        Wird aufgerufen, wenn ein Topic erfolgreich abonniert wurde.
        
        Args:
            client: Die MQTT-Client-Instanz
            userdata: Private Benutzerdaten (falls gesetzt)
            mid: Message ID der Abonnement-Anfrage
            granted_qos: Vom Broker gewährte Quality of Service Levels
        """
        if not self.silent:
            print("Subscribed: " + str(mid) + " " + str(granted_qos))

    def publish(self, topic, msg, retain=False):
        """
        Veröffentlicht eine Nachricht zu einem MQTT-Topic
        
        Args:
            topic (str): Das MQTT-Topic, zu dem publiziert werden soll
            msg (str): Die zu sendende Nachricht (kann JSON-String sein)
            retain (bool): Ob die Nachricht vom Broker gespeichert werden soll (für Discovery)
        """
        self.client.publish(topic=topic, payload=msg, retain=retain)

    def subscribe_to_test_topic(self, topic=test_topic):
        """
        Abonniert ein Test-Topic
        
        Args:
            topic (str): Das zu abonnierende Topic (Standard: test_topic)
        """
        self.client.subscribe(topic)

    def send_test_message(self, topic=test_topic):
        """
        Sendet eine Test-Nachricht mit aktuellem Zeitstempel
        
        Nützlich zum Testen der MQTT-Verbindung und Funktionalität.
        
        Args:
            topic (str): Das Topic für die Testnachricht (Standard: test_topic)
        """
        self.publish(topic=topic, msg=f"test message from python script at {datetime.datetime.now()}")

    def disconnect(self):
        """
        Trennt die Verbindung zum MQTT-Broker
        
        Beendet die Verbindung sauber und stoppt die Client-Loop.
        """
        # Sicher beenden, falls loop_start() verwendet wurde.
        self.client.loop_stop()
        self.client.disconnect()

    def loop(self):
        """
        Startet eine blockierende Event-Loop
        
        Hält das Programm am Laufen und verarbeitet MQTT-Ereignisse.
        Blockiert den aktuellen Thread bis disconnect() aufgerufen wird.
        """
        self.client.loop_forever()

# ==========================================
# Test-Bereich (wird nur bei direkter Ausführung dieser Datei ausgeführt)
# ==========================================
if __name__ == "__main__":
    """
    Test-Routine für die MQTT-Funktionalität
    
    Erstellt einen MQTT-Client, verbindet sich, abonniert ein Test-Topic,
    sendet eine Testnachricht und trennt die Verbindung wieder.
    """
    logging.info("running MQTT test")
    mqtt_helper = basic_mqtt()          # Erstelle MQTT-Client-Instanz
    mqtt_helper.connect()               # Verbinde mit Broker
    mqtt_helper.subscribe_to_test_topic()  # Abonniere Test-Topic
    mqtt_helper.send_test_message()     # Sende Testnachricht
    mqtt_helper.disconnect()            # Trenne Verbindung