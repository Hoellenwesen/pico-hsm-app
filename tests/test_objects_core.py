"""
test_objects_core.py — Tests ohne echte Hardware.

Deckt ab, was OHNE Pico-HSM-Board verifizierbar ist: Eingabevalidierung,
Byte/Bit-Umrechnung, Attribut-Konstruktion und — am wichtigsten — die
EC-Kurvennamen-Zuordnung (siehe Test unten: die Brainpool-Namen aus
usage.md sind case-sensitiv anders als von asn1crypto erwartet, das
hier real gegen die installierte asn1crypto-Version geprüft wird,
nicht nur angenommen).

Was HIER NICHT getestet wird (siehe Architekturkonzept §12): ob
python-pkcs11s generate_keypair/generate_key/create_object exakt die
Objektattribute erzeugen, die die Firmware erwartet — das braucht
echte Hardware.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pkcs11
import pytest
from pkcs11 import Attribute, ObjectClass
from pkcs11.util.ec import encode_named_curve_parameters

from pico_hsm_tools import objects_core as oc


# --- RSA ---------------------------------------------------------------

def test_generate_rsa_keypair_rejects_unsupported_bits():
    session = MagicMock()
    with pytest.raises(oc.ObjectsError, match="1234"):
        oc.generate_rsa_keypair(session, 1234, id=b"\x01", label="x")
    session.generate_keypair.assert_not_called()


def test_generate_rsa_keypair_calls_session_with_expected_args():
    session = MagicMock()
    info = oc.generate_rsa_keypair(session, 2048, id=b"\x01", label="mykey")
    session.generate_keypair.assert_called_once_with(
        pkcs11.KeyType.RSA, key_length=2048, id=b"\x01", label="mykey", store=True,
    )
    assert info.key_type == "RSA"
    assert info.key_length_bits == 2048
    assert info.label == "mykey"


def test_generate_rsa_keypair_wraps_pkcs11_errors():
    session = MagicMock()
    session.generate_keypair.side_effect = pkcs11.exceptions.DeviceError("boom")
    with pytest.raises(oc.ObjectsError, match="boom"):
        oc.generate_rsa_keypair(session, 2048, id=b"\x01", label="x")


# --- EC — inklusive des real gefundenen Brainpool-Namensproblems -------

@pytest.mark.parametrize("curve", sorted(oc.EC_CURVES))
def test_all_documented_curves_resolve_without_error(curve):
    """Regressionstest für den Brainpool-Namensbug: jede in usage.md
    dokumentierte Kurve muss über EC_CURVES auf einen Namen abbilden,
    den asn1crypto tatsächlich kennt."""
    der = encode_named_curve_parameters(oc.EC_CURVES[curve])
    assert isinstance(der, bytes) and len(der) > 0


def test_generate_ec_keypair_uses_ec_params_public_template():
    session = MagicMock()
    oc.generate_ec_keypair(session, "brainpoolP256r1", id=b"\x02", label="ec1")
    _, kwargs = session.generate_keypair.call_args
    assert kwargs["id"] == b"\x02"
    assert kwargs["label"] == "ec1"
    assert kwargs["store"] is True
    expected_der = encode_named_curve_parameters("brainpoolp256r1")
    assert kwargs["public_template"][Attribute.EC_PARAMS] == expected_der


def test_generate_ec_keypair_rejects_unknown_curve():
    session = MagicMock()
    with pytest.raises(oc.ObjectsError, match="secp999"):
        oc.generate_ec_keypair(session, "secp999", id=b"\x01", label="x")
    session.generate_keypair.assert_not_called()


# --- AES -----------------------------------------------------------------

def test_generate_aes_key_converts_bytes_to_bits():
    session = MagicMock()
    info = oc.generate_aes_key(session, 32, id=b"\x0c", label="aes32")
    session.generate_key.assert_called_once_with(
        pkcs11.KeyType.AES, key_length=256, id=b"\x0c", label="aes32", store=True,
    )
    assert info.key_length_bits == 256


def test_generate_aes_key_rejects_unsupported_length():
    session = MagicMock()
    with pytest.raises(oc.ObjectsError, match="20"):
        oc.generate_aes_key(session, 20, id=b"\x01", label="x")
    session.generate_key.assert_not_called()


# --- list_objects / delete_object --------------------------------------------
# Hardware-Befund (Ersatzboard): ungefilterte get_objects()-Suche liefert
# selbst angelegte Objekte NICHT zurück (nur PROFILE-Systemobjekt) —
# Liste und Löschung arbeiten daher mit CLASS-Filtern (wie der
# funktionierende Read-Pfad). Tests bilden genau das ab.
# Display-Befund: seit Python 3.11 rendert str(IntEnum) die Zahl
# (`ObjectClass.PRIVATE_KEY` -> "3") — _display_name mappt auf Namen.

def _fake_object(label, id_=None, object_class="PRIVATE_KEY", key_type="RSA"):
    obj = MagicMock()
    obj.label = label
    obj.id = id_
    obj.object_class = object_class
    obj.key_type = key_type
    return obj


def _objects_by_class(mapping):
    """get_objects-Seiteneffekt: Antwort je CLASS-Filter (leere Suche
    gibt NICHTS zurück — wie echte Hardware)."""
    def side_effect(template=None):
        if not template or Attribute.CLASS not in template:
            return []
        return list(mapping.get(template[Attribute.CLASS], []))

    return side_effect


def test_list_objects_maps_fields():
    session = MagicMock()
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.PRIVATE_KEY: [_fake_object("key1", id_=b"\x01")],
        ObjectClass.DATA: [_fake_object(
            "data1", object_class="DATA", key_type=None)],
    })
    result = oc.list_objects(session)
    assert [o.label for o in result] == ["key1", "data1"]
    assert result[0].id == b"\x01"
    assert result[0].key_type == "RSA"
    assert result[1].key_type is None


def test_list_objects_never_searches_unfiltered():
    """Regressionstest für den Hardware-Befund: keine leere Suche
    (die auf echter Hardware selbst angelegte Objekte unterschlägt)."""
    session = MagicMock()
    session.get_objects.side_effect = _objects_by_class({})
    oc.list_objects(session)
    assert session.get_objects.call_count == len(oc._LIST_SEARCH_CLASSES)
    for call in session.get_objects.call_args_list:
        template = call.args[0]
        assert Attribute.CLASS in template


def test_list_objects_queries_all_classes():
    session = MagicMock()
    session.get_objects.side_effect = _objects_by_class({})
    oc.list_objects(session)
    queried = {
        call.args[0][Attribute.CLASS]
        for call in session.get_objects.call_args_list
    }
    assert queried == set(oc._LIST_SEARCH_CLASSES)


def test_delete_object_found_and_not_found():
    session = MagicMock()
    target = _fake_object("todelete")
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.PRIVATE_KEY: [target],
    })
    assert oc.delete_object(session, "todelete") is True
    target.destroy.assert_called_once()

    session2 = MagicMock()
    session2.get_objects.side_effect = _objects_by_class({})
    assert oc.delete_object(session2, "nichtda") is False


def test_delete_object_finds_data_object():
    """Der gemeldete Hardware-Fall (hwtest01): nur per CLASS+LABEL-Filter
    sichtbar, nie per Listen-Vergleich."""
    session = MagicMock()
    target = _fake_object("hwtest01", object_class="DATA", key_type=None)
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.DATA: [target],
    })
    assert oc.delete_object(session, "hwtest01") is True
    target.destroy.assert_called_once()
    templates = [
        call.args[0] for call in session.get_objects.call_args_list
    ]
    assert all(
        t.get(Attribute.LABEL) == "hwtest01" for t in templates
    )


def test_delete_object_searches_classes_in_order():
    session = MagicMock()
    session.get_objects.side_effect = _objects_by_class({})
    oc.delete_object(session, "x")
    classes = [
        call.args[0][Attribute.CLASS]
        for call in session.get_objects.call_args_list
    ]
    assert classes == list(oc._DELETE_SEARCH_CLASSES)


def test_list_shows_certificates():
    """Hardware-Befund (Unwrap-Blockade durch fid ce01): Zertifikate
    müssen sichtbar sein, sonst blockieren sie unsichtbar."""
    session = MagicMock()
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.CERTIFICATE: [_fake_object(
            "cert1", object_class="CERTIFICATE", key_type=None)],
    })
    result = oc.list_objects(session)
    assert [o.label for o in result] == ["cert1"]
    assert result[0].object_class == "CERTIFICATE"


def test_delete_removes_certificate():
    session = MagicMock()
    target = _fake_object("cert1", object_class="CERTIFICATE", key_type=None)
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.CERTIFICATE: [target],
    })
    assert oc.delete_object(session, "cert1") is True
    target.destroy.assert_called_once()


def test_display_name_maps_enums_not_numbers():
    """Seit Python 3.11: str(IntEnum) ist die Zahl — Anzeige braucht Namen."""
    assert oc._display_name(ObjectClass.PRIVATE_KEY) == "PRIVATE_KEY"
    assert oc._display_name(ObjectClass.PUBLIC_KEY) == "PUBLIC_KEY"
    assert oc._display_name("DATA") == "DATA"
    assert oc._display_name(None) == "None"


def test_list_objects_shows_enum_names():
    session = MagicMock()
    key = MagicMock()
    key.label = "k1"
    key.id = b"\x01"
    key.object_class = ObjectClass.PRIVATE_KEY
    from pkcs11 import KeyType
    key.key_type = KeyType.RSA
    session.get_objects.side_effect = _objects_by_class({
        ObjectClass.PRIVATE_KEY: [key],
    })
    result = oc.list_objects(session)
    assert result[0].object_class == "PRIVATE_KEY"
    assert result[0].key_type == "RSA"


# --- Datenobjekte ----------------------------------------------------------

def test_write_data_object_rejects_oversized_payload():
    session = MagicMock()
    with pytest.raises(oc.ObjectsError, match="4096"):
        oc.write_data_object(session, b"x" * 4097, label="too-big")
    session.create_object.assert_not_called()


def test_write_data_object_builds_expected_attrs_private_default():
    session = MagicMock()
    oc.write_data_object(session, b"hello", label="test1", id=b"\x01")
    (attrs,), _ = session.create_object.call_args
    assert attrs[Attribute.CLASS] == ObjectClass.DATA
    assert attrs[Attribute.LABEL] == "test1"
    assert attrs[Attribute.APPLICATION] == "test1"
    assert attrs[Attribute.VALUE] == b"hello"
    assert attrs[Attribute.PRIVATE] is True  # sicherer Default
    assert attrs[Attribute.TOKEN] is True
    assert attrs[Attribute.ID] == b"\x01"


def test_write_data_object_not_private_opt_out():
    session = MagicMock()
    oc.write_data_object(session, b"hello", label="test2", private=False)
    (attrs,), _ = session.create_object.call_args
    assert attrs[Attribute.PRIVATE] is False
    assert Attribute.ID not in attrs  # kein id angegeben


def test_read_data_object_found_and_not_found():
    session = MagicMock()
    obj = MagicMock()
    obj.__getitem__.return_value = b"payload"
    session.get_objects.return_value = [obj]
    assert oc.read_data_object(session, "test1") == b"payload"

    session2 = MagicMock()
    session2.get_objects.return_value = []
    with pytest.raises(oc.ObjectsError, match="test1"):
        oc.read_data_object(session2, "test1")


# --- Zufallszahlen ----------------------------------------------------------

def test_generate_random_converts_bytes_to_bits():
    session = MagicMock()
    session.generate_random.return_value = b"\x00" * 64
    result = oc.generate_random(session, 64)
    session.generate_random.assert_called_once_with(512)
    assert result == b"\x00" * 64


@pytest.mark.parametrize("num_bytes", [0, -1, 1025, 5000])
def test_generate_random_rejects_out_of_range(num_bytes):
    session = MagicMock()
    with pytest.raises(oc.ObjectsError):
        oc.generate_random(session, num_bytes)
    session.generate_random.assert_not_called()
