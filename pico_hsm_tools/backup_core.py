"""
backup_core.py — Shamir/age-Backup, komplett in Python (kein Bash mehr).

Ersetzt die verlorenen backup-split.sh/backup-restore.sh/recovery-drill-
check.py durch reine Python-Orchestrierung. age und ssss bleiben externe
Programme (bewusste Entscheidung: nur die Orchestrierung wird Python,
keine eigene Krypto-Reimplementierung).

Empirisch verifiziert (siehe Session-Notizen) gegen echte age 1.1.1 und
ssss 0.5 Installationen:
  - age-keygen -o <file> erzeugt Identity-Datei (Kommentarzeilen mit '#'
    + eine Zeile "AGE-SECRET-KEY-1...")
  - age-keygen -y <file> leitet den Public Key aus einer Identity ab
  - ssss-split -t M -n N -x -q liest das Secret als Hex-String von stdin
    (max. 64 Byte / 128 Hexzeichen) und gibt Zeilen "share_id-hexdata" aus
  - ssss-combine -t M -x -q schreibt das rekonstruierte Secret nach
    STDERR, nicht STDOUT (bekannte Eigenheit, hier korrekt behandelt)

Der age-Identity-String (74 Zeichen Bech32) ist zu lang für ssss (Limit
64 Byte). Daher wird er über age_bech32 auf die rohen 32 Byte reduziert,
die exakt in ssss' 64-Byte-Limit passen (als 64 Hexzeichen).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import age_bech32

DRILL_LOG = Path.home() / ".pico_hsm" / "recovery_drills.jsonl"
LEFTOVER_SHARES_FILENAME = "shares-DO-NOT-KEEP-TOGETHER.txt"


class BackupError(Exception):
    pass


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
# age-Identity: erzeugen oder vorhandene verwenden
# --------------------------------------------------------------------------

def generate_identity(tmp_path: Path) -> tuple[str, str]:
    """Erzeugt ein neues age-Keypair. Gibt (identity_str, pubkey) zurück.
    tmp_path wird als Zwischendatei genutzt und danach gelöscht — die
    Identity existiert nach dieser Funktion nur noch als Rückgabewert
    und (gesplittet) in den Shares."""
    result = _run(["age-keygen", "-o", str(tmp_path)])
    if result.returncode != 0:
        raise BackupError(f"age-keygen fehlgeschlagen: {result.stderr.strip()}")
    identity_str = _read_identity_file(tmp_path)
    pubkey = derive_pubkey(tmp_path)
    tmp_path.unlink(missing_ok=True)
    return identity_str, pubkey


def _read_identity_file(path: Path) -> str:
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise BackupError(f"Keine Identity-Zeile in {path} gefunden.")


def derive_pubkey(identity_path: Path) -> str:
    result = _run(["age-keygen", "-y", str(identity_path)])
    if result.returncode != 0:
        raise BackupError(f"Konnte Public Key nicht ableiten: {result.stderr.strip()}")
    return result.stdout.strip()


def load_existing_identity(identity_path: Path) -> tuple[str, str]:
    """Für --identity-file: vorhandene Identity-Datei einlesen, Pubkey
    ableiten. Rührt die Datei selbst nicht an."""
    identity_str = _read_identity_file(identity_path)
    pubkey = derive_pubkey(identity_path)
    return identity_str, pubkey


# --------------------------------------------------------------------------
# age encrypt/decrypt
# --------------------------------------------------------------------------

def encrypt_file(input_path: Path, pubkey: str, output_path: Path) -> None:
    result = _run(["age", "-r", pubkey, "-o", str(output_path), str(input_path)])
    if result.returncode != 0:
        raise BackupError(f"age-Verschlüsselung fehlgeschlagen: {result.stderr.strip()}")


def decrypt_file(input_path: Path, identity_path: Path, output_path: Path) -> None:
    result = _run([
        "age", "-d", "-i", str(identity_path),
        "-o", str(output_path), str(input_path),
    ])
    if result.returncode != 0:
        raise BackupError(f"age-Entschlüsselung fehlgeschlagen: {result.stderr.strip()}")


# --------------------------------------------------------------------------
# Shamir-Split/Combine über ssss (nur der rohe 32-Byte age-Key)
# --------------------------------------------------------------------------

def split_identity(identity_str: str, threshold: int, total: int) -> list[str]:
    raw = age_bech32.age_identity_to_raw(identity_str)
    hex_secret = raw.hex()
    result = _run(
        ["ssss-split", "-t", str(threshold), "-n", str(total), "-x", "-q"],
        input_text=hex_secret,
    )
    if result.returncode != 0:
        raise BackupError(f"ssss-split fehlgeschlagen: {result.stderr.strip()}")
    shares = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    if len(shares) != total:
        raise BackupError(
            f"Erwartet {total} Shares von ssss-split, bekommen {len(shares)}."
        )
    return shares


def combine_shares(shares: list[str], threshold: int) -> str:
    """Gibt den rekonstruierten age-Identity-String zurück."""
    input_text = "\n".join(shares) + "\n"
    result = _run(
        ["ssss-combine", "-t", str(threshold), "-x", "-q"],
        input_text=input_text,
    )
    if result.returncode != 0:
        raise BackupError(f"ssss-combine fehlgeschlagen: {result.stderr.strip()}")
    # Bekannte Eigenheit: Ergebnis steht auf stderr, nicht stdout.
    hex_secret = result.stderr.strip()
    if len(hex_secret) != 64:
        raise BackupError(
            f"Unerwartete ssss-combine-Ausgabe (Länge {len(hex_secret)}, "
            "erwartet 64 Hexzeichen) — falsche/zu wenige Shares?"
        )
    try:
        raw = bytes.fromhex(hex_secret)
    except ValueError as exc:
        raise BackupError("ssss-combine-Ausgabe ist kein gültiger Hex-String.") from exc
    return age_bech32.raw_to_age_identity(raw)


# --------------------------------------------------------------------------
# Split-Workflow (backup split)
# --------------------------------------------------------------------------

def split_backup(
    export_file: Path,
    out_dir: Path,
    threshold: int,
    total: int,
    identity_file: Optional[Path] = None,
) -> dict:
    """Kompletter Split-Workflow. identity_file=None -> neues Keypair wird
    erzeugt; sonst wird die vorhandene Identity-Datei genutzt (Datei wird
    dabei nicht verändert oder gelöscht — das bleibt in der Verantwortung
    des Aufrufers)."""
    if threshold > total:
        raise BackupError("Schwelle (m) darf nicht größer als Gesamtzahl (n) sein.")
    if not export_file.exists():
        raise BackupError(f"Export-Datei nicht gefunden: {export_file}")

    out_dir.mkdir(parents=True, exist_ok=True)
    shares_dir = out_dir / "individual-shares"
    shares_dir.mkdir(exist_ok=True)

    if identity_file is not None:
        identity_str, pubkey = load_existing_identity(identity_file)
        source = "existing"
    else:
        tmp_identity = out_dir / ".tmp-identity"
        identity_str, pubkey = generate_identity(tmp_identity)
        source = "generated"

    plaintext_sha256 = _sha256_file(export_file)
    ciphertext_path = out_dir / (export_file.name + ".age")
    encrypt_file(export_file, pubkey, ciphertext_path)
    ciphertext_sha256 = _sha256_file(ciphertext_path)

    shares = split_identity(identity_str, threshold, total)
    for i, share in enumerate(shares, start=1):
        (shares_dir / f"share-{i}.txt").write_text(share + "\n")

    # Nur für den lokalen Drill — Hinweis zum Löschen im Rückgabewert.
    (out_dir / LEFTOVER_SHARES_FILENAME).write_text("\n".join(shares) + "\n")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "threshold": threshold,
        "total_shares": total,
        "identity_source": source,
        "pubkey": pubkey,
        "ciphertext_file": ciphertext_path.name,
        "ciphertext_sha256": ciphertext_sha256,
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
    shares: list[str],
) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupError(f"Kein manifest.json in {backup_dir} gefunden.")
    manifest = json.loads(manifest_path.read_text())

    ciphertext_path = backup_dir / manifest["ciphertext_file"]
    if not ciphertext_path.exists():
        raise BackupError(f"Ciphertext-Datei fehlt: {ciphertext_path}")

    # Ciphertext-Prüfsumme VOR dem Entschlüsseln prüfen — Manipulation/
    # Beschädigung frühzeitig erkennen, bevor Shares überhaupt genutzt werden.
    actual_ct_sha = _sha256_file(ciphertext_path)
    if actual_ct_sha != manifest["ciphertext_sha256"]:
        raise BackupError(
            "Ciphertext-Prüfsumme stimmt nicht mit dem Manifest überein — "
            "Datei möglicherweise beschädigt oder manipuliert. Abbruch "
            "vor dem Entschlüsseln."
        )

    if len(shares) < manifest["threshold"]:
        raise BackupError(
            f"Zu wenige Shares ({len(shares)}), benötigt: {manifest['threshold']}."
        )

    identity_str = combine_shares(shares, manifest["threshold"])

    tmp_identity = backup_dir / ".tmp-restore-identity"
    try:
        tmp_identity.write_text(identity_str + "\n")
        os.chmod(tmp_identity, 0o600)
        decrypt_file(ciphertext_path, tmp_identity, output_file)
    finally:
        tmp_identity.unlink(missing_ok=True)

    actual_pt_sha = _sha256_file(output_file)
    if actual_pt_sha != manifest["plaintext_sha256"]:
        output_file.unlink(missing_ok=True)
        raise BackupError(
            "Plaintext-Prüfsumme nach dem Entschlüsseln stimmt NICHT mit "
            "dem Manifest überein — falsche/zu wenige Shares oder stille "
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


def self_test(threshold: int = 3, total: int = 5) -> bool:
    """Vollautomatischer Selbsttest: synthetisches Secret, kein echtes
    Backup nötig. Prüft nur, ob age/ssss auf diesem System funktionieren."""
    try:
        raw = secrets.token_bytes(32)
        identity_str = age_bech32.raw_to_age_identity(raw)
        shares = split_identity(identity_str, threshold, total)
        chosen = shares[:threshold]
        recovered = combine_shares(chosen, threshold)
        ok = recovered == identity_str
        _log_drill(None, "PASS" if ok else "FAIL",
                   "identity mismatch" if not ok else "")
        return ok
    except Exception as exc:  # noqa: BLE001
        _log_drill(None, "FAIL", str(exc))
        raise


def real_drill(backup_dir: Path, shares: list[str]) -> bool:
    """Echter Drill: restore in eine temporäre Datei, Ergebnis protokollieren."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "drill-output.bin"
            restore_backup(backup_dir, out_path, shares)
        _log_drill(backup_dir, "PASS")
        return True
    except BackupError as exc:
        _log_drill(backup_dir, "FAIL", str(exc))
        raise
