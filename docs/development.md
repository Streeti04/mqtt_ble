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
| `mqtt_ble.config` | Validate JSON settings and environment overrides |
| `mqtt_ble.paths` | Locate local configuration outside the installed package |
| `mqtt_ble.devices` | Match configured device names, normalize addresses, decode readings, validate commands |
| `mqtt_ble.ble.learning` | Persist admitted devices and manage the learning window |
| `mqtt_ble.ble.observer` | Coordinate BLE advertisements, admission, publishing, and light commands |
| `mqtt_ble.ble.autorelease`, `mqtt_ble.ble.scanner_cache` | Bound native and device-cache memory during continuous scanning |
| `mqtt_ble.lan.bridge` | Own LAN discovery, device registry, commands, polling, and socket lifetime |
| `mqtt_ble.mqtt.config` | Load MQTT credentials without exposing their values |
| `mqtt_ble.mqtt.client` | Own a Paho client and register callbacks before connecting |
| `mqtt_ble.mqtt.discovery` | Build discovery payloads with stable topics and identifiers |
| `mqtt_ble.mqtt.monitor` | Format incoming sensor readings |

Importing entry-point modules never reads credentials, constructs application clients,
binds sockets, or starts a scan. Runtime state belongs to `BleObserver`, `LanBridge`, or
`GoveeMonitor` instances. Their constructors accept dependencies so behavior can be
verified without hardware.

The application is an installable package under `src/mqtt_ble/`. Install it with
`pip install -e .` during development so changes take effect immediately; no `PYTHONPATH`
or `sys.path` modifications are needed. `pyproject.toml` defines the three console commands.
The JSON defaults are package data loaded through `importlib.resources`, so they are
included in wheel installations. MQTT callbacks use Paho's version 2 API.

CI also builds a wheel, replaces the editable installation with that wheel, and runs the
suite from a directory outside the checkout. This catches missing package data and
imports that accidentally depend on files at the repository root.

When changing discovery, preserve topics and `unique_id` values to avoid duplicate Home
Assistant entities. Model compatibility belongs in the JSON defaults; adding a new
packet format or command protocol requires implementation and regression tests.

## Migrating from root scripts

Reinstall after updating the checkout:

```bash
.venv/bin/python -m pip install -e . -c constraints.txt
```

Update terminal commands and service definitions:

| Previous command | Replacement |
|---|---|
| `.venv/bin/python observe_bleak.py` | `.venv/bin/mqtt-ble-observer` |
| `.venv/bin/python govee_lan.py` or `.venv/bin/python goove_mqtt_bridge.py` | `.venv/bin/mqtt-ble-lan` |
| `.venv/bin/python monitor_mqtt.py` | `.venv/bin/mqtt-ble-monitor` |

The modules also support `python -m mqtt_ble.ble.observer`,
`python -m mqtt_ble.lan.bridge`, and `python -m mqtt_ble.mqtt.monitor`.
Top-level Python scripts and imports have been removed; external Python callers should
use the package modules listed above, and `basic_mqtt` callers should use `MqttClient`.

Existing `mqtt.env`, `config.json`, and `state/` directories stay at the checkout root;
there is no need to move credentials or relearn devices. Templates now live in `config/`.
Only the launch commands change. For wheel installations, see the
[configuration directory rules](configuration.md#configuration-directory).
