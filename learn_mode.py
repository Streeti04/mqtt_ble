"""Persistent device admission and time-limited learning without BLE or MQTT.

Allowlist stores stable Home Assistant device IDs. LearnWindow admits supported
unknown devices for a limited duration and always starts disabled.
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEARN_SECONDS = 10 * 60

DECISION_ALLOW = "allow"  # Known device: publish normally
DECISION_LEARN = "learn"  # Unknown device while learning: persist and publish
DECISION_IGNORE = "ignore"  # Unknown device outside learning: ignore


def decide(device_id, allowlist, learning):
    """Choose whether to publish, learn, or ignore a supported device."""
    if allowlist.contains(device_id):
        return DECISION_ALLOW
    if learning:
        return DECISION_LEARN
    return DECISION_IGNORE


def parse_switch_payload(payload):
    """Parse case-insensitive ON/OFF bytes or strings; return None otherwise."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    value = str(payload).strip().upper()
    if value == "ON":
        return True
    if value == "OFF":
        return False
    return None


class LearnWindow:
    """Thread-safe learning window with automatic expiry.

    An injectable monotonic clock lets tests advance time without waiting.
    """

    def __init__(self, duration_seconds=DEFAULT_LEARN_SECONDS, clock=time.monotonic):
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be greater than zero")
        self.duration = duration_seconds
        self._clock = clock
        self._until = None
        self._lock = threading.Lock()

    def enable(self):
        """Start or extend the window from the current time."""
        with self._lock:
            self._until = self._clock() + self.duration

    def disable(self):
        """Close the window and report whether it was previously enabled."""
        with self._lock:
            was_active = self._until is not None
            self._until = None
            return was_active

    def is_active(self):
        with self._lock:
            return self._until is not None and self._clock() < self._until

    def remaining(self):
        with self._lock:
            if self._until is None:
                return 0.0
            return max(0.0, self._until - self._clock())

    def poll_expired(self):
        """Close an expired window and report the transition exactly once."""
        with self._lock:
            if self._until is not None and self._clock() >= self._until:
                self._until = None
                return True
            return False


class Allowlist:
    """Persist device metadata as JSON using atomic file replacement.

    Format: {"version": 1, "devices": {"<id>": {"name": ..., "added": ..., "source": ...}}}
    """

    VERSION = 1

    def __init__(self, path):
        self.path = Path(path)
        self._devices = {}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path):
        """Load a saved allowlist; a missing file starts empty.

        Reject corrupt files rather than silently discarding known devices.
        """
        allowlist = cls(path)
        if allowlist.path.is_file():
            data = json.loads(allowlist.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "devices" not in data:
                raise ValueError(
                    f"{allowlist.path}: Missing 'devices' key or invalid allowlist format"
                )
            devices = data["devices"]
            if isinstance(devices, list):
                devices = {str(d): {} for d in devices}
            if not isinstance(devices, dict):
                raise ValueError(
                    f"{allowlist.path}: 'devices' must be a list or object"
                )
            allowlist._devices = {str(k): dict(v or {}) for k, v in devices.items()}
        return allowlist

    def contains(self, device_id):
        with self._lock:
            return device_id in self._devices

    def ids(self):
        with self._lock:
            return sorted(self._devices)

    def __len__(self):
        with self._lock:
            return len(self._devices)

    def add(self, device_id, name=None, source="learn"):
        """Persist a new device immediately; return False if already present."""
        with self._lock:
            if device_id in self._devices:
                return False
            self._devices[device_id] = {
                "name": name,
                "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
            }
            try:
                self._save_locked()
            except OSError:
                # Roll back in-memory admission if persistence fails
                del self._devices[device_id]
                raise
            return True

    def save(self):
        with self._lock:
            self._save_locked()

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.VERSION, "devices": self._devices}
        fd, tmp = tempfile.mkstemp(
            prefix=".known_devices.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


class OnceLogger:
    """Track previously logged device IDs with a bounded set."""

    def __init__(self, max_entries=2000):
        self.max_entries = max_entries
        self._seen = set()
        self._lock = threading.Lock()

    def first_time(self, key):
        with self._lock:
            if key in self._seen:
                return False
            if len(self._seen) >= self.max_entries:
                self._seen.clear()
            self._seen.add(key)
            return True

    def forget(self, key):
        with self._lock:
            self._seen.discard(key)
