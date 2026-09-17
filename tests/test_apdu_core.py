"""
test_apdu_core.py — Tests ohne echte Hardware.

Deckt ab, was OHNE Pico-HSM-Board verifizierbar ist: exakte
APDU-Bytefolgen gegen die Beispiele aus pico-hsm/doc/extra_command.md,
Datetime-Kodierung (inkl. Wochentag), Options-Bitmaske und
Fehlerabbildung. Die Verbindung wird durch eine Fake-Klasse mit
derselben transmit()-Signatur wie pyscards CardConnection ersetzt.

Was HIER NICHT getestet wird (siehe Architekturkonzept §12,
apdu_core-Moduldoc): ob die Firmware die APDUs tatsächlich so
beantwortet — das braucht echte Hardware. Insbesondere das
Dynamic-Options-GET (`80 64 06 00 01`) ist nur aus dem Befehlsschema
abgeleitet, nicht dokumentiert.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime

import pytest

from pico_hsm_tools import apdu_core as ac


SW_OK = (0x90, 0x00)


class FakeConnection:
    """Fake für pyscards CardConnection: zeichnet APDUs auf, liefert
    vorgefertigte Antworten. SW programmierbar für Fehlertests."""

    def __init__(
        self,
        response: list[int],
        sw: tuple[int, int] = SW_OK,
    ) -> None:
        self.response = response
        self.sw = sw
        self.sent: list[list[int]] = []
        self.disconnected = False

    def transmit(self, apdu: list[int]) -> tuple[list[int], int, int]:
        self.sent.append(list(apdu))
        return list(self.response), self.sw[0], self.sw[1]

    def disconnect(self) -> None:
        self.disconnected = True


# --- Datetime GET ----------------------------------------------------------

def test_get_datetime_sends_documented_apdu():
    conn = FakeConnection([0x07, 0xE6, 0x04, 0x06, 0x03, 0x13, 0x29, 0x1E])
    ac.get_datetime(conn)
    assert conn.sent == [[0x80, 0x64, 0x0A, 0x00, 0x08]]


def test_get_datetime_parses_documented_example():
    """Antwortbytes aus extra_command.md: 07 E6 04 06 03 13 29 1E."""
    conn = FakeConnection([0x07, 0xE6, 0x04, 0x06, 0x03, 0x13, 0x29, 0x1E])
    value = ac.get_datetime(conn)
    assert value == datetime(2022, 4, 6, 19, 41, 30)
    assert value.isoweekday() == 3  # Mittwoch, wie im Doc-Byte 03


def test_get_datetime_rejects_bad_sw():
    conn = FakeConnection([], sw=(0x6A, 0x82))
    with pytest.raises(ac.ApduError, match="6A82"):
        ac.get_datetime(conn)


def test_get_datetime_rejects_wrong_length():
    conn = FakeConnection([0x07, 0xE6, 0x04])
    with pytest.raises(ac.ApduError, match="8 Byte"):
        ac.get_datetime(conn)


def test_get_datetime_rejects_invalid_date():
    conn = FakeConnection([0x07, 0xE6, 0x0D, 0x06, 0x03, 0x13, 0x29, 0x1E])
    with pytest.raises(ac.ApduError, match="Ungültig"):
        ac.get_datetime(conn)


# --- Datetime SET ----------------------------------------------------------

def test_set_datetime_builds_documented_apdu():
    """Bytefolge aus extra_command.md: setzt 2022-04-06 19:41:23."""
    conn = FakeConnection([])
    ac.set_datetime(conn, datetime(2022, 4, 6, 19, 41, 23))
    assert conn.sent == [[
        0x80, 0x64, 0x0A, 0x00, 0x08,
        0x07, 0xE6, 0x04, 0x06, 0x03, 0x13, 0x29, 0x17,
    ]]


@pytest.mark.parametrize(
    "iso,weekday_byte",
    [
        ("2022-04-03T00:00:00", 0x00),  # Sonntag
        ("2022-04-04T00:00:00", 0x01),  # Montag
        ("2026-09-16T12:00:00", 0x03),  # Mittwoch
        ("2022-04-09T23:59:59", 0x06),  # Samstag
    ],
)
def test_set_datetime_weekday_encoding(iso, weekday_byte):
    """Firmware: 00h=So..06h=Sa (extra_command.md)."""
    conn = FakeConnection([])
    ac.set_datetime(conn, datetime.fromisoformat(iso))
    assert conn.sent[0][9] == weekday_byte


def test_set_datetime_rejects_year_out_of_range():
    conn = FakeConnection([])
    with pytest.raises(ac.ApduError, match="2019"):
        ac.set_datetime(conn, datetime(2019, 12, 31, 23, 59, 59))
    with pytest.raises(ac.ApduError, match="2100"):
        ac.set_datetime(conn, datetime(2100, 1, 1, 0, 0, 0))
    assert conn.sent == []


def test_set_datetime_propagates_sw_error():
    conn = FakeConnection([], sw=(0x69, 0x85))
    with pytest.raises(ac.ApduError, match="6985"):
        ac.set_datetime(conn, datetime(2022, 4, 6, 19, 41, 23))


# --- Dynamic Options ---------------------------------------------------------

@pytest.mark.parametrize(
    "mask,ptc,counter",
    [(0x00, False, False), (0x01, True, False),
     (0x02, False, True), (0x03, True, True)],
)
def test_dynamic_options_bitmask_roundtrip(mask, ptc, counter):
    options = ac.DynamicOptions.from_byte(mask)
    assert options.press_to_confirm is ptc
    assert options.key_usage_counter is counter
    assert options.to_byte() == mask


def test_dynamic_options_ignores_unknown_bits():
    """Zukünftige Firmware-Bits dürfen Lesen nicht brechen."""
    options = ac.DynamicOptions.from_byte(0xFC)
    assert options.to_byte() == 0x00


def test_dynamic_options_rejects_out_of_range():
    with pytest.raises(ac.ApduError, match="0x100"):
        ac.DynamicOptions.from_byte(0x100)


@pytest.mark.parametrize("mask", [0x00, 0x01, 0x02, 0x03])
def test_set_dynamic_options_sends_documented_apdu(mask):
    """`80 64 06 00 01 <mask>` — vgl. 806406000101/…00 in der Doku."""
    conn = FakeConnection([])
    ac.set_dynamic_options(conn, ac.DynamicOptions.from_byte(mask))
    assert conn.sent == [[0x80, 0x64, 0x06, 0x00, 0x01, mask]]


def test_get_dynamic_options_sends_le_form():
    """`80 64 06 00 01` (Le=1) — aus dem Schema abgeleitet, NICHT
    dokumentiert, daher am Board zu verifizieren (§12)."""
    conn = FakeConnection([0x03])
    options = ac.get_dynamic_options(conn)
    assert conn.sent == [[0x80, 0x64, 0x06, 0x00, 0x01]]
    assert options.press_to_confirm is True
    assert options.key_usage_counter is True


def test_get_dynamic_options_rejects_wrong_length():
    conn = FakeConnection([0x01, 0x02])
    with pytest.raises(ac.ApduError, match="1 Byte"):
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
