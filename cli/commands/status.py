from __future__ import annotations

import click

from pico_hsm_tools import daemon_status
from pico_hsm_tools.pkcs11_session import get_token

from ..context import CliContext, pass_ctx


@click.group()
def status() -> None:
    """Read-only Statusabfragen (kein PKCS#11-Lock)."""


@status.command("device")
@pass_ctx
def device(ctx: CliContext) -> None:
    """Geräteerkennung, Firmware-Version, Verbindungsmodus."""
    try:
        token = get_token(ctx.pkcs11_lib)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Kein Gerät erkannt oder PKCS#11-Fehler: {exc}")
        return
    payload = {
        "label": token.label,
        "model": token.model,
        "serial": token.serial,
    }
    ctx.emit_json(payload)
    ctx.echo(
        f"Erkannt: {payload['label']} "
        f"(Modell: {payload['model']}, Seriennummer: {payload['serial']})"
    )


@status.command("daemon")
@pass_ctx
def daemon_cmd(ctx: CliContext) -> None:
    """Ist pico-hsm-daemon aktuell erreichbar?"""
    state = daemon_status.check_daemon_running()
    ctx.emit_json({"running": state.running, "socket_path": str(state.socket_path)})
    if state.running:
        ctx.echo(
            "🟢 Daemon läuft — Vorsicht bei schreibenden Operationen "
            "(paralleler PKCS#11-Zugriff ist unsafe)."
        )
    else:
        ctx.echo("⚪ Daemon nicht erreichbar / gestoppt.")


@status.command("audit")
@click.option("-n", "--limit", default=20, help="Anzahl anzuzeigender Einträge.")
@pass_ctx
def audit(ctx: CliContext, limit: int) -> None:
    """Hash-Chain-Integrität + letzte Firmware-Update-Audit-Einträge."""
    intact = daemon_status.fc.verify_audit_chain()
    entries = daemon_status.tail_flash_audit_log(limit)
    ctx.emit_json({
        "chain_intact": intact,
        "entries": [e.__dict__ for e in entries],
    })
    if intact:
        ctx.echo("✓ Hash-Chain intakt.")
    else:
        ctx.echo("⚠ Hash-Chain GEBROCHEN — Log möglicherweise verändert.")
    for e in entries:
        ctx.echo(f"  {e.timestamp}  {e.status}  {e.details}")


@status.command("all")
@click.pass_context
def all_cmd(click_ctx: click.Context) -> None:
    """Kombination aus device + daemon + audit."""
    click_ctx.invoke(device)
    click_ctx.invoke(daemon_cmd)
    click_ctx.invoke(audit)
