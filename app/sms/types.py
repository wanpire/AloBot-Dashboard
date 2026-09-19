from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Classification = str  # BANK_TRANSACTION | BALANCE | OTP | PROMOTIONAL | UNKNOWN
Direction = str  # CREDIT | DEBIT | UNKNOWN


@dataclass
class ParseResult:
    classification: Classification
    direction: Direction = "UNKNOWN"
    amount_irr: int | None = None
    balance_irr: int | None = None
    account_hint: str | None = None
    reference: str | None = None
    bank_name: str | None = None
    confidence: float = 0.0
    parser_id: str | None = None
    parser_version: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.classification in ("BANK_TRANSACTION", "BALANCE")
