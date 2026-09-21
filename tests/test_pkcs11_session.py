"""
test_pkcs11_session.py — Tests für Session-Handling und Konflikt-Check.

Deckt ab: Lib-Pfad-Auswahl, Token-Serial-Formatierung, Token-Auswahl
und die Fehlertext-Konstruktion im harten Check (gemockte Session,
keine echte Hardware).
"""

from __future__ import annotations

import platform
from unittest.mock import MagicMock, patch

import pkcs11
import pytest

from pico_hsm_tools import pkcs11_session as ps


# --- default_pkcs11_lib_path (Windows-Fallback-Kette) --------------------------

class _FakePath:
    """Path-Ersatz für Kandidaten-Tests (existiert nur wo gewünscht)."""

    existing: set = set()

    def __init__(self, path):
        self._path = path

    def exists(self):
        return self._path in _FakePath.existing


def test_windows_lib_prefers_existing_candidate(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(ps, "Path", _FakePath)
    _FakePath.existing = {
        r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
    }
    try:
        assert ps.default_pkcs11_lib_path() == (
            r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
        )
    finally:
        _FakePath.existing = set()


def test_windows_lib_falls_back_to_first_candidate(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(ps, "Path", _FakePath)
    _FakePath.existing = set()
    try:
        assert ps.default_pkcs11_lib_path() == ps._WINDOWS_PKCS11_CANDIDATES[0]
    finally:
        _FakePath.existing = set()


def test_non_windows_lib_paths_unchanged(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert ps.default_pkcs11_lib_path() == ps.DEFAULT_PKCS11_LIB_LINUX
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert ps.default_pkcs11_lib_path() == ps.DEFAULT_PKCS11_LIB_MACOS


# --- format_token_serial (Hardware-Befunde Ersatzboard) -------------------------

def test_format_token_serial_printable_bytes():
    assert ps.format_token_serial(b"ESPICOHSMTR") == "ESPICOHSMTR"


def test_format_token_serial_zeroed_bytes_becomes_hex():
    raw = b"\x00" * 16
    assert ps.format_token_serial(raw) == "00" * 16


def test_format_token_serial_non_ascii_bytes_becomes_hex():
    assert ps.format_token_serial(b"\xff\xfe") == "fffe"


def test_format_token_serial_str_passthrough():
    assert ps.format_token_serial("  ABC123  ") == "ABC123"


def test_format_token_serial_other_types():
    assert ps.format_token_serial(None) == "None"
    assert ps.format_token_serial(123) == "123"


# --- exclusive_session: Belegt-Fehlermeldung ---------------------------------------

def _make_failing_token(exc: Exception):
    token = MagicMock()
    cm = MagicMock()
    cm.__enter__.side_effect = exc
    token.open.return_value = cm
    return token


def test_session_conflict_message_mentions_busy_reader():
    failing = _make_failing_token(pkcs11.exceptions.DeviceError("boom"))
    with patch.object(ps, "get_token", return_value=failing):
        with pytest.raises(ps.SessionConflictError) as excinfo:
            with ps.exclusive_session("1234"):
                pass
    msg = str(excinfo.value)
    assert "belegten Reader" in msg
    assert "DeviceError" in msg
    assert "Gateway" not in msg


def test_non_conflict_errors_propagate_unchanged():
    """Fehler, die NICHT auf einen belegten Reader hindeuten (z.B.
    falsche PIN), werden nicht in SessionConflictError umgewandelt."""
    failing = _make_failing_token(pkcs11.exceptions.PinIncorrect("falsche PIN"))
    with patch.object(ps, "get_token", return_value=failing):
        with pytest.raises(pkcs11.exceptions.PinIncorrect):
            with ps.exclusive_session("0000"):
                pass
