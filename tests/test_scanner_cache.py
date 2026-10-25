import types
import unittest

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from scanner_cache import clear_scanner_cache


def make_scanner(n):
    """BleakScanner ohne Bluetooth-Zugriff, mit n Eintraegen im Backend-Cache."""
    scanner = BleakScanner.__new__(BleakScanner)
    scanner._backend = types.SimpleNamespace(
        seen_devices={f"uuid-{i}": (BLEDevice(f"addr-{i}", "n", None), object()) for i in range(n)}
    )
    return scanner


class ClearScannerCacheTest(unittest.TestCase):
    def test_public_property_clear_is_a_noop(self):
        # Regression: der alte Fix leerte nur diese Kopie.
        scanner = make_scanner(3)
        scanner.discovered_devices_and_advertisement_data.clear()
        self.assertEqual(len(scanner._backend.seen_devices), 3)

    def test_clears_real_backend_cache(self):
        scanner = make_scanner(5)
        self.assertEqual(clear_scanner_cache(scanner), 5)
        self.assertEqual(len(scanner._backend.seen_devices), 0)
        self.assertEqual(scanner.discovered_devices, [])

    def test_unknown_layout_returns_none(self):
        scanner = BleakScanner.__new__(BleakScanner)
        scanner._backend = types.SimpleNamespace()
        self.assertIsNone(clear_scanner_cache(scanner))


if __name__ == "__main__":
    unittest.main()
