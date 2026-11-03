import importlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app_config import load_app_config
from basic_mqtt import MqttClient
from govee_lan import LanBridge
from ha_discovery import LEARN_COMMAND_TOPIC, light_device_id, sensor_device_id
from mqtt_config import MqttConfig
from observe_bleak import BleObserver


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = load_app_config(environ={})
        self.ble = replace(self.config.ble, state_dir=Path(self.tmp.name))
        self.mqtt = mock.Mock()
        self.clock = mock.Mock(return_value=10)
        self.observer = BleObserver(
            self.ble, self.mqtt, controller=mock.Mock(devices={}), clock=self.clock
        )
        self.device = SimpleNamespace(name="GVH5075_TEST", address="AA:BB-CC:DD")
        self.data = b"\x00" + (235567).to_bytes(3, "big") + bytes([85])
        self.advertisement = SimpleNamespace(
            manufacturer_data={0xEC88: self.data}, rssi=-60
        )

    def test_unknown_sensor_is_not_published_without_learning(self):
        self.observer.on_advertisement(self.device, self.advertisement)
        self.mqtt.publish.assert_not_called()
        self.assertEqual(len(self.observer.allowlist), 0)

    def test_learned_sensor_keeps_legacy_id_and_publishes_after_interval(self):
        self.observer.handle_learn_command(b"ON")
        self.mqtt.reset_mock()
        self.observer.on_advertisement(self.device, self.advertisement)
        self.assertTrue(self.observer.allowlist.contains(sensor_device_id("AABB-CCDD")))
        self.assertTrue((Path(self.tmp.name) / "known_devices.json").is_file())
        self.assertEqual(self.mqtt.publish.call_count, 4)
        self.mqtt.reset_mock()
        self.observer.on_advertisement(self.device, self.advertisement)
        self.mqtt.publish.assert_not_called()
        self.clock.return_value += self.ble.sensor_publish_seconds
        self.observer.on_advertisement(self.device, self.advertisement)
        self.mqtt.publish.assert_called_once()
        self.assertEqual(self.mqtt.publish.call_args.args[0], "govee/AABB-CCDD")

    def test_retained_learning_command_cannot_admit_new_devices(self):
        message = SimpleNamespace(topic=LEARN_COMMAND_TOPIC, payload=b"ON", retain=True)
        self.observer.on_message(None, None, message)
        self.observer.on_advertisement(self.device, self.advertisement)
        self.assertFalse(self.observer.learn_window.is_active())
        self.mqtt.publish.assert_not_called()

    def test_presence_only_fallback_does_not_create_a_controllable_entity(self):
        observer = BleObserver(
            replace(self.ble, fallback_light_model=None),
            self.mqtt,
            controller=mock.Mock(devices={}),
            clock=self.clock,
        )
        observer.allowlist.add(light_device_id("AABBCCDD"))
        device = SimpleNamespace(name="H6_unknown", address="AA:BB-CC:DD")
        observer.on_advertisement(device, self.advertisement)
        topics = [call.args[0] for call in self.mqtt.publish.call_args_list]
        self.assertIn(
            "homeassistant/sensor/govee_ble_light_aabbccdd/rssi/config", topics
        )
        self.assertNotIn("homeassistant/light/govee_ble_light_aabbccdd/config", topics)
        observer.controller.register_ble_device.assert_not_called()

    def test_light_model_from_configuration_reaches_controller(self):
        config = replace(
            self.ble, light_name_prefixes=("CUSTOM",), fallback_light_model="H1234"
        )
        observer = BleObserver(
            config, self.mqtt, controller=mock.Mock(devices={}), clock=self.clock
        )
        observer.allowlist.add(light_device_id("AABBCCDD"))
        observer.on_advertisement(
            SimpleNamespace(name="CUSTOM", address="AA:BB-CC:DD"), self.advertisement
        )
        self.assertEqual(observer.light_devices["AABBCCDD"].model, "H1234")

    def test_importing_entry_points_has_no_credentials_or_socket_side_effects(self):
        # Dependencies are loaded first; only application imports are under test.
        import goove_mqtt_bridge
        import monitor_mqtt

        with (
            mock.patch("socket.socket", side_effect=AssertionError("socket opened")),
            mock.patch(
                "basic_mqtt.load_mqtt_config_or_exit",
                side_effect=AssertionError("credentials loaded"),
            ),
        ):
            for module in (
                importlib.import_module("observe_bleak"),
                importlib.import_module("govee_lan"),
                monitor_mqtt,
                goove_mqtt_bridge,
            ):
                importlib.reload(module)


class MqttCallbackTests(unittest.TestCase):
    def test_callbacks_are_installed_before_connect_and_loop(self):
        config = MqttConfig("example.invalid", 1883, "dummy-user", "dummy-password")
        with mock.patch("basic_mqtt.mqtt_client.Client") as constructor:
            mqtt = MqttClient(config=config)
            connected = mock.Mock()
            received = mock.Mock()

            def verify_callbacks(*args, **kwargs):
                self.assertIs(mqtt.client.on_connect, connected)
                self.assertIs(mqtt.client.on_message, received)

            constructor.return_value.connect.side_effect = verify_callbacks
            constructor.return_value.loop_start.side_effect = verify_callbacks
            mqtt.connect(on_connect=connected, on_message=received)
            constructor.return_value.loop_start.assert_called_once()


