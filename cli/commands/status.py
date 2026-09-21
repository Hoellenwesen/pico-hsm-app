from __future__ import annotations

from typing import Any

import click

from pico_hsm_tools import audit_log
from pico_hsm_tools.pkcs11_session import format_token_serial, get_token

from ..context import CliContext, pass_ctx


@click.group()
def status() -> None:
    """Read-only Statusabfragen (kein PKCS#11-Lock)."""


def _device_payload(ctx: CliContext) -> dict[str, Any]:
    """Geräte-Payload bauen (wirft bei fehlendem Gerät — Aufrufer mappen)."""
    token = get_token(ctx.pkcs11_lib, serial=ctx.serial)
    return {
        "label": token.label,
        "model": token.model,
        "serial": format_token_serial(token.serial),
    }


def _audit_payload(limit: int) -> dict[str, Any]:
    return {
        "chain_intact": audit_log.audit_chain_intact(),
        "entries": [e.__dict__ for e in audit_log.tail_flash_audit_log(limit)],
    }


@status.command("device")
@pass_ctx
def device(ctx: CliContext) -> None:
    """Geräteerkennung, Firmware-Version, Verbindungsmodus."""
    try:
        payload = _device_payload(ctx)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Kein Gerät erkannt oder PKCS#11-Fehler: {exc}")
        return
    ctx.emit_json(payload)
    ctx.echo(
        f"Erkannt: {payload['label']} "
        f"(Modell: {payload['model']}, Seriennummer: {payload['serial']})"
    )


@status.command("audit")
@click.option("-n", "--limit", default=20, help="Anzahl anzuzeigender Einträge.")
@pass_ctx
def audit(ctx: CliContext, limit: int) -> None:
    """Hash-Chain-Integrität + letzte Firmware-Update-Audit-Einträge."""
    payload = _audit_payload(limit)
    intact = payload["chain_intact"]
    entries = payload["entries"]
    ctx.emit_json(payload)
    if intact:
        ctx.echo("[OK] Hash-Chain intakt.")
    else:
        ctx.echo("[WARN] Hash-Chain GEBROCHEN — Log möglicherweise verändert.")
    for e in entries:
        ctx.echo(f"  {e['timestamp']}  {e['status']}  {e['details']}")


@status.command("all")
@click.option("-n", "--limit", default=20, help="Anzahl anzuzeigender Audit-Einträge.")
@click.pass_context
def all_cmd(click_ctx: click.Context, limit: int) -> None:
    """Kombination aus device + audit.

    Im --json-Modus ein einzelnes, gültiges Dokument (statt zwei
    Fragmenten aus zwei invoke-Aufrufen).
    """
    ctx: CliContext = click_ctx.obj
    if not ctx.json_output:
        click_ctx.invoke(device)
        click_ctx.invoke(audit, limit=limit)
        return
    try:
        combined = {
            "device": _device_payload(ctx),
            "audit": _audit_payload(limit),
        }
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Statusabfrage fehlgeschlagen: {exc}", exit_code=2)
        return
    ctx.emit_json(combined)
