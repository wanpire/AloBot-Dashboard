"""Phase 2 task 2: the two conversions at the AloBot boundary - Toman to Rial,
and AloBot's order/invoice codes - pinned to what AloBot's own source says."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.alobot.codes import decode_id, encode_id
from app.alobot.money import rial_to_toman, toman_to_rial

ALOBOT = Path(__file__).parent.parent / "vendor" / "alobot"


def test_toman_to_rial_multiplies_by_ten_exactly():
    assert toman_to_rial(Decimal("125000.00")) == 1_250_000
    assert toman_to_rial(Decimal("0.5")) == 5
    assert toman_to_rial(None) is None


def test_toman_with_sub_rial_precision_is_refused_not_rounded():
    with pytest.raises(ValueError):
        toman_to_rial(Decimal("0.25"))


def test_rial_to_toman_is_the_inverse():
    assert rial_to_toman(1_250_000) == Decimal("125000")
    assert rial_to_toman(5) == Decimal("0.5")


def test_order_codes_use_alobots_own_alphabet():
    source = (ALOBOT / "app" / "services" / "order_codes.py").read_text()
    alphabet = source.split('_ALPHABET = "')[1].split('"')[0]
    from app.alobot.codes import ALPHABET

    assert ALPHABET == alphabet, "AloBot changed its code alphabet - update codes.py and ALOBOT_COMMIT"


def test_order_codes_round_trip_and_reject_garbage():
    assert decode_id(encode_id(12345)) == 12345
    assert len(encode_id(1)) >= 6
    assert decode_id("not-a-code!") is None
    assert decode_id("") is None
