"""
test_cli_wiring.py — CLI-Integrations-Regressions­tests ohne Hardware.

Deckt die Schritt-4-Lücken ab:
  - `status all --json` emittiert EIN gültiges JSON-Dokument (statt drei
    Fragmenten aus drei invoke-Aufrufen).
  - `status all` im Textmodus ruft device/gateway/audit auf (unverändert).
  - `keys import` hält die ctx-Konvention ein (kein rohes click.echo,
    JSON im --json-Modus).

Hardware-Zugriffe werden auf Modulebene gemockt
(_device_payload/_gateway_payload/_audit_payload); getestet wird die
Verdrahtung, nicht die Firmware.
"""

from __future__ import annotations

import io
import json
import sys

import pytest
from click.testing import CliRunner

from cli.commands import status as status_mod
from cli.context import CliContext
from cli.main import cli


def _fake_payloads(monkeypatch):
    monkeypatch.setattr(
        status_mod, "_device_payload",
        lambda ctx: {"label": "T", "model": "M", "serial": "S"},
    )
    monkeypatch.setattr(
        status_mod, "_gateway_payload",
        lambda ctx: {"reachable": True, "host": "h", "port": 1},
    )
    monkeypatch.setattr(
        status_mod, "_audit_payload",
        lambda limit: {"chain_intact": True, "entries": []},
    )


def test_status_all_json_is_single_document(monkeypatch):
    """Regressionstest für die drei JSON-Fragmente: genau EIN Dokument."""
    _fake_payloads(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "status", "all"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)  # wirft bei Fragmenten
    assert set(doc) == {"device", "gateway", "audit"}
    assert doc["device"]["label"] == "T"
    assert doc["gateway"]["reachable"] is True
    assert doc["audit"]["chain_intact"] is True


def test_status_all_text_invokes_all_three(monkeypatch):
    _fake_payloads(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "all"])
    assert result.exit_code == 0, result.output
    assert "Erkannt:" in result.output
    assert "Gateway" in result.output
    assert "Hash-Chain" in result.output


def test_status_all_json_propagates_device_failure(monkeypatch):
    _fake_payloads(monkeypatch)

    def boom(ctx):
        raise RuntimeError("kein Board")

    monkeypatch.setattr(status_mod, "_device_payload", boom)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "status", "all"])
    assert result.exit_code == 2
    doc = json.loads(result.output)
    assert "error" in doc


def test_keys_import_text_shows_hint():
    runner = CliRunner()
    result = runner.invoke(cli, ["keys", "import"])
    assert result.exit_code == 0, result.output
    assert "dkek unwrap-key" in result.output


def test_keys_import_json_is_json_only():
    """Kein Klartext im --json-Modus (früher rohes click.echo)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "keys", "import"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)  # wirft bei zusätzlichem Klartext
    assert doc["status"] == "ok"
    assert doc["hint"] == "dkek unwrap-key"


# --- F4: PIN-Eingabe ohne TTY (Hang-Schutz) -------------------------------------

_NON_TTY = io.StringIO("")


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_get_pin_fails_fast_without_tty(monkeypatch):
    """Regressionstest F4: ohne TTY kein getpass-Hang, sondern fail."""
    monkeypatch.setattr(sys, "stdin", _NON_TTY)

    def no_getpass(prompt=""):
        raise AssertionError("getpass darf ohne TTY nicht aufgerufen werden")

    monkeypatch.setattr("cli.context.getpass", no_getpass)
    ctx = CliContext()
    with pytest.raises(SystemExit) as exc_info:
        ctx.get_pin()
    assert exc_info.value.code == 1


def test_get_pin_uses_env_without_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _NON_TTY)
    monkeypatch.setenv("PICO_TEST_PIN", "env-9999")
    ctx = CliContext(pin_env="PICO_TEST_PIN")
    assert ctx.get_pin() == "env-9999"


def test_get_pin_prompts_with_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(
        "cli.context.getpass", lambda prompt="": "typed-1234",
    )
    ctx = CliContext()
    assert ctx.get_pin() == "typed-1234"
    assert ctx.get_pin() == "typed-1234"  # Cache, kein zweiter Prompt
