import json
import tempfile
import unittest
from pathlib import Path

from mqtt_ble.config import AppConfigError, load_app_config
from mqtt_ble.devices import is_supported_light, light_control_model


class AppConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve() / "config.json"

    def load(self, data, environ=None):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return load_app_config(self.path, environ={} if environ is None else environ)

    def test_partial_override_preserves_defaults(self):
        config = self.load({"ble": {"sensor_publish_seconds": 12}})
        self.assertEqual(config.ble.sensor_publish_seconds, 12)
        self.assertEqual(config.ble.light_publish_seconds, 30)
        self.assertEqual(config.lan.discovery_retries, 3)
        self.assertEqual(config.ble.state_dir, self.path.parent / "state")

    def test_custom_models_and_disabled_fallback_require_no_code_edit(self):
        config = self.load(
            {
                "ble": {
                    "light_models": ["h1234"],
                    "light_model_prefixes": [],
                    "light_name_prefixes": [],
                    "fallback_light_model": None,
                }
            }
        ).ble
        self.assertTrue(is_supported_light("Govee H1234", config))
        self.assertFalse(is_supported_light("Govee H6099", config))
        self.assertIsNone(light_control_model("Unidentified", config))

    def test_lists_replace_defaults_and_macs_are_normalized(self):
        config = self.load(
            {
                "lan": {
                    "allowed_macs": ["aa:bb:cc:dd:ee:ff"],
                    "custom_device_names": {"aa:bb:cc:dd:ee:ff": "Desk light"},
                }
            }
        )
        self.assertEqual(config.lan.allowed_macs, ("AABBCCDDEEFF",))
        self.assertEqual(config.lan.custom_device_names, {"AABBCCDDEEFF": "Desk light"})

    def test_environment_overrides_json(self):
        config = self.load(
            {"ble": {"learn_seconds": 12}},
            {
                "MQTT_BLE_LEARN_SECONDS": "45",
                "MQTT_BLE_STATE_DIR": "custom-state",
            },
        )
        self.assertEqual(config.ble.learn_seconds, 45)
        self.assertEqual(config.ble.state_dir, self.path.parent / "custom-state")

    def test_environment_selects_config_file(self):
        self.path.write_text('{"ble": {"learn_seconds": 42}}', encoding="utf-8")
        config = load_app_config(environ={"MQTT_BLE_CONFIG": str(self.path)})
        self.assertEqual(config.ble.learn_seconds, 42)

    def test_explicit_missing_file_is_an_error(self):
        with self.assertRaises(AppConfigError):
            load_app_config(self.path, environ={})

    def test_invalid_json_does_not_echo_contents(self):
        self.path.write_text("private-device-details", encoding="utf-8")
        with self.assertRaises(AppConfigError) as error:
            load_app_config(self.path, environ={})
        self.assertNotIn("private-device-details", str(error.exception))

    def test_invalid_settings_fail_before_runtime(self):
        cases = [
            [],
            {"unknown": {}},
            {"ble": []},
            {"ble": {"typo": 1}},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(AppConfigError):
                self.load(data)

    def test_invalid_intervals_lists_and_names_are_rejected(self):
        cases = [
            {"ble": {"sensor_publish_seconds": value}}
            for value in (0, -1, True, "30", float("nan"), float("inf"))
        ]
        cases += [
            {"ble": {"light_models": "H6099"}},
            {"ble": {"light_models": [""]}},
            {"ble": {"fallback_light_model": 9}},
            {"ble": {"state_dir": ""}},
            {"lan": {"discovery_retries": 1.5}},
            {"lan": {"custom_device_names": []}},
            {"lan": {"custom_device_names": {"AABB": ""}}},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(AppConfigError):
                self.load(data)

    def test_invalid_legacy_environment_duration_is_rejected(self):
        for value in ("invalid", "0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(AppConfigError):
                self.load({}, {"MQTT_BLE_LEARN_SECONDS": value})

    def test_example_is_valid_without_mqtt_credentials(self):
        example = Path(__file__).resolve().parents[1] / "config" / "config.example.json"
        load_app_config(example, environ={})
