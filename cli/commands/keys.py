from __future__ import annotations

import pathlib
import sys

import click

from pico_hsm_tools import objects_core as oc

from ..context import CliContext, pass_ctx
from ..pkcs11_helpers import cli_exclusive_session, cli_read_only_session


def _parse_id(hex_str: str, ctx: CliContext) -> bytes:
    try:
        return bytes.fromhex(hex_str)
    except ValueError:
        ctx.fail(f"Ungültige --id '{hex_str}' — erwartet Hex-String (z.B. 01, 0a1b).")
        raise  # unreachable, ctx.fail() beendet den Prozess


@click.group()
def keys() -> None:
    """Schlüssel-/Objektverwaltung.

    Bewusst KEIN generisches `keys sign`/`keys derive` — kryptografische
    Operationen im laufenden Betrieb sind Aufgabe des HSM-API-Gateways
    (pico-hsm-api-connector), nicht dieser Config-CLI (klare Trennung
    der Zuständigkeiten). Ein reiner Diagnose-Modus über das Gateway
    ist separat geplant (siehe `diagnose`-Kommandogruppe, Architektur-
    konzept §7.c).
    """


@keys.command("list")
@pass_ctx
def list_cmd(ctx: CliContext) -> None:
    """Objektliste: Label, ID, Klasse, Key-Typ (Keys UND Datenobjekte)."""
    try:
        with cli_read_only_session(ctx) as session:
            objects = oc.list_objects(session)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Konnte Objekte nicht auflisten: {exc}", exit_code=2)
        return

    rows = [{
        "label": o.label,
        "id": o.id.hex() if o.id else "",
        "class": o.object_class,
        "key_type": o.key_type or "",
    } for o in objects]
    ctx.emit_json({"objects": rows})
    for row in rows:
        ctx.echo(
            f"  {row['label']:<24} {row['id']:<12} "
            f"{row['class']:<14} {row['key_type']}"
        )
    if rows:
        ctx.echo(
            "\nHinweis: für `dkek wrap-key --key-reference` wird der "
            "numerische Key-Reference-Wert benötigt, nicht Label/ID — "
            "diesen ggf. zusätzlich mit `pkcs15-tool -D` auslesen "
            "(python-pkcs11 exponiert ihn hier noch nicht direkt)."
        )


@keys.command("delete")
@click.argument("label")
@pass_ctx
def delete_cmd(ctx: CliContext, label: str) -> None:
    """Objekt (Key ODER Datenobjekt) mit gegebenem Label unwiderruflich
    löschen (mit Bestätigung, außer --force)."""
    if not ctx.confirm(f"Objekt '{label}' unwiderruflich löschen?"):
        ctx.fail("Abgebrochen.")
        return

    try:
        with cli_exclusive_session(ctx) as session:
            found = oc.delete_object(session, label)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Löschen fehlgeschlagen: {exc}", exit_code=2)
        return

    if not found:
        ctx.fail(f"Kein Objekt mit Label '{label}' gefunden.")
        return

    ctx.emit_json({"status": "ok", "label": label})
    ctx.echo(f"[OK] Objekt '{label}' gelöscht.")


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
    ctx: CliContext = click_ctx.obj
    hint = (
        "Kein separater Roh-Import — bitte `pico-hsm-cli dkek unwrap-key "
        "<datei> --key-reference <ref>` verwenden (siehe "
        "docs.picokeys.com/picohsm, backup-and-restore.md)."
    )
    ctx.emit_json({"status": "ok", "hint": "dkek unwrap-key", "detail": hint})
    ctx.echo(hint)


@keys.command("generate")
@click.option(
    "--type", "key_type", required=True,
    type=click.Choice(["rsa", "ec"]), help="Schlüsseltyp.",
)
@click.option(
    "--bits", type=click.Choice([str(b) for b in oc.RSA_KEY_LENGTHS_BITS]),
    help="RSA-Schlüssellänge in Bit (nur mit --type rsa).",
)
@click.option(
    "--curve", type=click.Choice(sorted(oc.EC_CURVES)),
    help="EC-Kurve (nur mit --type ec).",
)
@click.option("--id", "obj_id", required=True, help="Objekt-ID als Hex-String (z.B. 01).")
@click.option("--label", required=True, help="Objekt-Label.")
@pass_ctx
def generate(
    ctx: CliContext,
    key_type: str,
    bits: str | None,
    curve: str | None,
    obj_id: str,
    label: str,
) -> None:
    """RSA- oder EC-Keypair auf dem Gerät erzeugen (Private Key verlässt
    das HSM nie, intern verschlüsselt gespeichert).

    ACHTUNG Laufzeit (am Board gemessen, Doku unzuverlässig):
    RSA-2048 ca. 2:45 Minuten, RSA-4096 ca. 15:00 Minuten —
    die CLI blockiert währenddessen.
    """
    if key_type == "rsa" and not bits:
        ctx.fail("--bits ist bei --type rsa erforderlich.")
        return
    if key_type == "ec" and not curve:
        ctx.fail("--curve ist bei --type ec erforderlich.")
        return

    id_bytes = _parse_id(obj_id, ctx)

    try:
        if key_type == "rsa":
            bits_int = int(bits)
            warning = oc.RSA_SLOW_WARNING_BITS.get(bits_int)
            if warning:
                ctx.echo(f"[INFO] RSA-{bits_int}-Erzeugung {warning} — bitte warten.")
            with cli_exclusive_session(ctx) as session:
                info = oc.generate_rsa_keypair(session, bits_int, id_bytes, label)
        else:
            with cli_exclusive_session(ctx) as session:
                info = oc.generate_ec_keypair(session, curve, id_bytes, label)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Keypair-Erzeugung fehlgeschlagen: {exc}", exit_code=2)
        return

    ctx.emit_json({"status": "ok", "label": info.label, "key_type": info.key_type})
    ctx.echo(f"[OK] {info.key_type}-Keypair '{label}' erzeugt (ID {obj_id}).")


