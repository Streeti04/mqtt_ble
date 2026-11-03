# Development

## Setup and checks

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -c constraints.txt
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
```

Tests use temporary state directories and mocked MQTT/Bluetooth clients. They do not
connect to a broker, bind the LAN response port, or scan nearby devices. macOS tests
exercise the CoreBluetooth autorelease wrapper; those checks are skipped on other
platforms. CI runs on Python 3.10 and 3.13 on Linux, plus Python 3.13 on macOS.

## Modules and responsibilities

| Module | Responsibility |
|---|---|
| `app_config.py` | Validate JSON settings and environment overrides |
| `mqtt_config.py` | Load MQTT credentials without exposing their values |
| `basic_mqtt.py` | Own a Paho client and register callbacks before connecting |
| `govee_devices.py` | Match configured device names, normalize addresses, decode readings, validate commands |
| `ha_discovery.py` | Build discovery payloads with stable topics and identifiers |
| `learn_mode.py` | Persist admitted devices and manage the learning window |
| `observe_bleak.py` | Coordinate BLE advertisements, admission, publishing, and light commands |
| `govee_lan.py` | Own LAN discovery, device registry, command handling, polling, and socket lifetime |
| `monitor_mqtt.py` | Format incoming sensor readings |
| `bleak_autorelease.py`, `scanner_cache.py` | Bound native and device-cache memory during continuous scanning |

Importing entry-point modules never reads credentials, constructs application clients,
binds sockets, or starts a scan. Runtime state belongs to `BleObserver`, `LanBridge`, or
`GoveeMonitor` instances. Their constructors accept dependencies so behavior can be
verified without hardware.

`goove_mqtt_bridge.py` remains a compatibility entry point for existing launch commands.
New setups can run `govee_lan.py` directly. `basic_mqtt` remains an alias for `MqttClient`;
internal code uses the descriptive class name. MQTT callbacks use Paho's version 2 API.

When changing discovery, preserve topics and `unique_id` values to avoid duplicate Home
Assistant entities. Model compatibility belongs in the JSON defaults; adding a new
packet format or command protocol requires implementation and regression tests.
