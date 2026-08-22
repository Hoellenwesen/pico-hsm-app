from __future__ import annotations

import click

from ..context import CliContext, pass_ctx
from ..pkcs11_helpers import cli_exclusive_session, cli_read_only_session


@click.group()
def keys() -> None:
    """Schlüssel-/Objektverwaltung.

    Bewusst KEIN generisches `keys sign`/`keys derive` — kryptografische
    Operationen im laufenden Betrieb sind Aufgabe von pico-hsm-daemon und
    hsm-api-gateway, nicht dieser Config-CLI (klare Trennung der
    Zuständigkeiten).
    """


@keys.command("list")
@pass_ctx
def list_cmd(ctx: CliContext) -> None:
    """Objektliste: Label, Klasse, Key-Typ, Usage-Counter."""
    try:
        with cli_read_only_session(ctx) as session:
            objects = list(session.get_objects())
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Konnte Objekte nicht auflisten: {exc}", exit_code=2)
        return

    rows = []
    for obj in objects:
        rows.append({
            "label": getattr(obj, "label", "") or "",
            "class": str(getattr(obj, "object_class", "")),
            "key_type": str(getattr(obj, "key_type", "")),
        })
    ctx.emit_json({"objects": rows})
    for row in rows:
        ctx.echo(f"  {row['label']:<24} {row['class']:<18} {row['key_type']}")
    if rows:
        ctx.echo(
            "\nHinweis: für `dkek wrap-key --key-reference` wird der "
            "numerische Key-Reference-Wert benötigt, nicht das Label — "
            "diesen ggf. zusätzlich mit `pkcs15-tool -D` auslesen "
            "(python-pkcs11 exponiert ihn hier noch nicht direkt)."
        )


@keys.command("delete")
@click.argument("label")
@pass_ctx
def delete_cmd(ctx: CliContext, label: str) -> None:
    """Objekt mit gegebenem Label löschen (mit Bestätigung, außer --force)."""
    if not ctx.confirm(f"Objekt '{label}' unwiderruflich löschen?"):
        ctx.fail("Abgebrochen.")
        return

    found = False
    try:
        with cli_exclusive_session(ctx) as session:
            for obj in session.get_objects():
                if getattr(obj, "label", None) == label:
                    obj.destroy()
                    found = True
                    break
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Löschen fehlgeschlagen: {exc}", exit_code=2)
        return

    if not found:
        ctx.fail(f"Kein Objekt mit Label '{label}' gefunden.")
        return

    ctx.emit_json({"status": "ok", "label": label})
    ctx.echo(f"✓ Objekt '{label}' gelöscht.")


@keys.command("import")
@click.pass_context
def import_cmd(click_ctx: click.Context) -> None:
    """Schlüssel-Import — siehe `dkek unwrap-key`.

    Pico HSM importiert Private Keys ausschließlich über den DKEK-Wrap-
    Mechanismus (`sc-hsm-tool --unwrap-key`), es gibt keinen separaten
    PKCS#11-Rohimport-Weg. Diese Gruppe leitet deshalb bewusst auf
    `dkek unwrap-key` weiter, statt eine zweite, nicht existierende
    Import-Variante vorzutäuschen.
    """
    click.echo(
        "Kein separater Roh-Import — bitte `pico-hsm-cli dkek unwrap-key "
        "<datei> --key-reference <ref>` verwenden (siehe "
        "docs.picokeys.com/picohsm, backup-and-restore.md)."
    )
