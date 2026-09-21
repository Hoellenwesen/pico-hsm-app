"""
session_helpers.py — Qt-Pendant zu cli/pkcs11_helpers.py (Konzept §5/§8).

Dialoge statt click.confirm/click.echo: Bei SessionConflictError
erscheint eine MessageBox mit derselben Meldung wie im CLI-Fall — nur
"OK"/"Erneut versuchen", kein "Trotzdem fortfahren" (§8: der Fehler ist
zu dem Zeitpunkt bereits real aufgetreten, ein Retry ändert daran
nichts — "Erneut versuchen" wiederholt lediglich den Versuch auf
Wunsch des Nutzers).

Session-Opener (Keys-Tab u.a.): dünne Contextmanager um
pkcs11_session — kein PIN-Caching hier (PIN-Verwaltung liegt beim
aufrufenden Tab, z.B. ein Feld pro Tab mit Leerung bei hideEvent).
SessionConflictError läuft zum Aufrufer durch (dort show_conflict mit
Retry-Schleife auf Nutzerwunsch — kein Auto-Loop).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from PySide6.QtWidgets import QWidget
from qfluentwidgets import MessageBox

from pico_hsm_tools.pkcs11_session import (
    exclusive_session,
    read_only_session,
)


def confirm_destructive(parent: QWidget, title: str, text: str) -> bool:
    """Explizite Bestätigung für destruktive Aktionen (z.B. Löschen,
    Deaktivieren von Security-Features — Konzept §9).

    Gibt True zurück bei "Ja, ausführen", sonst False. `parent` muss ein
    reales Widget sein (MessageBox braucht es für die Geometrie).
    """
    box = MessageBox(title, text, parent)
    box.yesButton.setText("Ja, ausführen")
    box.cancelButton.setText("Abbrechen")
    return bool(box.exec())


def show_conflict(
    parent: QWidget, message: str, allow_retry: bool = True,
) -> bool:
    """Session-Konflikt-Dialog (vgl. SessionConflictError im CLI).

    Gibt True zurück bei "Erneut versuchen", sonst False. Ohne
    `allow_retry` steht nur "OK" zur Verfügung.
    """
    box = MessageBox("Gerät belegt", message, parent)
    if allow_retry:
        box.yesButton.setText("Erneut versuchen")
        box.cancelButton.setText("OK")
    else:
        box.yesButton.setText("OK")
        box.hideCancelButton()
    return bool(box.exec())


@contextmanager
def open_exclusive_session(
    pin: str | None = None, serial: str | None = None,
) -> Iterator:
    """Schreibende PKCS#11-Session (Default-Lib, kein Lib-Override-Feld
    im Tab). PIN None = aus dem Vault (Anmeldung); Serial None = Auswahl
    aus der GUI-Config (Status-Dropdown). SessionConflictError läuft
    durch zum Aufrufer."""
    resolved = pin if pin is not None else _vault_pin_or_raise()
    with exclusive_session(
        user_pin=resolved, serial=serial or selected_serial(),
    ) as session:
        yield session


@contextmanager
def open_read_only_session(
    pin: Optional[str] = None, serial: str | None = None,
) -> Iterator:
    """Lesende PKCS#11-Session (PIN optional — öffentliche Objekte gehen
    auch ohne; None = aus dem Vault, fällt zurück auf ohne PIN).
    Serial None = Auswahl aus der GUI-Config.
    SessionConflictError läuft durch zum Aufrufer."""
    from gui.pin_vault import vault

    resolved = pin if pin is not None else vault.get()
    with read_only_session(
        user_pin=resolved, serial=serial or selected_serial(),
    ) as session:
        yield session


def _vault_pin_or_raise() -> str:
    """Vault-PIN oder Fehler (Aufrufer zeigt Hinweis statt Blind-Fehler)."""
    from gui.pin_vault import vault

    pin = vault.get()
    if not pin:
        raise VaultLockedError("Gesperrt — bitte zuerst anmelden (Start-Tab).")
    return pin


class VaultLockedError(Exception):
    """PIN-Cache leer — Anmeldung erforderlich (kein Hardware-Fehler)."""


def close_info_bars(bars: list) -> None:
    """Gespeicherte InfoBars schließen + Liste leeren (tolerant).

    Bars, die der Nutzer per X geschlossen hat (C++-Objekt bereits
    gelöscht), werfen RuntimeError aus libshiboken — ignorieren statt
    crashen (Regression: Wizard-DKEK-Abfrage nach X-Klick).
    """
    for bar in bars:
        try:
            bar.close()
        except RuntimeError:  # noqa: BLE001 — C++-Objekt schon weg
            pass
    bars.clear()


def selected_serial() -> str | None:
    """Gewählte Token-Seriennummer aus der GUI-Config (None = automatisch)."""
    from gui import config as gui_config

    value = gui_config.cfg.tokenSerial.value.strip()
    return value or None
