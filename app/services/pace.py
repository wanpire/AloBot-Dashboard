"""One clock both senders share.

Telegram's limit is on the BOT, not on either loop that talks to it. The
outbox and the broadcast drain each learn about a 429 from their own call;
without a shared pause the other one keeps sending into the ban and makes it
longer. Process-wide state is enough while this runs as one container.
"""

from __future__ import annotations

import datetime as dt

_pause_until: dt.datetime | None = None


def note_rate_limit(retry_after: int, now: dt.datetime) -> None:
    global _pause_until
    until = now + dt.timedelta(seconds=max(1, int(retry_after)))
    if _pause_until is None or until > _pause_until:
        _pause_until = until


def paused_until(now: dt.datetime) -> dt.datetime | None:
    return _pause_until if _pause_until and _pause_until > now else None


def is_paused(now: dt.datetime) -> bool:
    return paused_until(now) is not None


def reset() -> None:
    global _pause_until
    _pause_until = None
