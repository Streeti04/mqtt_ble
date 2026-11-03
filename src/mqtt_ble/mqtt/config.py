"""Load MQTT credentials from the environment and a local mqtt.env file.

Environment variables take precedence. MQTT_ENV_FILE selects another file.
Missing credentials fail explicitly; credential values are never logged.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mqtt_ble.paths import default_config_dir

REQUIRED_KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASSWORD")


class MqttConfigError(RuntimeError):
    """Missing or invalid credentials; messages never include their values."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)


def parse_env_text(text):
    """Parse KEY=VALUE lines without dependencies.

    Ignore blank lines and comments, accept optional export and surrounding quotes.
    Keep inline hash characters because they may be part of a password.
    """
    values = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise MqttConfigError(f"Line {lineno}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            raise MqttConfigError(f"Line {lineno}: empty key")
        values[key] = value
    return values


def load_mqtt_config(env_file=None, environ=None):
    """Load and validate credentials, raising MqttConfigError on failure."""
    environ = os.environ if environ is None else environ
    path = Path(
        env_file or environ.get("MQTT_ENV_FILE") or default_config_dir() / "mqtt.env"
    )

    file_values = {}
    file_error = None
    if path.is_file():
        try:
            file_values = parse_env_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            raise MqttConfigError(f"Cannot read MQTT credentials from {path}") from None
    else:
        file_error = f"File {path} not found"

    merged = {}
    for key in REQUIRED_KEYS:
        value = environ.get(key)
        if value is None or value == "":
            value = file_values.get(key, "")
        merged[key] = value

    missing = [k for k in REQUIRED_KEYS if not merged[k]]
    if missing:
        hint = f" ({file_error})" if file_error else f" (read: {path})"
        raise MqttConfigError(
            "Incomplete MQTT configuration; missing: "
            + ", ".join(missing)
            + hint
            + ". Copy config/mqtt.env.example to mqtt.env and fill in the settings."
        )

    for key in ("MQTT_USER", "MQTT_PASSWORD"):
        if merged[key] == "CHANGE_ME":
            raise MqttConfigError(f"{key} still contains the CHANGE_ME placeholder")

    try:
        port = int(merged["MQTT_PORT"])
    except ValueError:
        raise MqttConfigError("MQTT_PORT must be an integer") from None
    if not 0 < port < 65536:
        raise MqttConfigError("MQTT_PORT must be between 1 and 65535")

    return MqttConfig(
        host=merged["MQTT_HOST"],
        port=port,
        user=merged["MQTT_USER"],
        password=merged["MQTT_PASSWORD"],
    )


def load_mqtt_config_or_exit(env_file=None, environ=None):
    """Exit with status 2 and a clear message when credentials are invalid."""
    try:
        return load_mqtt_config(env_file=env_file, environ=environ)
    except MqttConfigError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr, flush=True)
        sys.exit(2)
