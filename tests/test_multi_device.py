"""
test_multi_device.py — Mehrgeräte-Support ohne Hardware.

Core (list_tokens, get_token mit Serial-Filter, Session-Weitergabe),
APDU-Readerliste, CLI-Flags --serial/--reader, GUI-Auswahl-Helper.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pkcs11.exceptions
import pytest

from pico_hsm_tools import apdu_core as ac
from pico_hsm_tools import pkcs11_session as ps


def _token(label, serial):
    return SimpleNamespace(label=label, model="PicoHSM", serial=serial)


def _fake_lib(monkeypatch, tokens):
    lib = SimpleNamespace(
        get_tokens=lambda *a, **k: iter(tokens),
        get_token=MagicMock(
            side_effect=pkcs11.exceptions.MultipleTokensReturned("mehrere")
            if len(tokens) > 1 else None,
            return_value=tokens[0] if len(tokens) == 1 else None,
        ),
    )
    monkeypatch.setattr(ps, "pkcs11", SimpleNamespace(lib=lambda path: lib))
    return lib


# --- list_tokens / get_token ---------------------------------------------------

def test_list_tokens_maps_fields(monkeypatch):
    _fake_lib(monkeypatch, [_token("A", "SN1"), _token("B", "SN2")])
    assert ps.list_tokens() == [
        {"label": "A", "model": "PicoHSM", "serial": "SN1"},
        {"label": "B", "model": "PicoHSM", "serial": "SN2"},
    ]


def test_list_tokens_empty_without_hardware(monkeypatch):
    _fake_lib(monkeypatch, [])
    assert ps.list_tokens() == []


def test_get_token_selects_by_serial(monkeypatch):
    _fake_lib(monkeypatch, [_token("A", "SN1"), _token("B", "SN2")])
    assert ps.get_token(serial="SN2").label == "B"


def test_get_token_unknown_serial_lists_available(monkeypatch):
    _fake_lib(monkeypatch, [_token("A", "SN1")])
    with pytest.raises(pkcs11.exceptions.NoSuchToken) as excinfo:
        ps.get_token(serial="SN9")
    assert "SN1" in str(excinfo.value)


def test_get_token_multiple_without_filter_guides(monkeypatch):
    _fake_lib(monkeypatch, [_token("A", "SN1"), _token("B", "SN2")])
    with pytest.raises(pkcs11.exceptions.MultipleTokensReturned) as excinfo:
        ps.get_token()
    message = str(excinfo.value)
    assert "SN1" in message and "SN2" in message
    assert "--serial" in message


def test_sessions_pass_serial(monkeypatch):
    seen: dict = {}

    def fake_get_token(lib_path=None, serial=None):
        seen["serial"] = serial
        token = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock()
        token.open.return_value = cm
        return token

    with patch.object(ps, "get_token", side_effect=fake_get_token):
        with ps.exclusive_session("1234", serial="SN2"):
            pass
        assert seen["serial"] == "SN2"
        with ps.read_only_session(serial="SN1"):
            pass
        assert seen["serial"] == "SN1"


# --- APDU-Readerliste -------------------------------------------------------------

def test_list_readers_without_pyscard(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("smartcard"):
            raise ImportError("kein pyscard")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ac.ApduError) as excinfo:
        ac.list_readers()
    assert "pyscard" in str(excinfo.value)


# --- CLI-Flags ----------------------------------------------------------------------

def test_cli_has_serial_and_reader_flags():
    from click.testing import CliRunner

    from cli.main import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--serial" in result.output
    assert "--reader" in result.output


def test_cli_context_carries_selection():
    from cli.main import cli
    from click.testing import CliRunner

    seen: dict = {}
    from cli.commands import status as status_mod

    real_payload = status_mod._device_payload

    def fake_payload(ctx):
        seen["serial"] = ctx.serial
        seen["reader"] = ctx.reader
        return real_payload(ctx)

    import pico_hsm_tools.pkcs11_session as ps_mod

    with (
        patch.object(status_mod, "_device_payload", side_effect=fake_payload),
        patch.object(ps_mod, "get_token", side_effect=Exception("kein Board")),
    ):
        result = CliRunner().invoke(
            cli, ["--serial", "SN2", "--reader", "R1", "status", "device"],
        )
    assert seen == {"serial": "SN2", "reader": "R1"}
    assert result.exit_code == 1  # kein Board, aber Auswahl kam an


# --- GUI-Auswahl ----------------------------------------------------------------------

def test_selected_serial_defaults_to_auto(qapp, monkeypatch):
    from gui import config as gui_config
    from gui.session_helpers import selected_serial

    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "")
    assert selected_serial() is None
    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "SN2")
    assert selected_serial() == "SN2"
