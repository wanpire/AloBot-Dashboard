from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppEvent(Base):
    """A structured event that outlives the container's log stream. Written by
    the logger sink for WARNING and above (and for a few INFO events that
    operators need to see), pruned after the retention window."""

    __tablename__ = "app_events"
    __table_args__ = (Index("ix_app_events_at_id", "at", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String(16), index=True)
    event: Mapped[str] = mapped_column(String(96), index=True)
    service: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fields: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    err: Mapped[str | None] = mapped_column(Text, nullable=True)
