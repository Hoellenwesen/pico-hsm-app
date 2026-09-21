"""
test_apdu_core.py — Tests ohne echte Hardware.

Deckt ab, was OHNE Pico-HSM-Board verifizierbar ist: exakte
APDU-Bytefolgen (SET-Beispiele aus pico-hsm/doc/extra_command.md,
GET-Form + Bitlage aus pico-hsm/src/hsm/cmd_extras.c + sc_hsm.h),
Options-Bitmaske, GET-RESPONSE/Le-Korrektur und Fehlerabbildung. Die
Verbindung wird durch eine Fake-Klasse mit derselben transmit()-
Signatur wie pyscards CardConnection ersetzt (wahlweise mit
Antwort-Skript für Mehrschritt-Verläufe).

Hardware-Verifikation am Ersatzboard: SW-Codes (6982 ohne Login, 6A86
für nicht existierendes Datetime-Kommando, 6101 mit 1 Byte) und
2-Byte-GET verhalten sich quellkonform; Voll-Roundtrip mit gesetzten
Bits steht aus (danach P2C-Tastendruck-Pflicht beachten, cmd_extras.c).
"""

from __future__ import annotations

import sys
import types

import pytest

from pico_hsm_tools import apdu_core as ac


SW_OK = (0x90, 0x00)


class FakeConnection:
    """Fake für pyscards CardConnection: zeichnet APDUs auf, liefert
    vorgefertigte Antworten. `sw` für Ein-Schritt-Fälle; `script` als
    Liste von (response, sw)-Tupeln für Mehrschritt-Verläufe
    (z.B. 61 XX -> GET RESPONSE)."""

    def __init__(
        self,
        response: list[int],
        sw: tuple[int, int] = SW_OK,
        script: list[tuple[list[int], tuple[int, int]]] | None = None,
    ) -> None:
        self.response = response
        self.sw = sw
        self.script = list(script) if script is not None else None
        self.sent: list[list[int]] = []
        self.disconnected = False

    def transmit(self, apdu: list[int]) -> tuple[list[int], int, int]:
        self.sent.append(list(apdu))
        if self.script:
            response, sw = self.script.pop(0)
            return list(response), sw[0], sw[1]
        return list(self.response), self.sw[0], self.sw[1]

    def disconnect(self) -> None:
        self.disconnected = True


# --- Dynamic Options: Bitmaske ---------------------------------------------------
# Bitlage HIGH-Byte (sc_hsm.h): 0x0100 = Press-to-Confirm
# (HSM_OPT_BOOTSEL_BUTTON), 0x0200 = Key-Usage-Counter
# (HSM_OPT_KEY_COUNTER_ALL). SET-Datenbyte landet im High-Byte.

@pytest.mark.parametrize(
    "mask,ptc,counter",
    [(0x00, False, False), (0x01, True, False),
     (0x02, False, True), (0x03, True, True)],
)
def test_dynamic_options_to_byte(mask, ptc, counter):
    options = ac.DynamicOptions(
        press_to_confirm=ptc, key_usage_counter=counter,
    )
    assert options.to_byte() == mask


@pytest.mark.parametrize(
    "high,ptc,counter",
    [(0x00, False, False), (0x01, True, False),
     (0x02, False, True), (0x03, True, True)],
)
def test_dynamic_options_from_response_high_byte(high, ptc, counter):
    options = ac.DynamicOptions.from_response([high, 0x00])
    assert options.press_to_confirm is ptc
    assert options.key_usage_counter is counter


def test_dynamic_options_ignores_unknown_bits():
    """Zukünftige Firmware-Bits dürfen Lesen nicht brechen."""
    options = ac.DynamicOptions.from_response([0xFC, 0x00])
    assert options.to_byte() == 0x00


def test_dynamic_options_rejects_wrong_length():
    with pytest.raises(ac.ApduError, match="2 Byte"):
        ac.DynamicOptions.from_response([0x01])
    with pytest.raises(ac.ApduError, match="2 Byte"):
        ac.DynamicOptions.from_response([0x01, 0x02, 0x03])


# --- Dynamic Options SET -----------------------------------------------------------
# Byte-identisch zu extra_command.md: 80 64 06 00 01 <mask>.

@pytest.mark.parametrize("mask", [0x00, 0x01, 0x02, 0x03])
def test_set_dynamic_options_sends_documented_apdu(mask):
    conn = FakeConnection([])
    ac.set_dynamic_options(
        conn,
        ac.DynamicOptions(
            press_to_confirm=bool(mask & 0x01),
            key_usage_counter=bool(mask & 0x02),
        ),
    )
    assert conn.sent == [[0x80, 0x64, 0x06, 0x00, 0x01, mask]]


def test_set_dynamic_options_propagates_sw_error():
    conn = FakeConnection([], sw=(0x69, 0x85))
    with pytest.raises(ac.ApduError, match="6985"):
        ac.set_dynamic_options(conn, ac.DynamicOptions())


# --- Dynamic Options GET ---------------------------------------------------------------
# `80 64 06 00 02` (Le=2, uint16-BE) — Form quellverifiziert
# (put_uint16_be, cmd_extras.c); am Board gemessen: Le=1-Variante
# antwortet 61 01 (daher GET-RESPONSE-Behandlung unten).

