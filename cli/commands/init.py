from __future__ import annotations

import subprocess
from getpass import getpass

import click

from pico_hsm_tools.pkcs11_session import check_daemon_running

from ..context import CliContext, pass_ctx


def _check_daemon_guard(ctx: CliContext) -> None:
    state = check_daemon_running()
    if state.running and not ctx.force:
        if not ctx.confirm(
            f"pico-hsm-daemon läuft (Socket {state.socket_path}) — "
            "paralleler Zugriff ist unsafe. Trotzdem fortfahren?"
        ):
            ctx.fail("Abgebrochen: Daemon-Konflikt nicht bestätigt.")


@click.group()
def init() -> None:
    """Erstinitialisierung des Pico HSM (SmartCard-HSM/PKCS#15-Ebene).

    Getrennt von `setup` (das behandelt nur die RP2350-OTP-Flags wie
    Secure Boot/Anti-Rollback). Diese Gruppe initialisiert die Token-
    Ebene: SO-PIN, User-PIN und optional die Anzahl der DKEK-Shares
    (siehe `dkek create-share`/`dkek import-share` für die Shares selbst).

    Nutzt `sc-hsm-tool --initialize`, verifiziert gegen die offizielle
    OpenSC-Manpage.
    """


@init.command("token")
@click.option(
    "--so-pin-env", default=None,
    help="Umgebungsvariable mit dem SO-PIN (16 Hex-Zeichen), statt Prompt.",
)
@click.option(
    "--pin-env", "user_pin_env", default=None,
    help="Umgebungsvariable mit der initialen User-PIN, statt Prompt.",
)
@click.option(
    "--dkek-shares", "-s", type=int, default=None,
    help=(
        "Anzahl DKEK-Shares für Key-Wrap/Unwrap (0 = zufälliger, nicht "
        "exportierbarer DKEK; weglassen = DKEK komplett deaktiviert)."
    ),
)
@click.option("--pin-retry", type=int, default=None, help="Max. Anzahl Fehlversuche vor PIN-Sperre.")
@click.option("--label", default=None, help="Token-Label.")
@pass_ctx
def token(
    ctx: CliContext,
    so_pin_env: str | None,
    user_pin_env: str | None,
    dkek_shares: int | None,
    pin_retry: int | None,
    label: str | None,
) -> None:
    """Token initialisieren — LÖSCHT alle vorhandenen Keys/Zertifikate/Dateien.

    Danach, falls --dkek-shares > 0 gesetzt: `dkek create-share` pro
    Custodian ausführen und mit `dkek import-share` einspielen, bis alle
    Shares geladen sind (siehe `dkek status`).
    """
    ctx.echo(
        "⚠ ACHTUNG: Diese Operation löscht ALLE vorhandenen Keys, "
        "Zertifikate und Dateien auf dem Token unwiderruflich."
    )
    if not ctx.confirm("Wirklich initialisieren?"):
        ctx.fail("Abgebrochen.")
        return

    _check_daemon_guard(ctx)

    import os
    so_pin = os.environ.get(so_pin_env) if so_pin_env else None
    if not so_pin:
        so_pin = getpass("SO-PIN (16 Hex-Zeichen): ")
    user_pin = os.environ.get(user_pin_env) if user_pin_env else None
    if not user_pin:
        user_pin = getpass("Initiale User-PIN: ")
        confirm_pin = getpass("Initiale User-PIN bestätigen: ")
        if user_pin != confirm_pin:
            ctx.fail("PINs stimmen nicht überein.")
            return

    args = ["sc-hsm-tool", "--initialize", "--so-pin", so_pin, "--pin", user_pin]
    if dkek_shares is not None:
        args += ["--dkek-shares", str(dkek_shares)]
    if pin_retry is not None:
        args += ["--pin-retry", str(pin_retry)]
    if label:
        args += ["--label", label]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        ctx.fail("sc-hsm-tool nicht gefunden (Teil von OpenSC).")
        return
    if result.returncode != 0:
        ctx.fail(result.stderr.strip() or "Initialisierung fehlgeschlagen.", exit_code=2)
        return

    ctx.emit_json({"status": "ok", "dkek_shares": dkek_shares})
    ctx.echo("✓ Token initialisiert.")
    if dkek_shares:
        ctx.echo(
            f"  {dkek_shares} DKEK-Share(s) konfiguriert — jetzt "
            "`dkek create-share` pro Custodian ausführen und mit "
            "`dkek import-share` einspielen (`dkek status` zeigt den "
            "Fortschritt)."
        )
