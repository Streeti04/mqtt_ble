# mqtt_ble

Python services that connect BLE sensors and Govee lights to an MQTT broker and Home
Assistant. The project includes a BLE scanner, a Govee LAN bridge, an optional MQTT
monitor, and a gated device learning mode.

## Requirements

- macOS with Bluetooth access
- Python 3.10 or newer
- An MQTT broker reachable from this machine
- Govee LAN Control enabled for lights using the LAN bridge

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
cp mqtt.env.example mqtt.env
```

Edit `mqtt.env` with the broker host, port, username, and password. Keep this file local;
it is ignored by Git. The services fail with a clear error when required settings are
missing. You can also provide the same values as environment variables.

## Start services

```bash
.venv/bin/python observe_bleak.py
.venv/bin/python goove_mqtt_bridge.py
```

Optional MQTT diagnostics:

```bash
.venv/bin/python monitor_mqtt.py
```

`observe_bleak.py` publishes supported BLE sensor readings and light state through MQTT
Discovery. `goove_mqtt_bridge.py` discovers and controls Govee lights on the local network.

## Configuration and Home Assistant

See [HOME_ASSISTANT_SETUP.md](HOME_ASSISTANT_SETUP.md) for the MQTT integration, entities,
topics, and troubleshooting notes. `mqtt_config.py` loads configuration from environment
variables first, then `mqtt.env` beside the project. Never commit real broker credentials.

## Tests

```bash
python -m unittest discover -s tests
```
