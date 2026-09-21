"""dashboard.py — Handlungs-Empfehlungen für den Status-Tab (eine Quelle).

Reine Funktion `collect()`: aus PIN-Flags, Audit-Chain-Status,
Wizard-Fortschritt und Backup-Hygiene werden Empfehlungen mit
Sprung-Ziel (Tab-Route). Kein UI, kein I/O außer übergebenen Daten —
leicht testbar. Daten sammelt der Status-Tab in seinem Worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Recommendation:
    text: str
    route: Optional[str]  # None = kein Sprung (reine Info)


def collect(
    device_present: bool,
    pin_flags: Optional[dict],
    chain_intact: Optional[bool],
    setup_done: Optional[dict] = None,
    backup_warnings: Optional[list[str]] = None,
    critical_pin_flags: tuple[str, ...] = (),
    pin_labels: tuple[tuple[str, str], ...] = (),
) -> list[Recommendation]:
    """Empfehlungen aus Zustands-Schnappschuss ableiten (Priorität: Gerät,
    PIN, Chain, Setup, Backup). Leere Liste = alles bereit."""
    reco: list[Recommendation] = []
    if not device_present:
        reco.append(Recommendation(
            "Kein Board erkannt — USB prüfen (Normal für PIN/Keys, "
            "BOOTSEL für OTP/Firmware).",
            None,
        ))
        return reco

    if pin_flags is None:
        reco.append(Recommendation(
            "PIN-Status nicht lesbar — Details im PIN-Tab.", "pin",
        ))
    else:
        if not pin_flags.get("user_pin_initialized", True):
            reco.append(Recommendation(
                "User-PIN nicht eingerichtet — im Start-Tab einrichten.",
                "wizard",
            ))
        critical = [key for key in critical_pin_flags if pin_flags.get(key)]
        if critical:
            labels = dict(pin_labels)
            names = ", ".join(labels.get(key, key) for key in critical)
            reco.append(Recommendation(
                f"PIN-Status kritisch ({names}) — im PIN-Tab "
                "entsperren/ändern.",
                "pin",
            ))

    if chain_intact is False:
        reco.append(Recommendation(
            "Audit-Chain GEBROCHEN — Log im Logs-Tab prüfen.", "logs",
        ))

    if setup_done is not None:
        open_steps = [key for key, done in setup_done.items() if not done]
        if open_steps:
            reco.append(Recommendation(
                f"Ersteinrichtung unvollständig ({len(setup_done) - len(open_steps)}"
                f"/{len(setup_done)} Schritte) — im Start-Tab fortsetzen.",
                "wizard",
            ))

    for warning in backup_warnings or []:
        reco.append(Recommendation(
            f"Backup-Hygiene: {warning} — im Backup-Tab prüfen.", "backup",
        ))

    return reco
