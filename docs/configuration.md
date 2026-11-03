# Configuration

The bridges keep MQTT credentials separate from device and runtime settings.
All settings are read once at startup; restart the relevant process after editing them.

## MQTT credentials

Copy `mqtt.env.example` to `mqtt.env`, fill in all four values, and restrict access:

```bash
cp mqtt.env.example mqtt.env
chmod 600 mqtt.env
```

`MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, and `MQTT_PASSWORD` environment variables take
precedence over the file. `MQTT_ENV_FILE` can select another credentials file. Empty
environment values fall back to the file. Missing values, placeholder credentials, and
invalid ports stop startup with exit status 2. Credential values are never included in
configuration errors or the configuration object's representation.

## Device and runtime settings

The complete, versioned defaults live in [`config/defaults.json`](../config/defaults.json).
To override them locally:

```bash
cp config.example.json config.json
```

`config.json` and `mqtt.env` are ignored by Git. The example files contain no personal
addresses or credentials. JSON requires double quotes and does not allow comments.

Settings are resolved in this order, from lowest to highest priority:

1. Bundled `config/defaults.json`.
2. Local `config.json`, or the file selected by `MQTT_BLE_CONFIG`.
3. Existing `MQTT_BLE_STATE_DIR` and `MQTT_BLE_LEARN_SECONDS` environment overrides.

Local files only need to include changed settings. Omitted keys keep their defaults;
provided lists and maps replace the corresponding default value. Explicitly selected
files must exist. Unknown sections or keys and invalid types are rejected before any
network connection or Bluetooth scan starts.

Relative `ble.state_dir` paths, including the environment override, are resolved against
the selected configuration file's directory. Without a selected file they are relative
to the project directory. This keeps state independent of the shell's working directory.

### BLE settings

| Setting under `ble` | Default | Purpose |
|---|---|---|
| `sensor_name_prefixes` | `["GVH5"]` | Names eligible for the existing sensor decoder |
| `light_models` | `["H6099"]` | Exact advertised model identifiers eligible for discovery |
| `light_model_prefixes` | `["H60", "H61", "H70", "H71"]` | Eligible model families |
| `light_name_prefixes` | `["H6", "H7"]` | Eligible advertisement name prefixes |
| `fallback_light_model` | `"H6099"` | Controller model used only when no model is advertised; `null` disables the fallback |
| `sensor_publish_seconds` | `60` | Minimum interval between sensor readings |
| `light_publish_seconds` | `30` | Minimum interval between light presence updates |
| `cache_clear_seconds` | `3600` | Interval for clearing Bleak's internal device cache |
| `learn_seconds` | `600` | Duration of the device learning window |
| `state_dir` | `"state"` | Directory containing `known_devices.json` |

Model names and prefixes are matched without regard to case. If an advertised name
contains an `H` followed by four digits, that model is passed to the light controller.
Otherwise, the configured fallback is used. With a `null` fallback, a matching light
without an advertised model only gets presence/RSSI discovery, without a control entity.

For an exact list of lights, clear both prefix lists as well as setting `light_models`:

```json
{
  "ble": {
    "light_models": ["H6099"],
    "light_model_prefixes": [],
    "light_name_prefixes": [],
    "fallback_light_model": null
  }
}
```

Matching a name makes a device eligible for discovery; it does not add protocol support.
The sensor decoder still handles the existing manufacturer payload format, and BLE light
commands still depend on the models supported by `govee-led-wez`. Fixed manufacturer
identifiers and packet layouts belong to the protocol helpers in `govee_devices.py`.

### LAN settings

| Setting under `lan` | Default | Purpose |
|---|---|---|
| `device_name_prefix` | `"Govee"` | Used when a light does not advertise a name |
| `custom_device_names` | `{}` | Override names by MAC address |
| `allowed_macs` | `[]` | Limit the bridge to these addresses; empty means all discovered LAN lights |
| `discovery_timeout_seconds` | `4` | Response window for each startup scan |
| `discovery_retries` | `3` | Number of startup scans; must be a positive integer |
| `status_poll_seconds` | `5` | Interval between polling all discovered lights |

Example with a documentation-only address:

```json
{
  "lan": {
    "custom_device_names": {"AA:BB:CC:DD:EE:FF": "Desk light"},
    "allowed_macs": ["AA:BB:CC:DD:EE:FF"]
  }
}
```

MAC address keys are normalized by removing colons and hyphens and converting to
uppercase. The LAN multicast address and ports remain protocol constants in `govee_lan.py`.
LAN discovery runs at startup. Restart the bridge to discover lights added later.

## Device learning and state

The BLE observer starts with learning **OFF**, even if MQTT retains an earlier command.
Only devices in `state/known_devices.json` publish data during normal operation.

In Home Assistant, enable **Allow new BLE devices** to learn nearby supported devices for
the configured duration. The switch turns off automatically. Disabling it early closes
the window immediately. Learned devices are persisted atomically and survive restarts.
An unreadable or corrupt allowlist stops startup instead of discarding known devices.

Existing discovery topics, unique IDs, and allowlist IDs are preserved. In particular,
sensor IDs keep their historical macOS UUID hyphens while BLE light IDs omit them.
English discovery names may update the default display names of existing entities.
