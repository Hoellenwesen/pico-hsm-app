from __future__ import annotations

from typing import Any

import click

from pico_hsm_tools import gateway_status
from pico_hsm_tools.pkcs11_session import get_token

from ..context import CliContext, pass_ctx


@click.group()
def status() -> None:
    """Read-only Statusabfragen (kein PKCS#11-Lock)."""


def _device_payload(ctx: CliContext) -> dict[str, Any]:
    """Geräte-Payload bauen (wirft bei fehlendem Gerät — Aufrufer mappen)."""
    token = get_token(ctx.pkcs11_lib)
    return {
        "label": token.label,
        "model": token.model,
        "serial": token.serial,
    }


def _gateway_payload(ctx: CliContext) -> dict[str, Any]:
    state = gateway_status.check_gateway_reachable(ctx.gateway_host, ctx.gateway_port)
    return {
        "reachable": state.reachable,
        "host": state.host,
        "port": state.port,
    }


def _audit_payload(limit: int) -> dict[str, Any]:
    return {
        "chain_intact": gateway_status.fc.verify_audit_chain(),
        "entries": [e.__dict__ for e in gateway_status.tail_flash_audit_log(limit)],
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


@status.command("gateway")
@pass_ctx
def gateway_cmd(ctx: CliContext) -> None:
    """Ist das HSM-API-Gateway (mTLS-Netzwerkdienst) erreichbar?

    Ersetzt das frühere `status daemon`, das den inzwischen abgelösten
    pico-hsm-daemon (Unix-Socket) abfragte — siehe
    pico-hsm-api-connector/MIGRATION.md. Rein informativ: TCP-Connect,
    kein TLS-Handshake, keine authentifizierte Anfrage (siehe
    pico_hsm_tools.pkcs11_session.check_gateway_reachable).
    """
    payload = _gateway_payload(ctx)
    ctx.emit_json(payload)
    if payload["reachable"]:
        ctx.echo(f"[OK] Gateway unter {payload['host']}:{payload['port']} erreichbar.")
    elif payload["host"]:
        ctx.echo(f"[INFO] Gateway unter {payload['host']}:{payload['port']} nicht erreichbar.")
    else:
        ctx.echo(
            "[INFO] Keine Gateway-Adresse konfiguriert "
            "(--gateway-host/--gateway-port)."
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
    """Kombination aus device + gateway + audit.

    Im --json-Modus ein einzelnes, gültiges Dokument (statt drei
    Fragmenten aus drei invoke-Aufrufen).
    """
    ctx: CliContext = click_ctx.obj
    if not ctx.json_output:
        click_ctx.invoke(device)
        click_ctx.invoke(gateway_cmd)
        click_ctx.invoke(audit, limit=limit)
        return
    try:
        combined = {
            "device": _device_payload(ctx),
            "gateway": _gateway_payload(ctx),
            "audit": _audit_payload(limit),
        }
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Statusabfrage fehlgeschlagen: {exc}", exit_code=2)
        return
    ctx.emit_json(combined)
