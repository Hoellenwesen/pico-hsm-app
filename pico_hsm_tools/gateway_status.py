"""
gateway_status.py — read-only Monitoring für den Status-Tab.

Vormals `daemon_status.py`: prüfte den inzwischen abgelösten lokalen
pico-hsm-daemon (Unix-Socket). Prüft jetzt stattdessen die
Erreichbarkeit des pico-hsm-api-connector-Gateways (siehe
pkcs11_session.check_gateway_reachable). Weiterhin bewusst ohne
jegliche authentifizierte Anfrage: nur ein TCP-Connect-Test und
lokales Lesen der JSONL-Audit-Logs, damit der Status-Tab jederzeit
angezeigt werden kann, auch während anderswo exklusive
PKCS#11-Zugriffe laufen, ohne selbst eine Session zu belegen oder ein
mTLS-Handshake zu machen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import flash_core as fc
from .pkcs11_session import GatewayState, check_gateway_reachable


@dataclass
class AuditEntry:
    timestamp: str
    status: str
    details: dict = field(default_factory=dict)


def tail_flash_audit_log(n: int = 20) -> list[AuditEntry]:
    """Letzte n Einträge aus dem Firmware-Update-Audit-Log."""
    if not fc.AUDIT_LOG.exists():
        return []
    lines = [ln for ln in fc.AUDIT_LOG.read_text().splitlines() if ln.strip()]
    entries = []
    for line in lines[-n:]:
        raw = json.loads(line)
        entries.append(AuditEntry(
            timestamp=raw.get("timestamp", "?"),
            status=raw.get("status", "?"),
            details={k: v for k, v in raw.items()
                     if k not in ("timestamp", "status")},
        ))
    return entries


@dataclass
class StatusSnapshot:
    gateway: GatewayState
    audit_chain_intact: bool
    recent_flash_events: list[AuditEntry]


def get_status_snapshot(
    gateway_host: Optional[str] = None,
    gateway_port: Optional[int] = None,
    audit_limit: int = 20,
) -> StatusSnapshot:
    """Gebündelter Snapshot für die GUI (Status-Tab braucht i.d.R. alle
    drei Werte auf einmal statt dreier Einzelaufrufe wie im CLI-Pfad)."""
    return StatusSnapshot(
        gateway=check_gateway_reachable(gateway_host, gateway_port),
        audit_chain_intact=fc.verify_audit_chain(),
        recent_flash_events=tail_flash_audit_log(audit_limit),
    )
