"""Local Govee LAN discovery, MQTT commands, and status polling."""

import json
import logging
import socket
import threading
import time

from app_config import load_app_config_or_exit
from basic_mqtt import MqttClient
from govee_devices import normalize_mac, validate_light_command
from ha_discovery import lan_discovery, lan_topics

logger = logging.getLogger(__name__)

# Govee LAN wire protocol endpoints; these do not depend on a user's devices.
SCAN_TARGET = ("239.255.255.250", 4001)
RESPONSE_PORT = 4002
CONTROL_PORT = 4003
COMMAND_TOPIC = "govee/+/set"


class LanBridge:
    """Keep device registry and socket lifetime inside one bridge instance."""

    def __init__(self, config, mqtt):
        self.config = config
        self.mqtt = mqtt
        self.devices = {}
        self.ip_to_mac = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.sock = None

    def register_device(self, data, source_ip):
        address = data.get("device")
        if not isinstance(address, str) or not address:
            return None
        address = normalize_mac(address)
        if self.config.allowed_macs and address not in self.config.allowed_macs:
            return None
        ip = data.get("ip") or source_ip
        if not isinstance(ip, str) or not ip:
            return None
        name = self.config.custom_device_names.get(address)
        if not name:
            name = data.get("deviceName") or data.get("devName") or data.get("name")
        if not name:
            name = " ".join(
                str(part)
                for part in (
                    self.config.device_name_prefix,
                    data.get("sku"),
                    address[-4:],
                )
                if part
            )
        with self.lock:
            previous = self.devices.get(address)
            if previous and previous["ip"] != ip:
                self.ip_to_mac.pop(previous["ip"], None)
            device = {
                "mac": address,
                "ip": ip,
                "sku": data.get("sku"),
                "name": str(name),
                "topics": lan_topics(address),
            }
            self.devices[address] = device
            self.ip_to_mac[ip] = address
        return device

    def discover(self):
        scan = json.dumps(
            {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
        ).encode()
        for attempt in range(self.config.discovery_retries):
            logger.info("Govee LAN discovery attempt %d", attempt + 1)
            self.sock.sendto(scan, SCAN_TARGET)
            deadline = time.monotonic() + self.config.discovery_timeout_seconds
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    data, source = self.sock.recvfrom(4096)
                except socket.timeout:
                    break
                try:
                    msg = json.loads(data)["msg"]
                    if msg.get("cmd") == "scan":
                        self.register_device(msg["data"], source[0])
                except (ValueError, KeyError, TypeError, AttributeError):
                    logger.debug("Ignoring malformed LAN discovery response")
        return len(self.devices)

    def send_command(self, ip, command, value=None):
        data = {} if value is None else {"value": value}
        payload = {"msg": {"cmd": command, "data": data}}
        try:
            self.sock.sendto(json.dumps(payload).encode(), (ip, CONTROL_PORT))
        except OSError:
            if not self.stop.is_set():
                logger.exception("LAN command could not be sent")

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            logger.error("MQTT connection failed: %s", reason_code)
            return
        with self.lock:
            devices = list(self.devices.values())
        for device in devices:
            self.mqtt.publish(
                device["topics"]["discovery"],
                json.dumps(lan_discovery(device)),
                retain=True,
            )
            self.send_command(device["ip"], "devStatus")
        client.subscribe(COMMAND_TOPIC)

    def on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[0] != "govee" or parts[-1] != "set":
            return
        with self.lock:
            device = self.devices.get(normalize_mac(parts[1]))
            ip = device["ip"] if device else None
        if ip is None:
            return
        try:
            payload = json.loads(msg.payload)
            if not validate_light_command(payload):
                logger.warning("Ignoring an invalid LAN light command")
                return
            if "state" in payload:
                self.send_command(ip, "turn", 1 if payload["state"] == "ON" else 0)
                # Allow the lamp to process power before a brightness update.
                time.sleep(0.1)
            if "brightness" in payload:
                self.send_command(
                    ip, "brightness", max(1, int(payload["brightness"] / 255 * 100))
                )
            self.send_command(ip, "devStatus")
        except (ValueError, UnicodeDecodeError, OSError):
            logger.warning("Could not process a LAN light command")

    def handle_status(self, packet, source_ip):
        """Translate a response and track DHCP changes without stale IP mappings."""
        msg = packet["msg"]
        if msg.get("cmd") != "devStatus":
            return
        data = msg["data"]
        address = data.get("device")
        with self.lock:
            address = (
                normalize_mac(address) if address else self.ip_to_mac.get(source_ip)
            )
            device = self.devices.get(address)
            if device is None:
                return
            if device["ip"] != source_ip:
                self.ip_to_mac.pop(device["ip"], None)
                device["ip"] = source_ip
                self.ip_to_mac[source_ip] = address
            topic = device["topics"]["state"]
        brightness = max(0, min(255, int(data.get("brightness", 100) / 100 * 255)))
        state = {
            "state": "ON" if data.get("onOff", 0) == 1 else "OFF",
            "brightness": brightness,
        }
        self.mqtt.publish(topic, json.dumps(state))

    def listen(self):
        self.sock.settimeout(1)
        while not self.stop.is_set():
            try:
                data, source = self.sock.recvfrom(4096)
                self.handle_status(json.loads(data), source[0])
            except socket.timeout:
                continue
            except OSError:
                if not self.stop.is_set():
                    logger.exception("LAN socket read failed")
                return
            except (ValueError, KeyError, TypeError, AttributeError):
                logger.debug("Ignoring malformed LAN status response")

    def poll_status(self):
        while not self.stop.is_set():
            with self.lock:
                ips = [device["ip"] for device in self.devices.values()]
            for ip in ips:
                if self.stop.is_set():
                    return
                try:
                    self.send_command(ip, "devStatus")
                except OSError:
                    if not self.stop.is_set():
                        logger.exception("LAN status request failed")
                if self.stop.wait(0.05):
                    return
            self.stop.wait(self.config.status_poll_seconds)

    def run(self):
        threads = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("0.0.0.0", RESPONSE_PORT))
            if self.discover() == 0:
                logger.error(
                    "No Govee lights found; enable LAN Control in the Govee app"
                )
                return 1
            self.mqtt.connect(
                start_loop=False, on_connect=self.on_connect, on_message=self.on_message
            )
            for target in (self.listen, self.poll_status):
                thread = threading.Thread(target=target, daemon=True)
                threads.append(thread)
                thread.start()
            logger.info("LAN bridge started with %d devices", len(self.devices))
            self.mqtt.loop()
            return 0
        finally:
            self.stop.set()
            for thread in threads:
                thread.join(timeout=2)
            self.sock.close()
            self.mqtt.disconnect()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_app_config_or_exit()
    bridge = LanBridge(config.lan, MqttClient(client_id="govee_lan_bridge"))
    try:
        raise SystemExit(bridge.run())
    except KeyboardInterrupt:
        logger.info("LAN bridge stopped")
    except OSError as exc:
        logger.error("LAN bridge could not start: %s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
