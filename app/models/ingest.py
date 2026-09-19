"""Tables behind the SMS pipeline: the phones that relay, the accounts and
cards money lands on, the bank vocabulary an operator edits, the messages
themselves, and what the parser made of them."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CREDENTIAL_STATUSES = ("ACTIVE", "REVOKED")
ACCOUNT_STATUSES = ("PENDING", "ACTIVE", "MUTED", "DECLINED")
IDENTIFIER_KINDS = ("CARD", "CARD_LAST4", "ACCOUNT", "ACCOUNT_LAST4", "IBAN", "OTHER")
CARD_STATUSES = ("ACTIVE", "DISABLED")
CLASSIFICATIONS = ("BANK_TRANSACTION", "BALANCE", "OTP", "PROMOTIONAL", "UNKNOWN")
DIRECTIONS = ("CREDIT", "DEBIT", "UNKNOWN")
DISPOSITIONS = ("ACTIONABLE", "DECLINED_INCOME", "IGNORED")
AMOUNT_UNITS = ("IRR", "TOMAN")


class Device(Base):
    """A relay phone. `code` is what the phone sends as deviceId."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auth_failure_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeviceCredential(Base):
    """The phone's token, stored as a hash plus a 4-char prefix for lookup.
    One ACTIVE credential per device, by partial unique index: revoking
    must leave nothing that still works."""

    __tablename__ = "device_credentials"
    __table_args__ = (
        CheckConstraint(f"status IN {CREDENTIAL_STATUSES}", name="device_credentials_status_check"),
        Index("uq_device_credentials_one_active", "device_id", unique=True, postgresql_where="status = 'ACTIVE'"),
        Index("ix_device_credentials_prefix", "token_prefix"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinancialAccount(Base):
    """A bank account money can land on. PENDING rows are created by ingest
    when an SMS names an identifier nobody registered; an operator accepts,
    mutes or declines them. Only ACTIVE accounts take part in matching."""

    __tablename__ = "financial_accounts"
    __table_args__ = (CheckConstraint(f"status IN {ACCOUNT_STATUSES}", name="financial_accounts_status_check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(64))
    owner_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    inferred_from_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FinancialAccountIdentifier(Base):
    """How an SMS names an account: last four of a card, of an account number,
    an IBAN, a full number. One identifier belongs to one account."""

    __tablename__ = "financial_account_identifiers"
    __table_args__ = (
        CheckConstraint(f"kind IN {IDENTIFIER_KINDS}", name="financial_account_identifiers_kind_check"),
        UniqueConstraint("kind", "value", name="uq_financial_account_identifiers_kind_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("financial_accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentCard(Base):
    """A destination card customers pay to. Belongs to the account whose SMS
    reports the deposit, so a card number on an invoice resolves to the
    account the bank will text about."""

    __tablename__ = "payment_cards"
    __table_args__ = (
        CheckConstraint("card_number ~ '^[0-9]{16}$'", name="payment_cards_number_check"),
        CheckConstraint(f"status IN {CARD_STATUSES}", name="payment_cards_status_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("financial_accounts.id", ondelete="CASCADE"), index=True)
    card_number: Mapped[str] = mapped_column(String(16), unique=True)
    holder_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BankCardPrefix(Base):
    """Card prefix → bank. Longest prefix wins; a split range is one more row."""

    __tablename__ = "bank_card_prefixes"
    __table_args__ = (CheckConstraint("prefix ~ '^[0-9]{4,8}$'", name="bank_card_prefixes_prefix_check"),)

    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    bank_name: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(254), nullable=True)


class BankSmsPattern(Base):
    """An operator-written parser for one bank's SMS shape. Disabled until an
    operator enables it after the sandbox test; additive - see app.sms.patterns."""

    __tablename__ = "bank_sms_patterns"
    __table_args__ = (
        CheckConstraint("id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'", name="bank_sms_patterns_id_check"),
        CheckConstraint(f"amount_unit IN {AMOUNT_UNITS}", name="bank_sms_patterns_unit_check"),
        CheckConstraint("direction IN ('CREDIT', 'DEBIT')", name="bank_sms_patterns_direction_check"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bank_name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    detect_re: Mapped[str] = mapped_column(String(500))
    amount_re: Mapped[str] = mapped_column(String(500))
    amount_unit: Mapped[str] = mapped_column(String(8), default="IRR", server_default="IRR")
    direction: Mapped[str] = mapped_column(String(8), default="CREDIT", server_default="CREDIT")
    balance_re: Mapped[str | None] = mapped_column(String(500), nullable=True)
    account_re: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_re: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(254), nullable=True)


class SmsEvent(Base):
    """One message as the phone delivered it. `body` is stored AFTER OTP
    redaction; `dedupe_key` is what makes redelivery harmless."""

    __tablename__ = "sms_events"
    __table_args__ = (
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="sms_events_classification_check"),
        Index("ix_sms_events_received", "received_at"),
        Index("ix_sms_events_device_time", "device_id", "sms_timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    sender: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sms_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    classification: Mapped[str] = mapped_column(String(24))
    parser_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    warnings: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TransactionCandidate(Base):
    """What the parser read out of a bank SMS. One per event. Amounts are
    integer Rial; `account_id` is resolved from the identifier the SMS named,
    `inferred_account_id` is the balance-chain guess when nobody knew it."""

    __tablename__ = "transaction_candidates"
    __table_args__ = (
        CheckConstraint(f"direction IN {DIRECTIONS}", name="transaction_candidates_direction_check"),
        CheckConstraint(f"disposition IN {DISPOSITIONS}", name="transaction_candidates_disposition_check"),
        CheckConstraint("amount_irr IS NULL OR amount_irr >= 0", name="transaction_candidates_amount_check"),
        CheckConstraint("balance_irr IS NULL OR balance_irr >= 0", name="transaction_candidates_balance_check"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="transaction_candidates_confidence_check"),
        Index("ix_transaction_candidates_account_time", "account_id", "bank_timestamp"),
        Index("ix_transaction_candidates_amount", "amount_irr"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sms_event_id: Mapped[int] = mapped_column(ForeignKey("sms_events.id", ondelete="CASCADE"), unique=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("financial_accounts.id", ondelete="SET NULL"), nullable=True)
    inferred_account_id: Mapped[int | None] = mapped_column(ForeignKey("financial_accounts.id", ondelete="SET NULL"), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    amount_irr: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_irr: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bank_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    parser_id: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    disposition: Mapped[str] = mapped_column(String(16), default="ACTIONABLE", server_default="ACTIONABLE")
    disposition_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
