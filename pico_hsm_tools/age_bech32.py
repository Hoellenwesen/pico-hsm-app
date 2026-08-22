"""
age_bech32.py — Bech32 encode/decode (BIP-173) für age-Identity-Keys.

age-Secret-Keys sind Bech32-kodierte 32-Byte X25519-Scalare mit dem
Human-Readable-Part "age-secret-key" (im Text als "AGE-SECRET-KEY-1..."
dargestellt, komplett großgeschrieben). Dieses Modul dekodiert einen
solchen String auf die rohen 32 Byte (damit sie mit ssss-split, das auf
max. 64 Byte/128 Hexzeichen begrenzt ist, gesplittet werden können) und
kodiert sie nach der Shamir-Rekonstruktion wieder zurück in einen
gültigen age-Identity-String.

Reine Formatkonvertierung, keine eigene Kryptografie — die eigentliche
Verschlüsselung bleibt bei `age`. Verifiziert gegen echte age-keygen-
Ausgabe (siehe Kommentar in backup_core.py).

Referenzimplementierung nach BIP-173 (public domain, Pieter Wuille),
angepasst für dieses Projekt.
"""

from __future__ import annotations

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
AGE_IDENTITY_HRP = "age-secret-key-"


class Bech32Error(Exception):
    pass


def _polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= generator[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _create_checksum(hrp: str, data: list[int]) -> list[int]:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: list[int]) -> bool:
    return _polymod(_hrp_expand(hrp) + data) == 1


def bech32_encode(hrp: str, data: list[int]) -> str:
    combined = data + _create_checksum(hrp, data)
    return hrp + "1" + "".join(CHARSET[d] for d in combined)


def bech32_decode(bech: str) -> tuple[str, list[int]]:
    if any(ord(c) < 33 or ord(c) > 126 for c in bech):
        raise Bech32Error("Ungültiges Zeichen in Bech32-String.")
    lowered, uppered = bech.lower(), bech.upper()
    if bech != lowered and bech != uppered:
        raise Bech32Error("Bech32-String mischt Groß-/Kleinschreibung.")
    bech = lowered
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        raise Bech32Error("Bech32-Separator '1' nicht gefunden oder ungültig platziert.")
    hrp = bech[:pos]
    try:
        data = [CHARSET.index(c) for c in bech[pos + 1:]]
    except ValueError as exc:
        raise Bech32Error("Ungültiges Bech32-Zeichen in der Daten-Sektion.") from exc
    if not _verify_checksum(hrp, data):
        raise Bech32Error("Bech32-Checksumme ungültig — String beschädigt oder falsch?")
    return hrp, data[:-6]


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int]:
    acc, bits = 0, 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            raise Bech32Error("Ungültiger Wert beim Bit-Umwandeln.")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise Bech32Error("Ungültiges Padding beim Bit-Umwandeln.")
    return ret


def age_identity_to_raw(identity_str: str) -> bytes:
    """'AGE-SECRET-KEY-1...' -> rohe 32 Byte."""
    hrp, data5 = bech32_decode(identity_str.strip())
    if hrp != AGE_IDENTITY_HRP:
        raise Bech32Error(
            f"Unerwarteter HRP '{hrp}', erwartet '{AGE_IDENTITY_HRP}' — "
            "ist das wirklich ein age-Identity-String?"
        )
    raw = bytes(_convertbits(data5, 5, 8, False))
    if len(raw) != 32:
        raise Bech32Error(f"Erwartet 32 Byte Rohschlüssel, bekommen {len(raw)}.")
    return raw


def raw_to_age_identity(raw: bytes) -> str:
    """Rohe 32 Byte -> 'AGE-SECRET-KEY-1...' (age-Spezifikation: komplett
    großgeschrieben)."""
    if len(raw) != 32:
        raise Bech32Error(f"Erwartet 32 Byte, bekommen {len(raw)}.")
    data5 = _convertbits(list(raw), 8, 5, True)
    encoded = bech32_encode(AGE_IDENTITY_HRP, data5)
    return encoded.upper()
