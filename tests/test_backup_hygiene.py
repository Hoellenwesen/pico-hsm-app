"""
test_backup_hygiene.py — Backup-Hygiene ohne Hardware und ohne Board.

Testet rein lesende Berechnung in backup_index: Alter, Drill-Status,
Vollständigkeit, Labels. Zeit via _now-Mock (fix: 2026-09-20 UTC).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from pico_hsm_tools import backup_index as bi

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _patch_now(monkeypatch):
    monkeypatch.setattr(bi, "_now", lambda: NOW)


def _manifest(**over):
    data = {
        "created_at": "2026-09-01T10:00:00+00:00",
        "threshold": 2,
        "total_shares": 3,
        "pubkey": "age1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        "ciphertext_file": "secret.bin.age",
        "ciphertext_sha256": hashlib.sha256(b"cipher").hexdigest(),
        "plaintext_sha256": "x" * 64,
    }
    data.update(over)
    return data


def _backup_dir(tmp_path, manifest=None, cipher=b"cipher", shares=3):
    target = tmp_path / "b1"
    target.mkdir()
    (target / "manifest.json").write_text(
        json.dumps(manifest if manifest is not None else _manifest()),
    )
    (target / "secret.bin.age").write_bytes(cipher)
    shares_dir = target / "individual-shares"
    shares_dir.mkdir()
    for i in range(1, shares + 1):
        (shares_dir / f"share-{i}.txt").write_text(f"share{i}\n")
    return target


# --- Alter / Drill-Status ----------------------------------------------------------

def test_age_days(monkeypatch):
    _patch_now(monkeypatch)
    assert bi.age_days("2026-09-01T10:00:00+00:00") == 18
    assert bi.age_days("2026-01-01") is not None
    assert bi.age_days(None) is None
    assert bi.age_days("kein-datum") is None


def test_drill_state_matrix(monkeypatch):
    _patch_now(monkeypatch)
    assert bi.drill_state(None, None) == ("nie", None)
    assert bi.drill_state("2026-09-19T00:00:00+00:00", "FAIL")[0] == "fehlgeschlagen"
    assert bi.drill_state("2026-08-01T00:00:00+00:00", "PASS") == ("alt", 50)
    assert bi.drill_state("2026-09-19T00:00:00+00:00", "PASS") == ("frisch", 1)


# --- Vollständigkeit -----------------------------------------------------------------

def test_completeness_ok(tmp_path):
    _backup_dir(tmp_path)
    assert bi.check_completeness(tmp_path / "b1", _manifest()) == []


def test_completeness_finds_problems(tmp_path):
    target = _backup_dir(tmp_path)
    (target / "secret.bin.age").write_bytes(b"manipuliert")
    problems = bi.check_completeness(target, _manifest(threshold=5, total_shares=3))
    assert any("unplausibel" in p for p in problems)
    assert any("SHA" in p for p in problems)


def test_completeness_missing_ciphertext(tmp_path):
    target = _backup_dir(tmp_path)
    (target / "secret.bin.age").unlink()
    problems = bi.check_completeness(target, _manifest())
    assert any("Ciphertext-Datei fehlt" in p for p in problems)


def test_completeness_invalid_manifest(tmp_path):
    target = tmp_path / "b1"
    target.mkdir()
    assert bi.check_completeness(target, {}) == ["manifest.json ungültig oder leer"]


# --- list_backups + Labels ----------------------------------------------------------------

def test_list_backups_computes_hygiene(monkeypatch, tmp_path):
    _patch_now(monkeypatch)
    monkeypatch.setattr(bi, "DRILL_LOG", tmp_path / "drills.jsonl")
    target = _backup_dir(tmp_path)
    infos = bi.list_backups(tmp_path)
    assert len(infos) == 1
    info = infos[0]
    assert info.age_days == 18
    assert info.drill_state == "nie"
    assert info.problems == []
    assert bi.hygiene_label(info) == "WARN (1)"  # nie Drill


def test_list_backups_with_pass_drill(monkeypatch, tmp_path):
    _patch_now(monkeypatch)
    log = tmp_path / "drills.jsonl"
    monkeypatch.setattr(bi, "DRILL_LOG", log)
    target = _backup_dir(tmp_path)
    log.write_text(
        json.dumps({
            "timestamp": "2026-09-14T10:00:00+00:00",
            "backup_dir": str(target), "result": "PASS",
        }) + "\n",
        encoding="utf-8",
    )
    (infos,) = bi.list_backups(tmp_path)
    assert (infos.drill_state, infos.drill_age_days) == ("frisch", 5)
    assert bi.hygiene_label(infos) == "OK"
    assert "vor 5 Tagen" in bi.drill_text(infos)


def test_labels_tolerate_legacy_objects():
    legacy = SimpleNamespace(
        problems=[], leftover_shares_file_present=False,
        drill_state="nie", drill_age_days=None, age_days=None,
        last_drill_at=None, last_drill_result=None,
    )
    assert bi.hygiene_label(legacy) == "WARN (1)"
    assert "noch nie" in bi.drill_text(legacy)
