import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from mqtt_ble.mqtt.monitor import GoveeMonitor


class MonitorTests(unittest.TestCase):
    def test_legacy_fahrenheit_readings_are_displayed_in_celsius(self):
        monitor = GoveeMonitor(mqtt=mock.Mock())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            monitor.on_message_received(
                None,
                None,
                SimpleNamespace(
                    topic="govee/TEST", payload=b'{"temp_f":68,"hum":50,"batt":80}'
                ),
            )
        self.assertIn("20.00°C", output.getvalue())
        self.assertEqual(monitor.message_count, 1)

    def test_non_sensor_payloads_are_ignored(self):
        monitor = GoveeMonitor(mqtt=mock.Mock())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            for payload in (
                b"[]",
                b"null",
                b"broken",
                b'{"state":"ON"}',
                b'{"hum":30}',
            ):
                monitor.on_message_received(
                    None, None, SimpleNamespace(topic="govee/TEST", payload=payload)
                )
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(monitor.message_count, 0)
