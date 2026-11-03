"""Bound temporary Objective-C objects during Bleak advertisement processing.

CoreBluetooth schedules did_discover_peripheral on the Python event loop.
An explicit autorelease pool prevents temporary Objective-C objects from
accumulating while the observer runs continuously without a native NSRunLoop.
"""

import functools


def install():
    """Install the pool wrapper once; return whether the patch is available."""
    try:
        import objc
        from bleak.backends.corebluetooth.CentralManagerDelegate import (
            CentralManagerDelegate,
        )
    except ImportError:  # Non-macOS host or unsupported Bleak layout
        return False

    original = getattr(CentralManagerDelegate, "did_discover_peripheral", None)
    if original is None:
        return False
    if getattr(original, "_autorelease_wrapped", False):
        return True

    @functools.wraps(original)
    def wrapped(self, *args, **kwargs):
        with objc.autorelease_pool():
            return original(self, *args, **kwargs)

    wrapped._autorelease_wrapped = True
    CentralManagerDelegate.did_discover_peripheral = wrapped
    return True
