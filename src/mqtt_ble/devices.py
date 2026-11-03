"""Pure device matching and payload helpers shared by the bridges.

Model compatibility is configured in JSON. Manufacturer IDs and byte layouts
below are wire protocol constants, rather than installation-specific settings.
"""

import re

SENSOR_COMPANY_ID = 0xEC88
LIGHT_MODEL_PATTERN = re.compile(r"H\d{4}")


def normalize_mac(address):
    """Normalize both hardware MACs and macOS CoreBluetooth UUIDs."""
    return address.replace(":", "").replace("-", "").upper()


def advertised_light_model(name):
    match = LIGHT_MODEL_PATTERN.search((name or "").upper())
    return match.group(0) if match else None


def light_control_model(name, config):
    """Use the advertised model, or the explicitly configured fallback."""
    return advertised_light_model(name) or config.fallback_light_model


def is_supported_light(name, config):
    if not name:
        return False
    name = name.upper()
    model = advertised_light_model(name)
    return bool(
        (
            model
            and (
                model in config.light_models
                or model.startswith(config.light_model_prefixes)
            )
        )
        or name.startswith(config.light_name_prefixes)
    )


def is_supported_sensor(name, config):
    return bool(name and name.upper().startswith(config.sensor_name_prefixes))


def decode_sensor_data(data):
    """Decode the supported packed sensor format, rejecting invalid readings."""
    if len(data) < 5:
        return None
    packed = int.from_bytes(data[1:4], "big")
    # Preserve the original packed-reading calculation during this refactor.
    temperature = float(packed / 10000)
    humidity = (packed % 1000) / 10
    battery = data[4]
    if not (-30 <= temperature <= 180 and 0 <= humidity <= 100 and 0 <= battery <= 100):
        return None
    return {"temp_c": temperature, "hum": humidity, "batt": battery}


def ha_brightness_to_pct(value):
    return max(0, min(100, round(int(value) / 255 * 100)))


def pct_to_ha_brightness(value):
    return max(0, min(255, round(int(value) / 100 * 255)))


def validate_light_command(payload):
    """Reject malformed commands before scheduling any device operations."""
    if not isinstance(payload, dict):
        return False
    if "state" in payload and payload["state"] not in ("ON", "OFF"):
        return False
    if "brightness" in payload:
        value = payload["brightness"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 255
        ):
            return False
    return "state" in payload or "brightness" in payload
