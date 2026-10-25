import tempfile
import unittest
from pathlib import Path

from mqtt_config import MqttConfigError, load_mqtt_config, parse_env_text

DUMMY = "dummy-#-pw!"  # Testwert, kein echtes Secret


class ParseEnvTextTests(unittest.TestCase):
    def test_parses_comments_quotes_export_and_hash_in_value(self):
        values = parse_env_text(
            "# Kommentar\n\nexport MQTT_HOST=127.0.0.1\nMQTT_USER='u'\n"
            f'MQTT_PASSWORD="{DUMMY}"\nMQTT_PORT = 1883 \n'
        )
        self.assertEqual(values["MQTT_HOST"], "127.0.0.1")
        self.assertEqual(values["MQTT_USER"], "u")
        self.assertEqual(values["MQTT_PASSWORD"], DUMMY)
        self.assertEqual(values["MQTT_PORT"], "1883")

    def test_rejects_line_without_equals(self):
        with self.assertRaises(MqttConfigError):
            parse_env_text("NOPE\n")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.tmp.name) / "mqtt.env"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        self.env_file.write_text(text, encoding="utf-8")

    def test_loads_from_file(self):
        self.write(f"MQTT_HOST=h\nMQTT_PORT=1884\nMQTT_USER=u\nMQTT_PASSWORD={DUMMY}\n")
        cfg = load_mqtt_config(env_file=self.env_file, environ={})
        self.assertEqual((cfg.host, cfg.port, cfg.user, cfg.password), ("h", 1884, "u", DUMMY))
        self.assertNotIn(DUMMY, repr(cfg))

    def test_environment_overrides_file(self):
        self.write(f"MQTT_HOST=h\nMQTT_PORT=1883\nMQTT_USER=u\nMQTT_PASSWORD={DUMMY}\n")
        cfg = load_mqtt_config(env_file=self.env_file, environ={"MQTT_USER": "other"})
        self.assertEqual(cfg.user, "other")

    def test_environment_only_without_file(self):
        env = {"MQTT_HOST": "h", "MQTT_PORT": "1883", "MQTT_USER": "u", "MQTT_PASSWORD": DUMMY}
        cfg = load_mqtt_config(env_file=self.env_file, environ=env)
        self.assertEqual(cfg.password, DUMMY)

    def test_missing_file_fails_clearly(self):
        with self.assertRaises(MqttConfigError) as ctx:
            load_mqtt_config(env_file=self.env_file, environ={})
        self.assertIn("MQTT_PASSWORD", str(ctx.exception))
        self.assertIn("nicht gefunden", str(ctx.exception))

    def test_empty_password_fails_and_value_not_leaked(self):
        self.write(f"MQTT_HOST=h\nMQTT_PORT=1883\nMQTT_USER={DUMMY}\nMQTT_PASSWORD=\n")
        with self.assertRaises(MqttConfigError) as ctx:
            load_mqtt_config(env_file=self.env_file, environ={})
        self.assertIn("MQTT_PASSWORD", str(ctx.exception))
        self.assertNotIn(DUMMY, str(ctx.exception))

    def test_placeholder_rejected(self):
        self.write("MQTT_HOST=h\nMQTT_PORT=1883\nMQTT_USER=u\nMQTT_PASSWORD=CHANGE_ME\n")
        with self.assertRaises(MqttConfigError):
            load_mqtt_config(env_file=self.env_file, environ={})

    def test_bad_port(self):
        self.write(f"MQTT_HOST=h\nMQTT_PORT=abc\nMQTT_USER=u\nMQTT_PASSWORD={DUMMY}\n")
        with self.assertRaises(MqttConfigError):
            load_mqtt_config(env_file=self.env_file, environ={})

    def test_example_file_is_placeholder_only(self):
        example = Path(__file__).resolve().parents[1] / "mqtt.env.example"
        values = parse_env_text(example.read_text(encoding="utf-8"))
        self.assertEqual(values["MQTT_PASSWORD"], "CHANGE_ME")
        self.assertEqual(values["MQTT_USER"], "CHANGE_ME")


if __name__ == "__main__":
    unittest.main()
