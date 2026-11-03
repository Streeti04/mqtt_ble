"""Build Home Assistant Discovery payloads without hardware or network access.

Device identifiers and topic names remain stable across the refactor so that
existing entities and persisted allowlists continue to work.
"""

from govee_devices import advertised_light_model

LEARN_COMMAND_TOPIC = "mqtt_ble/observe/learn/set"
LEARN_STATE_TOPIC = "mqtt_ble/observe/learn/state"
LEARN_DISCOVERY_TOPIC = "homeassistant/switch/mqtt_ble_observe_learn/config"
OBSERVER_STATUS_TOPIC = "mqtt_ble/observe/status"
LIGHT_COMMAND_TOPIC_WILDCARD = "govee_ble/light/+/set"


def sensor_device_id(address):
    return f"govee_{address.lower()}"


def light_device_id(address):
    return f"govee_ble_light_{address.lower()}"


def sensor_discovery(name, address):
    device_id = sensor_device_id(address)
    model, separator, short_id = name.partition("_")
    short_id = short_id if separator else address[-4:]
    device = {
        "identifiers": [device_id],
        "name": f"Govee {short_id}",
        "model": model,
        "manufacturer": "Govee",
    }
    entities = (
        ("temperature", "Temperature", "temp_c", "°C"),
        ("humidity", "Humidity", "hum", "%"),
        ("battery", "Battery", "batt", "%"),
    )
    return [
        (
            f"homeassistant/sensor/{device_id}/{kind}/config",
            {
                "name": f"Govee {short_id} {label}",
                "unique_id": f"{device_id}_{kind}",
                "state_topic": f"govee/{address}",
                "value_template": "{{ value_json." + field + " }}",
                "unit_of_measurement": unit,
                "device_class": kind,
                "state_class": "measurement",
                "device": device,
            },
        )
        for kind, label, field, unit in entities
    ]


def light_discovery(name, address):
    device_id = light_device_id(address)
    base_topic = f"govee_ble/light/{address}"
    device = {
        "identifiers": [device_id],
        "name": name or f"Govee Light {address[-4:]}",
        "model": advertised_light_model(name) or "Govee BLE Light",
        "manufacturer": "Govee",
    }
    return [
        (
            f"homeassistant/sensor/{device_id}/rssi/config",
            {
                "name": f"{device['name']} RSSI",
                "unique_id": f"{device_id}_rssi",
                "state_topic": base_topic,
                "value_template": "{{ value_json.rssi }}",
                "unit_of_measurement": "dBm",
                "state_class": "measurement",
                "icon": "mdi:wifi",
                "device": device,
            },
        ),
        (
            f"homeassistant/binary_sensor/{device_id}/online/config",
            {
                "name": f"{device['name']} Online",
                "unique_id": f"{device_id}_online",
                "state_topic": base_topic,
                "value_template": "{{ value_json.online }}",
                "payload_on": "True",
                "payload_off": "False",
                "device_class": "connectivity",
                "device": device,
            },
        ),
        (
            f"homeassistant/light/{device_id}/config",
            {
                "name": device["name"],
                "unique_id": f"{device_id}_light",
                "schema": "json",
                "command_topic": f"{base_topic}/set",
                "state_topic": f"{base_topic}/state",
                "brightness": True,
                "device": device,
            },
        ),
    ]


def learn_switch_discovery():
    return {
        "name": "Allow new BLE devices",
        "unique_id": "mqtt_ble_observe_learn",
        "command_topic": LEARN_COMMAND_TOPIC,
        "state_topic": LEARN_STATE_TOPIC,
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
        "retain": False,
        "optimistic": False,
        "availability_topic": OBSERVER_STATUS_TOPIC,
        "icon": "mdi:bluetooth-settings",
    }


def lan_topics(address):
    return {
        "state": f"govee/{address}/state",
        "set": f"govee/{address}/set",
        "discovery": f"homeassistant/light/govee_{address}/config",
    }


def lan_discovery(device):
    return {
        "name": device["name"],
        "unique_id": f"govee_light_{device['mac']}",
        "command_topic": device["topics"]["set"],
        "state_topic": device["topics"]["state"],
        "schema": "json",
        "brightness": True,
        "device": {
            "identifiers": [device["mac"]],
            "name": device["name"],
            "manufacturer": "Govee",
            "model": device.get("sku") or "LAN Light",
        },
    }
