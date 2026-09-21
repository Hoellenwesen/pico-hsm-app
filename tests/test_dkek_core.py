"""
test_dkek_core.py — dkek_core-Tests ohne Hardware und ohne sc-hsm-tool.

Mockt subprocess.run (Argument-Abbildung, Fehlerpfade). Deckt ab, was
ohne Board verifizierbar ist: exakte Tool-Argumente (1:1 aus
backup-and-restore.md), Fehler-Mapping, PIN-Vertrag (keine PIN-Werte
in Exceptions), ungeparste Status-Rückgabe.
"""

from __future__ import annotations

import subprocess

import pytest

from pico_hsm_tools import dkek_core as dc


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["sc-hsm-tool"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def _install_run(monkeypatch, behavior):
    """subprocess.run im dkek_core-Namensraum ersetzen. Gibt calls zurück."""
    calls: list = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs.get("timeout")))
        return behavior(list(args))

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    return calls


# --- wrap ----------------------------------------------------------------------------

def test_wrap_builds_exact_args(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    dc.wrap_key("C:/tmp/k.wrap", 7, "1234")
    assert calls == [(
        ["sc-hsm-tool", "--wrap-key", "C:/tmp/k.wrap",
         "--key-reference", "7", "--pin", "1234"], 30,
    )]


def test_wrap_maps_stderr_to_error(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(1, stderr="CKR_ERROR"))
    with pytest.raises(dc.DkekError, match="CKR_ERROR"):
        dc.wrap_key("C:/tmp/k.wrap", 7, "1234")


def test_wrap_missing_binary(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError("sc-hsm-tool")

    monkeypatch.setattr(dc.subprocess, "run", boom)
    with pytest.raises(dc.DkekError, match="nicht gefunden"):
        dc.wrap_key("C:/tmp/k.wrap", 7, "1234")


def test_wrap_timeout(monkeypatch):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(dc.subprocess, "run", boom)
    with pytest.raises(dc.DkekError, match="Timeout"):
        dc.wrap_key("C:/tmp/k.wrap", 7, "1234")


# --- unwrap ----------------------------------------------------------------------------

def test_unwrap_builds_exact_args(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    dc.unwrap_key("C:/tmp/k.wrap", 3, "1234")
    assert calls == [(
        ["sc-hsm-tool", "--unwrap-key", "C:/tmp/k.wrap",
         "--key-reference", "3", "--pin", "1234"], 30,
    )]


def test_unwrap_maps_stderr_to_error(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(1, stderr="CKR_ERROR"))
    with pytest.raises(dc.DkekError, match="CKR_ERROR"):
        dc.unwrap_key("C:/tmp/k.wrap", 3, "1234")


def test_failure_message_includes_stdout_detail(monkeypatch):
    """Hardware-Befund: Detail steht teils auf stdout (reines stderr
    zeigte nur `Using reader ...`)."""
    _install_run(
        monkeypatch,
        lambda args: _result(
            1, stderr="Using reader with a card: X",
            stdout="Found existing certificate in EF with fid ce01.",
        ),
    )
    with pytest.raises(dc.DkekError) as exc_info:
        dc.unwrap_key("C:/tmp/k.wrap", 3, "1234")
    assert "Using reader" in str(exc_info.value)
    assert "fid ce01" in str(exc_info.value)


def test_failure_message_falls_back_to_default(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(1))
    with pytest.raises(dc.DkekError, match="unwrap-key fehlgeschlagen"):
        dc.unwrap_key("C:/tmp/k.wrap", 3, "1234")


def test_unwrap_force_appends_flag(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    dc.unwrap_key("C:/tmp/k.wrap", 3, "1234", force=True)
    assert calls == [(
        ["sc-hsm-tool", "--unwrap-key", "C:/tmp/k.wrap",
         "--key-reference", "3", "--pin", "1234", "--force"], 30,
    )]


def test_unwrap_without_force_has_no_flag(monkeypatch):
    calls = _install_run(monkeypatch, lambda args: _result())
    dc.unwrap_key("C:/tmp/k.wrap", 3, "1234")
    assert "--force" not in calls[0][0]


# --- status ------------------------------------------------------------------------------

def test_status_returns_raw_text(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(stdout="  SHARES: 2\n"))
    assert dc.dkek_status() == "SHARES: 2"


def test_status_failure_maps_to_error(monkeypatch):
    _install_run(monkeypatch, lambda args: _result(1, stderr="kein Reader"))
    with pytest.raises(dc.DkekError, match="kein Reader"):
        dc.dkek_status()


# --- Sicherheitsvertrag ----------------------------------------------------------------------

def test_no_pin_values_in_errors(monkeypatch):
    """PIN-Vertrag: kein PIN-Wert in Exceptions."""
    _install_run(
        monkeypatch, lambda args: _result(1, stderr="generischer Fehler"),
    )
    with pytest.raises(dc.DkekError) as exc_info:
        dc.wrap_key("C:/tmp/k.wrap", 7, "s3cr3t-pin")
    assert "s3cr3t-pin" not in str(exc_info.value)

    with pytest.raises(dc.DkekError) as exc_info:
        dc.unwrap_key("C:/tmp/k.wrap", 7, "s3cr3t-pin")
    assert "s3cr3t-pin" not in str(exc_info.value)
