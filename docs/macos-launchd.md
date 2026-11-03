# macOS launchd and memory diagnostics

## Service startup

Install the package into the checkout's virtual environment first:

```bash
.venv/bin/python -m pip install -e . -c constraints.txt
.venv/bin/python -m pip check
```

Use Python 3.11 or newer on macOS. For a launchd agent, use absolute paths:

| launchd key | Value |
|---|---|
| `ProgramArguments` | One string: the absolute path to `.venv/bin/mqtt-ble-observer` |
| `WorkingDirectory` | Absolute path to the checkout |
| `EnvironmentVariables` | `PYTHONUNBUFFERED=1` |
| `RunAtLoad` | `true` |
| `KeepAlive` | `true` for a continuous service |
| `ThrottleInterval` | `30` |
| `StandardOutPath`, `StandardErrorPath` | Absolute paths in an existing writable log directory |

Use separate agents and labels for `mqtt-ble-lan` and `mqtt-ble-monitor`. Shell activation
is unnecessary; each installed executable selects the virtual environment's interpreter.
Keep service checkouts and virtual environments outside macOS-protected Desktop and
Documents directories to avoid background file-access permission prompts during startup.
When launching from a regular wheel installation, set `WorkingDirectory` to the directory
containing local settings, or set absolute `MQTT_BLE_CONFIG` and `MQTT_ENV_FILE` paths.
Do not put credentials into the plist.

Validate a generated plist with `plutil -lint /absolute/path/to/agent.plist` before loading
it. Inspect its state with `launchctl print gui/$(id -u)/your.agent.label`. A running
process alone does not establish that it receives Bluetooth advertisements or connects
to MQTT; check fresh sensor messages and error logs as well.

## Native memory growth

Three separate mechanisms matter during long-running scans:

1. The explicit autorelease pool bounds temporary Objective-C objects created on the
   asyncio thread by each advertisement callback.
2. Periodic cache cleanup releases Bleak's real `_backend.seen_devices` entries.
3. PyObjC 13 fixes a separate reference leak in `NSData.bytes()` and the conversions
   used by Bleak. An autorelease pool or clearing the device cache cannot release these
   incorrectly retained references. Reinstalling with the tested constraints is required
   for an older virtual environment, followed by restarting the affected service.

The upstream analysis is documented in the
[Bleak CoreBluetooth fix](https://github.com/hbldh/bleak/commit/e92a66ccb5958109a26ffbbd07565e10821b17c5)
and the [PyObjC 13 release notes](https://github.com/ronaldoussoren/pyobjc/releases/tag/v13.0).

To inspect a process without restarting it:

```bash
vmmap -summary PID
heap -s --noContent PID
```

Compare **physical footprint**, including swapped memory. RSS alone can substantially
understate retained allocations. Record multiple samples with the same PID after warmup;
a single large footprint does not prove continued growth. Millions of `NSData` objects
with only a small number of peripherals are consistent with the buffer reference leak.
A short smoke test verifies startup and immediate data flow, but cannot establish
multi-day memory stability.
