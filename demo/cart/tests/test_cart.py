import pytest

from cart import apply_discount, cart_total


def test_no_discount():
    assert cart_total([10, 20]) == 30


def test_ten_percent():
    assert apply_discount(100, 10) == 90


def test_rejects_bad_percent():
    with pytest.raises(ValueError):
        apply_discount(100, 120)
