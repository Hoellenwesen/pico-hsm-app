from __future__ import annotations

from pathlib import Path

import click

from pico_hsm_tools import flash_core as fc

from ..context import CliContext, pass_ctx

TOTP_SECRET_FILE = Path.home() / ".pico_hsm" / "totp_secret.txt"


@click.group()
def firmware() -> None:
    """Firmware-Update: Preflight-Checks, verifiziertes Flashen, Audit-Log.

    Nutzt ausschließlich pico_hsm_tools/flash_core.py — dieselbe Logik
    wie das eigenständige verify_and_flash.py-Skript. Keine doppelte
    Implementierung der Sicherheitschecks.
    """


@firmware.command("preflight")
@click.argument("uf2_file", type=click.Path(exists=True))
@pass_ctx
def preflight(ctx: CliContext, uf2_file: str) -> None:
    """Signatur, Board-Fingerprint, Rollback prüfen — flasht NICHT."""
    if not fc.verify_audit_chain():
        ctx.echo("⚠ Audit-Log-Hash-Chain ist gebrochen — Log möglicherweise verändert.")
        if not ctx.confirm("Trotzdem fortfahren?"):
            ctx.fail("Abgebrochen.")
            return

    try:
        result = fc.run_preflight(Path(uf2_file), fc.KNOWN_PUBKEY_FINGERPRINT)
    except fc.FlashError as exc:
        ctx.fail(str(exc))
        return

    payload = {
        "sha256": result.sha256,
        "version": f"{result.version.major}.{result.version.minor}",
        "rollback": result.version.rollback,
        "board_fingerprint": result.board_fingerprint,
    }
    ctx.emit_json(payload)
    ctx.echo(
        f"✓ Signatur gültig · Board-Fingerprint stimmt · "
        f"Version {payload['version']} (rollback={payload['rollback']}) · "
        f"SHA-256: {payload['sha256']}"
    )


@firmware.command("flash")
@click.argument("uf2_file", type=click.Path(exists=True))
@pass_ctx
def flash(ctx: CliContext, uf2_file: str) -> None:
    """Preflight + TOTP-Autorisierung (falls konfiguriert) + Flash + Audit-Log."""
    if not fc.verify_audit_chain():
        ctx.echo("⚠ Audit-Log-Hash-Chain ist gebrochen — Log möglicherweise verändert.")
        if not ctx.confirm("Trotzdem fortfahren?"):
            ctx.fail("Abgebrochen.")
            return

    try:
        result = fc.run_preflight(Path(uf2_file), fc.KNOWN_PUBKEY_FINGERPRINT)
    except fc.FlashError as exc:
        ctx.fail(str(exc))
        return

    ctx.echo(
        f"✓ Vorab-Prüfungen bestanden: Version "
        f"{result.version.major}.{result.version.minor} "
        f"(rollback={result.version.rollback})"
    )

    if TOTP_SECRET_FILE.exists():
        secret = TOTP_SECRET_FILE.read_text().strip()
        code = click.prompt("TOTP-Code vom getrennten Gerät", hide_input=False)
        if not fc.verify_totp_code(secret, code):
            fc.append_audit({
                "file": str(result.fw_path), "sha256": result.sha256,
                "status": "rejected_totp",
            })
            ctx.fail("TOTP-Code ungültig oder abgelaufen.")
            return
        ctx.echo("✓ TOTP-Autorisierung bestätigt.")
    else:
        ctx.echo(
            f"Hinweis: keine TOTP-Secret-Datei ({TOTP_SECRET_FILE}) — "
            "Autorisierungsschicht übersprungen."
        )

    if not ctx.confirm(
        "Board jetzt in BOOTSEL-Modus versetzen und mit Flashen fortfahren?"
    ):
        ctx.fail("Abgebrochen.", exit_code=0)
        return

    try:
        fc.do_flash(result, on_progress=ctx.echo)
    except fc.FlashError as exc:
        ctx.fail(str(exc), exit_code=2)
        return

    ctx.emit_json({"status": "flashed", "sha256": result.sha256})
    ctx.echo("✓ Firmware erfolgreich geflasht.")


@firmware.group("audit")
def audit_group() -> None:
    """Audit-Log-Operationen (Hash-Chain über flash_core.AUDIT_LOG)."""


@audit_group.command("tail")
@click.option("-n", "--limit", default=20)
@pass_ctx
def audit_tail(ctx: CliContext, limit: int) -> None:
    """Letzte N Audit-Log-Einträge."""
    if not fc.AUDIT_LOG.exists():
        ctx.echo("Kein Audit-Log vorhanden.")
        return
    lines = [ln for ln in fc.AUDIT_LOG.read_text().splitlines() if ln.strip()]
    import json
    entries = [json.loads(ln) for ln in lines[-limit:]]
    ctx.emit_json({"entries": entries})
    for e in entries:
        ctx.echo(f"  {e.get('timestamp')}  {e.get('status')}")


@audit_group.command("verify")
@pass_ctx
def audit_verify(ctx: CliContext) -> None:
    """Hash-Chain-Integritätscheck als eigenständiger Befehl (z.B. für
    einen Wazuh-Cronjob: Exit-Code 1 bei gebrochener Kette)."""
    intact = fc.verify_audit_chain()
    ctx.emit_json({"chain_intact": intact})
    if intact:
        ctx.echo("✓ Hash-Chain intakt.")
    else:
        ctx.fail("Hash-Chain GEBROCHEN.", exit_code=1)
