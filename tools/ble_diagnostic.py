"""Inspect BLE advertisements using the same Bleak backend as the observer.

Run from the project root: python tools/ble_diagnostic.py
This tool prints local advertisement data without connecting to MQTT.
"""

import argparse
import asyncio
import logging

from bleak import BleakScanner


def on_advertisement(device, advertisement, name_filter):
    name = advertisement.local_name or device.name or ""
    if name_filter and name_filter.upper() not in name.upper():
        return
    manufacturer = {
        f"0x{key:04X}": value.hex()
        for key, value in advertisement.manufacturer_data.items()
    }
    print(
        f"{device.address} name={name!r} rssi={advertisement.rssi} manufacturer={manufacturer}"
    )


async def scan(seconds, name_filter):
    def callback(device, advertisement):
        on_advertisement(device, advertisement, name_filter)

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds", type=float, default=30, help="Scan duration (default: 30)"
    )
    parser.add_argument(
        "--name", default="", help="Only show names containing this text"
    )
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(scan(args.seconds, args.name))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
