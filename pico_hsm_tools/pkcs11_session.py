"""
pkcs11_session.py — Session-Handling für die Config-App.

Wichtige Randbedingung aus dem Projekt: paralleles Nutzen von
pico-hsm-daemon (Python) und einem zweiten PKCS#11-Client auf demselben
Token ist unsafe (unkoordinierte Sessions können sich stören). Diese App
spricht bewusst DIREKT per PKCS#11, nicht über den Daemon — muss daher
selbst dafür sorgen, dass kein Konflikt entsteht:

  1. Vor dem Öffnen einer Session: prüfen, ob der Daemon aktuell läuft
     (Unix-Socket erreichbar). Falls ja: Nutzer warnen und Bestätigung
     verlangen, oder Aktion blockieren, bevor der Daemon nicht gestoppt
     wurde — analog zum maybe_stop_daemon/restore_daemon-Muster der CLI.
  2. Für schreibende Operationen (PIN ändern, Keys anlegen/löschen,
     DKEK-Import) IMMER eine exklusive Session verlangen.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import pkcs11
from pkcs11 import Token

DEFAULT_PKCS11_LIB_LINUX = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"
DEFAULT_PKCS11_LIB_WINDOWS = r"C:\Windows\System32\opensc-pkcs11.dll"
DEFAULT_PKCS11_LIB_MACOS = "/usr/local/lib/opensc-pkcs11.so"

# Muss zum tatsächlichen Pfad der daemon-config passen (Unix-Socket).
DAEMON_SOCKET_PATH = Path.home() / ".pico_hsm" / "daemon.sock"


class SessionConflictError(Exception):
    """Wird geworfen, wenn eine schreibende Operation versucht wird,
    während der Daemon nachweislich aktiv ist und der Nutzer den Konflikt
    nicht bestätigt/aufgelöst hat."""


@dataclass
class DaemonState:
    running: bool
    socket_path: Path


def check_daemon_running(socket_path: Path = DAEMON_SOCKET_PATH) -> DaemonState:
    """Rein lesende Prüfung: versucht, sich mit dem Unix-Socket des
    pico-hsm-daemon zu verbinden. Führt KEINE RPC-Calls aus, um keine
    eigene Session-Kollision zu riskieren — nur ein reiner Connect-Test."""
    if not socket_path.exists():
        return DaemonState(running=False, socket_path=socket_path)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(str(socket_path))
        return DaemonState(running=True, socket_path=socket_path)
    except OSError:
        return DaemonState(running=False, socket_path=socket_path)


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
    allow_daemon_conflict: bool = False,
) -> Iterator["pkcs11.Session"]:
    """Kontextmanager für schreibende Operationen. Prüft vor dem Öffnen
    der Session, ob der Daemon läuft, und bricht standardmäßig ab, statt
    stillschweigend parallel zuzugreifen."""
    state = check_daemon_running()
    if state.running and not allow_daemon_conflict:
        raise SessionConflictError(
            "pico-hsm-daemon läuft aktuell (Socket erreichbar unter "
            f"{state.socket_path}). Paralleler PKCS#11-Zugriff ist unsafe. "
            "Bitte Daemon stoppen (`systemctl stop pico-hsm-daemon`) und "
            "erneut versuchen, oder explizit bestätigen."
        )

    token = get_token(lib_path)
    with token.open(user_pin=user_pin, rw=True) as session:
        yield session


@contextmanager
def read_only_session(
    user_pin: Optional[str] = None,
    lib_path: Optional[str] = None,
) -> Iterator["pkcs11.Session"]:
    """Für reine Status-/Objektlisten-Abfragen (Status-Tab). Auch hier
    kein automatischer Daemon-Stop, aber Konflikte sind hier unkritischer
    da rein lesend."""
    token = get_token(lib_path)
    with token.open(user_pin=user_pin, rw=False) as session:
        yield session
