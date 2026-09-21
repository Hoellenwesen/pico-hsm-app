"""
backup_core.py — age-Backup mit Empfänger-Modus, komplett in Python.

Eine Datei wird mit `age` für einen oder mehrere Empfänger-Pubkeys
verschlüsselt (1-aus-n: jeder Empfänger öffnet mit eigenem Key).
Shamir/ssss ist entfernt (siehe docs/20-roadmap.md) — als künftige
Verbesserung ist Python-Shamir geparkt, kein externes Binary.

Nur die Orchestrierung ist Python; Kryptografie bleibt in `age`
(bewusste Entscheidung: keine eigene Krypto-Reimplementierung).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import base_dir

DRILL_LOG = base_dir() / "recovery_drills.jsonl"


class BackupError(Exception):
    pass


#: age-Pubkey-Format (Bech32, HRP "age1", 62 Zeichen Nutzdaten).
_AGE_PUBKEY_RE = re.compile(r"^age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{62}$")


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _run(cmd: list[str], input_text: Optional[str] = None, timeout: int = 30):
    try:
        return subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"Programm nicht gefunden: {cmd[0]}") from exc


# --------------------------------------------------------------------------
# age encrypt/decrypt (Empfänger-Modus)
# --------------------------------------------------------------------------

def encrypt_file(input_path: Path, pubkeys: list[str],
                 output_path: Path) -> None:
    """Für einen oder mehrere Empfänger verschlüsseln (eine Datei,
    1-aus-n: jeder Empfänger öffnet mit eigenem Key)."""
    cleaned = [key.strip() for key in pubkeys if key.strip()]
    if not cleaned:
        raise BackupError("Mindestens ein Empfänger-Pubkey erforderlich (--recipient).")
    for key in cleaned:
        if not _AGE_PUBKEY_RE.match(key):
            raise BackupError(
                f"Kein gültiger age-Pubkey: {key!r} (erwartet Format "
                "'age1...', z.B. per `age-keygen -o identity.txt`).")
    cmd = ["age", "-o", str(output_path)]
    for pubkey in cleaned:
        cmd += ["-r", pubkey]
    cmd.append(str(input_path))
    result = _run(cmd)
    if result.returncode != 0:
        raise BackupError(f"age-Verschlüsselung fehlgeschlagen: {result.stderr.strip()}")


def decrypt_file(input_path: Path, identity_path: Path,
                 output_path: Path) -> None:
    result = _run([
        "age", "-d", "-i", str(identity_path),
        "-o", str(output_path), str(input_path),
    ])
    if result.returncode != 0:
        raise BackupError(f"age-Entschlüsselung fehlgeschlagen: {result.stderr.strip()}")


# --------------------------------------------------------------------------
# Backup-Workflow (backup split -> nur Empfänger-Modus)
# --------------------------------------------------------------------------

def split_backup(
    export_file: Path,
    out_dir: Path,
    recipients: list[str],
    identity_file: Optional[Path] = None,
) -> dict:
    """Datei für Empfänger verschlüsseln. `identity_file` ist nur aus
    Kompatibilitätsgründen vorhanden und wird ignoriert."""
    del identity_file  # Empfänger-Modus braucht keine Identity
    if not export_file.exists():
        raise BackupError(f"Export-Datei nicht gefunden: {export_file}")
    cleaned = [key.strip() for key in recipients if key.strip()]
    if not cleaned:
        raise BackupError("Mindestens ein Empfänger-Pubkey erforderlich (--recipient).")

    out_dir.mkdir(parents=True, exist_ok=True)
    plaintext_sha256 = _sha256_file(export_file)
    ciphertext_path = out_dir / (export_file.name + ".age")
    encrypt_file(export_file, cleaned, ciphertext_path)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "recipients",
        "recipients": cleaned,
        "pubkey": cleaned[0],
        "ciphertext_file": ciphertext_path.name,
        "ciphertext_sha256": _sha256_file(ciphertext_path),
        "plaintext_sha256": plaintext_sha256,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# --------------------------------------------------------------------------
# Restore-Workflow (backup restore)
# --------------------------------------------------------------------------

def restore_backup(
    backup_dir: Path,
    output_file: Path,
    identity_file: Path,
) -> dict:
    """Mit der Identity-Datei eines Empfängers entschlüsseln."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupError(f"Kein manifest.json in {backup_dir} gefunden.")
    manifest = json.loads(manifest_path.read_text())

    ciphertext_path = backup_dir / manifest["ciphertext_file"]
    if not ciphertext_path.exists():
        raise BackupError(f"Ciphertext-Datei fehlt: {ciphertext_path}")

    # Ciphertext-Prüfsumme VOR dem Entschlüsseln prüfen — Manipulation/
    # Beschädigung frühzeitig erkennen.
    actual_ct_sha = _sha256_file(ciphertext_path)
    if actual_ct_sha != manifest["ciphertext_sha256"]:
        raise BackupError(
            "Ciphertext-Prüfsumme stimmt nicht mit dem Manifest überein — "
            "Datei möglicherweise beschädigt oder manipuliert. Abbruch "
            "vor dem Entschlüsseln."
        )

    if identity_file is None or not identity_file.exists():
        raise BackupError(
            "Identity-Datei eines Empfängers erforderlich "
            "(--identity-file).")
    decrypt_file(ciphertext_path, identity_file, output_file)

    actual_pt_sha = _sha256_file(output_file)
    if actual_pt_sha != manifest["plaintext_sha256"]:
        output_file.unlink(missing_ok=True)
        raise BackupError(
            "Plaintext-Prüfsumme nach dem Entschlüsseln stimmt NICHT mit "
            "dem Manifest überein — falsche Identity oder stille "
            "Fehlentschlüsselung. Ausgabedatei wurde gelöscht."
        )

    return manifest


# --------------------------------------------------------------------------
# Recovery-Drill
# --------------------------------------------------------------------------

def _log_drill(backup_dir: Optional[Path], result: str, detail: str = "") -> None:
    DRILL_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backup_dir": str(backup_dir) if backup_dir else "self-test",
        "result": result,
        "detail": detail,
    }
    with DRILL_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def real_drill(backup_dir: Path,
               identity_file: Path) -> bool:
    """Echter Drill: restore in eine temporäre Datei, Ergebnis protokollieren."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "drill-output.bin"
            restore_backup(backup_dir, out_path, identity_file)
        _log_drill(backup_dir, "PASS")
        return True
    except BackupError as exc:
        _log_drill(backup_dir, "FAIL", str(exc))
        raise
