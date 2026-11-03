"""Run the BLE observer: configured device matching, learning, and light control."""

import asyncio
import json
import logging
import threading
import time

from bleak import BleakScanner
from govee_led_wez.govee import GoveeController, GoveeDevice

from mqtt_ble.ble import autorelease
from mqtt_ble.ble.learning import (
    DECISION_ALLOW,
    DECISION_LEARN,
    Allowlist,
    LearnWindow,
    OnceLogger,
    decide,
    parse_switch_payload,
)
from mqtt_ble.ble.scanner_cache import clear_scanner_cache
from mqtt_ble.config import load_app_config_or_exit
from mqtt_ble.devices import (
    SENSOR_COMPANY_ID,
    decode_sensor_data,
    ha_brightness_to_pct,
    is_supported_light,
    is_supported_sensor,
    light_control_model,
    normalize_mac,
    pct_to_ha_brightness,
    validate_light_command,
)
from mqtt_ble.mqtt.client import MqttClient
from mqtt_ble.mqtt.discovery import (
    LEARN_COMMAND_TOPIC,
    LEARN_DISCOVERY_TOPIC,
    LEARN_STATE_TOPIC,
    LIGHT_COMMAND_TOPIC_WILDCARD,
    OBSERVER_STATUS_TOPIC,
    learn_switch_discovery,
    light_device_id,
    light_discovery,
    sensor_device_id,
    sensor_discovery,
)

logger = logging.getLogger(__name__)


