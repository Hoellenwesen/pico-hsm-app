from __future__ import annotations

import subprocess

import click

from pico_hsm_tools import flash_core as fc

from ..context import CliContext, pass_ctx

OTP_FLAG_FIELDS = [
    ("OTP_DATA_BOOT_FLAGS0.ROLLBACK_REQUIRED", "anti_rollback_active"),
    ("OTP_DATA_CRIT1.DEBUG_DISABLE", "swd_debug_locked"),
    ("OTP_DATA_CRIT1.SECURE_DEBUG_DISABLE", "secure_debug_locked"),
    ("DEFAULT_BOOT_VERSION0", "current_rollback_counter"),
]


def _otp_get(field: str) -> str:
    try:
        result = subprocess.run(
            ["picotool", "otp", "get", field],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return f"nicht lesbar ({result.stderr.strip() or 'kein Board?'})"
        return result.stdout.strip()
    except FileNotFoundError:
        return "picotool nicht gefunden"
    except subprocess.TimeoutExpired:
        return "Timeout beim Lesen"


@click.group()
def setup() -> None:
    """Read-only Anzeige der OTP-Konfiguration (Secure Boot etc.)."""


@setup.command("show")
@pass_ctx
def show(ctx: CliContext) -> None:
    """Secure-Boot-Fingerprint, Anti-Rollback, Debug-Lock, Rollback-Zähler.

    Bewusst kein `setup set ...`: OTP-Flags sind One-Way-Schalter
    (Thermometer-Code) mit Brick-Risiko bei Fehlbedienung. Das Setzen
    bleibt dem dokumentierten manuellen Ablauf mit Ersatzboard-Test
    vorbehalten (docs/02-firmware-update-security.md).

    Der Pubkey-Fingerprint nutzt dieselbe Funktion wie
    flash_core.py::get_burned_key_fingerprint() (mehrere BOOTKEY0_N-OTP-
    Wörter zusammengesetzt, hart abgelehnt statt unvollständig
    zurückgegeben — siehe dortige Docstring für Details/offene Punkte).
    """
    payload: dict[str, str] = {}

    try:
        payload["pubkey_fingerprint"] = fc.get_burned_key_fingerprint()
    except fc.FlashError as exc:
        payload["pubkey_fingerprint"] = f"nicht lesbar ({exc})"

    for field, key in OTP_FLAG_FIELDS:
        payload[key] = _otp_get(field)

    ctx.emit_json(payload)
    for key, value in payload.items():
        ctx.echo(f"{key}: {value}")