def test_get_dynamic_options_sends_two_byte_le():
    conn = FakeConnection([0x03, 0x00])
    options = ac.get_dynamic_options(conn)
    assert conn.sent == [[0x80, 0x64, 0x06, 0x00, 0x02]]
    assert options.press_to_confirm is True
    assert options.key_usage_counter is True


def test_get_dynamic_options_low_byte_ignored():
    """Nur High-Byte trägt Bits (Low-Byte bleibt beim SET erhalten)."""
    conn = FakeConnection([0x00, 0xFF])
    options = ac.get_dynamic_options(conn)
    assert options.press_to_confirm is False
    assert options.key_usage_counter is False


# --- GET RESPONSE (61 XX) / Le-Korrektur (6C XX) ------------------------------------

def test_transmit_fetches_remaining_via_get_response():
    """Am Board gemessen: Le=1-GET antwortet 61 01 — kein Fehler."""
    conn = FakeConnection(
        [], script=[([], (0x61, 0x01)), ([0x03, 0x00], (0x90, 0x00))],
    )
    options = ac.get_dynamic_options(conn)
    assert conn.sent[0] == [0x80, 0x64, 0x06, 0x00, 0x02]
    assert conn.sent[1] == [0x00, 0xC0, 0x00, 0x00, 0x01]
    assert options.press_to_confirm is True
    assert options.key_usage_counter is True


def test_transmit_repeats_get_response_until_done():
    conn = FakeConnection(
        [],
        script=[
            ([0xAA], (0x61, 0x01)),
            ([0xBB], (0x61, 0x01)),
            ([0xCC], (0x90, 0x00)),
        ],
    )
    raw = ac._transmit(conn, [0x80, 0x64, 0x06, 0x00, 0x02], "Test")
    assert raw == [0xAA, 0xBB, 0xCC]
    assert len(conn.sent) == 3


def test_transmit_retries_wrong_le_once():
    conn = FakeConnection(
        [], script=[([], (0x6C, 0x02)), ([0x03, 0x00], (0x90, 0x00))],
    )
    options = ac.get_dynamic_options(conn)
    assert conn.sent[0] == [0x80, 0x64, 0x06, 0x00, 0x02]
    assert conn.sent[1] == [0x80, 0x64, 0x06, 0x00, 0x02]
    assert options.press_to_confirm is True


def test_transmit_still_rejects_other_sw():
    conn = FakeConnection([], sw=(0x6A, 0x82))
    with pytest.raises(ac.ApduError, match="6A82"):
        ac.get_dynamic_options(conn)
    conn = FakeConnection([], sw=(0x69, 0x82))
    with pytest.raises(ac.ApduError, match="6982"):
        ac.get_dynamic_options(conn)


# --- Verbindung --------------------------------------------------------------

def _install_fake_smartcard(monkeypatch, readers):
    """Fake-`smartcard`-Modul für open_connection()-Tests."""
    system = types.ModuleType("smartcard.System")
    system.readers = lambda: readers  # noqa: E731
    package = types.ModuleType("smartcard")
    package.System = system  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "smartcard", package)
    monkeypatch.setitem(sys.modules, "smartcard.System", system)


class FakeReader:
    def __init__(self, name, has_card=True):
        self._name = name
        self._has_card = has_card
        self.connected = False

    def __str__(self):
        return self._name

    def createConnection(self):
        return FakeCard(self)


class FakeCard:
    def __init__(self, reader):
        self._reader = reader

    def connect(self):
        if not self._reader._has_card:
            raise RuntimeError("no card")
        self._reader.connected = True

    def disconnect(self):
        pass


def test_open_connection_missing_pyscard(monkeypatch):
    monkeypatch.setitem(sys.modules, "smartcard", None)
    monkeypatch.setitem(sys.modules, "smartcard.System", None)
    with pytest.raises(ac.ApduError, match="pyscard"):
        ac.open_connection()


def test_open_connection_no_reader(monkeypatch):
    _install_fake_smartcard(monkeypatch, [])
    with pytest.raises(ac.ApduError, match="Genau ein Reader"):
        ac.open_connection()


def test_open_connection_no_card(monkeypatch):
    _install_fake_smartcard(monkeypatch, [FakeReader("R1", has_card=False)])
    with pytest.raises(ac.ApduError, match="Genau ein Reader"):
        ac.open_connection()


def test_open_connection_ambiguous(monkeypatch):
    _install_fake_smartcard(
        monkeypatch, [FakeReader("R1"), FakeReader("R2")],
    )
    with pytest.raises(ac.ApduError, match="Genau ein Reader"):
        ac.open_connection()


def test_open_connection_single_reader(monkeypatch):
    reader = FakeReader("Pico HSM 0")
    _install_fake_smartcard(monkeypatch, [reader])
    conn = ac.open_connection()
    assert reader.connected is True
    assert isinstance(conn, FakeCard)


def test_open_connection_named_reader(monkeypatch):
    readers = [FakeReader("Anderer 0"), FakeReader("Pico HSM 0")]
    _install_fake_smartcard(monkeypatch, readers)
    conn = ac.open_connection(reader="Pico HSM 0")
    assert readers[1].connected is True
    assert isinstance(conn, FakeCard)


def test_open_connection_named_reader_missing(monkeypatch):
    _install_fake_smartcard(monkeypatch, [FakeReader("Anderer 0")])
    with pytest.raises(ac.ApduError, match="nicht gefunden"):
        ac.open_connection(reader="Pico HSM 0")
