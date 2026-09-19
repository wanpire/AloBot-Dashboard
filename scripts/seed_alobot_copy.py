#!/usr/bin/env python3
"""Fill a LOCAL copy of AloBot's database with synthetic data.

    python scripts/seed_alobot_copy.py --url postgresql+asyncpg://dashboard:dashboard@localhost:5432/alobot_copy

Refuses any host that is not local. Wipes the AloBot tables in that database
first - it is a demo fixture, not an import.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.alobot.seed import SeedRefused, seed_alobot_copy


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="superuser URL of the LOCAL AloBot copy")
    parser.add_argument("--customers", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    try:
        counts = await seed_alobot_copy(args.url, customers=args.customers, seed=args.seed)
    except SeedRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    for table, n in sorted(counts.items()):
        print(f"{table:24s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
