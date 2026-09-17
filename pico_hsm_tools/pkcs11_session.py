"""
pkcs11_session.py — Session-Handling für die Config-App.

Frühere Version prüfte vor jeder schreibenden Operation, ob ein lokaler
`pico-hsm-daemon` (Unix-Socket) lief, und verlangte bei Konflikt eine
Bestätigung, bevor sie stillschweigend trotzdem zugriff. Dieser Daemon
ist laut `pico-hsm-api-connector/MIGRATION.md` inzwischen komplett durch
das Gateway (mTLS-Netzwerkdienst, exklusiver PKCS#11-Konsument für
sign/verify/encrypt/decrypt/derive) abgelöst — der alte Check prüfte
also eine Komponente, die nicht mehr existiert.

Neuer, zweistufiger Ansatz (siehe Architekturkonzept §7.d):

  1. WEICH — `check_gateway_reachable()`: reiner TCP-Connect-Versuch
     gegen Host/Port des Gateways, sofort wieder getrennt. Kein
     TLS-Handshake, keine authentifizierte Anfrage. Rein informativ,
     blockiert nichts von sich aus.
  2. HART — `exclusive_session()` versucht weiterhin direkt, die
     PKCS#11-Session zu öffnen. Schlägt das mit einer Fehlerklasse fehl,
     die plausibel auf einen belegten Reader hindeutet, wird das als
     `SessionConflictError` neu geworfen — mit dem Ergebnis des weichen
     Checks als zusätzlichem, ausdrücklich unsicherem Kontext.

Anders als vorher gibt es dafür KEIN "trotzdem fortfahren" mehr: der
alte Check war eine Heuristik VOR dem eigentlichen Zugriff (mit
Override-Möglichkeit), der neue Fehler tritt erst auf, wenn das Öffnen
der Session tatsächlich real fehlgeschlagen ist — ein Bestätigungsdialog
würde daran nichts ändern.

BETRIEBSMODELL-HINWEIS: Der weiche Check ist nur dann wirklich
aussagekräftig, wenn App und Gateway sich denselben Reader am selben
Host teilen (echte Ressourcenkonkurrenz möglich). Wandert das
physische HSM stattdessen zwischen Hosts (z.B. Verwaltung an einem
Arbeitsplatzrechner per USB, Gateway dauerhaft auf einem separaten
System, HSM wird für Wartung umgesteckt), ist zu keinem Zeitpunkt mehr
als ein PKCS#11-Konsument tatsächlich am Gerät — der komplette Zustand
(PIN, Keys, DKEK-Shares) lebt auf dem Hardware-Token selbst, nicht auf
einem der Hosts. Der Check bleibt dann technisch nutzbar, aber seine
Aussagekraft über eine tatsächliche Gerätekonkurrenz sinkt gegen null.
Die Fehlermeldung unten formuliert das entsprechend vorsichtig, statt
eine Kausalität zum Gateway zu unterstellen, die es in diesem Betriebs-
modell so nicht gibt.

OFFENER PUNKT (Architekturkonzept §12): welche konkrete
Exception-Klasse `python-pkcs11` wirft, wenn ein zweiter Prozess
exklusiv auf denselben Reader zugreift, ist NICHT gegen echte Hardware
mit zwei parallelen Prozessen verifiziert. `_LIKELY_CONFLICT_ERRORS`
unten ist eine begründete Auswahl (siehe Kommentar dort), keine
verifizierte Zuordnung — nach dem ersten Hardwaretest ggf. anpassen.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import pkcs11
from pkcs11 import Token
from pkcs11.exceptions import DeviceError, SessionCount, TokenNotPresent

DEFAULT_PKCS11_LIB_LINUX = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"
DEFAULT_PKCS11_LIB_WINDOWS = r"C:\Windows\System32\opensc-pkcs11.dll"
DEFAULT_PKCS11_LIB_MACOS = "/usr/local/lib/opensc-pkcs11.so"

# Reiner TCP-Connect-Timeout für den weichen Erreichbarkeits-Check.
DEFAULT_GATEWAY_TIMEOUT = 0.5

# Fehlerklassen, die plausibel auf einen belegten Reader/ein belegtes
# Gerät hindeuten. Bewusst NICHT GeneralError/FunctionFailed: die sind
# zu generisch (treten z.B. auch bei falschem Template oder falscher
# Slot-ID auf) und würden unabhängige, unzusammenhängende Fehler
# fälschlich als "Gerät belegt" ausgeben.
_LIKELY_CONFLICT_ERRORS = (DeviceError, TokenNotPresent, SessionCount)


class SessionConflictError(Exception):
    """Wird geworfen, wenn das Öffnen einer schreibenden Session mit
    einer Fehlerklasse fehlschlägt, die auf einen aktuell belegten
    Reader hindeutet (typischerweise: ein anderer Prozess, z.B. das
    HSM-Gateway, hält gerade eine eigene Verbindung)."""


@dataclass
class GatewayState:
    reachable: bool
    host: Optional[str]
    port: Optional[int]


def check_gateway_reachable(
    host: Optional[str],
    port: Optional[int],
    timeout: float = DEFAULT_GATEWAY_TIMEOUT,
) -> GatewayState:
    """Rein informativer Erreichbarkeits-Check: TCP-Connect versuchen,
    sofort trennen. KEIN TLS-Handshake, KEINE authentifizierte Anfrage —
    das Gateway erfordert Client-Zertifikate, die im
    Management-Kontext dieser App nicht zwangsläufig vorhanden sind.
    Sagt also nur "auf dem Port horcht etwas", nicht "das Gateway läuft
    und funktioniert".

    Werden host/port nicht übergeben (z.B. weil die Gateway-Config aus
    Architekturkonzept §10 noch nicht existiert), liefert dies immer
    reachable=False, ohne Fehler zu werfen — der Check ist rein additiv
    und darf niemals einen Aufrufer blockieren, der (noch) keine
    Gateway-Adresse konfiguriert hat.
    """
    if not host or not port:
        return GatewayState(reachable=False, host=host, port=port)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return GatewayState(reachable=True, host=host, port=port)
    except OSError:
        return GatewayState(reachable=False, host=host, port=port)


def default_pkcs11_lib_path() -> str:
    import platform
    system = platform.system()
    if system == "Windows":
        return DEFAULT_PKCS11_LIB_WINDOWS
    if system == "Darwin":
        return DEFAULT_PKCS11_LIB_MACOS
    return DEFAULT_PKCS11_LIB_LINUX


def get_token(lib_path: Optional[str] = None) -> Token:
    lib = pkcs11.lib(lib_path or default_pkcs11_lib_path())
    token = lib.get_token()
    return token


@contextmanager
def exclusive_session(
    user_pin: str,
    lib_path: Optional[str] = None,
    gateway_host: Optional[str] = None,
    gateway_port: Optional[int] = None,
) -> Iterator["pkcs11.Session"]:
    """Kontextmanager für schreibende Operationen.

    Öffnet die PKCS#11-Session direkt — kein Vorab-Check mehr, der einen
    fremden Prozess "gutartig" per Socket abfragt (das gibt es mit dem
    Gateway nicht mehr auf dieselbe Art wie beim alten Daemon). Schlägt
    das Öffnen mit einer der `_LIKELY_CONFLICT_ERRORS` fehl, wird das
    als `SessionConflictError` neu geworfen, mit dem Ergebnis von
    `check_gateway_reachable()` als zusätzlichem Kontext-Hinweis (siehe
    dort für die Grenzen dieser Aussage).
    """
    token = get_token(lib_path)
    try:
        with token.open(user_pin=user_pin, rw=True) as session:
            yield session
    except _LIKELY_CONFLICT_ERRORS as exc:
        gw = check_gateway_reachable(gateway_host, gateway_port)
        if gw.host:
            gw_hint = (
                f"Gateway unter {gw.host}:{gw.port} ist "
                f"{'erreichbar' if gw.reachable else 'nicht erreichbar'}. "
                "Das ist nur aussagekräftig, falls App und Gateway auf "
                "demselben Host laufen und sich denselben Reader teilen "
                "— wandert das HSM stattdessen zwischen Hosts (z.B. "
                "Verwaltung an einem Arbeitsplatzrechner, Gateway auf "
                "einem separaten System), sagt diese Erreichbarkeit "
                "nichts über die Ursache dieses Fehlers aus."
            )
        else:
            gw_hint = (
                "Keine Gateway-Adresse konfiguriert (--gateway-host/"
                "--gateway-port)."
            )
        raise SessionConflictError(
            f"Öffnen der Session fehlgeschlagen "
            f"({exc.__class__.__name__}: {exc}). Das deutet auf einen "
            f"belegten Reader hin — z.B. ein anderer lokaler Prozess, der "
            f"gerade denselben USB-Reader offen hält. {gw_hint}"
        ) from exc


@contextmanager
def read_only_session(
    user_pin: Optional[str] = None,
    lib_path: Optional[str] = None,
) -> Iterator["pkcs11.Session"]:
    """Für reine Status-/Objektlisten-Abfragen. Rein lesende Zugriffe
    sind unkritischer, kein Conflict-Wrapping."""
    token = get_token(lib_path)
    with token.open(user_pin=user_pin, rw=False) as session:
        yield session
