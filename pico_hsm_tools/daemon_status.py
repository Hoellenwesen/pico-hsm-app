"""
daemon_status.py — read-only Monitoring für den Status-Tab.

Bewusst ohne jegliche RPC-Calls gegen den Daemon: nur Socket-Erreichbarkeit
und lokales Lesen der JSONL-Audit-Logs. So kann der Status-Tab jederzeit
angezeigt werden, auch während PIN/DKEK/Key-Operationen exklusiv laufen,
ohne selbst eine PKCS#11-Session zu belegen oder den Daemon zu stören.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import flash_core as fc
from .pkcs11_session import DaemonState, check_daemon_running


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
    daemon: DaemonState
    audit_chain_intact: bool
    recent_flash_events: list[AuditEntry]


def get_status_snapshot() -> StatusSnapshot:
    return StatusSnapshot(
        daemon=check_daemon_running(),
        audit_chain_intact=fc.verify_audit_chain(),
        recent_flash_events=tail_flash_audit_log(),
    )
