"""Print Govee sensor readings from MQTT without accessing Bluetooth."""

import json
import logging
from datetime import datetime

from basic_mqtt import MqttClient


class GoveeMonitor:
    def __init__(self, mqtt=None):
        self.mqtt = (
            mqtt
            if mqtt is not None
            else MqttClient(client_id="govee_monitor", silent=True)
        )
        self.message_count = 0

    def on_message_received(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            if not isinstance(payload, dict) or "hum" not in payload:
                return
            if "temp_c" in payload:
                temperature = float(payload["temp_c"])
            elif "temp_f" in payload:
                temperature = (float(payload["temp_f"]) - 32) * 5 / 9
            else:
                return
            humidity = float(payload["hum"])
            battery = int(payload.get("batt", 0))
        except (ValueError, TypeError, UnicodeDecodeError):
            return
        address = msg.topic.split("/")[-1]
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.message_count += 1
        print(
            f"[{timestamp}] {address:20s} | {temperature:6.2f}°C | {humidity:5.1f}% | Batt: {battery:3d}%"
        )

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe("govee/#")

    def start(self):
        print(
            "GOVEE MQTT MONITOR\nTime       | Sensor               | Temp     | Humidity | Battery"
        )
        try:
            self.mqtt.connect(
                start_loop=False,
                on_connect=self.on_connect,
                on_message=self.on_message_received,
            )
            self.mqtt.loop()
        except KeyboardInterrupt:
            print(f"\nMonitor stopped; received {self.message_count} readings")
        finally:
            self.mqtt.disconnect()


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    GoveeMonitor().start()


if __name__ == "__main__":
    main()
