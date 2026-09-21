"""
gui/config.py — persistente GUI-Einstellungen (QFluentWidgets qconfig).

Datei: `~/.pico_hsm/gui.json` (JSON via qconfig.load/save — dasselbe
Verzeichnis wie Audit-Logs/TOTP-Secret, kein neuer Ablageort).

Getroffene Entscheidung: Token-Auswahl in Dropdown + Datei (nicht nur
Session-lokal wie CLI-Flags). Alte `[Gateway]`-Einträge in
bestehenden gui.json-Dateien werden ignoriert (keine Migration nötig).
"""

from __future__ import annotations

from pathlib import Path

from qfluentwidgets import QConfig, ConfigItem, qconfig

from pico_hsm_tools.paths import base_dir


def default_config_path() -> Path:
    """Pfad der GUI-Config (Verzeichnis wird bei save angelegt)."""
    return base_dir() / "gui.json"


class AppConfig(QConfig):
    """GUI-Einstellungen. Leere Token-Seriennummer = automatisch
    (Ein-Gerät-Modell, Auswahl im Status-Dropdown)."""

    tokenSerial: ConfigItem = ConfigItem("Device", "Serial", "")


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
