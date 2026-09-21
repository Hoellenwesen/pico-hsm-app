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

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import base_dir

DRILL_LOG = base_dir() / "recovery_drills.jsonl"
LEFTOVER_SHARES_FILENAME = "shares-DO-NOT-KEEP-TOGETHER.txt"

#: Ab wann ein Backup als alt / ein Drill als überfällig gilt (Tage).
AGE_WARN_DAYS = 90
DRILL_WARN_DAYS = 30


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
    # Hygiene (Phase 2) — wird in list_backups() berechnet:
    age_days: Optional[int] = None
    drill_age_days: Optional[int] = None
    drill_state: str = "nie"  # nie|fehlgeschlagen|alt|frisch
    problems: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)


def _now() -> datetime:
    """Aktuelle Zeit (eigene Funktion: Tests stellen sie per Monkeypatch)."""
    return datetime.now(timezone.utc)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_days(created_at: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """Alter des Backups in Tagen (None bei unparsbarem Datum)."""
    parsed = _parse_ts(created_at)
    if parsed is None:
        return None
    return max(0, ((now or _now()) - parsed).days)


def drill_state(
    last_drill_at: Optional[str],
    last_drill_result: Optional[str],
    now: Optional[datetime] = None,
) -> tuple[str, Optional[int]]:
    """Drill-Status + Alter in Tagen: nie|fehlgeschlagen|alt|frisch."""
    if not last_drill_at:
        return "nie", None
    days = age_days(last_drill_at, now)
    if (last_drill_result or "").upper() != "PASS":
        return "fehlgeschlagen", days
    if days is not None and days > DRILL_WARN_DAYS:
        return "alt", days
    return "frisch", days


def check_completeness(candidate: Path, manifest: dict) -> list[str]:
    """Vollständigkeit prüfen (rein lesend). Gibt Problem-Texte zurück."""
    problems: list[str] = []
    if not manifest:
        return ["manifest.json ungültig oder leer"]
    mode = manifest.get("mode", "shamir")
    threshold = manifest.get("threshold")
    total = manifest.get("total_shares")
    if mode == "recipients":
        recipients = manifest.get("recipients") or []
        if not recipients:
            problems.append("Empfänger-Liste fehlt")
    elif not isinstance(threshold, int) or not isinstance(total, int):
        problems.append("Schema (m-von-n) unvollständig")
    elif threshold < 1 or total < 1 or threshold > total:
        problems.append(f"Schema unplausibel ({threshold}-von-{total})")

    cipher_name = manifest.get("ciphertext_file")
    expected_sha = manifest.get("ciphertext_sha256")
    if not cipher_name:
        problems.append("Ciphertext-Angabe fehlt")
    else:
        cipher_path = candidate / cipher_name
        if not cipher_path.exists():
            problems.append("Ciphertext-Datei fehlt")
        elif expected_sha:
            actual = hashlib.sha256(cipher_path.read_bytes()).hexdigest()
            if actual != expected_sha:
                problems.append("Ciphertext-SHA weicht vom Manifest ab")

    if isinstance(total, int) and total > 0 and mode != "recipients":
        shares_dir = candidate / "individual-shares"
        found = (
            len(list(shares_dir.glob("share-*.txt")))
            if shares_dir.is_dir() else 0
        )
        if 0 < found < total:
            problems.append(f"nur {found}/{total} Share-Dateien vorhanden")

    if not manifest.get("pubkey"):
        problems.append("Pubkey-Angabe fehlt")
    if not manifest.get("plaintext_sha256"):
        problems.append("Klartext-SHA-Angabe fehlt")
    return problems


def hygiene_warnings(info: object) -> list[str]:
    """Alle Hygiene-Warnungen (deutsch). Toleriert SimpleNamespace (Tests)."""
    get = getattr
    warnings: list[str] = []
    for problem in get(info, "problems", []) or []:
        warnings.append(problem)
    if get(info, "leftover_shares_file_present", False):
        warnings.append(
            "shares-DO-NOT-KEEP-TOGETHER.txt liegt noch hier — "
            "Gesamt-Secret an einem Ort, nach Drill löschen!"
        )
    state = get(info, "drill_state", "nie")
    drill_days = get(info, "drill_age_days", None)
    if state == "nie":
        warnings.append("noch nie Drill — Drill empfohlen!")
    elif state == "fehlgeschlagen":
        warnings.append("letzter Drill FEHLGESCHLAGEN")
    elif state == "alt":
        warnings.append(f"letzter Drill {drill_days} Tage her (alt)")
    age = get(info, "age_days", None)
    if age is not None and age > AGE_WARN_DAYS:
        warnings.append(f"Backup {age} Tage alt")
    return warnings


def schema_text(info: object) -> str:
    """Anzeige-Text für die Schema-Spalte (Shamir m-von-n oder Empfänger)."""
    get = getattr
    if get(info, "threshold", None) is None:
        recipients = get(info, "recipients", None)
        count = len(recipients) if recipients else "?"
        return f"Empfänger ({count})"
    return f"{get(info, 'threshold')}-von-{get(info, 'total_shares')}"


def hygiene_label(info: object) -> str:
    """Kurzanzeige für Tabelle/CLI: OK oder WARN (n).

    Modul-Funktion (getattr-tolerant) statt Property — verträgt auch
    ältere Info-Objekte ohne Hygiene-Felder.
    """
    warnings = hygiene_warnings(info)
    if not warnings:
        return "OK"
    return f"WARN ({len(warnings)})"


def drill_text(info: object) -> str:
    """Anzeige-Text für die Drill-Spalte (mit Abstand in Tagen)."""
    get = getattr
    state = get(info, "drill_state", "nie")
    days = get(info, "drill_age_days", None)
    at = get(info, "last_drill_at", None)
    result = get(info, "last_drill_result", None)
    if state == "nie" or not at:
        return "noch nie — Drill empfohlen!"
    if days == 0:
        age = "heute"
    elif days is None:
        age = str(at)
    else:
        age = f"vor {days} Tagen"
    if state == "fehlgeschlagen":
        return f"{age} -> FEHLGESCHLAGEN"
    if state == "alt":
        return f"{age} -> {result} (alt)"
    return f"{age} -> {result}"


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
        last_at, last_result = (drill[0], drill[1]) if drill else (None, None)
        state, drill_days = drill_state(last_at, last_result)
        info = BackupInfo(
            path=candidate,
            created_at=manifest.get("created_at"),
            threshold=manifest.get("threshold"),
            total_shares=manifest.get("total_shares"),
            ciphertext_sha256=manifest.get("ciphertext_sha256"),
            leftover_shares_file_present=leftover,
            last_drill_at=last_at,
            last_drill_result=last_result,
            age_days=age_days(manifest.get("created_at")),
            drill_age_days=drill_days,
            drill_state=state,
            problems=check_completeness(candidate, manifest),
            recipients=list(manifest.get("recipients") or []),
        )
        infos.append(info)

    return infos
