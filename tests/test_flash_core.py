"""
test_flash_core.py — Secure-Boot-Gate-Tests ohne Hardware und ohne Board.

Mockt _run_picotool (Geräte-Antworten) und die Preflight-Bausteine.
Deckt ab, was ohne Board verifizierbar ist: Secure-Boot-Parsing aus
echter `picotool info -a`-Ausgabe (secure boot: 0/1), Gate-Verhalten
(check enforced vs. übersprungen), Fehlerpfade (kein Board, keine
Zeile). Echte Board-Verifikation bleibt §12-Sache.
"""

from __future__ import annotations

import subprocess

import pytest

from pico_hsm_tools import flash_core as fc


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["picotool"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# Echte Ausgabe-Zeile (vom Ersatzboard, BOOTSEL):
#  " secure boot:            0"
def _info_output(secure_boot: str | None):
    lines = ["type:                   RP2350"]
    if secure_boot is not None:
        lines.append(f" secure boot:            {secure_boot}")
    lines.append(" debug enable:           1")
    return "\n".join(lines)


def _install_picotool(monkeypatch, returncode=0, stdout=""):
    calls: list = []

    def fake_run(args):
        calls.append(list(args))
        return _completed(returncode, stdout)

    monkeypatch.setattr(fc, "_run_picotool", fake_run)
    return calls


# --- is_secure_boot_enabled ------------------------------------------------------

def test_secure_boot_on_parses_real_line(monkeypatch):
    _install_picotool(monkeypatch, stdout=_info_output("1"))
    assert fc.is_secure_boot_enabled() is True


def test_secure_boot_off_parses_real_line(monkeypatch):
    _install_picotool(monkeypatch, stdout=_info_output("0"))
    assert fc.is_secure_boot_enabled() is False


def test_secure_boot_unreachable_board(monkeypatch):
    _install_picotool(monkeypatch, returncode=1)
    with pytest.raises(fc.FlashError, match="BOOTSEL"):
        fc.is_secure_boot_enabled()


def test_secure_boot_missing_line(monkeypatch):
    _install_picotool(monkeypatch, stdout=_info_output(None))
    with pytest.raises(fc.FlashError, match="nicht ermittelbar"):
        fc.is_secure_boot_enabled()


# --- run_preflight-Gate --------------------------------------------------------------

def _install_preflight_chain(monkeypatch, secure_boot: bool):
    """Preflight-Bausteine mocken (außer dem Gate-Entscheid)."""
    monkeypatch.setattr(fc, "check_picotool_available", lambda: None)
    monkeypatch.setattr(fc, "sha256_of", lambda path: "ab" * 32)
    monkeypatch.setattr(fc, "_signature_state_for_file", lambda path: True)
    monkeypatch.setattr(
        fc, "get_uf2_version", lambda path: fc.Uf2Info(1, 0, 3),
    )
    monkeypatch.setattr(fc, "load_last_known", lambda: {"rollback": 2})
    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_info_output("1" if secure_boot else "0")),
    )


def test_gate_enforced_when_secure_boot_on(monkeypatch, tmp_path):
    _install_preflight_chain(monkeypatch, secure_boot=True)
    calls: list = []
    monkeypatch.setattr(
        fc, "verify_board_identity",
        lambda expected: calls.append(expected) or "FF" * 64,
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    result = fc.run_preflight(fw, "FF" * 64)
    assert calls == ["FF" * 64]
    assert result.fingerprint_checked is True
    assert result.secure_boot_enabled is True
    assert result.board_fingerprint == "FF" * 64


def test_gate_skipped_when_secure_boot_off(monkeypatch, tmp_path):
    _install_preflight_chain(monkeypatch, secure_boot=False)

    def forbidden(expected):
        raise AssertionError("verify_board_identity darf nicht laufen")

    monkeypatch.setattr(fc, "verify_board_identity", forbidden)
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    result = fc.run_preflight(fw, "FF" * 64)
    assert result.fingerprint_checked is False
    assert result.secure_boot_enabled is False
    assert result.board_fingerprint == ""
    assert result.signature_checked is True
    assert result.version.rollback == 3


# --- Signatur-Tristate (Board-Format: keine signature-Zeile) ---------------------

def _file_info_output(signature: str | None):
    """Realistische `picotool info -a DATEI`-Ausgabe (Ersatzboard:
    KEINE signature-Zeile, nur `hash: verified` der Partitionstabelle)."""
    lines = ["version:                6.6", "hash:                   verified"]
    if signature is not None:
        lines.append(f"signature:              {signature}")
    return "\n".join(lines)


def test_signature_state_variants(monkeypatch):
    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_file_info_output("verified")),
    )
    assert fc._signature_state_for_file("x") is True
    assert fc.verify_signature("x") is True

    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_file_info_output("incorrect")),
    )
    assert fc._signature_state_for_file("x") is False
    assert fc.verify_signature("x") is False

    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_file_info_output(None)),
    )
    assert fc._signature_state_for_file("x") is None
    assert fc.verify_signature("x") is False


