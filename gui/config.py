"""
gui/config.py — persistente GUI-Einstellungen (QFluentWidgets qconfig).

Datei: `~/.pico_hsm/gui.json` (JSON via qconfig.load/save — dasselbe
Verzeichnis wie Audit-Logs/TOTP-Secret, kein neuer Ablageort).

Getroffene Entscheidung (Schritt 6a): Gateway-Adresse in Feldern +
Datei (nicht nur Session-lokal wie CLI-Flags).
"""

from __future__ import annotations

from pathlib import Path

from qfluentwidgets import QConfig, RangeConfigItem, ConfigItem, qconfig
from qfluentwidgets.common.config import RangeValidator


def default_config_path() -> Path:
    """Pfad der GUI-Config (~/.pico_hsm/, Verzeichnis wird bei save angelegt)."""
    return Path.home() / ".pico_hsm" / "gui.json"


class AppConfig(QConfig):
    """GUI-Einstellungen. Port 0 = nicht konfiguriert (wie CLI ohne
    --gateway-port: Check liefert reachable=False, blockiert nichts)."""

    gatewayHost: ConfigItem = ConfigItem("Gateway", "Host", "")
    gatewayPort: RangeConfigItem = RangeConfigItem(
        "Gateway", "Port", 0, RangeValidator(0, 65535),
    )


cfg = AppConfig()


def load_config(path: Path | None = None) -> AppConfig:
    """Config laden (fehlende Datei = Defaults, kein Fehler)."""
    qconfig.load(str(path or default_config_path()), cfg)
    return cfg


def save_config(path: Path | None = None) -> None:
    """Config schreiben (Verzeichnis wird angelegt)."""
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    qconfig.save()
