"""
objects_core.py — Objekt-Lifecycle für Keys, Zertifikate und Datenobjekte.

EINZIGE Quelle der Wahrheit für Objekt-Operationen (analog zu
flash_core.py/backup_core.py) — sowohl cli/commands/keys.py als auch die
künftige GUI (gui/tabs/keys_tab.py) importieren ausschließlich hieraus.

Konsolidiert außerdem die Objektliste/-löschung, die bisher inline in
cli/commands/keys.py steckte (Bruch mit dem sonst durchgehaltenen
"eine Quelle der Wahrheit"-Prinzip).

Deckt die in pico-hsm/doc/usage.md, aes.md und store_data.md
dokumentierten Firmware-Funktionen ab, die die bisherige CLI nicht
abbildete: Keypair-/AES-Erzeugung, Zertifikat-/Datenobjekte, RNG.
Bewusst NICHT abgedeckt: sign/verify/encrypt/decrypt/derive (siehe
gateway_client.py, geplanter Diagnose-Modus) und der DKEK-Wrap/Unwrap-
Importpfad (bleibt bei dkek.py/sc-hsm-tool).

OFFENE PUNKTE (Architekturkonzept §12): keine der Funktionen unten ist
gegen echte Pico-HSM-Hardware verifiziert — usage.md/aes.md/store_data.md
demonstrieren alles über pkcs11-tool/sc-hsm-tool als externe Programme,
nicht über python-pkcs11. Insbesondere die Attribut-Zuordnung für
Datenobjekte (write_data_object) ist aus der dokumentierten
pkcs11-tool-Ausgabe abgeleitet, nicht 1:1 aus der python-pkcs11-API-
Referenz übernommen.

WICHTIGER, REAL VERIFIZIERTER STOLPERSTEIN: Die Brainpool-Kurvennamen
aus usage.md ("brainpoolP256r1", Groß-P wie bei OpenSC) matchen NICHT
die von asn1crypto/python-pkcs11 erwarteten Namen ("brainpoolp256r1",
durchgehend klein) — ohne die Umbenennung in EC_CURVES unten wirft
encode_named_curve_parameters() einen ValueError. Gegen echte
asn1crypto-Version getestet, siehe Kommentar bei EC_CURVES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pkcs11
from pkcs11 import Attribute, KeyType, ObjectClass
from pkcs11.util.ec import encode_named_curve_parameters

# --- Erlaubte Kombinationen ---------------------------------------------
# Sicherheitsbetrachtung (Architekturkonzept §9): nur die in usage.md
# gelisteten Werte anbieten, kein Freitext-Mechanismus-/Kurven-Feld.

RSA_KEY_LENGTHS_BITS = (1024, 2048, 4096)

# RSA-2048 kann laut usage.md >20s dauern, RSA-4096 >20min. Aufrufer
# (CLI/GUI) MÜSSEN das vor dem Aufruf anzeigen, nicht erst beim Timeout
# entdecken — GUI-seitig zwingend in einem Worker-Thread ausführen
# (siehe Architekturkonzept §8).
RSA_SLOW_WARNING_BITS = {
    2048: "kann länger als 20 Sekunden dauern",
    4096: "kann länger als 20 Minuten dauern",
}

# Firmware-Doku-Namen (usage.md) -> asn1crypto/NamedCurve-kompatible
# Namen. Siehe Modul-Docstring: die Brainpool-Kurven brauchen die
# Umbenennung, die anderen sechs zufällig nicht — trotzdem alle explizit
# gelistet statt "wird schon passen".
EC_CURVES: dict[str, str] = {
    "secp192r1": "secp192r1",
    "secp256r1": "secp256r1",
    "secp384r1": "secp384r1",
    "secp521r1": "secp521r1",
    "brainpoolP256r1": "brainpoolp256r1",
    "brainpoolP384r1": "brainpoolp384r1",
    "brainpoolP512r1": "brainpoolp512r1",
    "secp192k1": "secp192k1",
    "secp256k1": "secp256k1",
}

AES_KEY_LENGTHS_BYTES = (16, 24, 32)  # 128/192/256 Bit, siehe aes.md

MAX_DATA_OBJECT_BYTES = 4096  # Firmware-Flash-Limit, siehe store_data.md
MAX_RANDOM_BYTES = 1024  # Firmware-Limit, siehe usage.md


class ObjectsError(Exception):
    """Fehler bei Objekt-Lifecycle-Operationen (Keys, Zertifikate,
    Datenobjekte) — sowohl für ungültige Parameter (z.B. nicht
    unterstützte Schlüssellänge) als auch für PKCS#11-Fehler beim
    eigentlichen Zugriff."""


@dataclass
class ObjectInfo:
    label: str
    id: Optional[bytes]
    object_class: str
    key_type: Optional[str] = None
    key_length_bits: Optional[int] = None


# --- Keypair-/AES-Erzeugung ----------------------------------------------

def generate_rsa_keypair(
    session: "pkcs11.Session", bits: int, id: bytes, label: str,
) -> ObjectInfo:
    """RSA-Keypair erzeugen. `session` muss eine offene, schreibende
    Session sein (siehe pkcs11_session.exclusive_session). Der private
    Schlüssel verlässt das HSM nie (intern AES-256-verschlüsselt
    gespeichert, siehe usage.md)."""
    if bits not in RSA_KEY_LENGTHS_BITS:
        raise ObjectsError(
            f"RSA-Schlüssellänge {bits} nicht unterstützt "
            f"(erlaubt: {RSA_KEY_LENGTHS_BITS})."
        )
    try:
        session.generate_keypair(
            KeyType.RSA, key_length=bits, id=id, label=label, store=True,
        )
    except pkcs11.exceptions.PKCS11Error as exc:
        raise ObjectsError(f"RSA-Keypair-Erzeugung fehlgeschlagen: {exc}") from exc
    return ObjectInfo(
        label=label, id=id, object_class="PRIVATE_KEY",
        key_type="RSA", key_length_bits=bits,
    )


def generate_ec_keypair(
    session: "pkcs11.Session", curve: str, id: bytes, label: str,
) -> ObjectInfo:
    """EC-Keypair erzeugen. `curve` ist einer der Firmware-Doku-Namen aus
    `EC_CURVES` (z.B. 'secp256r1', 'brainpoolP256r1' — Groß-P wie in
    usage.md, die interne Umbenennung passiert hier)."""
    if curve not in EC_CURVES:
        raise ObjectsError(
            f"EC-Kurve '{curve}' nicht unterstützt "
            f"(erlaubt: {sorted(EC_CURVES)})."
        )
    ec_params = encode_named_curve_parameters(EC_CURVES[curve])
    try:
        session.generate_keypair(
            KeyType.EC, id=id, label=label, store=True,
            public_template={Attribute.EC_PARAMS: ec_params},
        )
    except pkcs11.exceptions.PKCS11Error as exc:
        raise ObjectsError(f"EC-Keypair-Erzeugung fehlgeschlagen: {exc}") from exc
    return ObjectInfo(
        label=label, id=id, object_class="PRIVATE_KEY", key_type=f"EC:{curve}",
    )


def generate_aes_key(
    session: "pkcs11.Session", num_bytes: int, id: bytes, label: str,
) -> ObjectInfo:
    """AES-Secret-Key erzeugen. `num_bytes` in BYTE (16/24/32) — passend
    zur Firmware-Doku-Notation (`aes:16`/`aes:24`/`aes:32`).
    python-pkcs11s `generate_key()` erwartet Bit, die Umrechnung
    passiert hier."""
    if num_bytes not in AES_KEY_LENGTHS_BYTES:
        raise ObjectsError(
            f"AES-Schlüssellänge {num_bytes} Byte nicht unterstützt "
            f"(erlaubt: {AES_KEY_LENGTHS_BYTES})."
        )
    try:
        session.generate_key(
            KeyType.AES, key_length=num_bytes * 8, id=id, label=label, store=True,
        )
    except pkcs11.exceptions.PKCS11Error as exc:
        raise ObjectsError(f"AES-Key-Erzeugung fehlgeschlagen: {exc}") from exc
    return ObjectInfo(
        label=label, id=id, object_class="SECRET_KEY",
        key_type="AES", key_length_bits=num_bytes * 8,
    )


# --- Objekt-Liste/-Löschung (migriert aus cli/commands/keys.py) ---------

def list_objects(session: "pkcs11.Session") -> list[ObjectInfo]:
    """Alle Objekte auflisten (Keys UND Datenobjekte). Funktioniert mit
    read-only- oder exklusiver Session. Verhalten unverändert ggü. der
    vorherigen Inline-Logik in cli/commands/keys.py::list_cmd — nur
    hierher verschoben, damit CLI und künftige GUI dieselbe Quelle
    nutzen."""
    result = []
    for obj in session.get_objects():
        key_type = getattr(obj, "key_type", None)
        result.append(ObjectInfo(
            label=getattr(obj, "label", "") or "",
            id=getattr(obj, "id", None),
            object_class=str(getattr(obj, "object_class", "")),
            key_type=str(key_type) if key_type is not None else None,
        ))
    return result


def delete_object(session: "pkcs11.Session", label: str) -> bool:
    """Objekt (Key ODER Datenobjekt) mit gegebenem Label löschen.
    `session` muss schreibend sein. Verhalten unverändert ggü. der
    vorherigen Inline-Logik in cli/commands/keys.py::delete_cmd. Gibt
    False zurück, wenn kein Objekt mit diesem Label existiert (kein
    Fehler, Aufrufer entscheidet, wie das gemeldet wird)."""
    for obj in session.get_objects():
        if getattr(obj, "label", None) == label:
            obj.destroy()
            return True
    return False


# --- Zertifikat-/Datenobjekte --------------------------------------------

def write_data_object(
    session: "pkcs11.Session",
    data: bytes,
    label: str,
    id: Optional[bytes] = None,
    private: bool = True,
) -> ObjectInfo:
    """Beliebige Binärdaten (z.B. Zertifikat in DER-Form) als
    Datenobjekt ablegen. `private=True` (Default) verlangt die PIN zum
    Lesen — bewusster sicherer Default (Architekturkonzept §9: "private
    ist Default True, explizites Opt-out nötig, nicht umgekehrt")."""
    if len(data) > MAX_DATA_OBJECT_BYTES:
        raise ObjectsError(
            f"Datenobjekt zu groß ({len(data)} Byte, Firmware-Limit "
            f"{MAX_DATA_OBJECT_BYTES} Byte)."
        )
    attrs = {
        Attribute.CLASS: ObjectClass.DATA,
        Attribute.LABEL: label,
        Attribute.APPLICATION: label,
        Attribute.VALUE: data,
        Attribute.PRIVATE: private,
        Attribute.TOKEN: True,
        Attribute.MODIFIABLE: True,
    }
    if id is not None:
        attrs[Attribute.ID] = id
    try:
        session.create_object(attrs)
    except pkcs11.exceptions.PKCS11Error as exc:
        raise ObjectsError(f"Datenobjekt-Erstellung fehlgeschlagen: {exc}") from exc
    return ObjectInfo(label=label, id=id, object_class="DATA")


def read_data_object(session: "pkcs11.Session", label: str) -> bytes:
    """Datenobjekt anhand des Labels lesen. Für `private=True`-Objekte
    muss `session` mit PIN geöffnet worden sein."""
    for obj in session.get_objects(
        {Attribute.CLASS: ObjectClass.DATA, Attribute.LABEL: label}
    ):
        return obj[Attribute.VALUE]
    raise ObjectsError(f"Kein Datenobjekt mit Label '{label}' gefunden.")


def delete_data_object(session: "pkcs11.Session", label: str) -> bool:
    """Datenobjekt anhand des Labels löschen (typgefiltert — anders als
    `delete_object`, das auch Keys träfe, falls zufällig derselbe Label
    doppelt vergeben wäre). `session` muss schreibend sein."""
    for obj in session.get_objects(
        {Attribute.CLASS: ObjectClass.DATA, Attribute.LABEL: label}
    ):
        obj.destroy()
        return True
    return False


# --- Zufallszahlen --------------------------------------------------------

def generate_random(session: "pkcs11.Session", num_bytes: int) -> bytes:
    """Zufallsbytes vom HSM anfordern. `num_bytes` in BYTE (Firmware-
    Limit 1024, siehe usage.md). python-pkcs11s `generate_random()`
    erwartet Bit — die Umrechnung passiert hier."""
    if not 1 <= num_bytes <= MAX_RANDOM_BYTES:
        raise ObjectsError(
            f"num_bytes muss zwischen 1 und {MAX_RANDOM_BYTES} liegen "
            "(Firmware-Limit)."
        )
    return session.generate_random(num_bytes * 8)
