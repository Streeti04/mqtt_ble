"""Leeren des internen Geraete-Caches von BleakScanner.

bleak merkt sich jedes je gesehene BLE-Geraet samt letztem Advertisement in
``scanner._backend.seen_devices``. Bei Geraeten mit wechselnden (Resolvable
Private) Adressen waechst das ueber Wochen unbegrenzt. Die oeffentliche
Property ``discovered_devices_and_advertisement_data`` liefert seit bleak 0.19
nur eine Kopie — ``.clear()`` darauf bewirkt nichts.
"""


def clear_scanner_cache(scanner):
    """Leert den echten Cache des Scanners und gibt die Anzahl entfernter Eintraege zurueck.

    Gibt None zurueck, wenn bleak den Cache nicht unter ``_backend.seen_devices``
    ablegt (anderes bleak-Layout) — der Aufrufer sollte das melden.
    """
    cache = getattr(getattr(scanner, "_backend", None), "seen_devices", None)
    if cache is None:
        return None
    count = len(cache)
    cache.clear()
    return count
