#!/usr/bin/env python3
"""Prepare a demo: everything the walkthrough in docs/DEMO.md needs, and the
exact commands to run during it.

    python scripts/demo_setup.py                    # prepare and print the script
    python scripts/demo_setup.py --url https://dash.example.ir

It creates what is missing and leaves what is there: a bank account with the
card AloBot is configured to collect on, a relay device with a fresh token,
and claims mirrored from the AloBot copy. Then it prints, per open payment,
the bank SMS that would settle it.

REFUSES to run against ENV_NAME=production. The claims it mirrors come from
whatever ALOBOT_DATABASE_URL points at, and on a demo host that is a COPY.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

from sqlalchemy import select

from app.alobot.link import link
from app.core.config import EnvName, get_settings
from app.db.session import async_session_maker
from app.models import Device, FinancialAccount, FinancialAccountIdentifier, PaymentCard, PaymentClaim
from app.services import claims as claims_service
from app.services import ingest as ingest_service

LAST4 = "5299"
BANK = "ملی"


def sms_for(amount_irr: int, reference: int) -> str:
    return (
        f"واریز به حساب 4704{LAST4}\n"
        f"مبلغ: {amount_irr:,} ریال\n"
        f"مانده: 8,354,098\n"
        f"شماره پیگیری: {reference}"
    )


async def ensure_account(session, card_number: str) -> int:
    card = (await session.execute(select(PaymentCard).where(PaymentCard.card_number == card_number))).scalar_one_or_none()
    if card is not None:
        return card.account_id
    account = FinancialAccount(bank_name=BANK, display_name=f"{BANK} (دمو)", status="ACTIVE")
    session.add(account)
    await session.flush()
    session.add(FinancialAccountIdentifier(account_id=account.id, kind="ACCOUNT_LAST4", value=LAST4))
    session.add(PaymentCard(account_id=account.id, card_number=card_number, holder_name="نمایش"))
    await session.commit()
    return account.id


async def ensure_device(session, code: str) -> str | None:
    """A token is returned only when one is issued; an existing device keeps
    the token it already has, because issuing a new one would silently stop
    the phone that is using it."""
    device = (await session.execute(select(Device).where(Device.code == code))).scalar_one_or_none()
    if device is not None:
        return None
    device = Device(code=code, display_name="گوشی نمایش")
    session.add(device)
    await session.flush()
    token, _ = await ingest_service.issue_credential(session, device)
    await session.commit()
    return token


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8090", help="base URL of the deployment being demonstrated")
    parser.add_argument("--device", default="phone-demo")
    args = parser.parse_args()

    settings = get_settings()
    if settings.env_name is EnvName.production:
        print("refusing: this creates demo rows and is not for a production deployment", file=sys.stderr)
        return 2
    if not settings.alobot_database_url:
        print("refusing: ALOBOT_DATABASE_URL is blank, so there are no payments to mirror", file=sys.stderr)
        print("point it at a COPY of AloBot's database first (scripts/copy_alobot_db.sh)", file=sys.stderr)
        return 2

    await link.connect()
    if not link.available:
        print(f"refusing: the AloBot link is not usable: {link.problems}", file=sys.stderr)
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    async with async_session_maker() as session:
        async with link.session() as alobot:
            card_number = await claims_service._alobot_card_number(alobot)
        if card_number is None:
            print("refusing: AloBot's app_config has no 16-digit card_number", file=sys.stderr)
            return 2
        await ensure_account(session, card_number)
        token = await ensure_device(session, args.device)
        result = await claims_service.mirror_claims(session, now=now)
        open_claims = (
            await session.execute(
                select(PaymentClaim).where(PaymentClaim.status == "PENDING").order_by(PaymentClaim.paid_clicked_at.desc()).limit(5)
            )
        ).scalars().all()

    print("=" * 72)
    print("demo prepared")
    print("=" * 72)
    print(f"collecting card    {card_number} -> account with last four {LAST4}")
    print(f"relay device       {args.device}")
    print(f"device token       {token if token else '(unchanged - the device already had one)'}")
    print(f"claims mirrored    created={result['created']} updated={result['updated']} resolved={result['resolved']}")
    if not open_claims:
        print("\nNo pending payments in the AloBot copy. Seed some first:")
        print("  python scripts/seed_alobot_copy.py --url <copy superuser URL>")
        return 0
    if token is None:
        print("\nThe device already existed, so no token is printed. Rotate it in the panel")
        print("if you need one, or pass --device with a new code.")

    print("\nOpen payments and the bank SMS that settles each one:\n")
    for claim in open_claims:
        toman = claim.expected_amount_irr // 10
        print(f"  claim #{claim.id}  invoice {claim.invoice_code or claim.alobot_payment_id}  {toman:,} تومان")
        body = json.dumps(
            {
                "apiKey": token or "<the device token>",
                "deviceId": args.device,
                "message": sms_for(claim.expected_amount_irr, 800000 + claim.id),
                "sender": "BankDemo",
                # 90 seconds after the customer pressed "paid": the matcher
                # compares the bank's time to that click, not to the wall
                # clock, so a demo works on payments of any age.
                "timestamp": str(int((claim.paid_clicked_at + dt.timedelta(seconds=90)).timestamp() * 1000)),
            },
            ensure_ascii=False,
        )
        print(f"    curl -sS -X POST {args.url}/api/v1/sms \\")
        print(f"      -H 'content-type: application/json' \\")
        print(f"      -d '{body}'")
        print()
    print("Send one, wait for the settle sweep (about 25 seconds), and watch the")
    print("payment move to تایید خودکار. Send an amount that is off by a rial and")
    print("it lands in در انتظار بررسی instead. docs/DEMO.md is the full walkthrough.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
