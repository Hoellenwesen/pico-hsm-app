from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pico_hsm_tools.pkcs11_session import (
    SessionConflictError, exclusive_session, read_only_session,
)

from .context import CliContext


@contextmanager
def cli_exclusive_session(ctx: CliContext) -> Iterator["pkcs11.Session"]:  # noqa: F821
    """Öffnet eine schreibende Session. Bei Daemon-Konflikt: mit --force
    direkt fortfahren, sonst interaktiv nachfragen (außer --json, dann
    hart abbrechen, da eine interaktive Rückfrage im Skripting-Kontext
    keinen Sinn ergibt)."""
    pin = ctx.get_pin()
    try:
        with exclusive_session(pin, lib_path=ctx.pkcs11_lib) as session:
            yield session
        return
    except SessionConflictError as exc:
        if ctx.json_output and not ctx.force:
            ctx.fail(str(exc))
        if not ctx.force and not ctx.confirm(
            f"{exc}\nTrotzdem fortfahren?"
        ):
            ctx.fail("Abgebrochen: Daemon-Konflikt nicht bestätigt.")

    with exclusive_session(
        pin, lib_path=ctx.pkcs11_lib, allow_daemon_conflict=True
    ) as session:
        yield session


@contextmanager
def cli_read_only_session(ctx: CliContext, need_pin: bool = True):
    pin = ctx.get_pin() if need_pin else None
    with read_only_session(user_pin=pin, lib_path=ctx.pkcs11_lib) as session:
        yield session
