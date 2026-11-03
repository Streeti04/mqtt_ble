import unittest

from app_config import load_app_config
from govee_devices import (
    decode_sensor_data,
    ha_brightness_to_pct,
    is_supported_light,
    is_supported_sensor,
    light_control_model,
    normalize_mac,
    pct_to_ha_brightness,
    validate_light_command,
)
from ha_discovery import (
    lan_discovery,
    lan_topics,
    light_device_id,
    light_discovery,
    sensor_device_id,
    sensor_discovery,
)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.config = load_app_config(environ={}).ble

    def test_default_matching_preserves_supported_names(self):
        for name in ("Govee H6099", "H6101_ABCD", "H7000", "H6_unknown"):
            self.assertTrue(is_supported_light(name, self.config), name)
        for name in (None, "", "Unrelated device", "H1234"):
            self.assertFalse(is_supported_light(name, self.config), name)
        self.assertTrue(is_supported_sensor("GVH5075_ABCD", self.config))
        self.assertFalse(is_supported_sensor("Other", self.config))

    def test_model_extraction_and_configured_fallback(self):
        self.assertEqual(light_control_model("Govee H6123", self.config), "H6123")
        self.assertEqual(
            light_control_model("H6_unknown", self.config),
            self.config.fallback_light_model,
        )

    def test_mac_and_corebluetooth_uuid_normalization(self):
        self.assertEqual(normalize_mac("aa:bb-cc"), "AABBCC")
        self.assertEqual(normalize_mac("ABCD-1234"), "ABCD1234")

    def test_sensor_decoder_retains_existing_wire_calculation(self):
        data = b"\x00" + (235567).to_bytes(3, "big") + bytes([85])
        self.assertEqual(
            decode_sensor_data(data), {"temp_c": 23.5567, "hum": 56.7, "batt": 85}
        )
        self.assertIsNone(decode_sensor_data(b"\x00"))
        self.assertIsNone(decode_sensor_data(data[:-1] + bytes([255])))

    def test_brightness_scaling_and_clamping(self):
        self.assertEqual(ha_brightness_to_pct(255), 100)
        self.assertEqual(ha_brightness_to_pct(-5), 0)
        self.assertEqual(pct_to_ha_brightness(100), 255)
        self.assertEqual(pct_to_ha_brightness(150), 255)

    def test_malformed_commands_are_rejected(self):
        for payload in (
            [],
            None,
            {},
            {"state": "UNKNOWN"},
            {"brightness": True},
            {"brightness": "100"},
            {"brightness": -1},
            {"brightness": 256},
        ):
            self.assertFalse(validate_light_command(payload), payload)
        self.assertTrue(validate_light_command({"state": "ON", "brightness": 128}))


class DiscoveryCompatibilityTests(unittest.TestCase):
    def test_sensor_identifiers_and_payload_keys_stay_stable(self):
        address = "AABB-CCDD"
        self.assertEqual(sensor_device_id(address), "govee_aabb-ccdd")
        entities = sensor_discovery("GVH5075_TEST", address)
        self.assertEqual(
            [topic for topic, _ in entities],
            [
                f"homeassistant/sensor/govee_aabb-ccdd/{kind}/config"
                for kind in ("temperature", "humidity", "battery")
            ],
        )
        self.assertEqual(entities[0][1]["unique_id"], "govee_aabb-ccdd_temperature")
        self.assertEqual(entities[0][1]["state_topic"], "govee/AABB-CCDD")
        self.assertEqual(entities[0][1]["value_template"], "{{ value_json.temp_c }}")

    def test_ble_light_identifiers_and_command_topic_stay_stable(self):
        self.assertEqual(light_device_id("AABB"), "govee_ble_light_aabb")
        topic, payload = light_discovery("Govee H6099", "AABB")[-1]
        self.assertEqual(topic, "homeassistant/light/govee_ble_light_aabb/config")
        self.assertEqual(payload["unique_id"], "govee_ble_light_aabb_light")
        self.assertEqual(payload["command_topic"], "govee_ble/light/AABB/set")
        self.assertEqual(payload["device"]["model"], "H6099")

    def test_lan_light_identifiers_stay_stable(self):
        device = {
            "mac": "AABB",
            "name": "Desk",
            "sku": "Test",
            "topics": lan_topics("AABB"),
        }
        self.assertEqual(lan_discovery(device)["unique_id"], "govee_light_AABB")
        self.assertEqual(
            device["topics"]["discovery"], "homeassistant/light/govee_AABB/config"
        )
