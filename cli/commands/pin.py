from __future__ import annotations

import subprocess
from getpass import getpass

import click

from pico_hsm_tools.pkcs11_session import check_daemon_running, get_token
from pkcs11.constants import TokenFlag

from ..context import CliContext, pass_ctx


def _run_pkcs11_tool(ctx: CliContext, args: list[str]) -> subprocess.CompletedProcess:
    module = ctx.pkcs11_lib
    full_args = ["pkcs11-tool"]
    if module:
        full_args += ["--module", module]
    full_args += args
    try:
        return subprocess.run(full_args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        ctx.fail("pkcs11-tool nicht gefunden (Teil von OpenSC).")
        raise  # unreachable


def _check_daemon_guard(ctx: CliContext) -> None:
    state = check_daemon_running()
    if state.running and not ctx.force:
        if not ctx.confirm(
            f"pico-hsm-daemon läuft (Socket {state.socket_path}) — "
            "paralleler Zugriff ist unsafe. Trotzdem fortfahren?"
        ):
            ctx.fail("Abgebrochen: Daemon-Konflikt nicht bestätigt.")


@click.group()
def pin() -> None:
    """PIN-Verwaltung.

    WICHTIG (Korrektur): python-pkcs11 bietet KEINE PIN-Management-API
    (kein set_pin/unlock_user_pin in der Session-Klasse). Diese Gruppe
    nutzt daher pkcs11-tool (OpenSC), das C_SetPIN/C_InitPIN direkt
    exponiert. Verifiziert gegen die offizielle pkcs11-tool-Manpage und
    OpenSC-Wiki (SmartCardHSM-Seite).
    """


@pin.command("change")
@pass_ctx
def change(ctx: CliContext) -> None:
    """User-PIN ändern (--change-pin, erfordert aktuelle User-PIN)."""
    _check_daemon_guard(ctx)
    old_pin = ctx.get_pin("Aktuelle User-PIN: ")
    new_pin = getpass("Neue User-PIN: ")
    confirm_pin = getpass("Neue User-PIN bestätigen: ")
    if new_pin != confirm_pin:
        ctx.fail("Neue PINs stimmen nicht überein.")
        return

    result = _run_pkcs11_tool(ctx, [
        "--login", "--pin", old_pin,
        "--change-pin", "--new-pin", new_pin,
    ])
    if result.returncode != 0:
        ctx.fail(result.stderr.strip() or "PIN-Änderung fehlgeschlagen.", exit_code=2)
        return
    ctx.emit_json({"status": "ok"})
    ctx.echo("✓ PIN erfolgreich geändert.")


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
    _check_daemon_guard(ctx)
    import os
    so_pin = os.environ.get(puk_env) if puk_env else None
    if not so_pin:
        so_pin = getpass("SO-PIN: ")
    new_pin = getpass("Neue User-PIN: ")
    confirm_pin = getpass("Neue User-PIN bestätigen: ")
    if new_pin != confirm_pin:
        ctx.fail("Neue PINs stimmen nicht überein.")
        return

    result = _run_pkcs11_tool(ctx, [
        "--login", "--login-type", "so", "--so-pin", so_pin,
        "--init-pin", "--new-pin", new_pin,
    ])
    if result.returncode != 0:
        ctx.fail(result.stderr.strip() or "PIN-Unblock fehlgeschlagen.", exit_code=2)
        return
    ctx.emit_json({"status": "ok"})
    ctx.echo("✓ PIN zurückgesetzt.")


@pin.command("status")
@pass_ctx
def pin_status(ctx: CliContext) -> None:
    """PIN-Status über die Token-Flags (TokenFlag-Bitmaske).

    Korrektur: es gibt keine token.get_token_info() in python-pkcs11 —
    der Status steckt in token.flags als TokenFlag-Bitmaske.
    """
    try:
        token = get_token(ctx.pkcs11_lib)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Kein Gerät erkannt: {exc}", exit_code=2)
        return

    flags = token.flags
    payload = {
        "login_required": bool(flags & TokenFlag.LOGIN_REQUIRED),
        "user_pin_initialized": bool(flags & TokenFlag.USER_PIN_INITIALIZED),
        "user_pin_count_low": bool(flags & TokenFlag.USER_PIN_COUNT_LOW),
        "user_pin_final_try": bool(flags & TokenFlag.USER_PIN_FINAL_TRY),
        "user_pin_locked": bool(flags & TokenFlag.USER_PIN_LOCKED),
        "user_pin_to_be_changed": bool(flags & TokenFlag.USER_PIN_TO_BE_CHANGED),
    }
    ctx.emit_json(payload)
    for key, value in payload.items():
        ctx.echo(f"  {key}: {value}")
