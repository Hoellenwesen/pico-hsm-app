"""
test_backup_recipients.py — Empfänger-Modus (1-aus-n, ohne Shamir/ssss).

`age` wird auf _run-Ebene simuliert (kein Binary nötig):
verschlüsseln schreibt Marker, entschlüsseln stellt Plaintext her.
Deckt ab: Split, Restore, Drill, Hygiene/Completeness, Schema-Text.
CLI-Wege stehen in tests/test_hsm_backup.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index


def _fake_run_factory(plaintext: bytes = b"geheimnis"):
    """Simuliert age (encrypt schreibt Marker, decrypt stellt Plaintext her)."""

    class _Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, input_text=None, timeout=30):
        assert cmd[0] == "age", cmd
        out = Path(cmd[cmd.index("-o") + 1])
        if "-d" in cmd:
            out.write_bytes(plaintext)
        else:
            out.write_bytes(b"AGE-ENCRYPTED:" + b"|".join(
                c.encode() for c in cmd if c.startswith("age1")))
        return _Result()

    return fake_run


def _secret(tmp_path: Path) -> Path:
    src = tmp_path / "secret.bin"
    src.write_bytes(b"geheimnis")
    return src


def _identity(tmp_path: Path) -> Path:
    ident = tmp_path / "id.txt"
    ident.write_text("AGE-SECRET-KEY-1xyz\n")
    return ident


# --- Split ---------------------------------------------------------------------------

def test_split_recipients(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    out = tmp_path / "out"
    manifest = bc.split_backup(
        _secret(tmp_path), out, ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr", "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"])
    assert manifest["mode"] == "recipients"
    assert manifest["recipients"] == ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr", "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"]
    assert (out / "secret.bin.age").exists()
    assert not (out / "individual-shares").exists()
    assert json.loads((out / "manifest.json").read_text())["mode"] == "recipients"


def test_split_empty_or_missing_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    with pytest.raises(bc.BackupError, match="Empfänger"):
        bc.split_backup(_secret(tmp_path), tmp_path / "out", [])
    with pytest.raises(bc.BackupError, match="nicht gefunden"):
        bc.split_backup(
            tmp_path / "fehlt.bin", tmp_path / "out", ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"])


# --- Restore ---------------------------------------------------------------------------

def test_restore_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    out = tmp_path / "out"
    bc.split_backup(_secret(tmp_path), out, ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"])
    target = tmp_path / "restored.bin"
    manifest = bc.restore_backup(out, target, _identity(tmp_path))
    assert manifest["mode"] == "recipients"
    assert target.read_bytes() == b"geheimnis"


def test_restore_requires_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    out = tmp_path / "out"
    bc.split_backup(_secret(tmp_path), out, ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"])
    with pytest.raises(bc.BackupError, match="Identity-Datei"):
        bc.restore_backup(out, tmp_path / "x.bin", None)


def test_restore_detects_tampered_ciphertext(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    out = tmp_path / "out"
    bc.split_backup(_secret(tmp_path), out, ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"])
    (out / "secret.bin.age").write_bytes(b"manipuliert")
    with pytest.raises(bc.BackupError, match="Ciphertext"):
        bc.restore_backup(out, tmp_path / "x.bin", _identity(tmp_path))


# --- Drill -------------------------------------------------------------------------------

def test_drill_recipients(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "_run", _fake_run_factory())
    monkeypatch.setattr(bc, "DRILL_LOG", tmp_path / "drills.jsonl")
    out = tmp_path / "out"
    bc.split_backup(_secret(tmp_path), out, ["age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"])
    assert bc.real_drill(out, _identity(tmp_path)) is True


# --- Hygiene -------------------------------------------------------------------------------

def test_completeness_recipients_ok(tmp_path):
    target = tmp_path / "b"
    target.mkdir()
    (target / "f.age").write_bytes(b"x")
    manifest = {"mode": "recipients", "recipients": ["age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"],
                "ciphertext_file": "f.age",
                "ciphertext_sha256": hashlib.sha256(b"x").hexdigest(),
                "pubkey": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", "plaintext_sha256": "y"}
    assert backup_index.check_completeness(target, manifest) == []


def test_completeness_recipients_missing_list(tmp_path):
    target = tmp_path / "b"
    target.mkdir()
    assert "Empfänger-Liste fehlt" in backup_index.check_completeness(
        target, {"mode": "recipients"})


def test_schema_text_recipients():
    assert backup_index.schema_text(
        SimpleNamespace(threshold=None, total_shares=None,
                        recipients=["a", "b"])) == "Empfänger (2)"
