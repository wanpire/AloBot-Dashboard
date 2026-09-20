#!/usr/bin/env python3
"""Fire synthetic bank SMS at a deployed ingest endpoint and report what came
back. The same two shapes the test suite measures - a backlog flush and a
steady stream - but against a real host over TLS, where the network, the
reverse proxy and the server's own disk are in the picture.

IT WRITES ROWS. Every message it sends becomes an sms_event and, if it parses
as a credit, a transaction candidate the matcher will consider. That is
harmless on staging against a COPY of AloBot's database and is not something
to do to a live one, so the script refuses without --yes-this-writes-rows and
prints the host it is about to write to first.

    python scripts/loadtest_ingest.py \
        --url https://dash.example.ir --device phone-a --token <device token> \
        --burst 200 --rate 10 --seconds 30 --yes-this-writes-rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx

SENDER = "LoadTest"


def body(token: str, device: str, n: int) -> str:
    amount = 1_000_000 + n * 1_000
    message = (
        f"واریز به حساب 47045299\nمبلغ: {amount:,} ریال\n"
        f"مانده: 8,354,098\nشماره پیگیری: {int(time.time()) % 100000}{n:05d}"
    )
    return json.dumps(
        {"apiKey": token, "deviceId": device, "message": message, "sender": SENDER, "timestamp": str(int(time.time() * 1000) + n)}
    )


async def one(client: httpx.AsyncClient, url: str, payload: str) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        response = await client.post(url, content=payload, headers={"content-type": "application/json"})
        return response.status_code, time.perf_counter() - started
    except httpx.HTTPError:
        return 0, time.perf_counter() - started


def report(name: str, results: list[tuple[int, float]], seconds: float) -> None:
    codes: dict[int, int] = {}
    for code, _ in results:
        codes[code] = codes.get(code, 0) + 1
    latencies = sorted(d for _, d in results)
    print(f"\n{name}")
    print(f"  requests      {len(results)} in {seconds:.2f}s ({len(results) / seconds:.0f}/s)")
    print(f"  status codes  {codes}")
    print(f"  latency ms    p50 {statistics.median(latencies) * 1000:.0f}  p95 {latencies[int(len(latencies) * 0.95) - 1] * 1000:.0f}  max {latencies[-1] * 1000:.0f}")
    if 0 in codes:
        print("  ! connections were dropped - the phone would treat these as failures")
    if 500 in codes:
        print("  ! 500s: a saturated pool should answer 503, so these are faults, not capacity")
    if 503 in codes:
        print("  · 503s are load shedding: the endpoint stayed up and asked the phone to retry")
    if 429 in codes:
        print("  · 429s are the per-device or per-IP limit doing its job")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="base URL of the deployment, e.g. https://dash.example.ir")
    parser.add_argument("--device", required=True, help="the device code the token belongs to")
    parser.add_argument("--token", required=True, help="the device token (shown once when issued)")
    parser.add_argument("--burst", type=int, default=100, help="messages sent all at once")
    parser.add_argument("--rate", type=int, default=10, help="messages per second for the steady stream")
    parser.add_argument("--seconds", type=int, default=20, help="how long the steady stream runs")
    parser.add_argument("--yes-this-writes-rows", action="store_true", help="required: this sends real ingest traffic")
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/api/v1/sms"
    if not args.yes_this_writes_rows:
        print(f"refusing: this writes sms_event and transaction_candidate rows into {endpoint}")
        print("re-run with --yes-this-writes-rows once you are sure that is a staging database")
        return 2
    print(f"writing synthetic SMS to {endpoint} (sender {SENDER!r}, device {args.device!r})")

    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=max(args.burst, args.rate * 4))) as client:
        started = time.perf_counter()
        results = await asyncio.gather(*(one(client, endpoint, body(args.token, args.device, n)) for n in range(args.burst)))
        report(f"burst of {args.burst}", results, time.perf_counter() - started)

        pending: list[asyncio.Task] = []
        started = time.perf_counter()
        for tick in range(args.rate * args.seconds):
            pending.append(asyncio.create_task(one(client, endpoint, body(args.token, args.device, 100_000 + tick))))
            await asyncio.sleep(1 / args.rate)
        stream = await asyncio.gather(*pending)
        report(f"{args.rate}/s for {args.seconds}s", stream, time.perf_counter() - started)

    print("\nNow check the panel: پیامک‌ها should show the synthetic messages, and")
    print("the transactions screen should show them as unmatched credits to decline.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
