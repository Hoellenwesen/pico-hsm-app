"""
test_pin_core.py — pin_core-Tests ohne Hardware und ohne pkcs11-tool.

Mockt subprocess.run (Argument-Abbildung, Fehlerpfade) und get_token
(Flag-Bitmaske). Deckt ab, was ohne Board verifizierbar ist: exakte
Tool-Argumente, Fehler-Mapping, PIN-Vertrag (keine PIN-Werte in
Exceptions).
"""

from __future__ import annotations

import subprocess

import pytest
from pkcs11.constants import TokenFlag

from pico_hsm_tools import pin_core as pc


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["pkcs11-tool"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def _install_run(monkeypatch, behavior):
    """subprocess.run im pin_core-Namensraum ersetzen. Gibt calls zurück."""
    calls: list = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs.get("timeout")))
        return behavior(list(args))

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    return calls


# --- change ----------------------------------------------------------------------

def test_change_builds_exact_args(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    pc.change_user_pin("alt", "neu")
    assert calls == [(
        ["pkcs11-tool", "--login", "--pin", "alt",
         "--change-pin", "--new-pin", "neu"], 30,
    )]


def test_change_with_lib_adds_module(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    pc.change_user_pin("alt", "neu", lib_path="C:/x/opensc-pkcs11.dll")
    assert calls[0][0][:3] == ["pkcs11-tool", "--module", "C:/x/opensc-pkcs11.dll"]


def test_change_maps_stderr_to_error(monkeypatch):
    _install_run(
        monkeypatch, lambda args: _result(1, stderr="CKR_PIN_INCORRECT"),
    )
    with pytest.raises(pc.PinError, match="CKR_PIN_INCORRECT"):
        pc.change_user_pin("alt", "neu")


def test_change_missing_binary(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError("pkcs11-tool")

    monkeypatch.setattr(pc.subprocess, "run", boom)
    with pytest.raises(pc.PinError, match="nicht gefunden"):
        pc.change_user_pin("alt", "neu")


def test_change_timeout(monkeypatch):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(pc.subprocess, "run", boom)
    with pytest.raises(pc.PinError, match="Timeout"):
        pc.change_user_pin("alt", "neu")


# --- unblock -----------------------------------------------------------------------

def test_unblock_builds_exact_args(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    pc.unblock_user_pin("so-pin", "neu")
    assert calls == [(
        ["pkcs11-tool", "--login", "--login-type", "so", "--so-pin", "so-pin",
         "--init-pin", "--new-pin", "neu"], 30,
    )]


def test_unblock_maps_stderr_to_error(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(1, stderr="CKR_PIN_LOCKED"))
    with pytest.raises(pc.PinError, match="CKR_PIN_LOCKED"):
        pc.unblock_user_pin("so-pin", "neu")


# --- status --------------------------------------------------------------------------

def test_read_pin_flags_maps_bitmask(monkeypatch):
    class FakeToken:
        flags = TokenFlag.LOGIN_REQUIRED | TokenFlag.USER_PIN_LOCKED

    monkeypatch.setattr(
        pc, "get_token", lambda lib_path=None, serial=None: FakeToken(),
    )
    flags = pc.read_pin_flags()
    assert flags["login_required"] is True
    assert flags["user_pin_locked"] is True
    assert flags["user_pin_initialized"] is False
    assert flags["user_pin_count_low"] is False
    assert flags["user_pin_final_try"] is False
    assert flags["user_pin_to_be_changed"] is False


def test_read_pin_flags_wraps_token_error(monkeypatch):
    def boom(lib_path=None, serial=None):
        raise RuntimeError("kein Board")

    monkeypatch.setattr(pc, "get_token", boom)
    with pytest.raises(pc.PinError, match="Kein Gerät erkannt"):
        pc.read_pin_flags()


# --- Sicherheitsvertrag ------------------------------------------------------------------

def test_no_pin_values_in_errors(monkeypatch):
    """PIN-Vertrag: kein PIN-Wert in Exceptions (User- wie SO-PIN)."""
    _install_run(
        monkeypatch, lambda args: _result(1, stderr="generischer Fehler"),
    )
    with pytest.raises(pc.PinError) as exc_info:
        pc.change_user_pin("s3cr3t-alt", "s3cr3t-neu")
    assert "s3cr3t-alt" not in str(exc_info.value)
    assert "s3cr3t-neu" not in str(exc_info.value)

    with pytest.raises(pc.PinError) as exc_info:
        pc.unblock_user_pin("s3cr3t-so", "s3cr3t-neu")
    assert "s3cr3t-so" not in str(exc_info.value)
