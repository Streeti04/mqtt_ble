"""
Lernmodus und Allowlist fuer den BLE-Observer (reine Logik, ohne BLE/MQTT)
=========================================================================
- Allowlist: persistente Liste der Geraete-IDs (wie im HA-Discovery-Topic,
  z.B. "govee_ble_light_<mac>" oder "govee_<mac>"), die veroeffentlicht werden.
- LearnWindow: zeitlich begrenztes Fenster, in dem unbekannte, unterstuetzte
  Geraete aufgenommen werden. Startet immer AUS.
- decide(): Entscheidung pro Geraet: ALLOW / LEARN / IGNORE.
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEARN_SECONDS = 10 * 60

DECISION_ALLOW = "allow"    # bekannt -> wie bisher veroeffentlichen
DECISION_LEARN = "learn"    # unbekannt + Lernmodus -> aufnehmen und veroeffentlichen
DECISION_IGNORE = "ignore"  # unbekannt, kein Lernmodus -> nichts tun


def decide(device_id, allowlist, learning):
    """Allowlist-Entscheidung fuer ein (bereits als unterstuetzt erkanntes) Geraet."""
    if allowlist.contains(device_id):
        return DECISION_ALLOW
    if learning:
        return DECISION_LEARN
    return DECISION_IGNORE


def parse_switch_payload(payload):
    """'ON'/'OFF' (bytes oder str, case-insensitiv) -> True/False, sonst None."""
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
    """Thread-sicheres Lernfenster mit automatischem Ablauf.

    clock ist injizierbar (Default time.monotonic), damit Tests ohne Warten laufen.
    """

    def __init__(self, duration_seconds=DEFAULT_LEARN_SECONDS, clock=time.monotonic):
        if duration_seconds <= 0:
            raise ValueError("duration_seconds muss > 0 sein")
        self.duration = duration_seconds
        self._clock = clock
        self._until = None
        self._lock = threading.Lock()

    def enable(self):
        """Startet (oder verlaengert) das Fenster ab jetzt um duration Sekunden."""
        with self._lock:
            self._until = self._clock() + self.duration

    def disable(self):
        """Beendet das Fenster. Gibt True zurueck, wenn es vorher aktiv war."""
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
        """True genau einmal, wenn ein aktives Fenster abgelaufen ist (setzt es AUS)."""
        with self._lock:
            if self._until is not None and self._clock() >= self._until:
                self._until = None
                return True
            return False


class Allowlist:
    """Persistente Allowlist (JSON) mit atomischem Schreiben.

    Format: {"version": 1, "devices": {"<id>": {"name": ..., "added": ..., "source": ...}}}
    """

    VERSION = 1

    def __init__(self, path):
        self.path = Path(path)
        self._devices = {}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path):
        """Laedt die Datei; fehlt sie, ist die Liste leer. Kaputtes JSON -> Fehler.

        Bewusst kein stilles Ueberschreiben einer defekten Datei: lieber Abbruch,
        als versehentlich alle Geraete zu verlieren.
        """
        allowlist = cls(path)
        if allowlist.path.is_file():
            data = json.loads(allowlist.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "devices" not in data:
                raise ValueError(f"{allowlist.path}: Schluessel 'devices' fehlt oder Format ungueltig")
            devices = data["devices"]
            if isinstance(devices, list):
                devices = {str(d): {} for d in devices}
            if not isinstance(devices, dict):
                raise ValueError(f"{allowlist.path}: 'devices' hat ungueltiges Format")
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
        """Nimmt ein Geraet auf und speichert sofort. False, wenn schon vorhanden."""
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
                # Nicht im Speicher behalten, wenn es nicht auf die Platte kam
                del self._devices[device_id]
                raise
            return True

    def save(self):
        with self._lock:
            self._save_locked()

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.VERSION, "devices": self._devices}
        fd, tmp = tempfile.mkstemp(prefix=".known_devices.", suffix=".tmp", dir=self.path.parent)
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
    """Merkt sich, welche IDs schon geloggt wurden (begrenzte Groesse)."""

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
