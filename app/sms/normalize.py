"""Text normalisation for Persian bank SMS: one digit alphabet, one letter
alphabet, one kind of whitespace - so every parser sees the same text."""

from __future__ import annotations

import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_LETTERS = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی", "ة": "ه", "ً": ""})
_INVISIBLE = re.compile("[​‎‏‪-‮﻿]")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = text.translate(_DIGITS).translate(_LETTERS)
    text = _INVISIBLE.sub("", text)
    return _WS.sub(" ", text).strip()


_AMOUNT_JUNK = re.compile(r"[,٬٫'٬٫.\s]")


def parse_amount(raw: str) -> int | None:
    """'1,250,000' / '۱٬۲۵۰٬۰۰۰' / '+5.000.000' / '-450,000' → 1250000 …
    Separators (comma, Arabic comma, dot, space) are dropped; the sign is
    dropped too - direction is decided elsewhere from phrases and signs."""
    cleaned = _AMOUNT_JUNK.sub("", normalize(raw)).lstrip("+-−")
    if not cleaned.isdigit():
        return None
    return int(cleaned)
