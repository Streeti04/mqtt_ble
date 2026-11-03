import sys
import unittest

from mqtt_ble.ble import autorelease

try:
    import objc
    from bleak.backends.corebluetooth.CentralManagerDelegate import (
        CentralManagerDelegate,
    )
except ImportError:
    objc = None


@unittest.skipIf(
    sys.platform != "darwin" or objc is None, "CoreBluetooth backend requires macOS"
)
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.original = CentralManagerDelegate.did_discover_peripheral

    def tearDown(self):
        CentralManagerDelegate.did_discover_peripheral = self.original

    def test_wraps_discover_in_autorelease_pool(self):
        entered = []
        real_pool = objc.autorelease_pool

        class SpyPool:
            def __enter__(self_):
                entered.append(True)
                return real_pool().__enter__()

            def __exit__(self_, *exc):
                return False

        calls = []
        CentralManagerDelegate.did_discover_peripheral = lambda self, *a: calls.append(
            a
        )
        objc.autorelease_pool = SpyPool
        try:
            self.assertTrue(autorelease.install())
            CentralManagerDelegate.did_discover_peripheral(None, 1, 2, 3, 4)
        finally:
            objc.autorelease_pool = real_pool

        self.assertEqual(entered, [True])
        self.assertEqual(calls, [(1, 2, 3, 4)])

    def test_install_is_idempotent(self):
        autorelease.install()
        once = CentralManagerDelegate.did_discover_peripheral
        autorelease.install()
        self.assertIs(CentralManagerDelegate.did_discover_peripheral, once)


if __name__ == "__main__":
    unittest.main()
