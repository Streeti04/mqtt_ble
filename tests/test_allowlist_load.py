import tempfile
import unittest
from pathlib import Path
from unittest import mock

from learn_mode import Allowlist
from mqtt_config import MqttConfigError, load_mqtt_config


class AllowlistLoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "known_devices.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_top_level_list_is_rejected_not_silently_emptied(self):
        self.path.write_text('["govee_abc"]', encoding="utf-8")
        with self.assertRaises(ValueError):
            Allowlist.load(self.path)
        # Datei bleibt unverändert, nichts wurde überschrieben
        self.assertEqual(self.path.read_text(encoding="utf-8"), '["govee_abc"]')

    def test_dict_without_devices_key_is_rejected(self):
        self.path.write_text('{"version": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            Allowlist.load(self.path)

    def test_valid_file_still_loads(self):
        self.path.write_text('{"version": 1, "devices": {"govee_x": {}}}', encoding="utf-8")
        self.assertEqual(Allowlist.load(self.path).ids(), ["govee_x"])

    def test_failed_save_does_not_keep_device_in_memory(self):
        allowlist = Allowlist(self.path)
        with mock.patch.object(Allowlist, "_save_locked", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                allowlist.add("govee_new")
        self.assertFalse(allowlist.contains("govee_new"))


class ChangeMeUserTests(unittest.TestCase):
    def test_placeholder_user_is_rejected(self):
        env = {"MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1883", "MQTT_USER": "CHANGE_ME", "MQTT_PASSWORD": "secret"}
        with self.assertRaises(MqttConfigError):
            load_mqtt_config(env_file="/nonexistent", environ=env)


if __name__ == "__main__":
    unittest.main()
