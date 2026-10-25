# Home Assistant Setup

This project sends supported BLE sensor and light data to Home Assistant through MQTT.
The Govee LAN bridge publishes lights discovered on the local network.

## MQTT integration

Add the MQTT integration in Home Assistant and configure it to use the same broker
settings as the local `mqtt.env` file. Keep credentials in `mqtt.env`; it is ignored by
Git and must not be committed.

## Services

Start the BLE scanner and, if needed, the Govee LAN bridge in separate terminals:

```bash
.venv/bin/python observe_bleak.py
.venv/bin/python goove_mqtt_bridge.py
```

The optional MQTT monitor can help diagnose broker traffic:

```bash
.venv/bin/python monitor_mqtt.py
```

## Entities and topics

BLE sensors publish temperature, humidity, and battery readings through MQTT Discovery.
Supported BLE lights publish power, brightness, RSSI, and online state. Govee LAN lights
publish discovery configuration under `homeassistant/light/` and use `govee/<MAC>/set` and
`govee/<MAC>/state` topics.

## Troubleshooting

- If entities do not appear, listen to `homeassistant/#` in Home Assistant's MQTT tools.
- Check that the broker settings in `mqtt.env` match the Home Assistant MQTT integration.
- For Govee LAN lights, enable LAN Control in the Govee app and keep the bridge on the same
  local network.
- Keep BLE devices within range and ensure the process has macOS Bluetooth permission.
