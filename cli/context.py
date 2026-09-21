"""
context.py — gemeinsamer State/Helper für alle CLI-Befehle.

Zentralisiert:
  - PIN-Eingabe (interaktiv per getpass, oder aus Umgebungsvariable)
  - Global Flags (--pkcs11-lib, --json, --force, -v)
  - Einheitliche Exit-Code-Konvention:
      0 = OK
      1 = erwarteter/abgelehnter Fall (z.B. Signaturprüfung fehlgeschlagen)
      2 = unerwarteter Fehler
"""

from __future__ import annotations

import json as json_module
import os
import sys
from dataclasses import dataclass, field
from getpass import getpass
from typing import Any, Optional

import click


@dataclass
class CliContext:
    pkcs11_lib: Optional[str] = None
    json_output: bool = False
    force: bool = False
    verbose: bool = False
    pin_env: Optional[str] = None
    serial: Optional[str] = None
    reader: Optional[str] = None
    _pin_cache: Optional[str] = field(default=None, repr=False)

    def get_pin(self, prompt: str = "HSM User-PIN: ") -> str:
        if self._pin_cache is not None:
            return self._pin_cache
        if self.pin_env:
            value = os.environ.get(self.pin_env)
            if not value:
                self.fail(
                    f"Umgebungsvariable {self.pin_env} ist leer oder nicht "
                    "gesetzt."
                )
            self._pin_cache = value
            return value
        pin = self._read_pin_interactive(prompt)
        if not pin:
            self.fail("Keine PIN eingegeben.")
        self._pin_cache = pin
        return pin

    def _read_pin_interactive(self, prompt: str) -> str:
        """PIN interaktiv lesen — mit Hang-Schutz (F4).

        getpass liest unter Windows direkt von der Konsole (msvcrt),
        nicht von stdin: Ohne TTY (Skript/Pipe/CI) und ohne --pin-env
        würde es endlos hängen statt zu scheitern. Daher vorher
        abbrechen mit klarem Hinweis.
        """
        if not sys.stdin.isatty():
            self.fail(
                "Kein interaktives Terminal für die PIN-Eingabe — bitte "
                "--pin-env <VAR> mit Umgebungsvariable nutzen."
            )
            raise  # unreachable, ctx.fail() beendet den Prozess
        return getpass(prompt)

    def echo(self, message: str) -> None:
        if not self.json_output:
            click.echo(message)

    def emit_json(self, payload: dict[str, Any]) -> None:
        if self.json_output:
            click.echo(json_module.dumps(payload, indent=2, default=str))

    def log_verbose(self, message: str) -> None:
        if self.verbose:
            click.echo(f"[debug] {message}", err=True)

    def fail(self, message: str, exit_code: int = 1) -> None:
        """Erwarteter Fehlerfall: klare Meldung, definierter Exit-Code.
        JSON-Modus gibt strukturierten Fehler statt Klartext aus."""
        if self.json_output:
            click.echo(json_module.dumps({"error": message}), err=True)
        else:
            click.echo(f"FEHLER: {message}", err=True)
        sys.exit(exit_code)

    def confirm(self, question: str) -> bool:
        if self.force:
            return True
        return click.confirm(question, default=False)


pass_ctx = click.make_pass_decorator(CliContext)
