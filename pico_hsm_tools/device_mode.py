"""device_mode.py — zentrale Geräte-Modus-Erkennung (eine Quelle der Wahrheit).

Modi: KEIN_GERAET / BOOTSEL / NORMAL. Login-Status ist separat (PIN-Session).

Sensoren (alle billig + poll-tauglich, keine PIN, kein Flash, kein PC/SC-Lock):
- BOOTSEL: `flash_core.is_secure_boot_enabled()` (1x `picotool info -a`).
- NORMAL: `pkcs11_session.list_tokens()` (read-only, ohne Login).

Teure/exklusive Calls werden hier bewusst NICHT benutzt:
`get_burned_key_fingerprint` (16 Subprozesse), `open_connection`
(PC/SC-exklusiv), `exclusive_session` (braucht PIN), `run_preflight/do_flash`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeviceMode(str, Enum):
    KEIN_GERAET = "kein_geraet"
    BOOTSEL = "bootsel"
    NORMAL = "normal"


@dataclass(frozen=True)
class DeviceState:
    mode: DeviceMode
    picotool_ok: bool
    token_present: bool
    detail: str = ""


# Anzeigetexte für Pill + Tooltips (deutsch, konsistent mit GUI).
MODE_LABEL: dict[DeviceMode, str] = {
    DeviceMode.KEIN_GERAET: "Kein Gerät",
    DeviceMode.BOOTSEL: "BOOTSEL",
    DeviceMode.NORMAL: "Normal",
}

MODE_HINT: dict[DeviceMode, str] = {
    DeviceMode.KEIN_GERAET: "Kein Board erkannt — USB prüfen.",
    DeviceMode.BOOTSEL: "Board im BOOTSEL-Modus — nur OTP/Firmware nutzbar.",
    DeviceMode.NORMAL: "Board im Normal-Modus — PIN/DKEK/Schlüssel nutzbar.",
}


def detect() -> DeviceState:
    """Modus einmalig erkennen (nebenwirkungsfrei, Worker-Thread-tauglich)."""
    from pico_hsm_tools import flash_core as fc
    from pico_hsm_tools import pkcs11_session as ps

    picotool_ok = True
    try:
        fc.check_picotool_available()
    except Exception:  # noqa: BLE001 — Tool fehlt, BOOTSEL damit unmöglich
        picotool_ok = False

    if picotool_ok:
        try:
            fc.is_secure_boot_enabled()
            return DeviceState(
                mode=DeviceMode.BOOTSEL,
                picotool_ok=True,
                token_present=False,
                detail="BOOTSEL via picotool erkannt",
            )
        except Exception:  # noqa: BLE001 — kein BOOTSEL-Board, weiter prüfen
            pass

    try:
        tokens = ps.list_tokens()
        if tokens:
            detail = (
                f"{len(tokens)} Tokens via PKCS#11 erkannt"
                if len(tokens) > 1
                else "Token via PKCS#11 erkannt"
            )
            return DeviceState(
                mode=DeviceMode.NORMAL,
                picotool_ok=picotool_ok,
                token_present=True,
                detail=detail,
            )
    except Exception:  # noqa: BLE001 — kein Token
        pass
    detail = "Weder BOOTSEL noch Token erkannt"
    if not picotool_ok:
        detail += " (picotool fehlt)"
    return DeviceState(
        mode=DeviceMode.KEIN_GERAET,
        picotool_ok=picotool_ok,
        token_present=False,
        detail=detail,
    )


# --- Modus-Bedarf pro GUI-Route (strikt sperren, Tooltip erklärt Modus) ---
# True = in diesem Modus nutzbar. Backup/Status-Audit sind modusfrei.
ROUTE_MODES: dict[str, set[DeviceMode]] = {
    "wizard": {DeviceMode.KEIN_GERAET, DeviceMode.BOOTSEL, DeviceMode.NORMAL},
    "status": {DeviceMode.KEIN_GERAET, DeviceMode.BOOTSEL, DeviceMode.NORMAL},
    "setup": {DeviceMode.BOOTSEL, DeviceMode.NORMAL},  # intern je Sektion
    "pin": {DeviceMode.NORMAL},
    "dkek": {DeviceMode.NORMAL},
    "keys": {DeviceMode.NORMAL},
    "backup": {DeviceMode.KEIN_GERAET, DeviceMode.BOOTSEL, DeviceMode.NORMAL},
    "firmware": {DeviceMode.BOOTSEL, DeviceMode.NORMAL},  # Flash nur BOOTSEL
    "logs": {DeviceMode.KEIN_GERAET, DeviceMode.BOOTSEL, DeviceMode.NORMAL},
}

ROUTE_NEEDS_HINT: dict[str, str] = {
    "setup": "braucht BOOTSEL (OTP) bzw. Normal + PIN-Login (Dynamic Options)",
    "pin": "braucht Normal-Modus (Board mit Firmware)",
    "dkek": "braucht Normal-Modus (Board mit Firmware)",
    "keys": "braucht Normal-Modus + PIN-Login",
    "firmware": "Flash braucht BOOTSEL-Modus",
}
