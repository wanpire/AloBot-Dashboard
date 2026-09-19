from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NOTIFICATION_STATUSES = ("PENDING", "SENT", "FAILED", "DEAD")


class BotNotification(Base):
    """The outbox. Every Telegram message this project sends is a row here
    first; a sweep delivers it. `dedupe_key` is UNIQUE and never released,
    not even after SENT, so a retried sweep or a double-click cannot send the
    same message twice."""

    __tablename__ = "bot_notifications"
    __table_args__ = (
        CheckConstraint(f"status IN {NOTIFICATION_STATUSES}", name="bot_notifications_status_check"),
        Index(
            "ix_bot_notifications_due",
            "next_attempt_at",
            postgresql_where="status = 'PENDING'",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
