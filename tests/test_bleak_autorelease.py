import sys
import unittest
from unittest import mock

from mqtt_ble.ble import autorelease

try:
    import objc
    from bleak.backends.corebluetooth.CentralManagerDelegate import (
        CentralManagerDelegate,
    )
    from Foundation import NSData
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
        for fails in (False, True):
            with self.subTest(callback_raises=fails):
                self.assert_pool_wrap(fails)

    def assert_pool_wrap(self, fails):
        real_pool = objc.autorelease_pool
        events = []

        class SpyPool:
            def __init__(self_):
                self_.pool = real_pool()

            def __enter__(self_):
                self_.pool.__enter__()
                events.append("enter")

            def __exit__(self_, *exc):
                result = self_.pool.__exit__(*exc)
                events.append(("exit", exc[0]))
                return result

        def callback(delegate, *args):
            self.assertEqual(events, ["enter"])
            self.assertEqual(args, (1, 2, 3, 4))
            events.append("callback")
            if fails:
                raise RuntimeError("advertisement callback failed")
            return "handled"

        CentralManagerDelegate.did_discover_peripheral = callback
        with mock.patch.object(objc, "autorelease_pool", SpyPool):
            self.assertTrue(autorelease.install())
            if fails:
                with self.assertRaisesRegex(RuntimeError, "callback failed"):
                    CentralManagerDelegate.did_discover_peripheral(None, 1, 2, 3, 4)
            else:
                self.assertEqual(
                    CentralManagerDelegate.did_discover_peripheral(None, 1, 2, 3, 4),
                    "handled",
                )
        self.assertEqual(
            events, ["enter", "callback", ("exit", RuntimeError if fails else None)]
        )

    def test_nsdata_conversions_do_not_retain_advertisement_buffers(self):
        # Bleak converts NSData with bytes() and slicing while processing packets.
        # Older PyObjC releases leaked an exporter reference on every conversion,
        # even when an autorelease pool was drained after each advertisement.
        for convert in (bytes, lambda data: data[2:]):
            with self.subTest(conversion=convert), objc.autorelease_pool():
                data = NSData.alloc().initWithBytes_length_(b"test-data", 9)
                initial = sys.getrefcount(data)
                for _ in range(1000):
                    with objc.autorelease_pool():
                        converted = convert(data)
                        del converted
                self.assertEqual(sys.getrefcount(data), initial)

    def test_install_is_idempotent(self):
        autorelease.install()
        once = CentralManagerDelegate.did_discover_peripheral
        autorelease.install()
        self.assertIs(CentralManagerDelegate.did_discover_peripheral, once)


if __name__ == "__main__":
    unittest.main()
