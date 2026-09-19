"""Persian digits and Jalali dates for every screen an operator reads.

The Jalali conversion is the standard arithmetic algorithm (the same one the
common `jalaali` libraries implement), written out here so the project has
no dependency for a 20-line calculation and the tests pin known calendar
points including a leap year.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
THOUSANDS = "٬"  # U+066C Arabic thousands separator
DECIMAL = "٫"  # U+066B Arabic decimal separator
MINUS = "−"  # U+2212, renders correctly in RTL text


def fa_digits(text: str) -> str:
    return str(text).translate(_FA_DIGITS)


def fa_number(value: int | float) -> str:
    negative = value < 0
    value = abs(value)
    if isinstance(value, float) and not value.is_integer():
        whole, frac = f"{value:,.1f}".split(".")
        text = f"{whole.replace(',', THOUSANDS)}{DECIMAL}{frac}"
    else:
        text = f"{int(value):,}".replace(",", THOUSANDS)
    return fa_digits((MINUS if negative else "") + text)


def toman_from_rial(rial: int) -> str:
    toman = rial / 10
    return f"{fa_number(int(toman) if toman.is_integer() else toman)} تومان"


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_days_in_month = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_days_in_month[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_date(value: dt.date) -> str:
    jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
    return fa_digits(f"{jy:04d}/{jm:02d}/{jd:02d}")


def jalali_datetime(value: dt.datetime) -> str:
    local = value.astimezone(TEHRAN) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc).astimezone(TEHRAN)
    return f"{jalali_date(local.date())} {fa_digits(local.strftime('%H:%M'))}"
