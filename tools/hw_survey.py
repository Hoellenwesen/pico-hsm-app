#!/usr/bin/env python3
"""hw_survey.py - Bestandsaufnahme am Board (Phase 1, nur lesend).

Fragt ab, schreibt nichts, braucht keine PIN: PC/SC-Reader + ATR,
opensc-tool/pkcs11-tool/pkcs15-tool/sc-hsm-tool-Ausgaben. Fehlende
Binaries werden gemeldet statt abzustürzen. Ausgabe auf stdout
(Umleitung in hw-logs/ empfohlen, z.B. `... > hw-logs/01-survey.txt`).

Aufruf:  python tools/hw_survey.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run(tool: str, args: list[str], timeout: int = 15) -> str:
    """Tool aufrufen; immer String zurück (Fehler als Text, nie Exception)."""
    if shutil.which(tool) is None:
        return f"{tool}: NICHT GEFUNDEN (nicht installiert/nicht im PATH)"
    try:
        result = subprocess.run(
            [tool, *args], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{tool} {' '.join(args)}: TIMEOUT nach {timeout}s"
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    status = f"[exit {result.returncode}]"
    if out:
        return f"{tool} {' '.join(args)} {status}\n{out}"
    return f"{tool} {' '.join(args)} {status} (keine stdout, stderr: {err or '-'})"


def _pcsc_section() -> str:
    """Reader + ATR via pyscard (nur verbinden, keine PIN, keine Writes)."""
    lines = ["### PC/SC (pyscard)"]
    try:
        from smartcard.System import readers
    except ImportError:
        return "\n".join(lines + ["pyscard nicht installiert"])
    found = readers()
    if not found:
        return "\n".join(lines + ["keine Reader gefunden"])
    for reader in found:
        lines.append(f"Reader: {reader}")
        try:
            connection = reader.createConnection()
            connection.connect()
            try:
                atr = bytes(connection.getATR()).hex(" ").upper()
            finally:
                connection.disconnect()
            lines.append(f"  ATR: {atr}")
        except Exception as exc:  # noqa: BLE001 - z.B. keine Karte
            lines.append(f"  keine Karte/kein ATR ({exc})")
    return "\n".join(lines)


def main() -> int:
    print("=== PicoHSM Hardware-Survey (read-only) ===")
    print()
    print(_pcsc_section())
    print()
    print("### opensc-tool --list-readers")
    print(_run("opensc-tool", ["--list-readers"]))
    print()
    print("### pkcs11-tool --list-slots")
    print(_run("pkcs11-tool", ["--list-slots"]))
    print()
    print("### pkcs15-tool -D (ohne Login)")
    print(_run("pkcs15-tool", ["-D"]))
    print()
    print("### sc-hsm-tool (Status, ohne Argumente)")
    print(_run("sc-hsm-tool", []))
    print()
    print("=== Ende (manuell ergänzen: Firmware-Version, OTP-Dump aus BOOTSEL) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