class LanTests(unittest.TestCase):
    def setUp(self):
        self.config = load_app_config(environ={}).lan
        self.mqtt = mock.Mock()
        self.bridge = LanBridge(self.config, self.mqtt)
        self.bridge.sock = mock.Mock()
        self.device = self.bridge.register_device(
            {"device": "AA:BB:CC:DD", "sku": "Test"}, "192.0.2.10"
        )

    def test_allowlist_and_custom_names_use_normalized_addresses(self):
        config = replace(
            self.config,
            allowed_macs=("AABBCCDD",),
            custom_device_names={"AABBCCDD": "Desk"},
        )
        bridge = LanBridge(config, self.mqtt)
        self.assertIsNone(bridge.register_device({"device": "Other"}, "192.0.2.20"))
        device = bridge.register_device({"device": "AA:BB:CC:DD"}, "192.0.2.10")
        self.assertEqual(device["name"], "Desk")

    def test_dhcp_change_removes_stale_mapping_and_preserves_state_topic(self):
        self.bridge.handle_status(
            {
                "msg": {
                    "cmd": "devStatus",
                    "data": {
                        "device": "AA:BB:CC:DD",
                        "onOff": 1,
                        "brightness": 50,
                    },
                }
            },
            "192.0.2.11",
        )
        self.assertNotIn("192.0.2.10", self.bridge.ip_to_mac)
        self.assertEqual(self.device["ip"], "192.0.2.11")
        topic, payload = self.mqtt.publish.call_args.args
        self.assertEqual(topic, "govee/AABBCCDD/state")
        self.assertEqual(json.loads(payload), {"state": "ON", "brightness": 127})

    def test_invalid_command_does_not_send_udp(self):
        for payload in (b"[]", b"broken", b'{"brightness": 1000}'):
            self.bridge.on_message(
                None, None, SimpleNamespace(topic="govee/AABBCCDD/set", payload=payload)
            )
        self.bridge.sock.sendto.assert_not_called()

    def test_valid_command_sends_power_brightness_then_status(self):
        with mock.patch("govee_lan.time.sleep"):
            self.bridge.on_message(
                None,
                None,
                SimpleNamespace(
                    topic="govee/AABBCCDD/set",
                    payload=b'{"state":"ON","brightness":255}',
                ),
            )
        packets = [
            json.loads(call.args[0])["msg"]
            for call in self.bridge.sock.sendto.call_args_list
        ]
        self.assertEqual(
            [packet["cmd"] for packet in packets], ["turn", "brightness", "devStatus"]
        )
        self.assertEqual(packets[1]["data"]["value"], 100)


class ObserverShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_mqtt_disconnects_if_scanner_start_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(load_app_config(environ={}).ble, state_dir=Path(tmp))
            mqtt = mock.Mock()
            mqtt.client.is_connected.return_value = False
            observer = BleObserver(config, mqtt, controller=mock.Mock(devices={}))
            scanner = mock.Mock(
                start=mock.AsyncMock(side_effect=OSError("Bluetooth unavailable")),
                stop=mock.AsyncMock(),
            )
            with (
                mock.patch("observe_bleak.BleakScanner", return_value=scanner),
                mock.patch(
                    "observe_bleak.bleak_autorelease.install", return_value=True
                ),
            ):
                with self.assertRaises(OSError):
                    await observer.run()
            mqtt.disconnect.assert_called_once()


class BleControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_brightness_command_uses_configured_light_and_publishes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(load_app_config(environ={}).ble, state_dir=Path(tmp))
            mqtt = mock.Mock()
            controller = mock.Mock(
                devices={},
                set_power_state=mock.AsyncMock(),
                set_brightness=mock.AsyncMock(),
            )
            observer = BleObserver(config, mqtt, controller=controller)
            light = SimpleNamespace(name="H6099", address="AABB")
            observer.register_light(light, "AABB")
            await observer.handle_light_command(
                "AABB", {"state": "ON", "brightness": 255}
            )
            registered = observer.light_devices["AABB"]
            controller.set_power_state.assert_awaited_once_with(registered, True)
            controller.set_brightness.assert_awaited_once_with(registered, 100)
            topic, payload = mqtt.publish.call_args.args
            self.assertEqual(topic, "govee_ble/light/AABB/state")
            self.assertEqual(json.loads(payload), {"state": "ON", "brightness": 255})
            controller.reset_mock()
            await observer.handle_light_command(
                "AABB", {"state": "OFF", "brightness": 100}
            )
            controller.set_power_state.assert_awaited_once_with(registered, False)
            controller.set_brightness.assert_not_awaited()


class LanShutdownTests(unittest.TestCase):
    def test_no_devices_closes_socket_without_connecting_mqtt(self):
        mqtt = mock.Mock()
        bridge = LanBridge(load_app_config(environ={}).lan, mqtt)
        with (
            mock.patch("govee_lan.socket.socket") as socket_factory,
            mock.patch.object(bridge, "discover", return_value=0),
            self.assertLogs("govee_lan", level="ERROR"),
        ):
            self.assertEqual(bridge.run(), 1)
        socket_factory.return_value.close.assert_called_once()
        mqtt.connect.assert_not_called()

    def test_bind_failure_still_closes_socket(self):
        mqtt = mock.Mock()
        bridge = LanBridge(load_app_config(environ={}).lan, mqtt)
        with mock.patch("govee_lan.socket.socket") as socket_factory:
            socket_factory.return_value.bind.side_effect = OSError("port in use")
            with self.assertRaises(OSError):
                bridge.run()
        socket_factory.return_value.close.assert_called_once()
        mqtt.connect.assert_not_called()
