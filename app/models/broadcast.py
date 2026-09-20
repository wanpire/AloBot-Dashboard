"""A message sent to every customer at once, and one row per recipient.

The recipient rows are a SNAPSHOT taken when the broadcast is created: who
was in the audience at that moment, decided once. Without it, "who has been
sent to" would depend on a query re-run mid-send, and a customer who bought
something halfway through would be counted differently by two sweeps.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BROADCAST_STATUSES = ("PENDING", "SENDING", "DONE")
RECIPIENT_STATUSES = ("PENDING", "SENT", "FAILED")


class Broadcast(Base):
    __tablename__ = "broadcasts"
    __table_args__ = (CheckConstraint(f"status IN {BROADCAST_STATUSES}", name="broadcasts_status_check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Minted by the browser before the form is submitted, so a double submit
    # or a lost response lands on the same broadcast instead of a second one.
    batch_id: Mapped[str] = mapped_column(String(64), unique=True)
    audience: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    button_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    button_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        CheckConstraint(f"status IN {RECIPIENT_STATUSES}", name="broadcast_recipients_status_check"),
        UniqueConstraint("broadcast_id", "chat_id", name="uq_broadcast_recipient"),
        Index("ix_broadcast_recipients_pending", "broadcast_id", postgresql_where="status = 'PENDING'"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