def test_incorrect_signature_always_fails(monkeypatch, tmp_path):
    """Manipuliert/falsch signiert: Abbruch AUCH bei Secure Boot aus."""
    _install_preflight_chain(monkeypatch, secure_boot=False)
    monkeypatch.setattr(fc, "_signature_state_for_file", lambda path: False)
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    with pytest.raises(fc.FlashError, match="Signaturprüfung fehlgeschlagen"):
        fc.run_preflight(fw, "FF" * 64)


def test_absent_signature_fails_when_secure_boot_on(monkeypatch, tmp_path):
    _install_preflight_chain(monkeypatch, secure_boot=True)
    monkeypatch.setattr(fc, "_signature_state_for_file", lambda path: None)
    monkeypatch.setattr(
        fc, "verify_board_identity", lambda expected: "FF" * 64,
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    with pytest.raises(fc.FlashError, match="Keine Signatur"):
        fc.run_preflight(fw, "FF" * 64)


def test_absent_signature_warns_when_secure_boot_off(monkeypatch, tmp_path):
    _install_preflight_chain(monkeypatch, secure_boot=False)
    monkeypatch.setattr(fc, "_signature_state_for_file", lambda path: None)
    appended: list = []
    monkeypatch.setattr(
        fc, "append_audit", lambda entry: appended.append(entry),
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    result = fc.run_preflight(fw, "FF" * 64)
    assert result.signature_checked is False
    assert result.secure_boot_enabled is False
    assert [e["status"] for e in appended] == ["accepted_unsigned"]


# --- get_uf2_version: Rollback optional (Board-Befund) --------------------------
# Unsignierte Dev-Builds enthalten KEINE Rollback-Zeile (nur Version +
# Partitionstabellen-Hash) — früher harter Abbruch, jetzt -1.

def _version_output(*, version="6.6", rollback="3"):
    lines = [f"version:                {version}"]
    if rollback is not None:
        lines.append(f"rollback version:       {rollback}")
    return "\n".join(lines)


def test_version_parses_with_rollback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_version_output()),
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    info = fc.get_uf2_version(fw)
    assert (info.major, info.minor, info.rollback) == (6, 6, 3)


def test_version_missing_rollback_gives_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout=_version_output(rollback=None)),
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    info = fc.get_uf2_version(fw)
    assert (info.major, info.minor, info.rollback) == (6, 6, -1)


def test_version_missing_version_still_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fc, "_run_picotool", lambda args: _completed(stdout="hash: ok"),
    )
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    with pytest.raises(fc.FlashError, match="Keine Versions-Angabe"):
        fc.get_uf2_version(fw)


def test_format_version_rollback_known_and_unknown():
    assert fc.format_version_rollback(fc.Uf2Info(1, 0, 3)) == "1.0 (rollback=3)"
    assert fc.format_version_rollback(fc.Uf2Info(6, 6, -1)) == (
        "6.6 (rollback unbekannt)"
    )


def test_unknown_rollback_never_triggers_downgrade(monkeypatch, tmp_path):
    """Guard: -1 (unbekannt) darf keinen Fehlalarm auslösen, auch wenn
    je ein Rollback protokolliert wurde."""
    _install_preflight_chain(monkeypatch, secure_boot=False)
    monkeypatch.setattr(fc, "_signature_state_for_file", lambda path: None)
    monkeypatch.setattr(
        fc, "get_uf2_version", lambda path: fc.Uf2Info(6, 6, -1),
    )
    monkeypatch.setattr(fc, "load_last_known", lambda: {"rollback": 5})
    fw = tmp_path / "fw.uf2"
    fw.write_bytes(b"fake")
    result = fc.run_preflight(fw, "FF" * 64)
    assert result.version.rollback == -1