class BleObserver:
    """Own observer state; network and Bluetooth activity starts only in run()."""

    def __init__(self, config, mqtt, controller=None, clock=time.monotonic):
        self.config = config
        self.mqtt = mqtt
        self.controller = controller if controller is not None else GoveeController()
        self.clock = clock
        self.allowlist = Allowlist.load(config.state_dir / "known_devices.json")
        self.learn_window = LearnWindow(config.learn_seconds, clock=clock)
        self.ignored_log = OnceLogger()
        self.discovery = {}
        self.last_sensor_publish = {}
        self.last_light_publish = {}
        self.light_devices = {}
        self.light_states = {}
        self.light_lock = threading.Lock()
        self.event_loop = None

    def _publish_json(self, topic, payload, retain=False):
        self.mqtt.publish(topic, json.dumps(payload), retain=retain)

    def _publish_discovery(self, key, payloads):
        if key not in self.discovery:
            self.discovery[key] = payloads
            for topic, payload in payloads:
                self._publish_json(topic, payload, retain=True)

    def admit_device(self, device_id, name):
        decision = decide(device_id, self.allowlist, self.learn_window.is_active())
        if decision == DECISION_ALLOW:
            return True
        if decision == DECISION_LEARN:
            if self.allowlist.add(device_id, name=name, source="learn"):
                logger.info("Learned %s (%s)", name, device_id)
            return True
        if self.ignored_log.first_time(device_id):
            logger.info(
                "Ignoring %s (%s): device is not in the allowlist", name, device_id
            )
        return False

    def publish_learn_state(self):
        self.mqtt.publish(
            LEARN_STATE_TOPIC,
            "ON" if self.learn_window.is_active() else "OFF",
            retain=True,
        )

    def handle_learn_command(self, payload, retained=False):
        # Retained commands must never reopen learning after a restart.
        if retained:
            return
        wanted = parse_switch_payload(payload)
        if wanted is None:
            logger.warning("Ignoring an invalid learning command")
            return
        if wanted:
            self.learn_window.enable()
        else:
            self.learn_window.disable()
        self.publish_learn_state()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            logger.error("MQTT connection failed: %s", reason_code)
            return
        client.subscribe(LIGHT_COMMAND_TOPIC_WILDCARD)
        client.subscribe(LEARN_COMMAND_TOPIC)
        self.mqtt.publish(OBSERVER_STATUS_TOPIC, "online", retain=True)
        self._publish_json(LEARN_DISCOVERY_TOPIC, learn_switch_discovery(), retain=True)
        self.publish_learn_state()

    def on_message(self, client, userdata, msg):
        if msg.topic == LEARN_COMMAND_TOPIC:
            self.handle_learn_command(msg.payload, retained=msg.retain)
            return
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[:2] != ["govee_ble", "light"] or parts[-1] != "set":
            return
        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            logger.warning("Ignoring an invalid light command")
            return
        if not validate_light_command(payload) or self.event_loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.handle_light_command(parts[2], payload), self.event_loop
        )
        future.add_done_callback(self._command_done)

    @staticmethod
    def _command_done(future):
        if not future.cancelled() and future.exception():
            logger.error("BLE light command failed: %s", future.exception())

    def register_light(self, device, address):
        model = light_control_model(device.name, self.config)
        if model is None:
            # A null fallback allows presence discovery without guessing a protocol model.
            return False
        with self.light_lock:
            light = self.light_devices.get(address)
            if light is None:
                light = GoveeDevice(device_id=f"ble_{address}", model=model)
                self.light_devices[address] = light
                self.light_states[address] = {"state": "OFF", "brightness": 255}
            light.model = model
            light.ble_device = device
            self.controller.devices[light.device_id] = light
            self.controller.register_ble_device(device)
        return True

    async def handle_light_command(self, address, payload):
        with self.light_lock:
            light = self.light_devices.get(address)
            state = dict(self.light_states.get(address, {}))
        if light is None:
            return
        if payload.get("state") == "OFF":
            await self.controller.set_power_state(light, False)
            state["state"] = "OFF"
        else:
            if payload.get("state") == "ON":
                await self.controller.set_power_state(light, True)
                state["state"] = "ON"
            if "brightness" in payload:
                percent = ha_brightness_to_pct(payload["brightness"])
                await self.controller.set_brightness(light, percent)
                state.update(state="ON", brightness=pct_to_ha_brightness(percent))
        with self.light_lock:
            self.light_states[address] = state
        self._publish_json(f"govee_ble/light/{address}/state", state)

    def on_advertisement(self, device, advertisement):
        """Process supported advertisements without letting one device stop scanning."""
        try:
            self._process_advertisement(device, advertisement)
        except (OSError, ValueError, TypeError):
            logger.exception("Could not process a BLE advertisement")

    def _process_advertisement(self, device, advertisement):
        now = self.clock()
        # Retain the historical sensor identifier format, including UUID hyphens on macOS.
        sensor_address = device.address.replace(":", "")
        if is_supported_sensor(device.name, self.config) and self.admit_device(
            sensor_device_id(sensor_address), device.name
        ):
            data = advertisement.manufacturer_data.get(SENSOR_COMPANY_ID)
            reading = decode_sensor_data(data) if data is not None else None
            if reading is not None:
                self._publish_discovery(
                    sensor_device_id(sensor_address),
                    sensor_discovery(device.name, sensor_address),
                )
                previous = self.last_sensor_publish.get(sensor_address)
                if (
                    previous is None
                    or now - previous >= self.config.sensor_publish_seconds
                ):
                    self._publish_json(f"govee/{sensor_address}", reading)
                    self.last_sensor_publish[sensor_address] = now

        address = normalize_mac(device.address)
        if is_supported_light(device.name, self.config) and self.admit_device(
            light_device_id(address), device.name
        ):
            controllable = self.register_light(device, address)
            payloads = light_discovery(device.name, address)
            if not controllable:
                payloads = payloads[:2]
            self._publish_discovery(light_device_id(address), payloads)
            previous = self.last_light_publish.get(address)
            if previous is None or now - previous >= self.config.light_publish_seconds:
                self._publish_json(
                    f"govee_ble/light/{address}",
                    {
                        "name": device.name,
                        "online": True,
                        "rssi": advertisement.rssi,
                        "last_seen": int(time.time()),
                    },
                )
                with self.light_lock:
                    state = dict(self.light_states.get(address, {}))
                if controllable:
                    self._publish_json(f"govee_ble/light/{address}/state", state)
                self.last_light_publish[address] = now

    async def run(self):
        self.event_loop = asyncio.get_running_loop()
        if not autorelease.install():
            logger.warning("CoreBluetooth autorelease patch unavailable")
        scanner = BleakScanner(detection_callback=self.on_advertisement)
        self.mqtt.client.will_set(OBSERVER_STATUS_TOPIC, "offline", retain=True)
        started = False
        try:
            self.mqtt.connect(on_connect=self.on_connect, on_message=self.on_message)
            await scanner.start()
            started = True
            logger.info(
                "BLE observer started; %d known devices; learning OFF",
                len(self.allowlist),
            )
            last_clear = self.clock()
            while True:
                await asyncio.sleep(1)
                if self.learn_window.poll_expired():
                    self.publish_learn_state()
                if self.clock() - last_clear >= self.config.cache_clear_seconds:
                    if clear_scanner_cache(scanner) is None:
                        logger.warning(
                            "Bleak device cache unavailable; cache cleanup inactive"
                        )
                    last_clear = self.clock()
        finally:
            try:
                if started:
                    await scanner.stop()
            finally:
                try:
                    if self.mqtt.client.is_connected():
                        info = self.mqtt.publish(
                            OBSERVER_STATUS_TOPIC, "offline", retain=True
                        )
                        info.wait_for_publish(timeout=2)
                finally:
                    self.mqtt.disconnect()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("bleak").setLevel(logging.WARNING)
    config = load_app_config_or_exit()
    try:
        observer = BleObserver(
            config.ble, MqttClient(client_id="govee_scanner", silent=True)
        )
        asyncio.run(observer.run())
    except KeyboardInterrupt:
        logger.info("BLE observer stopped")
    except (OSError, ValueError) as exc:
        logger.error("BLE observer could not start: %s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
