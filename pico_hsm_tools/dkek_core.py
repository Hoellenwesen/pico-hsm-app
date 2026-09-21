"""
dkek_core.py — DKEK-Operationen als Core-Modul (wrap/unwrap/status).

EINZIGE Quelle der Wahrheit für die GUI-fähigen DKEK-Operationen
(analog zu flash_core.py/pin_core.py) — sowohl cli/commands/dkek.py
als auch die GUI (gui/tabs/dkek_tab.py) importieren ausschließlich
hieraus. Konsolidiert die bisher inline in dkek.py liegende
sc-hsm-tool-Ansteuerung.

BEWUSST AUSGENOMMEN: `create-share`/`import-share`. Beide brauchen ein
interaktives Terminal (Custodian-Passworteingabe — die CLI nutzt
absichtlich kein Pipe-Capture, siehe dkek.py). Ein GUI-Stdout-Füttern
per stdin wäre geraten (sc-hsm-tool liest ggf. direkt /dev/tty) und
ohne Hardware nicht verifizierbar — daher bleiben beide CLI-only
(getroffene Entscheidung).

SICHERHEITSVERTRAG (wie pin_core.py): Keine Exception aus diesem Modul
enthält je einen PIN-Wert — UI-Schichten dürfen exc-Texte frei
anzeigen. PINs nur als kurzlebige Locals, nie geloggt, nie persistiert.
Share-Inhalte werden nirgends angezeigt oder zwischengespeichert
(hier fließt nur der Dateipfad durch).

OFFENE PUNKTE (Architekturkonzept §12, ehrlich markiert): der
DKEK-Voll-Flow (init mit --dkek-shares, import-share, wrap/unwrap)
ist noch nicht am Board durchlaufen (docs/15 Checkliste 7 offen,
Teilerfolg hw-logs/16); die sc-hsm-tool-Aufrufe unten sind 1:1 aus
der manuell verifizierten CLI übernommen (Syntax aus
backup-and-restore.md).
"""

from __future__ import annotations

import subprocess
from typing import Optional

_SUBPROCESS_TIMEOUT_S = 30


class DkekError(Exception):
    """DKEK-Fehler mit Klartext (enthält NIE PIN-Werte, siehe Vertrag oben)."""


def _run_sc_hsm_tool(
    args: list[str],
) -> subprocess.CompletedProcess:
    """sc-hsm-tool aufrufen (captured). FileNotFound -> DkekError."""
    try:
        return subprocess.run(
            ["sc-hsm-tool", *args],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise DkekError("sc-hsm-tool nicht gefunden (Teil von OpenSC).") from exc
    except subprocess.TimeoutExpired as exc:
        raise DkekError("sc-hsm-tool antwortet nicht (Timeout).") from exc


def _failure_message(
    result: subprocess.CompletedProcess, default: str,
) -> str:
    """Fehlertext aus stderr + stdout (Hardware-Befund: sc-hsm-tool legt
    das Detail teils auf stdout — reines stderr unterschlägt es, z.B.
    nur `Using reader ...` ohne Grund). PIN-frei per Vertrag (Tool
    echot keine PINs)."""
    parts = [result.stderr.strip(), result.stdout.strip()]
    return "\n".join(part for part in parts if part) or default


def wrap_key(out_file: str, key_reference: int, pin: str) -> None:
    """Einzelnen Private Key mit dem DKEK wrappen/exportieren (Key-Backup)."""
    result = _run_sc_hsm_tool([
        "--wrap-key", out_file,
        "--key-reference", str(key_reference), "--pin", pin,
    ])
    if result.returncode != 0:
        raise DkekError(_failure_message(result, "wrap-key fehlgeschlagen."))


def unwrap_key(
    wrapped_file: str, key_reference: int, pin: str, force: bool = False,
) -> None:
    """Gewrappten Key importieren (Gerät muss mit demselben DKEK
    initialisiert sein wie beim Export). Ersetzt das vorher separate
    `keys import` — der reale Import-Mechanismus von Pico HSM.

    `force=True` reicht `-f/--force` an sc-hsm-tool durch (sonst
    verweigert das Tool den Import auf eine belegte Reference —
    Hardware-Befund: "Found existing private key description ... use
    --force"). Default False (kein stilles Überschreiben)."""
    args = [
        "--unwrap-key", wrapped_file,
        "--key-reference", str(key_reference), "--pin", pin,
    ]
    if force:
        args.append("--force")
    result = _run_sc_hsm_tool(args)
    if result.returncode != 0:
        raise DkekError(_failure_message(result, "unwrap-key fehlgeschlagen."))


def dkek_status() -> str:
    """DKEK-Status als Roh-Text (`sc-hsm-tool` ohne Argumente fragt den
    Status ab — verifiziert gegen die OpenSC-Manpage).

    Bewusst UNGEPARST zurückgegeben: Das Ausgabeformat ist nur über die
    Manpage belegt, nicht über Beispiele — Parsing wäre geraten
    (Rate-Bugs). Aufrufer zeigen den Text 1:1 an (getroffene
    Entscheidung).
    """
    result = _run_sc_hsm_tool([])
    if result.returncode != 0:
        raise DkekError(_failure_message(result, "Status-Abfrage fehlgeschlagen."))
    return result.stdout.strip()
