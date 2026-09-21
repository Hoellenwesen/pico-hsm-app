"""
objects_core.py — Objekt-Lifecycle für Keys, Zertifikate und Datenobjekte.

EINZIGE Quelle der Wahrheit für Objekt-Operationen (analog zu
flash_core.py/backup_core.py) — sowohl cli/commands/keys.py als auch die
GUI (gui/tabs/keys_tab.py) importieren ausschließlich hieraus.

Konsolidiert außerdem die Objektliste/-löschung, die bisher inline in
cli/commands/keys.py steckte (Bruch mit dem sonst durchgehaltenen
"eine Quelle der Wahrheit"-Prinzip).

Deckt die in pico-hsm/doc/usage.md, aes.md und store_data.md
dokumentierten Firmware-Funktionen ab, die die bisherige CLI nicht
abbildete: Keypair-/AES-Erzeugung, Zertifikat-/Datenobjekte, RNG.
Bewusst NICHT abgedeckt: sign/verify/encrypt/decrypt/derive (siehe
gateway_client.py, Diagnose-Modus aus der Roadmap) und der DKEK-Wrap/Unwrap-
Importpfad (bleibt bei dkek.py/sc-hsm-tool).

HARDWARE-VERIFIKATION (§12, docs/15 Phase 2, alle abgehakt):
Datenobjekt-Roundtrip byte-identisch inkl. Delete-Fix (CLASS-gefilterte
Suche, hw-logs/07+08), EC-Roundtrip vollständig inkl. Paar-Löschung
(hw-logs/09+10), RSA-2048 (ca. 2:45) und RSA-4096 (ca. 15:00) mit
Timing-Messung (hw-logs/11+12). Die Attribut-Zuordnung für
Datenobjekte (write_data_object) ist aus der dokumentierten
pkcs11-tool-Ausgabe abgeleitet und per Roundtrip am Board belegt.

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

# Laufzeiten am Ersatzboard gemessen (Doku lag bei 2048 um Faktor ~8
# daneben): RSA-2048 ca. 2:45 Min., RSA-4096 ca. 15:00 Min. Aufrufer
# (CLI/GUI) MÜSSEN das vor dem Aufruf anzeigen, nicht erst beim Timeout
# entdecken — GUI-seitig zwingend in einem Worker-Thread ausführen
# (siehe Architekturkonzept §8).
RSA_SLOW_WARNING_BITS = {
    2048: "kann mehrere Minuten dauern (am Board gemessen: ca. 2:45 Min.)",
    4096: "kann ca. eine Viertelstunde dauern (am Board gemessen: ca. 15:00 Min.)",
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


def _display_name(value: object) -> str:
    """Enum-Member auf Namen abbilden (statt Zahl).

    Hardware-Befund (Ersatzboard): Seit Python 3.11 rendert
    `str(IntEnum)` die ZAHL (`ObjectClass.PRIVATE_KEY` -> `"3"`) —
    die Objektliste zeigte daher `3`/`2` statt Namen. `.name`
    existiert auf allen Enum-Membern; alles andere (inkl. None ->
    `"None"` wie bisher) fällt auf `str()` zurück.
    """
    name = getattr(value, "name", None)
    return str(name if isinstance(name, str) else value)


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


# --- Objekt-Liste/-Löschung -------------------------------------------------
# Hardware-Befund (Ersatzboard, Pico-HSM-Firmware v6.6/OpenSC): Eine
# UNGEFILTERTE get_objects()-Suche liefert NICHT alle Objekte zurück
# (nur ein System-PROFILE-Objekt; selbst angelegte Datenobjekte fehlen,
# obwohl sie per gefilterter Suche lesbar sind). Deshalb arbeiten Liste
# und Löschung mit expliziten CLASS-Filtern statt einer leeren Suche
# plus Client-Vergleich — derselbe Mechanismus wie der funktionierende
# Read-Pfad. Zertifikate sind EINGESCHLOSSEN (getroffene Entscheidung):
# Key-Generierung legt Cert-Objekte an, die sonst unsichtbar den Import
# blockieren (`Found existing certificate ... use --force`-Befund).

# Reihenfolge mit Absicht: Geheimnisse zuerst (Private/Secret), dann
# Daten, öffentliche Hälfte und Zertifikat zuletzt (harmloser Rest,
# zweiter Aufruf entfernt ihn). Erster Treffer gewinnt (wie bisher).
_DELETE_SEARCH_CLASSES = (
    ObjectClass.PRIVATE_KEY,
    ObjectClass.SECRET_KEY,
    ObjectClass.DATA,
    ObjectClass.PUBLIC_KEY,
    ObjectClass.CERTIFICATE,
)

# Anzeige-Reihenfolge für die Objektliste (Keys zuerst, Daten zuletzt).
_LIST_SEARCH_CLASSES = (
    ObjectClass.PRIVATE_KEY,
    ObjectClass.SECRET_KEY,
    ObjectClass.PUBLIC_KEY,
    ObjectClass.DATA,
    ObjectClass.CERTIFICATE,
)

def list_objects(session: "pkcs11.Session") -> list[ObjectInfo]:
    """Alle Objekte auflisten (Keys UND Datenobjekte). Funktioniert mit
    read-only- oder exklusiver Session.

    Arbeitet mit expliziten CLASS-Filtern statt einer leeren Suche:
    Auf Pico-HSM-Firmware v6.6 via OpenSC liefert die ungefilterte
    Suche selbst angelegte Objekte NICHT zurück (nur PROFILE-System-
    objekt) — Hardware-Befund, siehe Moduldoc oben. Klassen sind
    exklusiv, daher keine Duplikate möglich.
    """
    result = []
    for object_class in _LIST_SEARCH_CLASSES:
        for obj in session.get_objects({Attribute.CLASS: object_class}):
            key_type = getattr(obj, "key_type", None)
            result.append(ObjectInfo(
                label=getattr(obj, "label", "") or "",
                id=getattr(obj, "id", None),
                object_class=_display_name(getattr(obj, "object_class", "")),
                key_type=(
                    _display_name(key_type) if key_type is not None else None
                ),
            ))
    return result


def delete_object(session: "pkcs11.Session", label: str) -> bool:
    """Objekt (Key ODER Datenobjekt) mit gegebenem Label unwiderruflich
    löschen. `session` muss schreibend sein.

    Sucht pro Klasse gefiltert statt per Client-Vergleich über eine
    leere Suche — dieselbe Suche fand auf echter Hardware das
    Datenobjekt nicht, obwohl es per Filter lesbar war (siehe Moduldoc).
    Erster Treffer gewinnt (Reihenfolge: _DELETE_SEARCH_CLASSES);
    False, wenn kein Objekt mit dem Label existiert (kein Fehler,
    Aufrufer entscheidet, wie das gemeldet wird).

    Hardware-Befund (Ersatzboard, `hw-logs/10-...`): Nach Löschen der
    privaten Hälfte eines Keypairs ist auch die öffentliche Hälfte weg
    (Paar wird vollständig geräumt oder verwaist unsichtbar,
    per `pkcs15-tool -D` gegengeprüft) — kein zweites Delete nötig.
    """
    for object_class in _DELETE_SEARCH_CLASSES:
        for obj in session.get_objects(
            {Attribute.CLASS: object_class, Attribute.LABEL: label}
        ):
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
