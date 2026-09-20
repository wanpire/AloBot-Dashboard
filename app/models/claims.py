"""A payment a customer says they made, and the bank credit that proves it.

`payment_claims` mirrors AloBot's pending card payments (one row per AloBot
payment id) and carries THIS project's decision separately from AloBot's own
status, because until the integration phase AloBot decides on its own and
this project only records what the bank said.

`reconciliation_matches` is where the money rule lives: two partial unique
indexes say a transaction settles at most one claim and a claim is settled
at most once. Suggested and rejected rows are not exclusive - only settling is.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CLAIM_STATUSES = ("PENDING", "AUTO_VERIFIED", "MANUAL_VERIFIED", "REJECTED", "FAKE", "FULFILLED_UNRECONCILED")
SETTLED_STATUSES = ("AUTO_VERIFIED", "MANUAL_VERIFIED")
MATCH_STATUSES = ("SUGGESTED", "AUTO_VERIFIED", "CONFIRMED", "REJECTED")
SETTLING_MATCH_STATUSES = ("AUTO_VERIFIED", "CONFIRMED")


class PaymentClaim(Base):
    __tablename__ = "payment_claims"
    __table_args__ = (
        CheckConstraint(f"status IN {CLAIM_STATUSES}", name="payment_claims_status_check"),
        CheckConstraint("expected_amount_irr >= 0", name="payment_claims_amount_check"),
        Index("ix_payment_claims_status_paid", "status", "paid_clicked_at"),
        Index("ix_payment_claims_telegram", "telegram_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alobot_payment_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(16))
    expected_amount_irr: Mapped[int] = mapped_column(BigInteger)
    ibsng_username: Mapped[str] = mapped_column(String(64))
    invoice_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_card_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_account_id: Mapped[int | None] = mapped_column(ForeignKey("financial_accounts.id", ondelete="SET NULL"), nullable=True)
    paid_clicked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    receipt_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", server_default="PENDING")
    alobot_status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    alobot_resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    continuity: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    suspect_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    candidate_transaction_ids: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    last_evaluated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "matcher" or operator email
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messaged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messaged_template: Mapped[str | None] = mapped_column(String(48), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReconciliationMatch(Base):
    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        CheckConstraint(f"status IN {MATCH_STATUSES}", name="reconciliation_matches_status_check"),
        Index("uq_match_one_settling_per_transaction", "transaction_id", unique=True, postgresql_where="status IN ('AUTO_VERIFIED', 'CONFIRMED')"),
        Index("uq_match_one_settling_per_claim", "claim_id", unique=True, postgresql_where="status IN ('AUTO_VERIFIED', 'CONFIRMED')"),
        Index("ix_reconciliation_matches_claim", "claim_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("payment_claims.id", ondelete="CASCADE"))
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transaction_candidates.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(48))
    time_delta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
