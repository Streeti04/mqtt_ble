"""
MQTT-Zugangsdaten fuer opt/mqtt_ble laden
=========================================
Quelle (absteigende Prioritaet):
  1. Umgebungsvariablen MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD
  2. Datei mqtt.env neben diesem Modul (Pfad per MQTT_ENV_FILE ueberschreibbar)

Fehlt ein Wert, wird mit einer klaren Fehlermeldung abgebrochen - niemals still
mit leerem Passwort verbinden. Werte werden nie geloggt.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASSWORD")
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / "mqtt.env"


class MqttConfigError(RuntimeError):
    """Konfiguration unvollstaendig oder ungueltig (Meldung enthaelt keine Werte)."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)


def parse_env_text(text):
    """Minimaler KEY=VALUE-Parser (ohne Abhaengigkeiten).

    - Leerzeilen und Zeilen mit fuehrendem '#' werden ignoriert
    - optionales 'export ' am Zeilenanfang
    - Wert wird nur am Rand von Leerzeichen befreit; ein Paar umschliessender
      einfacher/doppelter Anfuehrungszeichen wird entfernt
    - KEIN Inline-Kommentar (Passwoerter duerfen '#' enthalten)
    """
    values = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise MqttConfigError(f"Zeile {lineno}: erwartet KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            raise MqttConfigError(f"Zeile {lineno}: leerer Schluessel")
        values[key] = value
    return values


def load_mqtt_config(env_file=None, environ=None):
    """Laedt die MQTT-Konfiguration; wirft MqttConfigError bei fehlenden Werten."""
    environ = os.environ if environ is None else environ
    path = Path(env_file or environ.get("MQTT_ENV_FILE") or DEFAULT_ENV_FILE)

    file_values = {}
    file_error = None
    if path.is_file():
        file_values = parse_env_text(path.read_text(encoding="utf-8"))
    else:
        file_error = f"Datei {path} nicht gefunden"

    merged = {}
    for key in REQUIRED_KEYS:
        value = environ.get(key)
        if value is None or value == "":
            value = file_values.get(key, "")
        merged[key] = value

    missing = [k for k in REQUIRED_KEYS if not merged[k]]
    if missing:
        hint = f" ({file_error})" if file_error else f" (gelesen: {path})"
        raise MqttConfigError(
            "MQTT-Konfiguration unvollstaendig, fehlend: " + ", ".join(missing) + hint
            + ". Vorlage: mqtt.env.example -> mqtt.env kopieren und ausfuellen."
        )

    for key in ("MQTT_USER", "MQTT_PASSWORD"):
        if merged[key] == "CHANGE_ME":
            raise MqttConfigError(f"{key} ist noch der Platzhalter CHANGE_ME")

    try:
        port = int(merged["MQTT_PORT"])
    except ValueError:
        raise MqttConfigError("MQTT_PORT ist keine Zahl") from None
    if not 0 < port < 65536:
        raise MqttConfigError("MQTT_PORT ausserhalb 1-65535")

    return MqttConfig(
        host=merged["MQTT_HOST"],
        port=port,
        user=merged["MQTT_USER"],
        password=merged["MQTT_PASSWORD"],
    )


def load_mqtt_config_or_exit(env_file=None, environ=None):
    """Wie load_mqtt_config, beendet den Prozess aber mit Exit-Code 2 bei Fehlern."""
    try:
        return load_mqtt_config(env_file=env_file, environ=environ)
    except MqttConfigError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr, flush=True)
        sys.exit(2)
