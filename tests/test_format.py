"""Phase 1 task 6: every number an operator reads is Persian, every date Jalali."""

import datetime as dt

import pytest

from app.web.format import fa_digits, fa_number, jalali_date, jalali_datetime, toman_from_rial


def test_fa_digits_converts_every_ascii_digit():
    assert fa_digits("0123456789") == "۰۱۲۳۴۵۶۷۸۹"
    assert fa_digits("FX-0012") == "FX-۰۰۱۲"


def test_fa_number_groups_thousands_with_the_persian_separator():
    assert fa_number(0) == "۰"
    assert fa_number(1250000) == "۱٬۲۵۰٬۰۰۰"
    assert fa_number(-5000) == "−۵٬۰۰۰"


def test_toman_from_rial_divides_by_ten_and_labels():
    assert toman_from_rial(12_500_000) == "۱٬۲۵۰٬۰۰۰ تومان"
    assert toman_from_rial(5) == "۰٫۵ تومان"


@pytest.mark.parametrize(
    ("gregorian", "jalali"),
    [
        (dt.date(2026, 3, 21), "۱۴۰۵/۰۱/۰۱"),
        (dt.date(2026, 9, 19), "۱۴۰۵/۰۶/۲۸"),
        (dt.date(2026, 1, 1), "۱۴۰۴/۱۰/۱۱"),
        (dt.date(2025, 3, 20), "۱۴۰۳/۱۲/۳۰"),  # 1403 is a leap year
    ],
)
def test_jalali_date_matches_known_calendar_points(gregorian, jalali):
    assert jalali_date(gregorian) == jalali


def test_jalali_datetime_is_rendered_in_tehran_time():
    at = dt.datetime(2026, 9, 19, 21, 30, tzinfo=dt.timezone.utc)  # 01:00 next day in Tehran
    assert jalali_datetime(at) == "۱۴۰۵/۰۶/۲۹ ۰۱:۰۰"


def test_a_typed_jalali_date_round_trips_through_the_converter():
    """The inverse is only trustworthy if it agrees with the forward
    conversion the whole panel already renders with."""
    import datetime as dt

    from app.web.format import gregorian_to_jalali, jalali_to_gregorian, parse_jalali_date

    day = dt.date(2020, 1, 1)
    while day < dt.date(2032, 1, 1):
        jy, jm, jd = gregorian_to_jalali(day.year, day.month, day.day)
        assert jalali_to_gregorian(jy, jm, jd) == day, f"{day} -> {jy}/{jm}/{jd} -> back"
        day += dt.timedelta(days=1)

    assert parse_jalali_date("۱۴۰۵/۱۰/۱۰") == parse_jalali_date("1405/10/10")
    for bad in ("", "nonsense", "1405/13/01", "1405/10"):
        try:
            parse_jalali_date(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")
