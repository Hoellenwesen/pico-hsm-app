"""hsm_backup.py — echtes HSM-Backup (Token-Inhalt, kein Datei-Backup).

Anders als `backup_core.split_backup` (sichert eine beliebige DATEI,
fasst das Token nie an) sichert dieses Modul den Token-Inhalt selbst,
damit bei Hardware-Ausfall auf neuer Hardware wiederhergestellt werden
kann:

- Auswahl: `keys` (DKEK-Wrap je Key), `data` (Datenobjekte lesen),
  `options` (Dynamic Options) — einzeln oder Vollbackup (alle an).
- Gate: ohne DKEK mit Shares sind Keys nicht exportierbar (by design)
  -> Abbruch mit Anleitung statt Teilsicherung.
- Versiegelung: Bundle (Manifest + Wraps + Daten) als ZIP, danach
  `split_backup` per Empfänger (age, 1-aus-n) + Drill-Protokoll.
- Restore: auf initialisiertem Token mit demselben DKEK (Unwrap,
  Daten schreiben, Optionen setzen), danach Verifikation gegen Manifest.

Grundsätzlich NICHT migrierbar (ehrlich im UI-Text): PINs/SO-PIN (nie
lesbar, neu setzen), DKEK selbst (nur Share-Zeremonie), OTP/Secure-Boot
(chipgebunden, nur `setup show` read-only).
"""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import dkek_core as dc

PARTS = ("keys", "data", "options")
MANIFEST_NAME = "hsm-manifest.json"
SUBPROCESS_TIMEOUT_S = 30


class HsmBackupError(Exception):
    """HSM-Backup-Fehler mit Klartext (enthält NIE PIN-Werte)."""


@dataclass
class HsmManifest:
    created_at: str
    source_label: str
    source_serial: str
    parts: list[str] = field(default_factory=list)
    keys: list[dict] = field(default_factory=list)  # label/id/reference/file
    data_objects: list[dict] = field(default_factory=list)  # label/size/file
    options: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at,
            "source_label": self.source_label,
            "source_serial": self.source_serial,
            "parts": self.parts,
            "keys": self.keys,
            "data_objects": self.data_objects,
            "options": self.options,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(raw: dict) -> "HsmManifest":
        return HsmManifest(
            created_at=str(raw.get("created_at", "?")),
            source_label=str(raw.get("source_label", "?")),
            source_serial=str(raw.get("source_serial", "?")),
            parts=list(raw.get("parts", [])),
            keys=list(raw.get("keys", [])),
            data_objects=list(raw.get("data_objects", [])),
            options=dict(raw.get("options", {})),
            notes=list(raw.get("notes", [])),
        )


# --- DKEK-Gate ---------------------------------------------------------------

#: Textmarker für "kein DKEK eingerichtet" im (ungeparsten) Status-Text.
_NO_DKEK_MARKERS = ("shares 0", "shares: 0", "no dkek", "dkek disabled",
                    "dkek not initialized")


def check_dkek_ready(status_text: Optional[str] = None) -> None:
    """Abbruch mit Anleitung, wenn sicher kein DKEK eingerichtet ist.

    Der Status-Text ist bewusst ungeparst — gesucht wird nur nach
    eindeutigen Negativ-Markern. Unbekannt = weiter (ein fehlender DKEK
    scheitert später hart am ersten Wrap mit Anleitung).
    """
    if status_text is None:
        try:
            status_text = dc.dkek_status()
        except Exception as exc:  # noqa: BLE001 — Aufrufer mappen
            raise HsmBackupError(f"DKEK-Status nicht lesbar ({exc}).")
    lowered = status_text.lower()
    if any(marker in lowered for marker in _NO_DKEK_MARKERS):
        raise HsmBackupError(
            "Kein DKEK eingerichtet — Keys sind nicht exportierbar "
            "(by design). Erst DKEK mit Shares einrichten (`init token "
            "--dkek-shares N` + `dkek create/import-share`, siehe DKEK-Tab), "
            "dann Backup wiederholen."
        )


# --- Key-Reference-Auflösung ---------------------------------------------------

#: Tolerante Muster für `pkcs15-tool -D`-Ausgabe (Format nur via Doku
#: belegt — mehrere Varianten, Fallback: manueller Override).
_REFERENCE_PATTERNS = (
    re.compile(r"Reference\s*[:=]\s*(\d+)", re.IGNORECASE),
    re.compile(r"Key ref\s*:\s*(\d+)", re.IGNORECASE),
    re.compile(r"key-reference\s+(\d+)", re.IGNORECASE),
    re.compile(r"\(ref(?:erence)?\s*(\d+)\)", re.IGNORECASE),
)


