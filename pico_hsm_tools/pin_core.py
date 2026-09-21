"""
pin_core.py — PIN-Verwaltung als Core-Modul.

EINZIGE Quelle der Wahrheit für PIN-Operationen (analog zu
flash_core.py/backup_core.py/objects_core.py/apdu_core.py) — sowohl
cli/commands/pin.py als auch die GUI (gui/tabs/pin_tab.py) importieren
ausschließlich hieraus. Konsolidiert die bisher inline in pin.py
liegende pkcs11-tool-Ansteuerung.

WICHTIG (aus pin.py übernommen, verifiziert): python-pkcs11 bietet
KEINE PIN-Management-API (kein set_pin/unlock in der Session-Klasse).
Deshalb pkcs11-tool (OpenSC) als Subprozess, das C_SetPIN/C_InitPIN
direkt exponiert — verifiziert gegen pkcs11-tool-Manpage und
OpenSC-Wiki (SmartCardHSM-Seite).

SICHERHEITSVERTRAG: Keine Exception aus diesem Modul enthält je einen
PIN-Wert (weder User- noch SO-PIN) — UI-Schichten dürfen exc-Texte
daher frei anzeigen. Intern werden PINs nur als kurzlebige Locals
gehalten, nie geloggt, nie persistiert.

HARDWARE-VERIFIKATION (§12, docs/15 Phase 2.6, hw-logs/13):
PIN-Wechsel/Entsperr-Zyklus am Board ohne Fehlermeldung, neue PINs
jeweils nutzbar. Die pkcs11-tool-Aufrufe unten sind 1:1 aus der
bisherigen, manuell verifizierten CLI übernommen.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from pkcs11.constants import TokenFlag

from .pkcs11_session import get_token

_SUBPROCESS_TIMEOUT_S = 30


class PinError(Exception):
    """PIN-Fehler mit Klartext (enthält NIE PIN-Werte, siehe Vertrag oben)."""


def _run_pkcs11_tool(
    args: list[str], lib_path: Optional[str] = None,
    serial: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """pkcs11-tool aufrufen. FileNotFoundError -> PinError (klar)."""
    full_args = ["pkcs11-tool"]
    if lib_path:
        full_args += ["--module", lib_path]
    if serial:
        full_args += ["--serial", serial]
    full_args += args
    try:
        return subprocess.run(
            full_args, capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise PinError("pkcs11-tool nicht gefunden (Teil von OpenSC).") from exc
    except subprocess.TimeoutExpired as exc:
        raise PinError("pkcs11-tool antwortet nicht (Timeout).") from exc


def change_user_pin(
    old_pin: str, new_pin: str, lib_path: Optional[str] = None,
    serial: Optional[str] = None,
) -> None:
    """User-PIN ändern (--change-pin, braucht aktuelle User-PIN)."""
    result = _run_pkcs11_tool(
        ["--login", "--pin", old_pin, "--change-pin", "--new-pin", new_pin],
        lib_path, serial,
    )
    if result.returncode != 0:
        raise PinError(
            result.stderr.strip() or "PIN-Änderung fehlgeschlagen."
        )


def unblock_user_pin(
    so_pin: str, new_pin: str, lib_path: Optional[str] = None,
    serial: Optional[str] = None,
) -> None:
    """Gesperrte User-PIN mit SO-PIN zurücksetzen (--init-pin).

    Laut OpenSC-Doku funktioniert bei SmartCard-HSM-basierten Tokens
    (wie Pico HSM) --init-pin jederzeit mit dem SO-PIN, auch wenn die
    User-PIN bereits gesperrt ist.
    """
    result = _run_pkcs11_tool(
        ["--login", "--login-type", "so", "--so-pin", so_pin,
         "--init-pin", "--new-pin", new_pin],
        lib_path, serial,
    )
    if result.returncode != 0:
        raise PinError(
            result.stderr.strip() or "PIN-Unblock fehlgeschlagen."
        )


def read_pin_flags(
    lib_path: Optional[str] = None, serial: Optional[str] = None,
) -> dict[str, bool]:
    """PIN-Status aus der TokenFlag-Bitmaske (read-only, kein Lock).

    Korrektur (aus pin.py übernommen): es gibt keine
    token.get_token_info() in python-pkcs11 — der Status steckt in
    token.flags.
    """
    try:
        token = get_token(lib_path, serial=serial)
    except Exception as exc:  # noqa: BLE001 — Aufrufer mappen
        raise PinError(f"Kein Gerät erkannt: {exc}") from exc
    flags = token.flags
    return {
        "login_required": bool(flags & TokenFlag.LOGIN_REQUIRED),
        "user_pin_initialized": bool(flags & TokenFlag.USER_PIN_INITIALIZED),
        "user_pin_count_low": bool(flags & TokenFlag.USER_PIN_COUNT_LOW),
        "user_pin_final_try": bool(flags & TokenFlag.USER_PIN_FINAL_TRY),
        "user_pin_locked": bool(flags & TokenFlag.USER_PIN_LOCKED),
        "user_pin_to_be_changed": bool(flags & TokenFlag.USER_PIN_TO_BE_CHANGED),
    }


#: Flags, bei denen der PIN-Tab aktiv warnt (mit Entsperren-Hinweis).
CRITICAL_PIN_FLAGS = (
    "user_pin_count_low",
    "user_pin_final_try",
    "user_pin_locked",
)
