"""Load validated device settings separately from MQTT credentials.

Bundled defaults are overlaid with config.json, then legacy environment overrides.
Relative paths are anchored to the configuration file, never the working directory.
"""

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from govee_devices import normalize_mac

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULTS_FILE = PROJECT_DIR / "config" / "defaults.json"


class AppConfigError(ValueError):
    """Invalid application settings; messages identify keys, never values."""


@dataclass(frozen=True)
class BleConfig:
    sensor_name_prefixes: tuple[str, ...]
    light_models: tuple[str, ...]
    light_model_prefixes: tuple[str, ...]
    light_name_prefixes: tuple[str, ...]
    fallback_light_model: str | None
    sensor_publish_seconds: float
    light_publish_seconds: float
    cache_clear_seconds: float
    learn_seconds: float
    state_dir: Path


@dataclass(frozen=True)
class LanConfig:
    device_name_prefix: str
    custom_device_names: dict[str, str]
    allowed_macs: tuple[str, ...]
    discovery_timeout_seconds: float
    discovery_retries: int
    status_poll_seconds: float


@dataclass(frozen=True)
class AppConfig:
    ble: BleConfig
    lan: LanConfig


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AppConfigError(
            f"Cannot read a valid JSON configuration: {path}"
        ) from None
    if not isinstance(data, dict):
        raise AppConfigError("Configuration must be a JSON object")
    return data


def _positive(value, key, integer=False):
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise AppConfigError(
            f"{key} must be a positive {'integer' if integer else 'number'}"
        )
    if not math.isfinite(value) or value <= 0:
        raise AppConfigError(f"{key} must be positive and finite")
    return value


def _string(value, key):
    if not isinstance(value, str) or not value.strip():
        raise AppConfigError(f"{key} must be a nonempty string")
    return value


def _strings(value, key):
    if not isinstance(value, list):
        raise AppConfigError(f"{key} must be a list of nonempty strings")
    return tuple(_string(item, key).upper() for item in value)


def load_app_config(config_file=None, environ=None):
    """Load optional local overrides; explicitly selected files must exist."""
    environ = os.environ if environ is None else environ
    selected = config_file or environ.get("MQTT_BLE_CONFIG")
    path = (
        Path(selected).expanduser().resolve()
        if selected
        else PROJECT_DIR / "config.json"
    )
    data = _read_json(DEFAULTS_FILE)
    if selected or path.exists():
        overrides = _read_json(path)
        for section, values in overrides.items():
            if section not in data:
                raise AppConfigError(
                    "Unknown configuration section (expected ble or lan)"
                )
            if not isinstance(values, dict):
                raise AppConfigError(f"{section} must be an object")
            if values.keys() - data[section].keys():
                raise AppConfigError(f"Unknown setting in {section}")
            data[section].update(values)

    ble, lan = data["ble"], data["lan"]
    state_dir = _string(
        environ.get("MQTT_BLE_STATE_DIR") or ble["state_dir"], "ble.state_dir"
    )
    state_dir = Path(state_dir).expanduser()
    if not state_dir.is_absolute():
        state_dir = path.parent / state_dir
    if environ.get("MQTT_BLE_LEARN_SECONDS"):
        try:
            ble["learn_seconds"] = float(environ["MQTT_BLE_LEARN_SECONDS"])
        except ValueError:
            raise AppConfigError(
                "MQTT_BLE_LEARN_SECONDS must be a positive number"
            ) from None

    fallback = ble["fallback_light_model"]
    if fallback is not None:
        fallback = _string(fallback, "ble.fallback_light_model").upper()
    names = lan["custom_device_names"]
    if not isinstance(names, dict):
        raise AppConfigError("lan.custom_device_names must be an object")

    return AppConfig(
        ble=BleConfig(
            **{
                key: _strings(ble[key], f"ble.{key}")
                for key in (
                    "sensor_name_prefixes",
                    "light_models",
                    "light_model_prefixes",
                    "light_name_prefixes",
                )
            },
            fallback_light_model=fallback,
            **{
                key: _positive(ble[key], f"ble.{key}")
                for key in (
                    "sensor_publish_seconds",
                    "light_publish_seconds",
                    "cache_clear_seconds",
                    "learn_seconds",
                )
            },
            state_dir=state_dir,
        ),
        lan=LanConfig(
            device_name_prefix=_string(
                lan["device_name_prefix"], "lan.device_name_prefix"
            ),
            custom_device_names={
                normalize_mac(_string(key, "lan.custom_device_names")): _string(
                    value, "lan.custom_device_names"
                )
                for key, value in names.items()
            },
            allowed_macs=tuple(
                normalize_mac(mac)
                for mac in _strings(lan["allowed_macs"], "lan.allowed_macs")
            ),
            discovery_timeout_seconds=_positive(
                lan["discovery_timeout_seconds"], "lan.discovery_timeout_seconds"
            ),
            discovery_retries=_positive(
                lan["discovery_retries"], "lan.discovery_retries", integer=True
            ),
            status_poll_seconds=_positive(
                lan["status_poll_seconds"], "lan.status_poll_seconds"
            ),
        ),
    )


def load_app_config_or_exit():
    try:
        return load_app_config()
    except AppConfigError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(2) from None
