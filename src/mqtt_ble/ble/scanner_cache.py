"""Bound Bleak's internal cache of observed devices.

Devices using rotating private addresses can grow _backend.seen_devices
indefinitely. The public discovered_devices_and_advertisement_data property
returns a copy, so clearing it does not release the real cache.
"""


def clear_scanner_cache(scanner):
    """Clear the backend cache and return the number of removed entries.

    Return None for an unsupported Bleak layout so callers can report it.
    """
    cache = getattr(getattr(scanner, "_backend", None), "seen_devices", None)
    if cache is None:
        return None
    count = len(cache)
    cache.clear()
    return count