@keys.command("generate-aes")
@click.option(
    "--bits", required=True, type=click.Choice(["128", "192", "256"]),
    help="AES-Schlüssellänge in Bit.",
)
@click.option("--id", "obj_id", required=True, help="Objekt-ID als Hex-String.")
@click.option("--label", required=True, help="Objekt-Label.")
@pass_ctx
def generate_aes(ctx: CliContext, bits: str, obj_id: str, label: str) -> None:
    """AES-Secret-Key auf dem Gerät erzeugen."""
    id_bytes = _parse_id(obj_id, ctx)
    num_bytes = int(bits) // 8
    try:
        with cli_exclusive_session(ctx) as session:
            info = oc.generate_aes_key(session, num_bytes, id_bytes, label)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"AES-Key-Erzeugung fehlgeschlagen: {exc}", exit_code=2)
        return
    ctx.emit_json({
        "status": "ok", "label": info.label, "key_length_bits": info.key_length_bits,
    })
    ctx.echo(f"[OK] AES-{bits}-Key '{label}' erzeugt.")


@keys.command("write-object")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--label", required=True, help="Objekt-Label.")
@click.option("--id", "obj_id", default=None, help="Objekt-ID als Hex-String (optional).")
@click.option(
    "--not-private", is_flag=True,
    help=(
        "Ohne PIN lesbar ablegen (Default: PIN-geschützt — siehe "
        "Sicherheitsbetrachtung im Architekturkonzept, §9)."
    ),
)
@pass_ctx
def write_object(
    ctx: CliContext, file: str, label: str, obj_id: str | None, not_private: bool,
) -> None:
    """Beliebige Datei (z.B. Zertifikat in DER-Form, max. 4096 Byte) als
    Datenobjekt auf dem Gerät ablegen."""
    data = pathlib.Path(file).read_bytes()
    id_bytes = _parse_id(obj_id, ctx) if obj_id else None
    try:
        with cli_exclusive_session(ctx) as session:
            info = oc.write_data_object(
                session, data, label, id=id_bytes, private=not not_private,
            )
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Datenobjekt-Erstellung fehlgeschlagen: {exc}", exit_code=2)
        return
    ctx.emit_json({"status": "ok", "label": info.label, "bytes": len(data)})
    protection = "öffentlich lesbar" if not_private else "PIN-geschützt"
    ctx.echo(f"[OK] Datenobjekt '{label}' geschrieben ({len(data)} Byte, {protection}).")


@keys.command("read-object")
@click.option("--label", required=True, help="Objekt-Label.")
@click.option(
    "--out", type=click.Path(dir_okay=False), default=None,
    help="Ausgabedatei (Default: stdout, binär).",
)
@pass_ctx
def read_object(ctx: CliContext, label: str, out: str | None) -> None:
    """Datenobjekt anhand des Labels lesen (PIN wird angefragt, falls
    das Objekt privat ist — bei öffentlichen Objekten unnötig, aber
    unschädlich)."""
    try:
        with cli_read_only_session(ctx) as session:
            data = oc.read_data_object(session, label)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Lesen fehlgeschlagen: {exc}", exit_code=2)
        return
    if out:
        pathlib.Path(out).write_bytes(data)
        ctx.emit_json({"status": "ok", "label": label, "bytes": len(data), "out": out})
        ctx.echo(f"[OK] {len(data)} Byte nach {out} geschrieben.")
    else:
        # Binärdaten roh auf stdout — kein ctx.echo (das würde eine
        # Text-Kodierung erzwingen und könnte Binärinhalte beschädigen).
        sys.stdout.buffer.write(data)


@keys.command("random")
@click.argument("num_bytes", type=int)
@pass_ctx
def random_cmd(ctx: CliContext, num_bytes: int) -> None:
    """Zufallsbytes vom HSM anfordern und als Hex ausgeben (max. 1024 Byte)."""
    try:
        with cli_read_only_session(ctx) as session:
            data = oc.generate_random(session, num_bytes)
    except Exception as exc:  # noqa: BLE001
        ctx.fail(f"Zufallszahlen-Erzeugung fehlgeschlagen: {exc}", exit_code=2)
        return
    ctx.emit_json({"status": "ok", "hex": data.hex()})
    ctx.echo(data.hex())
