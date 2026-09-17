"""
test_pkcs11_session.py — Tests für den kombinierten Konflikt-Check.

Deckt ab: den weichen TCP-Erreichbarkeits-Check (mit echtem lokalen
Listener, keine PKCS#11-Hardware nötig) und die Fehlertext-Konstruktion
im harten Check (gemockte Session, keine echte Hardware).

Besonderer Fokus: die Fehlermeldung darf keine Kausalität zum Gateway
unterstellen, die im Betriebsmodell "HSM wandert zwischen Hosts" (App
verwaltet per USB, Gateway läuft woanders) gar nicht gegeben sein kann
— siehe Modul-Docstring in pkcs11_session.py.
"""

from __future__ import annotations

import socket
import threading
from contextlib import closing
from unittest.mock import MagicMock, patch

import pkcs11
import pytest

from pico_hsm_tools import pkcs11_session as ps


# --- check_gateway_reachable ---------------------------------------------

def test_no_host_or_port_is_never_reachable_without_error():
    assert ps.check_gateway_reachable(None, None) == ps.GatewayState(False, None, None)
    assert ps.check_gateway_reachable("host", None) == ps.GatewayState(False, "host", None)


def test_reachable_with_real_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        state = ps.check_gateway_reachable("127.0.0.1", port)
        assert state.reachable is True
    finally:
        stop.set()
        srv.close()
        t.join(timeout=1)


def test_not_reachable_when_nothing_listens():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    state = ps.check_gateway_reachable("127.0.0.1", free_port, timeout=0.2)
    assert state.reachable is False


# --- exclusive_session: Fehlertext darf keine Kausalität unterstellen ----

def _make_failing_token(exc: Exception):
    token = MagicMock()
    cm = MagicMock()
    cm.__enter__.side_effect = exc
    token.open.return_value = cm
    return token


def test_session_conflict_message_without_gateway_config():
    failing = _make_failing_token(pkcs11.exceptions.DeviceError("boom"))
    with patch.object(ps, "get_token", return_value=failing):
        with pytest.raises(ps.SessionConflictError) as excinfo:
            with ps.exclusive_session("1234"):
                pass
    msg = str(excinfo.value)
    assert "belegten Reader" in msg
    assert "Keine Gateway-Adresse konfiguriert" in msg


def test_session_conflict_message_does_not_overclaim_when_reachable():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        failing = _make_failing_token(pkcs11.exceptions.TokenNotPresent("boom"))
        with patch.object(ps, "get_token", return_value=failing):
            with pytest.raises(ps.SessionConflictError) as excinfo:
                with ps.exclusive_session(
                    "1234", gateway_host="127.0.0.1", gateway_port=port,
                ):
                    pass
    finally:
        stop.set()
        srv.close()
        t.join(timeout=1)

    msg = str(excinfo.value)
    assert "erreichbar" in msg
    # Keine unterstellte Kausalität zum Gateway (Regressionstest für den
    # Sebastian-Fix: "HSM wandert zwischen Hosts" macht die alte
    # Formulierung "möglicherweise belegt es den Reader" irreführend):
    assert "möglicherweise belegt" not in msg
    assert "vermutlich" not in msg
    # Stattdessen der Hinweis auf die Einschränkung der Aussagekraft:
    assert "nur aussagekräftig" in msg


def test_session_conflict_message_when_gateway_unreachable():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    failing = _make_failing_token(pkcs11.exceptions.SessionCount("boom"))
    with patch.object(ps, "get_token", return_value=failing):
        with pytest.raises(ps.SessionConflictError) as excinfo:
            with ps.exclusive_session(
                "1234", gateway_host="127.0.0.1", gateway_port=free_port,
            ):
                pass
    msg = str(excinfo.value)
    assert "nicht erreichbar" in msg


def test_non_conflict_errors_propagate_unchanged():
    """Fehler, die NICHT auf einen belegten Reader hindeuten (z.B.
    falsche PIN), werden nicht in SessionConflictError umgewandelt."""
    failing = _make_failing_token(pkcs11.exceptions.PinIncorrect("falsche PIN"))
    with patch.object(ps, "get_token", return_value=failing):
        with pytest.raises(pkcs11.exceptions.PinIncorrect):
            with ps.exclusive_session("0000"):
                pass
