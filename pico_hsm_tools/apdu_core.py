"""
apdu_core.py — Vendor-APDUs für RTC (Datetime) und Dynamic Options.

EINZIGE Quelle der Wahrheit für rohe APDU-Operationen (analog zu
flash_core.py/backup_core.py/objects_core.py) — sowohl
cli/commands/setup.py als auch die künftige GUI (gui/tabs/setup_tab.py)
importieren ausschließlich hieraus.

Abgedeckt sind die in pico-hsm/doc/extra_command.md dokumentierten
Vendor-Kommandos (CLA=0x80, INS=0x64):
  - Datetime lesen/schreiben (P1=0x0A, 8-Byte-Nutzdaten)
  - Dynamic Options lesen/schreiben (P1=0x06, 1-Byte-Bitmaske)

Implementierung über pyscard (smartcard.CardConnection), NICHT über
`opensc-tool -s` + Textparsing — das Projekt hat mit dem
picotool-Textparsing bereits zwei reale Bugs eingefangen (siehe README,
Abschnitt 5); dieselbe Fehlerklasse hier von vornherein vermeiden.

ZUGRIFFSREGEL (Architekturkonzept §7.b, getroffene Entscheidung):
APDU-Kommandos laufen SEQUENZIELL und NIE parallel zu einer offenen
PKCS#11-Session. Aufrufer (CLI/GUI) schließen eine etwaige
PKCS#11-Session, BEVOR eine APDU-Verbindung geöffnet wird. Der
OpenSC-Treiber-Sharing-Mode (SCARD_SHARE_SHARED) ist ohne echte Hardware
nicht verifizierbar — daher der sichere Default statt eines
parallelen Versuchs.

OFFENE PUNKTE (Architekturkonzept §12, ehrlich markiert):
  - Keine der Funktionen unten ist gegen echte Pico-HSM-Hardware
    verifiziert (kein Board vorhanden) — extra_command.md demonstriert
    alles über `opensc-tool -s`, nicht über pyscard.
  - Das Dynamic-Options-GET (`80 64 06 00 01`, Le=1) ist NICHT in
    extra_command.md dokumentiert (dort nur SET-Beispiele); es ist aus
    dem Befehlsschema `8064XX00[YY][ZZZZ][RR]` analog zum
    Datetime-GET abgeleitet und MUSS am echten Board verifiziert werden.
  - In extra_command.md steht beim Key-Usage-Counter-SET fälschlich
    `Sending: 80 64 06 00 01 01` (Copy-Paste aus dem
    Press-to-Confirm-Beispiel); der dokumentierte opensc-tool-String
    `806406000102` dekodiert zu `80 64 06 00 01 02` — LETZTERES ist
    implementiert, siehe Kommentar bei _SET_DYNOPTS_MASK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol


# --- Konstanten (extra_command.md) ---------------------------------------

_CLA_VENDOR = 0x80
_INS_CUSTOM = 0x64

_P1_DATETIME = 0x0A  # RTC lesen/schreiben, 8-Byte-Nutzdaten
_P1_DYNOPTS = 0x06  # Dynamic Options, 1-Byte-Bitmaske

_LEN_DATETIME = 8
_LEN_DYNOPTS = 1

_SW_OK = (0x90, 0x00)

# Dynamic-Options-Bitfeld (LSB-zählend, siehe extra_command.md):
#   Bit 0 = Press-to-Confirm, Bit 1 = Key-Usage-Counter.
_BIT_PRESS_TO_CONFIRM = 0x01
_BIT_KEY_USAGE_COUNTER = 0x02

# RTC-Plausibilitätsgrenzen (CLI-seitig; die Firmware-Epoche beginnt
# 2020-01-01 nach Reset, Obergrenze ist eine reine Plausibilitätsgrenze).
_MIN_YEAR = 2020
_MAX_YEAR = 2099

# Wochentag-Kodierung der Firmware: 00h = Sonntag ... 06h = Samstag.
# Python: isoweekday() Mo=1..So=7  ->  Firmware-Wert = isoweekday() % 7.
_SUNDAY_FIRMWARE = 0


# --- Fehler ---------------------------------------------------------------

class ApduError(Exception):
    """APDU-Fehler mit Klartext (inkl. Statuswort SW1/SW2 als Hex)."""


# --- Typen -----------------------------------------------------------------

class CardConnectionLike(Protocol):
    """Minimalprotokoll einer APDU-Verbindung.

    pyscards CardConnection erfüllt das (transmit(list) ->
    (list, sw1, sw2), disconnect()); Tests nutzen eine Fake-Klasse
    mit derselben Signatur — keine Hardware nötig.
    """

    def transmit(self, apdu: list[int]) -> tuple[list[int], int, int]: ...
    def disconnect(self) -> None: ...


@dataclass(frozen=True)
class DynamicOptions:
    """Dynamic Options als strukturierte Werte statt roher Bitmaske.

    Sicherheitsbetrachtung (Architekturkonzept §9): Deaktivieren von
    Security-Features (v.a. Press-to-Confirm) braucht in der UI-Schicht
    eine explizite Bestätigung — siehe setup.py.
    """

    press_to_confirm: bool = False  # Bit 0
    key_usage_counter: bool = False  # Bit 1

    def to_byte(self) -> int:
        value = 0
        if self.press_to_confirm:
            value |= _BIT_PRESS_TO_CONFIRM
        if self.key_usage_counter:
            value |= _BIT_KEY_USAGE_COUNTER
        return value

    @classmethod
    def from_byte(cls, value: int) -> "DynamicOptions":
        if not 0 <= value <= 0xFF:
            raise ApduError(f"Options-Byte außerhalb 0x00..0xFF: {value:#04x}")
        # Unbekannte Bits (> Bit 1) bewusst IGNORIEREN statt ablehnen:
        # künftige Firmware-Versionen dürfen das Bitfeld erweitern, ohne
        # dass diese App-Version das Lesen verweigert.
        return cls(
            press_to_confirm=bool(value & _BIT_PRESS_TO_CONFIRM),
            key_usage_counter=bool(value & _BIT_KEY_USAGE_COUNTER),
        )


# --- Interna -----------------------------------------------------------------

def _check_sw(sw1: int, sw2: int, what: str) -> None:
    if (sw1, sw2) != _SW_OK:
        raise ApduError(
            f"{what} fehlgeschlagen (SW={sw1:02X}{sw2:02X} statt 9000)."
        )


def _transmit(
    connection: CardConnectionLike, apdu: list[int], what: str,
) -> list[int]:
    try:
        response, sw1, sw2 = connection.transmit(apdu)
    except ApduError:
        raise
    except Exception as exc:  # noqa: BLE001 — PC/SC-Fehler wrappen
        raise ApduError(f"{what}: Übertragungsfehler ({exc})") from exc
    _check_sw(sw1, sw2, what)
    return list(response)


def _encode_datetime(dt: datetime) -> list[int]:
    if not _MIN_YEAR <= dt.year <= _MAX_YEAR:
        raise ApduError(
            f"Jahr {dt.year} außerhalb {_MIN_YEAR}..{_MAX_YEAR} "
            "(Firmware-Epoche beginnt 2020)."
        )
    weekday = dt.isoweekday() % 7  # Mo=1..So=7 -> So=0..Sa=6
    return [
        (dt.year >> 8) & 0xFF, dt.year & 0xFF,  # Jahr, MSB zuerst
        dt.month, dt.day, weekday,
        dt.hour, dt.minute, dt.second,
    ]


def _decode_datetime(raw: list[int]) -> datetime:
    if len(raw) != _LEN_DATETIME:
        raise ApduError(
            f"Datetime-Antwort hat {len(raw)} statt {_LEN_DATETIME} Byte."
        )
    year = (raw[0] << 8) | raw[1]
    try:
        return datetime(year, raw[2], raw[3], raw[5], raw[6], raw[7])
    except ValueError as exc:
        raise ApduError(f"Ungültige Datetime-Antwort ({exc}).") from exc
    # Hinweis: raw[4] (Wochentag) wird bewusst nicht geprüft/übernommen —
    # Python leitet den Wochentag aus dem Datum ab; ein abweichendes Byte
    # wäre ein Firmware-Artefakt, kein Anwendungsfehler.


# --- Öffentliche API (Signaturen aus Architekturkonzept §7.b) ----------------

def get_datetime(connection: CardConnectionLike) -> datetime:
    """RTC-Datetime lesen. APDU (dokumentiert): `80 64 0A 00 08`."""
    raw = _transmit(
        connection, [_CLA_VENDOR, _INS_CUSTOM, _P1_DATETIME, 0x00, _LEN_DATETIME],
        "Datetime lesen",
    )
    return _decode_datetime(raw)


def set_datetime(connection: CardConnectionLike, dt: datetime) -> None:
    """RTC-Datetime setzen. APDU (dokumentiert, Lc=08 + 8 Datenbytes).

    Der Wochentag wird aus `dt` berechnet, nicht übernommen — Aufrufer
    geben nur das Datum/die Uhrzeit an.
    """
    data = _encode_datetime(dt)
    _transmit(
        connection,
        [_CLA_VENDOR, _INS_CUSTOM, _P1_DATETIME, 0x00, _LEN_DATETIME, *data],
        "Datetime setzen",
    )


def get_dynamic_options(connection: CardConnectionLike) -> DynamicOptions:
    """Dynamic Options lesen. APDU (`80 64 06 00 01`, Le=1).

    [WARNUNG] NICHT hardwareverifiziert und NICHT in extra_command.md
    dokumentiert (dort nur SET) — aus dem Befehlsschema analog zum
    Datetime-GET abgeleitet. Am echten Board verifizieren (§12).
    """
    raw = _transmit(
        connection, [_CLA_VENDOR, _INS_CUSTOM, _P1_DYNOPTS, 0x00, _LEN_DYNOPTS],
        "Dynamic Options lesen",
    )
    if len(raw) != _LEN_DYNOPTS:
        raise ApduError(
            f"Options-Antwort hat {len(raw)} statt {_LEN_DYNOPTS} Byte."
        )
    return DynamicOptions.from_byte(raw[0])


def set_dynamic_options(
    connection: CardConnectionLike, options: DynamicOptions,
) -> None:
    """Dynamic Options setzen. APDU (dokumentiert, Lc=01 + Bitmaske).

    Beispiele aus extra_command.md: `80 64 06 00 01 01` (P2C an),
    `80 64 06 00 01 00` (aus), `80 64 06 00 01 02` (Counter an —
    der Doc-Text zeigt hier fälschlich `... 01 01`, der
    opensc-tool-String `806406000102` ist maßgeblich, siehe Moduldoc).
    """
    mask = options.to_byte()
    _transmit(
        connection,
        [_CLA_VENDOR, _INS_CUSTOM, _P1_DYNOPTS, 0x00, _LEN_DYNOPTS, mask],
        "Dynamic Options setzen",
    )


# --- Verbindung (pyscard, lazy import) ---------------------------------------

def open_connection(reader: Optional[str] = None) -> Any:
    """PC/SC-Verbindung zum Pico HSM öffnen (pyscard).

    `reader=None`: erster Reader mit eingesteckter Karte (Ein-Gerät-
    Modell wie bei pkcs11_session.get_token()). Sonst exakte
    Reader-Bezeichnung. Aufrufer MÜSSEN disconnect() aufrufen und
    dürfen parallel KEINE PKCS#11-Session offen halten (§7.b-Entscheid:
    sequenziell/exklusiv).

    pyscard wird hier LAZY importiert, damit dieses Modul (und seine
    Tests) auch ohne installiertes pyscard ladbar bleibt.
    """
    try:
        from smartcard.System import readers  # type: ignore
    except ImportError as exc:
        raise ApduError(
            "pyscard ist nicht installiert — für `setup datetime` / "
            "`setup dynamic-options` erforderlich "
            "(pip install pyscard, siehe pyproject.toml)."
        ) from exc

    try:
        available = readers()
    except Exception as exc:  # noqa: BLE001 — PC/SC-Dienstfehler
        raise ApduError(f"PC/SC-Readerliste nicht lesbar ({exc}).") from exc

    if reader is not None:
        match = [r for r in available if str(r) == reader]
        if not match:
            names = ", ".join(str(r) for r in available) or "keine"
            raise ApduError(
                f"Reader {reader!r} nicht gefunden (verfügbar: {names})."
            )
        chosen = match[0]
    else:
        with_card: list[Any] = []
        for r in available:
            try:
                probe = r.createConnection()
                probe.connect()
                probe.disconnect()
                with_card.append(r)
            except Exception:  # noqa: BLE001, S110 — kein Karte = weiter
                continue
        if len(with_card) != 1:
            names = ", ".join(str(r) for r in available) or "keine"
            raise ApduError(
                f"Genau ein Reader mit Karte erwartet, gefunden: "
                f"{len(with_card)} (verfügbar: {names})."
            )
        chosen = with_card[0]

    try:
        connection = chosen.createConnection()
        connection.connect()
    except Exception as exc:  # noqa: BLE001 — inkl. Reader-belegt-Fall
        raise ApduError(
            f"Reader {chosen} nicht nutzbar ({exc}) — evtl. hält ein "
            "anderer Prozess (z.B. eine offene PKCS#11-Session oder das "
            "Gateway) den Reader belegt. APDU-Zugriff ist sequenziell/"
            "exklusiv (§7.b)."
        ) from exc
    return connection