def _run_pkcs15_list() -> str:
    """`pkcs15-tool -D`-Ausgabe holen (nur lesen)."""
    try:
        result = subprocess.run(
            ["pkcs15-tool", "-D"], capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise HsmBackupError(
            "pkcs15-tool nicht gefunden (Teil von OpenSC).") from exc
    except subprocess.TimeoutExpired as exc:
        raise HsmBackupError(
            "pkcs15-tool antwortet nicht (Timeout).") from exc
    if result.returncode != 0:
        raise HsmBackupError(
            result.stderr.strip() or "pkcs15-tool -D fehlgeschlagen.")
    return result.stdout


def resolve_key_reference(label: str, listing: Optional[str] = None,
                          overrides: Optional[dict[str, int]] = None) -> int:
    """Numerische Key-Reference zu einem Label finden.

    1. Manueller Override (`overrides`), 2. tolerantes Parsen von
    `pkcs15-tool -D` (Block mit dem Label suchen, dann Muster), sonst
    HsmBackupError mit Anleitung.
    """
    if overrides and label in overrides:
        return int(overrides[label])
    if listing is None:
        listing = _run_pkcs15_list()
    blocks = re.split(r"\n\s*\n", listing)
    candidates = [b for b in blocks if label in b] or [listing]
    for block in candidates:
        for pattern in _REFERENCE_PATTERNS:
            match = pattern.search(block)
            if match:
                return int(match.group(1))
    raise HsmBackupError(
        f"Keine Key-Reference für Label '{label}' gefunden — bitte manuell "
        "angeben (--key-ref LABEL:REF) oder mit `pkcs15-tool -D` prüfen.")


# --- Sichern -------------------------------------------------------------------

def collect_inventory(pin: Optional[str] = None,
                      serial: Optional[str] = None,
                      lib_path: Optional[str] = None) -> dict:
    """Bestandsaufnahme ohne Secrets: Token-Identität, Objekte, Optionen."""
    from pico_hsm_tools import apdu_core as ac
    from pico_hsm_tools import objects_core as oc
    from pico_hsm_tools.pkcs11_session import (
        format_token_serial, get_token, read_only_session,
    )

    token = get_token(lib_path, serial=serial)
    inventory: dict = {
        "label": token.label,
        "model": token.model,
        "serial": format_token_serial(token.serial),
        "objects": [],
        "options": {},
    }
    with read_only_session(user_pin=pin, lib_path=lib_path,
                           serial=serial) as session:
        for obj in oc.list_objects(session):
            inventory["objects"].append({
                "label": obj.label,
                "id": obj.id.hex() if obj.id else None,
                "class": obj.object_class,
                "type": obj.key_type,
            })
    try:
        conn = ac.open_connection()
        try:
            options = ac.get_dynamic_options(conn)
            inventory["options"] = {
                "press_to_confirm": options.press_to_confirm,
                "key_usage_counter": options.key_usage_counter,
            }
        finally:
            conn.disconnect()
    except Exception:  # noqa: BLE001 — Optionen sind optional im Inventar
        pass
    return inventory


def export_parts(inventory: dict, parts: list[str], staging: Path, pin: str,
                 serial: Optional[str] = None,
                 lib_path: Optional[str] = None,
                 key_ref_overrides: Optional[dict[str, int]] = None) -> HsmManifest:
    """Gewählte Teile exportieren (Keys wrappen, Daten lesen, Optionen
    übernehmen). Schreibt Bundle-Dateien nach `staging`, gibt Manifest."""
    from pico_hsm_tools import objects_core as oc
    from pico_hsm_tools.pkcs11_session import read_only_session

    for part in parts:
        if part not in PARTS:
            raise HsmBackupError(f"Unbekannter Backup-Teil: {part!r}.")
    manifest = HsmManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        source_label=str(inventory.get("label", "?")),
        source_serial=str(inventory.get("serial", "?")),
        parts=list(parts),
        notes=[
            "PINs/SO-PIN, DKEK und OTP/Secure-Boot migrieren nie "
            "(siehe Doku).",
        ],
    )
    staging.mkdir(parents=True, exist_ok=True)

    if "options" in parts:
        manifest.options = dict(inventory.get("options", {}))

    if "keys" in parts or "data" in parts:
        with read_only_session(user_pin=pin, lib_path=lib_path,
                               serial=serial) as session:
            objects = inventory.get("objects", [])
            if "keys" in parts:
                keys_dir = staging / "keys"
                keys_dir.mkdir(exist_ok=True)
                for obj in objects:
                    if obj.get("class") == "DATA":
                        continue
                    label = str(obj.get("label", "?"))
                    if obj.get("class") != "PRIVATE_KEY":
                        # Öffentliche Hälften wandern beim Unwrap
                        # automatisch mit — kein Wrap nötig, nur Vermerk.
                        manifest.keys.append({
                            "label": label, "id": obj.get("id"),
                            "class": obj.get("class"),
                            "reference": None, "file": None,
                            "wrapped": False,
                        })
                        continue
                    reference = resolve_key_reference(
                        label, overrides=key_ref_overrides)
                    out_file = keys_dir / f"{label}.wrap"
                    try:
                        dc.wrap_key(str(out_file), reference, pin)
                    except Exception as exc:  # noqa: BLE001 — je Key melden
                        raise HsmBackupError(
                            f"Wrap für Key '{label}' fehlgeschlagen "
                            f"({exc}).") from exc
                    manifest.keys.append({
                        "label": label, "id": obj.get("id"),
                        "class": obj.get("class"),
                        "reference": reference,
                        "file": f"keys/{out_file.name}",
                        "wrapped": True,
                    })
            if "data" in parts:
                data_dir = staging / "data"
                data_dir.mkdir(exist_ok=True)
                for obj in objects:
                    if obj.get("class") != "DATA":
                        continue
                    label = str(obj.get("label", "?"))
                    try:
                        value = oc.read_data_object(session, label)
                    except Exception as exc:  # noqa: BLE001 — je Objekt melden
                        raise HsmBackupError(
                            f"Lesen von Datenobjekt '{label}' fehlgeschlagen "
                            f"({exc}).") from exc
                    out_file = data_dir / f"{label}.bin"
                    out_file.write_bytes(value)
                    manifest.data_objects.append({
                        "label": label, "size": len(value),
                        "file": f"data/{out_file.name}",
                    })
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return manifest


