"""
backup_index.py — read-only Hilfsfunktionen für `backup list`.

Durchsucht ein Elternverzeichnis nach Backup-Ordnern (erkannt an
vorhandener manifest.json, wie von backup-split.sh erzeugt) und reichert
sie mit dem Ergebnis des letzten Recovery-Drills an, falls vorhanden.
Rein lesend — keine Interaktion mit HSM oder Secrets.

HINWEIS: Die erwarteten Manifest-Felder (created_at, threshold,
total_shares, ciphertext_sha256) sind aus der Beschreibung von
backup-split.sh abgeleitet, aber noch nicht gegen die tatsächliche
Skript-Ausgabe verifiziert. Vor Produktiveinsatz gegenprüfen und
Feldnamen ggf. anpassen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DRILL_LOG = Path.home() / ".pico_hsm" / "recovery_drills.jsonl"
LEFTOVER_SHARES_FILENAME = "shares-DO-NOT-KEEP-TOGETHER.txt"


@dataclass
class BackupInfo:
    path: Path
    created_at: Optional[str]
    threshold: Optional[int]
    total_shares: Optional[int]
    ciphertext_sha256: Optional[str]
    leftover_shares_file_present: bool
    last_drill_at: Optional[str] = None
    last_drill_result: Optional[str] = None


def _read_manifest(manifest_path: Path) -> dict:
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _load_drill_results() -> dict[str, tuple[str, str]]:
    """Mappt Backup-Verzeichnis (als String) -> (timestamp, result) des
    jeweils letzten protokollierten Drills."""
    results: dict[str, tuple[str, str]] = {}
    if not DRILL_LOG.exists():
        return results
    with DRILL_LOG.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            backup_dir = entry.get("backup_dir")
            ts = entry.get("timestamp")
            result = entry.get("result")
            if backup_dir and ts:
                # Späterer Eintrag überschreibt früheren -> letzter Stand
                results[backup_dir] = (ts, result or "?")
    return results


def list_backups(parent_dir: Path) -> list[BackupInfo]:
    drill_results = _load_drill_results()
    infos: list[BackupInfo] = []

    if not parent_dir.exists():
        return infos

    for candidate in sorted(parent_dir.iterdir()):
        if not candidate.is_dir():
            continue
        manifest_path = candidate / "manifest.json"
        if not manifest_path.exists():
            continue

        manifest = _read_manifest(manifest_path)
        leftover = (candidate / LEFTOVER_SHARES_FILENAME).exists()

        drill = drill_results.get(str(candidate))
        info = BackupInfo(
            path=candidate,
            created_at=manifest.get("created_at"),
            threshold=manifest.get("threshold"),
            total_shares=manifest.get("total_shares"),
            ciphertext_sha256=manifest.get("ciphertext_sha256"),
            leftover_shares_file_present=leftover,
            last_drill_at=drill[0] if drill else None,
            last_drill_result=drill[1] if drill else None,
        )
        infos.append(info)

    return infos
