"""Small MQTT wrapper with callbacks registered before connecting.

Originally based on https://gist.github.com/fisherds/f302b253cf7a11c2a0d814acd424b9bb,
adapted by Austin of austinsnerdythings.com (2021-12-27).
"""

import logging
import uuid

from paho.mqtt import client as mqtt_client

from mqtt_ble.mqtt.config import load_mqtt_config_or_exit

logger = logging.getLogger(__name__)


class MqttClient:
    """Own one Paho client; construction never opens a network connection."""

    def __init__(self, client_id=None, silent=False, config=None):
        self.config = config if config is not None else load_mqtt_config_or_exit()
        self.client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"govee_client_{uuid.uuid4().hex[:8]}",
        )
        self.silent = silent

    def connect(self, start_loop=True, on_connect=None, on_message=None):
        """Install callbacks before starting the network loop to avoid races."""
        self.client.on_connect = on_connect or self._on_connect
        self.client.on_message = on_message
        self.client.username_pw_set(self.config.user, self.config.password)
        self.client.connect(self.config.host, self.config.port, keepalive=30)
        if start_loop:
            self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if not self.silent:
            logger.info("MQTT connection result: %s", reason_code)

    def publish(self, topic, msg, retain=False):
        return self.client.publish(topic, msg, retain=retain)

    def disconnect(self):
        self.client.disconnect()
        self.client.loop_stop()

    def loop(self):
        self.client.loop_forever()
