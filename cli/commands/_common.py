"""_common.py — Modus-Gates für CLI-Befehle (eine Quelle der Wahrheit).

Prinzip: Nur bei positivem Falsch-Modus-Nachweis blockieren (klare
Anleitung statt Blind-Fehler). Ohne Hardware lassen die Gates alles
durch — der jeweilige Befehl meldet dann seinen eigenen Fehler
(Tests ohne Hardware bleiben grün).
"""

from __future__ import annotations

import functools
from typing import Any, Callable


def _bootsel_present() -> bool:
    try:
        from pico_hsm_tools import flash_core as fc

        fc.check_picotool_available()
        fc.is_secure_boot_enabled()
        return True
    except Exception:  # noqa: BLE001 — kein BOOTSEL-Nachweis, kein Gate
        return False


def _token_present(ctx: Any) -> bool:
    try:
        from pico_hsm_tools.pkcs11_session import get_token

        get_token(getattr(ctx, "pkcs11_lib", None))
        return True
    except Exception:  # noqa: BLE001 — kein Token-Nachweis, kein Gate
        return False


def require_bootsel(func: Callable[..., Any]) -> Callable[..., Any]:
    """Befehl braucht BOOTSEL — bei Token im Normal-Modus klar anleiten."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = args[0] if args else kwargs.get("ctx")
        if ctx is not None and _token_present(ctx):
            ctx.fail(
                "Falscher Modus: Token im Normal-Modus erkannt — dieser "
                "Befehl braucht den BOOTSEL-Modus (BOOT-Taste halten + "
                "USB verbinden, Laufwerk RPI-RP2).",
                exit_code=2,
            )
            return None
        return func(*args, **kwargs)

    return wrapper


def require_normal(func: Callable[..., Any]) -> Callable[..., Any]:
    """Befehl braucht Normal-Modus — bei BOOTSEL klar anleiten."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _bootsel_present():
            ctx = args[0] if args else kwargs.get("ctx")
            if ctx is not None:
                ctx.fail(
                    "Falscher Modus: Board im BOOTSEL-Modus erkannt — "
                    "dieser Befehl braucht den Normal-Modus (Board normal "
                    "über USB verbinden, Firmware läuft).",
                    exit_code=2,
                )
                return None
        return func(*args, **kwargs)

    return wrapper