def seal_bundle(staging: Path, out_dir: Path,
                recipients: list[str]) -> dict:
    """Bundle zippen + per Empfänger versiegeln (1-aus-n, kein Shamir).

    Räumt `.hsm-staging` nach Erfolg weg (Klartext-Daten!); bei Fehler
    bleibt es zur Diagnose liegen.
    """
    import shutil
    import zipfile

    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_zip = staging / "hsm-bundle.zip"
    with zipfile.ZipFile(bundle_zip, "w",
                         compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path != bundle_zip:
                archive.write(path, path.relative_to(staging))
    try:
        sealed = bc.split_backup(bundle_zip, out_dir, recipients)
    except bc.BackupError as exc:
        raise HsmBackupError(str(exc)) from exc
    shutil.rmtree(staging, ignore_errors=True)
    return sealed


# --- Wiederherstellen ------------------------------------------------------------

def _imported_despite_error(label: str, pin: str,
                            lib_path: Optional[str], serial: Optional[str],
                            message: str) -> bool:
    """Quirk-Ausgleich (hw-logs/16, am Board bestätigt): sc-hsm-tool
    importiert per --force erfolgreich, meldet aber Exit-Code != 0
    (Zertifikat-Anteil). Nur wenn der Tool-Text Erfolg sagt UND das
    Label danach am Token steht, gilt der Import als gelungen."""
    if "successfully imported" not in message:
        return False
    try:
        from pico_hsm_tools import objects_core as oc
        from pico_hsm_tools.pkcs11_session import read_only_session

        with read_only_session(user_pin=pin, lib_path=lib_path,
                               serial=serial) as session:
            labels = {str(o.label) for o in oc.list_objects(session)}
        return label in labels
    except Exception:  # noqa: BLE001 — dann gilt der Fehler
        return False


def open_sealed_bundle(backup_dir: Path,
                       work_dir: Path,
                       identity_file: Path) -> Path:
    """Versiegeltes Bundle mit Empfänger-Identity öffnen. Gibt das ZIP zurück."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_zip = work_dir / "hsm-bundle.zip"
    try:
        bc.restore_backup(backup_dir, out_zip, identity_file)
    except bc.BackupError as exc:
        raise HsmBackupError(str(exc)) from exc
    return out_zip


def apply_bundle(bundle_zip: Path, pin: str,
                 serial: Optional[str] = None,
                 lib_path: Optional[str] = None,
                 force: bool = False) -> tuple[dict, dict]:
    """Bundle auf (neuem) Token anwenden: Unwrap, Daten, Optionen.

    Voraussetzung: Token initialisiert + derselbe DKEK per Shares
    importiert (sonst schlägt Unwrap hart fehl — Anleitung im Fehler).
    Gibt (report, manifest_dict) zurück; räumt Klartext weg.
    """
    import zipfile

    from pico_hsm_tools import apdu_core as ac
    from pico_hsm_tools import objects_core as oc
    from pico_hsm_tools.pkcs11_session import exclusive_session

    work = bundle_zip.parent / "apply"
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_zip) as archive:
        archive.extractall(work)
    manifest = HsmManifest.from_dict(json.loads(
        (work / MANIFEST_NAME).read_text(encoding="utf-8")))
    report: dict = {"keys": [], "data": [], "options": False, "missing": [],
                    "quirks": []}

    for entry in manifest.keys:
        if not entry.get("file"):
            continue  # öffentliche Hälfte, kommt per Unwrap mit
        try:
            dc.unwrap_key(
                str(work / entry["file"]), int(entry["reference"]), pin,
                force=force)
            report["keys"].append(entry["label"])
        except Exception as exc:  # noqa: BLE001 — je Key melden
            if _imported_despite_error(entry["label"], pin, lib_path,
                                       serial, str(exc)):
                report["keys"].append(entry["label"])
                report["quirks"].append(
                    f"Key '{entry['label']}': Tool meldete Erfolg trotz "
                    "Exit-Code (Zertifikat-Anteil, bekannt) — am Token "
                    "verifiziert vorhanden.")
            else:
                raise HsmBackupError(
                    f"Unwrap für Key '{entry['label']}' fehlgeschlagen "
                    f"({exc}) — Token mit demselben DKEK initialisiert + "
                    "Shares importiert?") from exc

    if manifest.data_objects:
        with exclusive_session(user_pin=pin, lib_path=lib_path,
                               serial=serial) as session:
            for entry in manifest.data_objects:
                try:
                    value = (work / entry["file"]).read_bytes()
                    oc.write_data_object(
                        session, value, entry["label"], private=True)
                    report["data"].append(entry["label"])
                except Exception as exc:  # noqa: BLE001 — je Objekt melden
                    raise HsmBackupError(
                        f"Schreiben von Datenobjekt '{entry['label']}' "
                        f"fehlgeschlagen ({exc}).") from exc

    if manifest.options:
        try:
            conn = ac.open_connection()
            try:
                ac.set_dynamic_options(conn, ac.DynamicOptions(
                    press_to_confirm=bool(
                        manifest.options.get("press_to_confirm", True)),
                    key_usage_counter=bool(
                        manifest.options.get("key_usage_counter", False)),
                ))
                report["options"] = True
            finally:
                conn.disconnect()
        except Exception as exc:  # noqa: BLE001 — melden, nicht raten
            raise HsmBackupError(
                f"Dynamic Options setzen fehlgeschlagen ({exc}) — ggf. "
                "vorher PIN-Login (Keys-Tab), Karte antwortet sonst 6982."
            ) from exc
    import shutil

    manifest_dict = manifest.to_dict()
    shutil.rmtree(work, ignore_errors=True)  # Klartext weg nach Erfolg
    try:
        bundle_zip.unlink(missing_ok=True)  # entschlüsseltes ZIP ebenso
    except OSError:  # noqa: BLE001 — kein Abbruch wegen Aufräumen
        pass
    return report, manifest_dict


def verify_against_manifest(manifest: HsmManifest, pin: Optional[str] = None,
                            serial: Optional[str] = None,
                            lib_path: Optional[str] = None) -> dict:
    """Wiederherstellung gegen Manifest prüfen (Label/Masken-Vergleich)."""
    from pico_hsm_tools import apdu_core as ac
    from pico_hsm_tools.pkcs11_session import read_only_session

    with read_only_session(user_pin=pin, lib_path=lib_path,
                           serial=serial) as session:
        from pico_hsm_tools import objects_core as oc

        present = {str(o.label) for o in oc.list_objects(session)}
    missing_keys = [k["label"] for k in manifest.keys
                    if k["label"] not in present]
    missing_data = [d["label"] for d in manifest.data_objects
                    if d["label"] not in present]
    options_ok: Optional[bool] = None
    if manifest.options:
        try:
            conn = ac.open_connection()
            try:
                current = ac.get_dynamic_options(conn)
                options_ok = (
                    current.press_to_confirm == bool(
                        manifest.options.get("press_to_confirm", True))
                    and current.key_usage_counter == bool(
                        manifest.options.get("key_usage_counter", False))
                )
            finally:
                conn.disconnect()
        except Exception:  # noqa: BLE001 — kein Login = nicht prüfbar
            options_ok = None
    return {"missing_keys": missing_keys, "missing_data": missing_data,
            "options_ok": options_ok}
