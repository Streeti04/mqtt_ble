"""Check installed commands and configuration paths across installation modes."""

import json
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import distribution
from pathlib import Path

import mqtt_ble

CHECKOUT = Path(__file__).resolve().parents[1]
EDITABLE = Path(mqtt_ble.__file__).resolve().parent == CHECKOUT / "src" / "mqtt_ble"


class PackagingTests(unittest.TestCase):
    def run_isolated(self, directory, script):
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=directory,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_console_commands_resolve_to_callable_entry_points(self):
        commands = {
            entry.name: entry
            for entry in distribution("mqtt-ble").entry_points
            if entry.group == "console_scripts"
        }
        self.assertEqual(
            set(commands), {"mqtt-ble-observer", "mqtt-ble-lan", "mqtt-ble-monitor"}
        )
        for name, entry in commands.items():
            with self.subTest(command=name):
                self.assertTrue(callable(entry.load()))

    def write_config(self, directory):
        directory.joinpath("config.json").write_text(
            json.dumps({"ble": {"learn_seconds": 42}}), encoding="utf-8"
        )
        directory.joinpath("mqtt.env").write_text(
            "MQTT_HOST=example.invalid\nMQTT_PORT=1884\n"
            "MQTT_USER=dummy-user\nMQTT_PASSWORD=dummy-password\n",
            encoding="utf-8",
        )

    def test_explicit_config_paths_work_from_another_directory(self):
        with tempfile.TemporaryDirectory() as config_dir:
            directory = Path(config_dir).resolve()
            self.write_config(directory)
            with tempfile.TemporaryDirectory() as working_dir:
                self.run_isolated(
                    working_dir,
                    f"""
from pathlib import Path
from mqtt_ble.config import load_app_config
from mqtt_ble.mqtt.config import load_mqtt_config
directory = Path({str(directory)!r})
config = load_app_config(environ={{'MQTT_BLE_CONFIG': str(directory / 'config.json')}})
assert config.ble.learn_seconds == 42
assert config.ble.state_dir == directory / 'state'
mqtt = load_mqtt_config(environ={{'MQTT_ENV_FILE': str(directory / 'mqtt.env')}})
assert mqtt.host == 'example.invalid'
assert mqtt.port == 1884
""",
                )

    @unittest.skipUnless(EDITABLE, "Requires an editable installation")
    def test_editable_install_keeps_checkout_state_path_from_another_directory(self):
        with tempfile.TemporaryDirectory() as working_dir:
            self.run_isolated(
                working_dir,
                f"""
from pathlib import Path
from mqtt_ble.config import load_app_config
config = load_app_config(environ={{}})
assert config.ble.state_dir == Path({str(CHECKOUT)!r}) / 'state'
""",
            )

    @unittest.skipIf(EDITABLE, "Requires a wheel installation")
    def test_wheel_reads_local_settings_and_keeps_state_outside_package(self):
        with tempfile.TemporaryDirectory() as working_dir:
            directory = Path(working_dir).resolve()
            self.write_config(directory)
            self.run_isolated(
                directory,
                """
from pathlib import Path
from mqtt_ble.config import load_app_config
from mqtt_ble.mqtt.config import load_mqtt_config
config = load_app_config(environ={})
assert config.ble.learn_seconds == 42
assert config.ble.state_dir == Path.cwd() / 'state'
assert load_mqtt_config(environ={}).host == 'example.invalid'
""",
            )
