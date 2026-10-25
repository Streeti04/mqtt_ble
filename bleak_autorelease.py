"""Autorelease-Pool um bleaks CoreBluetooth-Advertisement-Verarbeitung.

bleak ruft ``CentralManagerDelegate.did_discover_peripheral`` per
``call_soon_threadsafe`` im Python-Main-Thread auf. Dort laeuft asyncio ohne
NSRunLoop, der Autorelease-Pool des Main-Threads wird also nie geleert. Jedes
Advertisement (~20/s) hinterlaesst autoreleased ObjC-Objekte (UUIDString,
NSData, NSDictionary-Enumeratoren) — das Prozess-Footprint waechst um ~35 MB/h
(531 MB nach 16 h), obwohl der Python-Heap flach bleibt.

``install()`` ersetzt die Methode durch eine Variante mit eigenem Pool.
"""

import functools


def install():
    """Patcht bleak. Gibt True zurueck, wenn der Pool aktiv ist, sonst False."""
    try:
        import objc
        from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
    except ImportError:  # kein macOS / anderes bleak-Layout
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
