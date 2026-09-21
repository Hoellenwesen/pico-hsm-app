"""
pkcs11_session.py — Session-Handling für die Config-App.

`exclusive_session()` öffnet schreibende Sessions direkt (rw +
eingeloggt) — KEIN Vorab-Check, KEIN Override-Dialog: Schlägt das
Öffnen mit einer Fehlerklasse fehl, die plausibel auf einen belegten
Reader hindeutet, wird das als `SessionConflictError` neu geworfen —
der Fehler ist zu dem Zeitpunkt bereits real aufgetreten, ein
Bestätigungsdialog würde daran nichts ändern.

Hinweis zum Betrieb: Diese App und das HSM-API-Gateway laufen auf
getrennten Systemen (Verwaltung per USB am Arbeitsplatzrechner,
Gateway dauerhaft separat) — es gibt daher keinen gemeinsamen
Reader und keinen app-seitigen Erreichbarkeits-Check mehr. Der
komplette Zustand (PIN, Keys, DKEK-Shares) lebt auf dem
Hardware-Token selbst.

VERIFIZIERT (Architekturkonzept §12, docs/15 Phase 2.8, hw-logs/17):
zweite exklusive Session + APDU bei gehaltener Session laufen
störungsfrei — PKCS#11 kennt keinen OS-exklusiven Session-Lock.
`_LIKELY_CONFLICT_ERRORS` unten bleibt als Sicherheitsnetz für echte
Belegt-Fälle (seltener als ursprünglich angenommen).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import pkcs11
from pkcs11 import Token
from pkcs11.exceptions import (
    DeviceError,
    MultipleTokensReturned,
    NoSuchToken,
    SessionCount,
    TokenNotPresent,
)

DEFAULT_PKCS11_LIB_LINUX = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"
DEFAULT_PKCS11_LIB_WINDOWS = r"C:\Windows\System32\opensc-pkcs11.dll"
DEFAULT_PKCS11_LIB_MACOS = "/usr/local/lib/opensc-pkcs11.so"

# Windows-Fallback-Kette: Der OpenSC-MSI-Installer legt die DLL NICHT
# nach System32 (alter Default), sondern nach <InstallDir>\pkcs11\.
# Erster existierender Pfad gewinnt; sonst der alte Default (damit
# Fehlermeldungen das bekannte Pfadbild behalten).
_WINDOWS_PKCS11_CANDIDATES = (
    DEFAULT_PKCS11_LIB_WINDOWS,
    r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll",
)

# Fehlerklassen, die plausibel auf einen belegten Reader/ein belegtes
# Gerät hindeuten. Bewusst NICHT GeneralError/FunctionFailed: die sind
# zu generisch (treten z.B. auch bei falschem Template oder falscher
# Slot-ID auf) und würden unabhängige, unzusammenhängende Fehler
# fälschlich als "Gerät belegt" ausgeben.
_LIKELY_CONFLICT_ERRORS = (DeviceError, TokenNotPresent, SessionCount)


class SessionConflictError(Exception):
    """Wird geworfen, wenn das Öffnen einer schreibenden Session mit
    einer Fehlerklasse fehlschlägt, die auf einen aktuell belegten
    Reader hindeutet (typischerweise: ein anderer lokaler Prozess
    hält gerade denselben USB-Reader offen)."""


def default_pkcs11_lib_path() -> str:
    import platform
    system = platform.system()
    if system == "Windows":
        for candidate in _WINDOWS_PKCS11_CANDIDATES:
            if Path(candidate).exists():
                return candidate
        return _WINDOWS_PKCS11_CANDIDATES[0]
    if system == "Darwin":
        return DEFAULT_PKCS11_LIB_MACOS
    return DEFAULT_PKCS11_LIB_LINUX


def format_token_serial(raw: object) -> str:
    """Token-Seriennummer anzeigbar (und JSON-fähig) machen.

    Hardware-Befunde (Ersatzboard): leere/genullte Seriennummer kam als
    rohe `bytes` (`b'\\x00...'`-Repr in der Anzeige; `json.dumps` würde
    auf bytes sogar mit TypeError scheitern). Regeln: druckbares ASCII
    (NUL-/Leer-Ränder weg) als Text, sonst Hex, str wie-ist-gestrippt,
    Rest via `str()`.
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return raw.hex()
        stripped = text.strip("\x00").strip()
        if stripped and stripped.isprintable():
            return stripped
        return raw.hex()
    if isinstance(raw, str):
        return raw.strip()
    return str(raw)


