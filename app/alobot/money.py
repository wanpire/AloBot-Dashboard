"""The one place Toman becomes Rial.

AloBot stores Toman as Numeric(12,2). Bank SMS and this project's own tables
use integer Rial. A Toman amount with more precision than a tenth of a Toman
cannot be a real price and is refused rather than rounded: rounding money
silently is how two systems disagree by one Rial forever.
"""

from __future__ import annotations

from decimal import Decimal


def toman_to_rial(toman: Decimal | int | None) -> int | None:
    if toman is None:
        return None
    rial = Decimal(toman) * 10
    if rial != rial.to_integral_value():
        raise ValueError(f"{toman} Toman is not a whole number of Rial")
    return int(rial)


def rial_to_toman(rial: int) -> Decimal:
    return (Decimal(rial) / 10).normalize() if rial % 10 else Decimal(rial // 10)
