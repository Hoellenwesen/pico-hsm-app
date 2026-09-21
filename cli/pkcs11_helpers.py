"""
pkcs11_helpers.py — CLI-spezifische Kopplung an pico_hsm_tools.pkcs11_session.

Bündelt für die CLI-Befehle `cli_exclusive_session`/`cli_read_only_session`:
PIN aus CliContext ziehen, `SessionConflictError` sauber über `ctx.fail()`
melden (eine Quelle der Wahrheit).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pico_hsm_tools.pkcs11_session import (
    SessionConflictError, exclusive_session,
    read_only_session,
)

from .context import CliContext


@contextmanager
def cli_exclusive_session(ctx: CliContext) -> Iterator["pkcs11.Session"]:  # noqa: F821
    """Öffnet eine schreibende Session.

    Bei `SessionConflictError` (der Reader war beim tatsächlichen
    Öffnen nachweislich belegt) gibt es kein automatisches Retry/
    Override mehr — anders als beim früheren, rein heuristischen
    Daemon-Vorab-Check ist der Fehler hier bereits real aufgetreten;
    ein Bestätigungsdialog würde daran nichts ändern. Sinnvoll ist nur
    ein manueller erneuter Versuch, nachdem die Ursache geklärt ist.
    """
    pin = ctx.get_pin()
    with exclusive_session(
        pin,
        lib_path=ctx.pkcs11_lib,
        serial=ctx.serial,
    ) as session:
        yield session


@contextmanager
def cli_read_only_session(ctx: CliContext, need_pin: bool = True):
    pin = ctx.get_pin() if need_pin else None
    with read_only_session(
        user_pin=pin, lib_path=ctx.pkcs11_lib, serial=ctx.serial,
    ) as session:
        yield session
