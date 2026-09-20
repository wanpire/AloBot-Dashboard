"""What the dashboard did to a reseller's balance.

AloBot's `resellers.balance` is a single column with no history behind it, so
a top-up leaves no trace on AloBot's side beyond the new number. This is that
trace: what was asked for, what the balance was before, what it was after,
and whether the read-back agreed. `verified=False` means the money did not
land as intended and somebody must look.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResellerTopUp(Base):
    __tablename__ = "reseller_topups"
    __table_args__ = (CheckConstraint("amount_irr > 0", name="reseller_topups_amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount_irr: Mapped[int] = mapped_column(BigInteger)
    balance_before_irr: Mapped[int] = mapped_column(BigInteger)
    balance_after_irr: Mapped[int] = mapped_column(BigInteger)
    # False when the balance read back afterwards did not match what this
    # top-up should have produced - see app/alobot/writes/resellers.py.
    verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id", ondelete="SET NULL"), nullable=True)
    operator_email: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