def _otp_get_output(value: str) -> str:
    """Realistische `picotool otp get`-Antwort (Ersatzboard, BOOTSEL)."""
    return (
        "ROW 0x0080: OTP_DATA_BOOTKEY0_0 (Part 1/16)\n"
        '        "Bits 15:0 of SHA-256 hash of boot key 0 (ECC)"\n'
        "\n"
        f"    VALUE {value}\n"
    )


def _install_otp_rows(monkeypatch, values: dict[int, str] | None = None,
                      fail_on: int | None = None):
    """16 BOOTKEY-Rows mocken (default: alles 0x0000 wie frisches Board)."""
    calls: list = []

    def fake_run(args):
        calls.append(list(args))
        field = args[2]
        index = int(field.rsplit("_", 1)[1])
        if fail_on is not None and index == fail_on:
            return _completed(returncode=1, stdout="", stderr="Fehler")
        value = (values or {}).get(index, "0x0000")
        return _completed(stdout=_otp_get_output(value))

    monkeypatch.setattr(fc, "_run_picotool", fake_run)
    return calls


def test_fingerprint_unburned_board_is_zeros(monkeypatch):
    """Erwartungswert fürs Ersatzboard (nichts gebrannt)."""
    _install_otp_rows(monkeypatch)
    assert fc.get_burned_key_fingerprint() == "00" * 32


def test_fingerprint_assembles_rows_in_order(monkeypatch):
    values = {i: f"0x{i:04X}" for i in range(16)}
    calls = _install_otp_rows(monkeypatch, values)
    result = fc.get_burned_key_fingerprint()
    assert result == "".join(f"{i:04x}" for i in range(16))
    assert len(result) == 64
    requested = [c[2] for c in calls]
    assert requested == [f"OTP_DATA_BOOTKEY0_{i}" for i in range(16)]


def test_fingerprint_row_failure_aborts(monkeypatch):
    _install_otp_rows(monkeypatch, fail_on=7)
    with pytest.raises(fc.FlashError, match="OTP_DATA_BOOTKEY0_7"):
        fc.get_burned_key_fingerprint()


def test_fingerprint_missing_value_aborts(monkeypatch):
    monkeypatch.setattr(
        fc, "_run_picotool",
        lambda args: _completed(stdout="ROW 0x0080: OTP_DATA_BOOTKEY0_0\n"),
    )
    with pytest.raises(fc.FlashError, match="Kein VALUE"):
        fc.get_burned_key_fingerprint()


def test_fingerprint_overlong_value_aborts(monkeypatch):
    calls = _install_otp_rows(monkeypatch, {3: "0x12345"})
    with pytest.raises(fc.FlashError, match="kein stilles"):
        fc.get_burned_key_fingerprint()
    assert len(calls) == 4  # Abbruch sofort bei der auffälligen Row


# --- read_otp_field ------------------------------------------------------------------

def test_read_otp_field_returns_value(monkeypatch):
    output = (
        "ROW 0x0048: OTP_DATA_BOOT_FLAGS0\n"
        '        "Disable/Enable boot paths ... (RBIT-3)"\n'
        "\n"
        "    VALUE 0x000000\n"
        "\n"
        "    field ROLLBACK_REQUIRED (bit 11) = 0\n"
    )
    monkeypatch.setattr(
        fc.subprocess, "run",
        lambda *a, **k: _completed(stdout=output),
    )
    assert fc.read_otp_field("OTP_DATA_BOOT_FLAGS0") == "0x000000"


def test_read_otp_field_failure_modes(monkeypatch):
    monkeypatch.setattr(
        fc.subprocess, "run",
        lambda *a, **k: _completed(returncode=1, stderr="kaputt"),
    )
    assert "nicht lesbar" in fc.read_otp_field("X")

    def boom(*a, **k):
        raise FileNotFoundError("picotool")

    monkeypatch.setattr(fc.subprocess, "run", boom)
    assert fc.read_otp_field("X") == "picotool nicht gefunden"