def get_token(lib_path: Optional[str] = None, serial: Optional[str] = None) -> Token:
    """Token holen, optional per Seriennummer gewählt (Mehrgeräte-Support).

    Ohne `serial`: genau ein Token erwartet (Ein-Gerät-Modell). Bei
    mehreren Tokens wirft dies MultipleTokensReturned mit Auswahl-
    Anleitung (--serial) statt still das erste zu nehmen.
    """
    lib = pkcs11.lib(lib_path or default_pkcs11_lib_path())
    if serial:
        for token in lib.get_tokens():
            if format_token_serial(token.serial) == serial:
                return token
        available = ", ".join(
            format_token_serial(t.serial) for t in lib.get_tokens()
        ) or "keine"
        raise NoSuchToken(
            f"Kein Token mit Seriennummer {serial!r} "
            f"(verfügbar: {available})."
        )
    try:
        return lib.get_token()
    except MultipleTokensReturned as exc:
        available = ", ".join(
            format_token_serial(t.serial) for t in lib.get_tokens()
        ) or "keine"
        raise MultipleTokensReturned(
            f"Mehrere Tokens erkannt (Seriennummern: {available}) — "
            "bitte mit --serial <nummer> wählen."
        ) from exc


def list_tokens(lib_path: Optional[str] = None) -> list[dict]:
    """Alle Tokens auflisten (Label/Modell/Seriennummer) — für Auswahl.

    Rein lesend, ohne Login. Leere Liste bei keinem Token (wirft nicht).
    """
    lib = pkcs11.lib(lib_path or default_pkcs11_lib_path())
    try:
        tokens = list(lib.get_tokens())
    except Exception:  # noqa: BLE001 — kein Token/DLL-Fehler = leere Liste
        return []
    return [
        {
            "label": token.label,
            "model": token.model,
            "serial": format_token_serial(token.serial),
        }
        for token in tokens
    ]


@contextmanager
def exclusive_session(
    user_pin: str,
    lib_path: Optional[str] = None,
    serial: Optional[str] = None,
) -> Iterator["pkcs11.Session"]:
    """Kontextmanager für schreibende Operationen.

    "Exklusiv" heißt hier: schreibend (rw) + eingeloggt — KEIN
    OS-/Treiber-Lock. Hardware-Befund (Ersatzboard, `hw-logs/17-...`):
    parallele Sessions (zweiter Prozess, APDU daneben) funktionieren
    störungsfrei; PKCS#11 kennt keinen exklusiven Session-Lock,
    SmartCard-HSM/OpenSC serialisieren intern.

    Öffnet die PKCS#11-Session direkt. Schlägt das Öffnen mit einer
    der `_LIKELY_CONFLICT_ERRORS` fehl, wird das als
    `SessionConflictError` neu geworfen (Sicherheitsnetz für echte
    Belegt-Fälle — seltener als ursprünglich angenommen).
    """
    token = get_token(lib_path, serial=serial)
    try:
        with token.open(user_pin=user_pin, rw=True) as session:
            yield session
    except _LIKELY_CONFLICT_ERRORS as exc:
        raise SessionConflictError(
            f"Öffnen der Session fehlgeschlagen "
            f"({exc.__class__.__name__}: {exc}). Das deutet auf einen "
            f"belegten Reader hin — z.B. ein anderer lokaler Prozess, der "
            f"gerade denselben USB-Reader offen hält."
        ) from exc


@contextmanager
def read_only_session(
    user_pin: Optional[str] = None,
    lib_path: Optional[str] = None,
    serial: Optional[str] = None,
) -> Iterator["pkcs11.Session"]:
    """Für reine Status-/Objektlisten-Abfragen. Rein lesende Zugriffe
    sind unkritischer, kein Conflict-Wrapping."""
    token = get_token(lib_path, serial=serial)
    with token.open(user_pin=user_pin, rw=False) as session:
        yield session
