"""
apdu_core.py — Vendor-APDUs für Dynamic Options.

EINZIGE Quelle der Wahrheit für rohe APDU-Operationen (analog zu
flash_core.py/backup_core.py/objects_core.py) — sowohl
cli/commands/setup.py als auch gui/tabs/setup_tab.py importieren
ausschließlich hieraus.

Abgedeckt: Dynamic Options lesen/schreiben (CLA=0x80, INS=0x64,
P1=0x06) — Byte-Layout QUELLENVERIFIZIERT gegen
pico-hsm/src/hsm/cmd_extras.c + sc_hsm.h (statt nur Doku):
  - GET `80 64 06 00 02` -> 2-Byte-Antwort uint16-BE (nicht 1 Byte!).
  - Bitlage im HIGH-Byte: 0x0100 = Press-to-Confirm
    (HSM_OPT_BOOTSEL_BUTTON), 0x0200 = Key-Usage-Counter
    (HSM_OPT_KEY_COUNTER_ALL).
  - SET `80 64 06 00 01 <mask>` (Lc=01): Datenbyte wird HIGH-Byte,
    Low-Byte bleibt erhalten — daher Bit0/Bit1 des Datenbytes wie in
    extra_command.md dokumentiert (Beispiele verifiziert).

WICHTIG, am Board gemessen: ALLE Vendor-Kommandos brauchen vorherigen
PIN-Login (`isUserAuthenticated`, cmd_extras.c) — ohne Login antwortet
die Karte SW=6982. Aufrufer loggen sich zuerst per PKCS#11 ein (oder
nutzen eine eingeloggte Session-Umgebung); diese Schicht nimmt KEINE
PIN entgegen.

NICHT (mehr) enthalten: RTC-Datetime (P1=0x0A). `CMD_DATETIME` ist in
der Firmware definiert, wird aber nirgends behandelt — die Karte
antwortet `6A86` (am Board gemessen + quellverifiziert). Die Befehle
wurden daher entfernt (getroffene Entscheidung); extra_command.md
beschreibt hier Firmware-Fiktion (v6.6).

Implementierung über pyscard (smartcard.CardConnection), NICHT über
`opensc-tool -s` + Textparsing — das Projekt hat mit dem
picotool-Textparsing bereits zwei reale Bugs eingefangen (siehe README,
Abschnitt 5); dieselbe Fehlerklasse hier von vornherein vermeiden.
GET-RESPONSE (`61 XX`) und Le-Korrektur (`6C XX`) werden behandelt —
`61 01` auf ein Le=1-GET ist normale Antwort, kein Fehler.

ZUGRIFFSREGEL (Architekturkonzept §7.b, getroffene Entscheidung):
APDU-Kommandos laufen SEQUENZIELL und NIE parallel zu einer offenen
PKCS#11-Session — mit EINER Ausnahme: dem vorausgehenden PIN-Login
selbst (Session danach schließen, bevor APDUs laufen). Messung am
Board (docs/15 Phase 2.8, hw-logs/17): zweite exklusive Session +
APDU bei gehaltener Session funktionieren störungsfrei — PKCS#11
kennt keinen OS-exklusiven Session-Lock; die Regel bleibt als
sicherer Default, SessionConflictError als Sicherheitsnetz.

OFFENE PUNKTE (Architekturkonzept §12, ehrlich markiert):
  - Dynamic-Options-Voll-Roundtrip VERIFIZIERT (docs/15 Phase 2.1,
    hw-logs/06: 0x00->0x02->0x03->0x00 mit exakten Masken). Offen nur
    der P2C-Enforcement-Umfang: kein Tastendruck trotz aktivem P2C
    beobachtet (kein ENABLE_EMULATION im Build) — ggf. Timing-Test
    nachholen.
  - In extra_command.md steht beim Key-Usage-Counter-SET fälschlich
    `Sending: 80 64 06 00 01 01` (Copy-Paste aus dem
    Press-to-Confirm-Beispiel); der dokumentierte opensc-tool-String
    `806406000102` dekodiert zu `80 64 06 00 01 02` — LETZTERES ist
    implementiert (konsistent mit High-Byte-Bitlage).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


# --- Konstanten (cmd_extras.c + sc_hsm.h, extra_command.md) -----------------

_CLA_VENDOR = 0x80
_INS_CUSTOM = 0x64

_P1_DYNOPTS = 0x06  # Dynamic Options (SET: Lc=01 + Maske; GET: Le=02)

_LEN_DYNOPTS_SET = 1  # Lc für SET
_LEN_DYNOPTS_GET = 2  # Le für GET (uint16-BE-Antwort!)

_GET_RESPONSE_CLA = 0x00
_GET_RESPONSE_INS = 0xC0
_MAX_GET_RESPONSE_ROUNDS = 8  # Endlosschutz

_SW_OK = (0x90, 0x00)

# Dynamic-Options-Bitfeld, HIGH-Byte des uint16 (sc_hsm.h):
#   0x0100 (HSM_OPT_BOOTSEL_BUTTON) = Press-to-Confirm,
#   0x0200 (HSM_OPT_KEY_COUNTER_ALL) = Key-Usage-Counter.
# Das SET-Datenbyte landet im High-Byte (newopts[0] = data[0]) —
# daher Bit0/Bit1 des Datenbytes wie in extra_command.md dokumentiert.
_BIT_PRESS_TO_CONFIRM = 0x01  # Datenbyte-Bit 0 -> uint16-Bit 8
_BIT_KEY_USAGE_COUNTER = 0x02  # Datenbyte-Bit 1 -> uint16-Bit 9


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
        """Datenbyte für SET (`80 64 06 00 01 <mask>`) — landet im
        High-Byte des uint16 (Firmware: newopts[0] = data[0])."""
        value = 0
        if self.press_to_confirm:
            value |= _BIT_PRESS_TO_CONFIRM
        if self.key_usage_counter:
            value |= _BIT_KEY_USAGE_COUNTER
        return value

    @classmethod
    def from_response(cls, data: list[int]) -> "DynamicOptions":
        """Aus 2-Byte-GET-Antwort (uint16-BE) parsen. Bits sitzen im
        HIGH-Byte (data[0]); unbekannte Bits bewusst IGNORIEREN statt
        ablehnen: künftige Firmware-Versionen dürfen das Bitfeld
        erweitern, ohne dass diese App-Version das Lesen verweigert."""
        if len(data) != _LEN_DYNOPTS_GET:
            raise ApduError(
                f"Options-Antwort hat {len(data)} statt "
                f"{_LEN_DYNOPTS_GET} Byte."
            )
        high = data[0]
        return cls(
            press_to_confirm=bool(high & _BIT_PRESS_TO_CONFIRM),
            key_usage_counter=bool(high & _BIT_KEY_USAGE_COUNTER),
        )


# --- Interna -----------------------------------------------------------------

def _check_sw(sw1: int, sw2: int, what: str) -> None:
    if (sw1, sw2) != _SW_OK:
        raise ApduError(
            f"{what} fehlgeschlagen (SW={sw1:02X}{sw2:02X} statt 9000)."
        )


def _raw_transmit(
    connection: CardConnectionLike, apdu: list[int], what: str,
) -> tuple[list[int], int, int]:
    """Einzelnes transmit mit Fehler-Wrapping (keine SW-Auswertung)."""
    try:
        response, sw1, sw2 = connection.transmit(apdu)
    except ApduError:
        raise
    except Exception as exc:  # noqa: BLE001 — PC/SC-Fehler wrappen
        raise ApduError(f"{what}: Übertragungsfehler ({exc})") from exc
    return list(response), sw1, sw2


def _transmit(
    connection: CardConnectionLike, apdu: list[int], what: str,
) -> list[int]:
    """APDU senden mit Standard-Antwortbehandlung (ISO 7816):

    - `90 00` -> Daten direkt zurück.
    - `61 XX` -> GET RESPONSE (`00 C0 00 00 <XX>`, XX=0 heißt 256),
      ggf. mehrmals, bis kein `61 XX` mehr kommt (Endlosschutz).
    - `6C XX` (falsches Le, nur bei Le-tragenden APDUs der Länge 5) ->
      genau einmal mit korrigiertem Le wiederholen.
    - Sonst: ApduError mit SW.
    """
    response, sw1, sw2 = _raw_transmit(connection, apdu, what)
    if sw1 == 0x6C and len(apdu) == 5:
        response, sw1, sw2 = _raw_transmit(
            connection, [*apdu[:-1], sw2], f"{what} (Le-Korrektur)",
        )
    data = list(response)
    rounds = 0
    while sw1 == 0x61:
        rounds += 1
        if rounds > _MAX_GET_RESPONSE_ROUNDS:
            raise ApduError(
                f"{what}: GET-RESPONSE-Endlosschutz "
                f"(>{_MAX_GET_RESPONSE_ROUNDS} Runden)."
            )
        chunk, sw1, sw2 = _raw_transmit(
            connection,
            # Le-Byte 0x00 heißt 256 (kommt hier nur bei sw2 == 0 vor).
            [_GET_RESPONSE_CLA, _GET_RESPONSE_INS, 0x00, 0x00, sw2],
            f"{what} (GET RESPONSE)",
        )
        data.extend(chunk)
    _check_sw(sw1, sw2, what)
    return data


# --- Öffentliche API (Signaturen aus Architekturkonzept §7.b) ----------------

def get_dynamic_options(connection: CardConnectionLike) -> DynamicOptions:
    """Dynamic Options lesen. APDU (`80 64 06 00 02`, Le=2).

    Antwort: 2 Bytes uint16-BE (put_uint16_be, cmd_extras.c) — Bits im
    HIGH-Byte. VORAUSSETZUNG: vorheriger PIN-Login (sonst SW=6982,
    am Board gemessen).
    """
    raw = _transmit(
        connection,
        [_CLA_VENDOR, _INS_CUSTOM, _P1_DYNOPTS, 0x00, _LEN_DYNOPTS_GET],
        "Dynamic Options lesen",
    )
    return DynamicOptions.from_response(raw)


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
        [_CLA_VENDOR, _INS_CUSTOM, _P1_DYNOPTS, 0x00, _LEN_DYNOPTS_SET, mask],
        "Dynamic Options setzen",
    )


# --- Verbindung (pyscard, lazy import) ---------------------------------------

def list_readers() -> list[str]:
    """PC/SC-Reader-Namen auflisten (ohne Karten-Probe, billig/pollbar).

    Rein lesend — wirft ApduError bei fehlendem pyscard/PCSC-Dienst.
    """
    try:
        from smartcard.System import readers  # type: ignore
    except ImportError as exc:
        raise ApduError(
            "pyscard ist nicht installiert (pip install pyscard)."
        ) from exc
    try:
        return [str(reader) for reader in readers()]
    except Exception as exc:  # noqa: BLE001 — PC/SC-Dienstfehler
        raise ApduError(f"PC/SC-Readerliste nicht lesbar ({exc}).") from exc


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
            "pyscard ist nicht installiert — für `setup dynamic-options` "
            "erforderlich (pip install pyscard, siehe pyproject.toml)."
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
            "anderer lokaler Prozess (z.B. eine offene PKCS#11-Session) "
            "den Reader belegt. APDU-Zugriff ist sequenziell/"
            "exklusiv (§7.b)."
        ) from exc
    return connection
