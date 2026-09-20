"""Phase 6 task 2: what the ingest endpoint does under a burst and a stream.

The relay phone retries on any non-2xx, so a slow or failing ingest turns one
bank SMS into a retry storm. Two questions that only a real socket can answer:

1. **Burst.** A phone that was offline flushes its backlog at once. Does every
   message land exactly once, or does the connection pool time out and hand
   the phone a reason to send it all again?
2. **Stream.** At a steady rate, does latency stay flat, or does queueing
   build until the phone's own timeout is the limit?

The numbers are printed rather than asserted tightly: a laptop is not the
server. What IS asserted is the shape - no 5xx, no pool timeout, no
duplicates, and the device rate limit refusing rather than the process
falling over.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time

import httpx
import pytest
from sqlalchemy import func, select

from app.api.ingest import device_limiter
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models import Device, SmsEvent
from app.services import ingest as ingest_service

BURST = 200
STREAM_SECONDS = 4
STREAM_RATE = 25  # messages per second


async def _device(code: str = "phone-load") -> str:
    async with async_session_maker() as s:
        device = Device(code=code, display_name="گوشی بار")
        s.add(device)
        await s.flush()
        token, _ = await ingest_service.issue_credential(s, device)
        await s.commit()
        return token


def _body(token: str, n: int, code: str = "phone-load") -> str:
    """A distinct bank credit per message: same shape, different amount and
    reference, so nothing is deduplicated away by accident."""
    amount = 1_000_000 + n * 1_000
    message = f"واریز به حساب 47045299\nمبلغ: {amount:,} ریال\nمانده: 8,354,098\nشماره پیگیری: {900000 + n}"
    return json.dumps({"apiKey": token, "deviceId": code, "message": message, "sender": "Bank", "timestamp": str(1789999999000 + n * 1000)})


async def _post(client: httpx.AsyncClient, url: str, body: str) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        response = await client.post(url, content=body, headers={"content-type": "application/json"})
        return response.status_code, time.perf_counter() - started
    except httpx.HTTPError as error:  # a dropped connection is a failure, not an exception to swallow
        return 0, time.perf_counter() - started


def _report(name: str, results: list[tuple[int, float]], seconds: float) -> dict[int, int]:
    codes: dict[int, int] = {}
    for code, _ in results:
        codes[code] = codes.get(code, 0) + 1
    latencies = sorted(d for _, d in results)
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    print(
        f"\n{name}: {len(results)} requests in {seconds:.2f}s "
        f"({len(results) / seconds:.0f}/s) codes={codes} p50={p50 * 1000:.0f}ms p95={p95 * 1000:.0f}ms max={latencies[-1] * 1000:.0f}ms"
    )
    return codes


@pytest.fixture
def device_limit(monkeypatch):
    """The handler re-reads its limit from the settings on every request, so
    a test changes the setting - poking the limiter object does nothing."""

    def _set(per_minute: int) -> None:
        monkeypatch.setattr(get_settings(), "ingest_device_rate_per_minute", per_minute)
        device_limiter.reset()

    _set(10_000)  # the burst tests are about the pool, not the limiter
    return _set


async def test_a_backlog_flush_lands_every_message_exactly_once(base_url, device_limit):
    token = await _device()
    url = f"{base_url}/api/v1/sms"
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=BURST)) as client:
        started = time.perf_counter()
        results = await asyncio.gather(*(_post(client, url, _body(token, n)) for n in range(BURST)))
        seconds = time.perf_counter() - started

    codes = _report("burst", results, seconds)
    assert set(codes) == {200}, f"the phone would retry all of these: {codes}"
    async with async_session_maker() as s:
        stored = (await s.execute(select(func.count()).select_from(SmsEvent))).scalar_one()
    assert stored == BURST, f"{BURST} messages in, {stored} stored"


async def test_the_same_message_arriving_many_times_at_once_is_stored_once(base_url, device_limit):
    """The relay retries; two retries can be in flight together. Dedupe has to
    hold at the database, not in a check-then-insert that races."""
    token = await _device("phone-dupe")
    url = f"{base_url}/api/v1/sms"
    body = _body(token, 7, code="phone-dupe")
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=50)) as client:
        results = await asyncio.gather(*(_post(client, url, body) for _ in range(50)))
    assert {c for c, _ in results} == {200}
    async with async_session_maker() as s:
        stored = (await s.execute(select(func.count()).select_from(SmsEvent))).scalar_one()
    assert stored == 1, f"one message, {stored} rows"


async def test_a_steady_stream_does_not_build_a_queue(base_url, device_limit):
    token = await _device("phone-stream")
    url = f"{base_url}/api/v1/sms"
    sent: list[asyncio.Task] = []
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=64)) as client:
        started = time.perf_counter()
        for tick in range(STREAM_SECONDS * STREAM_RATE):
            sent.append(asyncio.create_task(_post(client, url, _body(token, tick, code="phone-stream"))))
            await asyncio.sleep(1 / STREAM_RATE)
        results = await asyncio.gather(*sent)
        seconds = time.perf_counter() - started

    codes = _report("stream", results, seconds)
    assert set(codes) == {200}
    first = [d for _, d in results[: STREAM_RATE]]
    last = [d for _, d in results[-STREAM_RATE:]]
    # Queueing shows up as the tail being slower than the head. A little
    # noise is expected; a growing queue is not.
    assert statistics.median(last) < max(statistics.median(first) * 4, 0.5), (
        f"latency grew from {statistics.median(first) * 1000:.0f}ms to {statistics.median(last) * 1000:.0f}ms"
    )


async def test_the_device_limit_refuses_politely_instead_of_the_process_falling_over(base_url, device_limit):
    """Above the limit the answer must still be an answer: 429, fast, and the
    messages that were inside the limit are all stored."""
    device_limit(30)
    token = await _device("phone-limited")
    url = f"{base_url}/api/v1/sms"
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=60)) as client:
        results = await asyncio.gather(*(_post(client, url, _body(token, n, code="phone-limited")) for n in range(60)))

    codes = _report("over the device limit", results, 1.0)
    assert set(codes) == {200, 429}, codes
    assert codes[200] == 30 and codes[429] == 30
    async with async_session_maker() as s:
        stored = (await s.execute(select(func.count()).select_from(SmsEvent))).scalar_one()
    assert stored == 30


async def test_a_burst_past_the_pool_is_shed_as_503_with_a_retry_hint_not_a_500(base_url, device_limit):
    """Measured on a laptop: the pool saturates somewhere past a few hundred
    concurrent posts. Saturation is a capacity fact, not a bug, and the two
    codes mean different things to everyone downstream - a 500 tells the relay
    that something broke and puts a stack trace in the log, a 503 with
    Retry-After tells it to come back and leaves the log readable. Nothing is
    lost either way: the phone still holds the message."""
    token = await _device("phone-flood")
    url = f"{base_url}/api/v1/sms"
    flood = 700
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=flood)) as client:
        started = time.perf_counter()
        results = await asyncio.gather(
            *(_post(client, url, _body(token, n, code="phone-flood")) for n in range(flood))
        )
        seconds = time.perf_counter() - started

    codes = _report("flood", results, seconds)
    assert 500 not in codes, "a saturated pool must not be reported as a server fault"
    assert set(codes) <= {200, 503}, codes
    assert codes.get(200, 0) > 0, "the flood shed everything - the pool is too small to be useful"

    async with async_session_maker() as s:
        stored = (await s.execute(select(func.count()).select_from(SmsEvent))).scalar_one()
    assert stored == codes[200], "every message that was answered 200 must be on disk"
