from __future__ import annotations

import os
from getpass import getpass

import click

from pico_hsm_tools import pin_core as pc

from ..context import CliContext, pass_ctx
from ..pkcs11_helpers import warn_if_gateway_reachable


@click.group()
def pin() -> None:
    """PIN-Verwaltung.

    WICHTIG (Korrektur): python-pkcs11 bietet KEINE PIN-Management-API
    (kein set_pin/unlock_user_pin in der Session-Klasse). Diese Gruppe
    nutzt daher pkcs11-tool (OpenSC) über pico_hsm_tools/pin_core.py,
    das C_SetPIN/C_InitPIN direkt exponiert. Verifiziert gegen die
    offizielle pkcs11-tool-Manpage und OpenSC-Wiki (SmartCardHSM-Seite).
    """


@pin.command("change")
@pass_ctx
def change(ctx: CliContext) -> None:
    """User-PIN ändern (--change-pin, erfordert aktuelle User-PIN)."""
    warn_if_gateway_reachable(ctx)
    old_pin = ctx.get_pin("Aktuelle User-PIN: ")
    new_pin = getpass("Neue User-PIN: ")
    confirm_pin = getpass("Neue User-PIN bestätigen: ")
    if new_pin != confirm_pin:
        ctx.fail("Neue PINs stimmen nicht überein.")
        return

    try:
        pc.change_user_pin(old_pin, new_pin, ctx.pkcs11_lib)
    except pc.PinError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok"})
    ctx.echo("[OK] PIN erfolgreich geändert.")


@pin.command("unblock")
@click.option("--puk-env", help="Umgebungsvariable mit dem SO-PIN/PUK (statt Prompt).")
@pass_ctx
def unblock(ctx: CliContext, puk_env: str | None) -> None:
    """Gesperrte User-PIN mit SO-PIN zurücksetzen (--init-pin).

    Laut OpenSC-Doku funktioniert bei SmartCard-HSM-basierten Tokens
    (wie Pico HSM) --init-pin jederzeit mit dem SO-PIN, auch wenn die
    User-PIN bereits gesperrt ist — anders als --change-pin, das die
    aktuelle User-PIN voraussetzt.
    """
    warn_if_gateway_reachable(ctx)
    so_pin = os.environ.get(puk_env) if puk_env else None
    if not so_pin:
        so_pin = getpass("SO-PIN: ")
    new_pin = getpass("Neue User-PIN: ")
    confirm_pin = getpass("Neue User-PIN bestätigen: ")
    if new_pin != confirm_pin:
        ctx.fail("Neue PINs stimmen nicht überein.")
        return

    try:
        pc.unblock_user_pin(so_pin, new_pin, ctx.pkcs11_lib)
    except pc.PinError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok"})
    ctx.echo("[OK] PIN zurückgesetzt.")


@pin.command("status")
@pass_ctx
def pin_status(ctx: CliContext) -> None:
    """PIN-Status über die Token-Flags (TokenFlag-Bitmaske).

    Korrektur: es gibt keine token.get_token_info() in python-pkcs11 —
    der Status steckt in token.flags als TokenFlag-Bitmaske.
    """
    try:
        payload = pc.read_pin_flags(ctx.pkcs11_lib)
    except pc.PinError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json(payload)
    for key, value in payload.items():
        ctx.echo(f"  {key}: {value}")
