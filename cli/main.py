#!/usr/bin/env python3
"""
pico-hsm-cli — Konfigurations-CLI für den Pico-HSM-Teil des PicoHSM-Projekts.

Nutzung:
  pico-hsm-cli status all
  pico-hsm-cli firmware preflight firmware.uf2
  pico-hsm-cli backup list ~/backups
  pico-hsm-cli --help
"""

from __future__ import annotations

import click

from .commands.backup import backup
from .commands.dkek import dkek
from .commands.firmware import firmware
from .commands.init import init
from .commands.keys import keys
from .commands.pin import pin
from .commands.setup import setup
from .commands.status import status
from .context import CliContext


@click.group()
@click.option("--pkcs11-lib", default=None, help="Pfad zur PKCS#11-Library (Override).")
@click.option("--json", "json_output", is_flag=True, help="Maschinenlesbare JSON-Ausgabe.")
@click.option("--force", is_flag=True, help="Bestätigungen überspringen.")
@click.option("--pin-env", default=None, help="Umgebungsvariable mit der HSM-PIN.")
@click.option(
    "--serial", default=None,
    help="Token-Seriennummer (bei mehreren Tokens wählen, siehe Status).",
)
@click.option(
    "--reader", default=None,
    help="PC/SC-Reader-Name für APDU-Zugriff (`setup dynamic-options`).",
)
@click.option("-v", "--verbose", is_flag=True, help="Debug-Ausgaben auf stderr.")
@click.pass_context
def cli(
    click_ctx: click.Context,
    pkcs11_lib: str | None,
    json_output: bool,
    force: bool,
    pin_env: str | None,
    serial: str | None,
    reader: str | None,
    verbose: bool,
) -> None:
    """pico-hsm-cli — Setup, PIN/DKEK, Schlüssel, Backup, Firmware."""
    click_ctx.obj = CliContext(
        pkcs11_lib=pkcs11_lib,
        json_output=json_output,
        force=force,
        pin_env=pin_env,
        serial=serial,
        reader=reader,
        verbose=verbose,
    )


cli.add_command(status)
cli.add_command(init)
cli.add_command(setup)
cli.add_command(pin)
cli.add_command(dkek)
cli.add_command(keys)
cli.add_command(backup)
cli.add_command(firmware)


if __name__ == "__main__":
    cli()
