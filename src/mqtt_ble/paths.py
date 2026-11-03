"""Locate user configuration without putting writable files in the package."""

from pathlib import Path


def default_config_dir():
    """Preserve checkout-relative settings; wheel installs use the working directory."""
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (
        checkout / "src" / "mqtt_ble" / "__init__.py"
    ).is_file():
        return checkout
    return Path.cwd()
