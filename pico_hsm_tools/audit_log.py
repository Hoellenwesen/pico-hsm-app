"""
audit_log.py — read-only Zugriff auf das Firmware-Update-Audit-Log.

Lokales Lesen der JSONL-Datei (`~/.pico_hsm/update_audit.jsonl`),
damit Logs-Tab (und früher Status-Tab) sie jederzeit anzeigen können —
auch während anderswo exklusive PKCS#11-Zugriffe laufen, ohne selbst
eine Session zu belegen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import flash_core as fc


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


def audit_chain_intact() -> bool:
    """Hash-Chain-Status (True auch bei fehlender Datei)."""
    return fc.verify_audit_chain()
