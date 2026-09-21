"""
flash_core.py — gemeinsame, sicherheitskritische Logik für das
Pico-HSM-Firmware-Update.

Dies ist die EINZIGE Quelle der Wahrheit für den Update-Workflow.
Sowohl das CLI-Skript (verify_and_flash.py) als auch die GUI
(gui/tabs/firmware_tab.py) importieren ausschließlich aus diesem Modul.
Keine Seite darf eigene Kopien der Prüf-Logik halten.

Prüft vor jedem Flash-Vorgang, in dieser Reihenfolge:
  1. Board-Identität: gebrannter Pubkey-Fingerprint (OTP) == erwarteter Key
  2. UF2-Signatur gültig (picotool verify)
  3. Rollback-Version >= letzte bekannte, erfolgreich geflashte Version
  4. TOTP-Autorisierung (Secret lebt NUR auf einem physisch getrennten
     Gerät, hier wird lediglich der 6-stellige Code entgegengenommen)
  5. Tatsächliches Flashen über picotool
  6. Manipulationssicheres Hash-Chain-Audit-Log für jeden Schritt

Kein Schritt hier ersetzt Secure Boot / das RP2350-Boot-ROM — das bleibt
der eigentliche Vertrauensanker (siehe docs/03-authorization-review.md).
Diese Logik ist Defense-in-Depth für den Operator: Fehlbedienung,
Downgrades und unautorisierte Updates früh abfangen und protokollieren.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .paths import base_dir

try:
    import pyotp
except ImportError:  # pragma: no cover - optionale Abhängigkeit
    pyotp = None


AUDIT_LOG = base_dir() / "update_audit.jsonl"
GENESIS_HASH = "0" * 64

# Muss vor dem ersten produktiven Einsatz gesetzt werden:
#   openssl pkey -in secure-boot-key.pem -pubout -outform DER | sha256sum
KNOWN_PUBKEY_FINGERPRINT = "REPLACE_WITH_YOUR_KEY_SHA256_FINGERPRINT"

# Empirisch gegen echte picotool-v2.3.0-Ausgabe verifiziert (siehe
# get_uf2_version()/verify_signature()). Beide Felder erscheinen mehrfach
# in der Ausgabe (Program-Information-Summary + Metadata-Block-Detail),
# re.search() nimmt bewusst das erste Vorkommen — beide Vorkommen sind
# bei einer konsistenten Datei identisch.
_VERSION_RE = re.compile(r"^\s*version:\s+(\d+)\.(\d+)\s*$", re.MULTILINE)
_ROLLBACK_RE = re.compile(r"^\s*rollback version:\s+(\d+)\s*$", re.MULTILINE)
_SIGNATURE_RE = re.compile(r"^\s*signature:\s+(\S+)\s*$", re.MULTILINE)
_SECURE_BOOT_RE = re.compile(
    r"^\s*secure boot:\s*([01])\s*$", re.MULTILINE | re.IGNORECASE,
)


class FlashError(Exception):
    """Fehler im Verify/Flash-Workflow. Immer mit sprechender Meldung,
    nie stillschweigend abbrechen (siehe docs/03, Grundsatz zu Fix 2)."""


@dataclass
class Uf2Info:
    major: int
    minor: int
    rollback: int


@dataclass
class PreflightResult:
    """Ergebnis aller Vorab-Prüfungen, bevor überhaupt eine TOTP-Abfrage
    oder ein Flash-Versuch stattfindet. Wird von CLI und GUI gleichermaßen
    zur Anzeige genutzt, bevor der Nutzer bestätigt."""
    fw_path: Path
    sha256: str
    version: Uf2Info
    board_fingerprint: str
    last_known_rollback: int
    # Getroffene Entscheidung (Hardware-Validierung): Fingerprint-Check
    # nur bei Secure Boot an erzwungen (secure_boot_enabled True). Ohne
    # Secure-Boot-Anker ist nichts gebrannt — Vergleich wäre immer rot,
    # daher Warnung statt Abbruch (fingerprint_checked False). Defaults
    # erhalten das bisherige Verhalten für bestehende Konstruktoren.
    secure_boot_enabled: bool = True
    fingerprint_checked: bool = True
    signature_checked: bool = True


# --------------------------------------------------------------------------
# Hilfsfunktionen: Hashing, picotool-Wrapper
# --------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _run_picotool(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["picotool", *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise FlashError(
            "picotool wurde nicht gefunden. Installation prüfen "
            "(muss USB-Support haben — die CMake-Auto-Download-Variante "
            "hat das NICHT, siehe docs/02-setup.md §2)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FlashError(
            f"picotool antwortet nicht (Timeout {timeout:g}s bei "
            f"{' '.join(args)}) — Board angeschlossen und im richtigen "
            "Modus (BOOTSEL für otp/info)?"
        ) from exc


def check_picotool_available() -> None:
    """Frühzeitiger, expliziter Check statt später kryptisch zu scheitern."""
    result = _run_picotool(["version"])
    if result.returncode != 0:
        raise FlashError(
            "picotool ist nicht lauffähig oder hat keinen USB-Support "
            "(bekannte Falle: die CMake-auto-heruntergeladene Variante). "
            f"stderr: {result.stderr.strip()}"
        )


# --------------------------------------------------------------------------
# Fix 1: Board-Identität über OTP-Pubkey-Fingerprint
# --------------------------------------------------------------------------

def get_burned_key_fingerprint() -> str:
    """Liest den tatsächlich in OTP gebrannten Public-Key-Hash (32 Byte,
    SHA-256) vom angeschlossenen Board — das ist der reale Vertrauensanker,
    nicht eine lokale PEM-Datei, die manipuliert sein könnte.

    Format VERIFIZIERT am Ersatzboard (BOOTSEL, picotool v2.3.1): Der Hash
    liegt in 16 Rows `OTP_DATA_BOOTKEY0_0..15` (je 16 Bit). Jede wird per
    `picotool otp get <feld>` gelesen; die Antwort enthält nach einer
    Leerzeile die Zeile `    VALUE 0x....` (z.B. `VALUE 0x0000` auf
    unberührtem Board). Die 16×4 Hexzeichen ergeben den 64-zeichen
    Fingerprint (lowercase).

    Hart abbrechen (FlashError) statt raten: fehlende VALUE-Zeile,
    picotool-Fehler oder überlange Werte (niemals Hash-Material still
    kürzen) — ein falscher Fingerprint wäre schlimmer als ein klarer
    Fehler. Hinweis: `picotool otp list` zeigt KEINE Werte (nur Struktur),
    daher der Einzel-Row-Ansatz statt Listen-Parsing.

    VERIFIZIERT (§12, docs/15 Phase 1): komponierter Wert am Board
    gemessen — unberührtes Board: 64× `0`.
    """
    parts: list[str] = []
    for index in range(16):
        field = f"OTP_DATA_BOOTKEY0_{index}"
        result = _run_picotool(["otp", "get", field])
        if result.returncode != 0:
            raise FlashError(
                f"Konnte OTP-Feld {field} nicht lesen (`picotool otp get` "
                f"fehlgeschlagen, Board im BOOTSEL-Modus angeschlossen?). "
                f"stderr: {result.stderr.strip()}"
            )
        digits = _extract_otp_value(result.stdout, field)
        if len(digits) > 4:
            raise FlashError(
                f"Unerwartet langer Wert in {field} ({len(digits)} statt "
                "max. 4 Hexzeichen für 16-Bit-Row) — kein stilles Kürzen "
                "von Hash-Material."
            )
        parts.append(digits.zfill(4))

    fingerprint = "".join(parts).lower()
    if len(fingerprint) != 64:
        raise FlashError(
            f"Zusammengesetzter Fingerprint hat {len(fingerprint)} statt "
            "64 Hexzeichen (32 Byte) — nicht mit einem unvollständigen "
            "Fingerprint weitermachen."
        )
    return fingerprint


_OTP_VALUE_RE = re.compile(
    r"^\s*VALUE\s+0[xX]([0-9a-fA-F]+)\s*$", re.MULTILINE,
)


def _extract_otp_value(output: str, field: str) -> str:
    """Hex-Ziffern aus der VALUE-Zeile einer `picotool otp get`-Antwort
    holen (ohne 0x-Präfix). Format verifiziert, siehe oben."""
    match = _OTP_VALUE_RE.search(output)
    if not match:
        raise FlashError(
            f"Kein VALUE in `picotool otp get {field}`-Ausgabe gefunden."
        )
    return match.group(1)


def verify_board_identity(expected_fingerprint: str) -> str:
    """Gibt den gelesenen Fingerprint zurück oder wirft FlashError.
    Wird VOR jedem Flash-Versuch aufgerufen — verhindert auch simple
    Verwechslungen, wenn mehrere Boards im Homelab rumliegen."""
    board_fingerprint = get_burned_key_fingerprint()
    if board_fingerprint.lower() != expected_fingerprint.lower():
        raise FlashError(
            "Board-Fingerprint weicht vom erwarteten ab "
            f"(Board: {board_fingerprint}, erwartet: {expected_fingerprint}). "
            "Falsches Board oder Manipulationsversuch. Abbruch."
        )
    return board_fingerprint


# --------------------------------------------------------------------------
# OTP-Anzeige (read-only): Fingerprint siehe oben, Einzelfelder hier.
# Konsolidiert aus cli/commands/setup.py (Prinzip "eine Quelle der
# Wahrheit") — CLI (`setup show`) und GUI (Setup-Tab) nutzen diese
# Funktionen statt eigenem picotool-Parsing.
# --------------------------------------------------------------------------

# (picotool-Feldname, Anzeigename) für `setup show` / Setup-Tab.
OTP_FLAG_FIELDS = [
    ("OTP_DATA_BOOT_FLAGS0.ROLLBACK_REQUIRED", "anti_rollback_active"),
    ("OTP_DATA_CRIT1.DEBUG_DISABLE", "swd_debug_locked"),
    ("OTP_DATA_CRIT1.SECURE_DEBUG_DISABLE", "secure_debug_locked"),
    ("DEFAULT_BOOT_VERSION0", "current_rollback_counter"),
]


def read_otp_field(field: str) -> str:
    """Einzelnes OTP-Feld lesen — Anzeige, kein Fehlerwurf.

    Gibt den VALUE-Wert (`0x...`, Format verifiziert siehe
    get_burned_key_fingerprint) oder eine lesbare Ersatzmeldung zurück
    (statt zu werfen: fehlendes Board/picotool ist im Anzeige-Kontext
    ein erwarteter Zustand, kein Programmfehler).
    """
    try:
        result = subprocess.run(
            ["picotool", "otp", "get", field],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return "picotool nicht gefunden"
    except subprocess.TimeoutExpired:
        return "Timeout beim Lesen"
    if result.returncode != 0:
        return f"nicht lesbar ({result.stderr.strip() or 'kein Board?'})"
    try:
        return "0x" + _extract_otp_value(result.stdout, field)
    except FlashError:
        return "nicht lesbar (kein VALUE in Ausgabe)"


# --------------------------------------------------------------------------
# Fix 2: Robustes Parsing (JSON, hart fehlschlagen statt raten)
# --------------------------------------------------------------------------

def get_uf2_version(path: Path) -> Uf2Info:
    """Liest Version/Rollback aus der KLARTEXT-Ausgabe von `picotool info -a`.

    KORREKTUR (empirisch gegen echte picotool v2.3.0 verifiziert, gebaut
    aus dem Quellcode + Testbinary mit `picotool seal --sign --major 1
    --minor 0 --rollback 3`): `picotool info` unterstützt KEIN --json —
    dieses Flag existiert nicht (`picotool help info` zeigt nur
    -b/-m/-p/-d/--debug/-l/-a). Die Version erscheint als eine Zeile
    "version:    MAJOR.MINOR" und "rollback version:    N" im Klartext.

    Hardware-Korrektur (Ersatzboard): Unsignierte Dev-Builds enthalten
    KEINE Rollback-Zeile — dann rollback=-1 (unbekannt) statt Abbruch.
    Die Version selbst bleibt Pflicht (ohne sie ist die Datei unbrauchbar
    für den Update-Vergleich).
    """
    result = _run_picotool(["info", "-a", str(path)])
    if result.returncode != 0:
        raise FlashError(
            "picotool info fehlgeschlagen — Datei kein gültiges UF2/ELF/BIN? "
            f"stderr: {result.stderr.strip()}"
        )

    version_match = _VERSION_RE.search(result.stdout)
    if not version_match:
        raise FlashError(
            "Keine Versions-Angabe in der Datei gefunden — wurde "
            "sie mit `picotool seal --sign --major .. --minor .. "
            "--rollback ..` versiegelt? Bei unklarer Angabe wird "
            "bewusst abgebrochen, nicht geraten."
        )
    rollback_match = _ROLLBACK_RE.search(result.stdout)
    return Uf2Info(
        major=int(version_match.group(1)),
        minor=int(version_match.group(2)),
        rollback=int(rollback_match.group(1)) if rollback_match else -1,
    )


def format_version_rollback(version: Uf2Info) -> str:
    """Anzeige-Text für Version/Rollback (`-1` = unbekannt, siehe oben)."""
    if version.rollback < 0:
        return f"{version.major}.{version.minor} (rollback unbekannt)"
    return f"{version.major}.{version.minor} (rollback={version.rollback})"


def _signature_state_for_file(path: Path) -> bool | None:
    """Signatur-Status einer UF2-Datei: True = verifiziert, False =
    Zeile vorhanden aber NICHT verified (manipuliert/falsch signiert),
    None = KEINE signature-Zeile (unsigniertes Dev-Build, z.B.
    selbst-kompilierte Firmware ohne Secure-Boot-Keys — am Board
    gemessen: nur `hash: verified` der Partitionstabelle vorhanden)."""
    result = _run_picotool(["info", "-a", str(path)])
    if result.returncode != 0:
        return None
    match = _SIGNATURE_RE.search(result.stdout)
    if not match:
        return None
    return match.group(1).strip().lower() == "verified"


def verify_signature(path: Path) -> bool:
    """Prüft die Signatur über die Klartext-Ausgabe von `picotool info -a`.

    KORREKTUR (empirisch verifiziert): `picotool verify <datei>` ist NICHT
    das, wonach es klingt — dieser Befehl vergleicht ein angeschlossenes
    Gerät gegen eine Datei und schlägt ohne Gerät grundsätzlich mit "No
    accessible RP-series devices" fehl (exakt der Zustand vor jedem
    Flash-Vorgang!). Die tatsächliche Signaturprüfung einer eigenständigen
    Datei steht in der `info -a`-Ausgabe als Zeile "signature: verified"
    bzw. "signature: incorrect". WICHTIG: picotool gibt bei ungültiger
    Signatur trotzdem Exit-Code 0 zurück — der Text muss geparst werden,
    der Exit-Code allein reicht nicht (gegen absichtlich manipulierte
    Testdatei verifiziert)."""
    return _signature_state_for_file(path) is True


def is_secure_boot_enabled() -> bool:
    """Secure-Boot-Status des angeschlossenen Boards (BOOTSEL nötig).

    Liest `picotool info -a` (Gerät, keine Datei) und parst die Zeile
    `secure boot: 0/1` (verifiziert gegen reale Ausgabe:
    ` secure boot:            0`). Wirft FlashError, wenn kein Board
    erreichbar oder die Zeile fehlt — Aufrufer melden BOOTSEL-Hinweis.
    """
    result = _run_picotool(["info", "-a"])
    if result.returncode != 0:
        raise FlashError(
            "Board nicht erreichbar (`picotool info -a` fehlgeschlagen) — "
            "Board im BOOTSEL-Modus anschließen."
        )
    match = _SECURE_BOOT_RE.search(result.stdout)
    if not match:
        raise FlashError(
            "Secure-Boot-Status nicht ermittelbar (keine `secure boot:`-"
            "Zeile in `picotool info -a`-Ausgabe)."
        )
    return match.group(1) == "1"


# --------------------------------------------------------------------------
# Fix 3: Manipulationssicheres Hash-Chain-Audit-Log
# --------------------------------------------------------------------------

def _last_entry_hash() -> str:
    if not AUDIT_LOG.exists():
        return GENESIS_HASH
    last_line = None
    with AUDIT_LOG.open() as f:
        for line in f:
            if line.strip():
                last_line = line
    if not last_line:
        return GENESIS_HASH
    return json.loads(last_line)["entry_hash"]


def append_audit(entry: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(entry)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entry["prev_hash"] = _last_entry_hash()
    payload = json.dumps(entry, sort_keys=True)
    entry["entry_hash"] = hashlib.sha256(payload.encode()).hexdigest()
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def verify_audit_chain() -> bool:
    """Prüft, ob die Hash-Chain intakt ist. Für Wazuh/periodische Checks
    UND vor jedem neuen Update-Versuch aus GUI/CLI aufrufbar."""
    if not AUDIT_LOG.exists():
        return True
    expected_prev = GENESIS_HASH
    with AUDIT_LOG.open() as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            entry = json.loads(line)
            stored_hash = entry.pop("entry_hash")
            if entry["prev_hash"] != expected_prev:
                return False
            payload = json.dumps(entry, sort_keys=True)
            recomputed = hashlib.sha256(payload.encode()).hexdigest()
            if recomputed != stored_hash:
                return False
            expected_prev = stored_hash
    return True


def load_last_known() -> dict:
    """Letzter Eintrag mit status == 'flashed', für den Rollback-Vergleich."""
    if not AUDIT_LOG.exists():
        return {"rollback": -1}
    last = {"rollback": -1}
    with AUDIT_LOG.open() as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("status") == "flashed":
                last = entry
    return last


# --------------------------------------------------------------------------
# Fix 4: TOTP-Autorisierung — Secret lebt NUR auf getrenntem Gerät.
# Dieses Modul nimmt niemals ein Secret entgegen und speichert keins;
# es verifiziert nur den vom Nutzer eingegebenen 6-stelligen Code gegen
# ein lokal (in dieser Konfiguration, NICHT im Code) hinterlegtes Secret.
# --------------------------------------------------------------------------

#: Pfad der lokalen TOTP-Secret-Datei (eine Quelle der Wahrheit für
#: CLI und GUI; fehlt die Datei, ist die Schicht deaktiviert).
TOTP_SECRET_FILE = base_dir() / "totp_secret.txt"

def verify_totp_code(totp_secret_b32: str, entered_code: str) -> bool:
    """entered_code kommt manuell vom Nutzer, abgetippt von einem
    physisch getrennten Gerät (z.B. Bitwarden-App auf dem Handy).
    totp_secret_b32 wird von der aufrufenden Seite aus einer lokalen,
    nicht versionierten Konfigurationsdatei geladen (siehe README) —
    niemals aus Vaultwarden auf demselben Host automatisiert abgerufen,
    das würde den Zweck der Trennung untergraben."""
    if pyotp is None:
        raise FlashError(
            "pyotp ist nicht installiert (`pip install pyotp`), aber für "
            "die TOTP-Autorisierung erforderlich."
        )
    totp = pyotp.TOTP(totp_secret_b32)
    return totp.verify(entered_code.strip(), valid_window=1)


# --------------------------------------------------------------------------
# Orchestrierung: Preflight (reine Prüfungen, keine Seiteneffekte außer
# Audit-Log bei Ablehnung) + der eigentliche Flash-Schritt.
# Beide werden von CLI und GUI identisch aufgerufen; die GUI übergibt
# lediglich andere Callbacks für Nutzerinteraktion (TOTP-Eingabe,
# BOOTSEL-Bestätigung) statt input().
# --------------------------------------------------------------------------

def run_preflight(
    fw_path: Path,
    expected_fingerprint: str = KNOWN_PUBKEY_FINGERPRINT,
) -> PreflightResult:
    """Führt alle Nur-Lese-Prüfungen aus (Board-Identität, Signatur,
    Version/Rollback). Wirft FlashError mit sprechender Meldung und
    protokolliert Ablehnungen. Flasht noch NICHT."""
    if not fw_path.exists():
        raise FlashError(f"Datei nicht gefunden: {fw_path}")

    check_picotool_available()
    digest = sha256_of(fw_path)

    secure_boot = is_secure_boot_enabled()
    sig_state = _signature_state_for_file(fw_path)
    if sig_state is False:
        # Echte Fehl-Signatur (Zeile vorhanden, nicht "verified") —
        # immer ein Abbruch, unabhängig vom Secure-Boot-Status.
        append_audit({
            "file": str(fw_path), "sha256": digest,
            "status": "rejected_signature",
        })
        raise FlashError("Signaturprüfung fehlgeschlagen. Abbruch.")
    if sig_state is None and secure_boot:
        # Release ohne Signaturzeile bei aktivem Secure Boot.
        append_audit({
            "file": str(fw_path), "sha256": digest,
            "status": "rejected_signature",
        })
        raise FlashError(
            "Keine Signatur in der Firmware-Datei gefunden, Secure Boot "
            "ist aber aktiv. Abbruch."
        )
    signature_checked = sig_state is True
    if not signature_checked:
        # Getroffene Entscheidung (Hardware-Validierung, Secure Boot aus,
        # unsigniertes Dev-Build): Warnung statt Abbruch — sonst wäre auf
        # solchen Boards gar kein Flash möglich. Aufrufer zeigen die
        # Warnung an; Audit hält die Entscheidung fest.
        append_audit({
            "file": str(fw_path), "sha256": digest,
            "status": "accepted_unsigned",
        })

    if secure_boot:
        board_fingerprint = verify_board_identity(expected_fingerprint)
        fingerprint_checked = True
    else:
        # Getroffene Entscheidung (Hardware-Validierung, Secure Boot aus):
        # Ohne Secure-Boot-Anker ist nichts gebrannt — ein Vergleich wäre
        # immer rot. Warnung statt Abbruch; Aufrufer zeigen sie an.
        # get_burned_key_fingerprint() wird hier bewusst NICHT aufgerufen.
        board_fingerprint = ""
        fingerprint_checked = False

    version = get_uf2_version(fw_path)
    last = load_last_known()
    last_rollback = last.get("rollback", -1)
    # Guard: rollback=-1 heißt UNBEKANNT (unsigniertes Dev-Build) und darf
    # niemals einen Downgrade-Fehlalarm auslösen (sonst wäre jeder Dev-Flash
    # blockiert, sobald je ein Rollback protokolliert wurde).
    if version.rollback >= 0 and version.rollback < last_rollback:
        append_audit({
            "file": str(fw_path), "sha256": digest,
            "version": f"{version.major}.{version.minor}",
            "rollback": version.rollback,
            "status": "rejected_rollback",
        })
        raise FlashError(
            f"Rollback-Downgrade erkannt (neu: {version.rollback}, "
            f"zuletzt geflasht: {last_rollback}). Abbruch."
        )

    return PreflightResult(
        fw_path=fw_path,
        sha256=digest,
        version=version,
        board_fingerprint=board_fingerprint,
        last_known_rollback=last_rollback,
        secure_boot_enabled=secure_boot,
        fingerprint_checked=fingerprint_checked,
        signature_checked=signature_checked,
    )


def do_flash(
    preflight: PreflightResult,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Der eigentliche, protokollierte Flash-Vorgang. Erwartet, dass
    run_preflight() bereits erfolgreich durchlaufen wurde und (falls
    konfiguriert) die TOTP-Autorisierung bereits separat geprüft wurde."""
    log = on_progress or (lambda _msg: None)
    log("Flashe über picotool ...")
    result = _run_picotool(["load", "-x", str(preflight.fw_path)])
    if result.returncode != 0:
        append_audit({
            "file": str(preflight.fw_path), "sha256": preflight.sha256,
            "version": f"{preflight.version.major}.{preflight.version.minor}",
            "rollback": preflight.version.rollback,
            "status": "flash_failed",
        })
        raise FlashError(f"Fehler beim Flashen: {result.stderr.strip()}")

    append_audit({
        "file": str(preflight.fw_path), "sha256": preflight.sha256,
        "version": f"{preflight.version.major}.{preflight.version.minor}",
        "rollback": preflight.version.rollback,
        "status": "flashed",
    })
    # KEIN Erfolgs-Log hier: Erfolgsmeldung ist Sache der Aufrufer
    # (CLI echo / GUI InfoBar) — sonst erscheint sie doppelt (Bug
    # am Board gemeldet). Erfolg = Rückkehr ohne Exception + Audit.
