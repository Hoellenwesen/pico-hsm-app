from __future__ import annotations

import subprocess

import click

from pico_hsm_tools import dkek_core as dc

from ..context import CliContext, pass_ctx


@click.group()
def dkek() -> None:
    """DKEK-Share-Verwaltung via sc-hsm-tool (OpenSC).

    Reale CLI-Referenz: https://github.com/polhenarejos/pico-hsm/blob/master/doc/backup-and-restore.md

    Wichtig: das --pwd-shares-threshold/--pwd-shares-total-Schema von
    sc-hsm-tool splittet nur das Passwort EINES DKEK-Shares unter mehreren
    Custodians. Es ist NICHT dasselbe wie das Empfänger-Backup
    (`backup hsm-backup`, age 1-aus-n) — beide Mechanismen sind
    unabhängig voneinander und schützen unterschiedliche
    Dinge (DKEK-Passwort vs. HSM-Token-Export).
    """


@dkek.command("create-share")
@click.argument("share_file", type=click.Path())
@click.option(
    "--threshold", "-m", type=int, default=None,
    help="n-of-m-Schwelle für das Share-Passwort (optional).",
)
@click.option(
    "--total", "-n", type=int, default=None,
    help="Gesamtzahl Custodians für das Share-Passwort (optional).",
)
@pass_ctx
def create_share(ctx: CliContext, share_file: str, threshold: int | None, total: int | None) -> None:
    """Neuen DKEK-Share erzeugen (interaktiv: fragt Passwort bzw. n-of-m-
    Werte am Terminal ab — kein Pipe-Capture, damit die interaktive
    Custodian-Eingabe funktioniert)."""
    args = ["--create-dkek-share", share_file]
    if threshold is not None:
        args += ["--pwd-shares-threshold", str(threshold)]
    if total is not None:
        args += ["--pwd-shares-total", str(total)]

    ctx.log_verbose(f"sc-hsm-tool {' '.join(args)}")
    try:
        result = subprocess.run(["sc-hsm-tool", *args])  # kein Capture: interaktiv
    except FileNotFoundError:
        ctx.fail("sc-hsm-tool nicht gefunden (Teil von OpenSC).")
        return
    if result.returncode != 0:
        ctx.fail("create-dkek-share fehlgeschlagen.", exit_code=2)
        return
    ctx.echo(f"[OK] DKEK-Share nach {share_file} geschrieben.")


@dkek.command("import-share")
@click.argument("share_file", type=click.Path(exists=True))
@click.option(
    "--total", "-n", type=int, default=None,
    help="Gesamtzahl Custodians, falls Share mit n-of-m-Schema erzeugt wurde.",
)
@pass_ctx
def import_share(ctx: CliContext, share_file: str, total: int | None) -> None:
    """DKEK-Share importieren (interaktiv, Custodian-Eingabe am Terminal)."""
    args = ["--import-dkek-share", share_file]
    if total is not None:
        args += ["--pwd-shares-total", str(total)]

    try:
        result = subprocess.run(["sc-hsm-tool", *args])  # kein Capture: interaktiv
    except FileNotFoundError:
        ctx.fail("sc-hsm-tool nicht gefunden (Teil von OpenSC).")
        return
    if result.returncode != 0:
        ctx.fail("import-dkek-share fehlgeschlagen.", exit_code=2)
        return
    ctx.echo(f"[OK] DKEK-Share aus {share_file} importiert.")


@dkek.command("wrap-key")
@click.argument("out_file", type=click.Path())
@click.option("--key-reference", "-r", required=True, type=int, help="Key-Reference (aus pkcs15-tool -D).")
@pass_ctx
def wrap_key(ctx: CliContext, out_file: str, key_reference: int) -> None:
    """Einzelnen Private Key mit dem DKEK wrappen/exportieren (Key-Backup)."""
    pin = ctx.get_pin()
    try:
        dc.wrap_key(out_file, key_reference, pin)
    except dc.DkekError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok", "out_file": out_file, "key_reference": key_reference})
    ctx.echo(f"[OK] Key {key_reference} nach {out_file} exportiert (DKEK-verschlüsselt).")


@dkek.command("unwrap-key")
@click.argument("wrapped_file", type=click.Path(exists=True))
@click.option("--key-reference", "-r", required=True, type=int, help="Ziel-Key-Reference im Gerät.")
@click.option(
    "--force", is_flag=True,
    help=(
        "Belegte Reference ersetzen (reicht -f an sc-hsm-tool durch; "
        "ohne das Flag verweigert das Tool den Import auf belegte Refs)."
    ),
)
@pass_ctx
def unwrap_key(
    ctx: CliContext, wrapped_file: str, key_reference: int, force: bool,
) -> None:
    """Gewrappten Key wieder importieren (Gerät muss mit demselben DKEK
    initialisiert sein wie beim Export). Ersetzt das vorher separate
    `keys import` — das ist der reale Import-Mechanismus von Pico HSM."""
    pin = ctx.get_pin()
    try:
        dc.unwrap_key(wrapped_file, key_reference, pin, force=force)
    except dc.DkekError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok", "key_reference": key_reference})
    ctx.echo(f"[OK] Key erfolgreich als Reference {key_reference} importiert.")


@dkek.command("status")
@pass_ctx
def status_cmd(ctx: CliContext) -> None:
    """DKEK-Status (Anzahl importierter Shares, Key-Check-Value).

    `sc-hsm-tool` ohne weitere Argumente fragt den aktuellen Status ab —
    verifiziert gegen die offizielle OpenSC-Manpage ("can be used to
    query the status of a SmartCard-HSM").
    """
    try:
        output = dc.dkek_status()
    except dc.DkekError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"raw_output": output})
    ctx.echo(output)
