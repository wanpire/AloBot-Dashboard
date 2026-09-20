"""Synthetic AloBot data for demos and tests.

Deterministic (a seed drives every choice), idempotent (it wipes the AloBot
COPY first), and refused on any host that is not local: this writes through
a superuser URL and a typo pointing it at production would wipe the shop.
Shapes follow AloBot's real flows - every payment status, method and purpose
the bot produces appears, so screens are exercised on the cases that exist.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.alobot.codes import encode_id

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}

TABLES_IN_DEPENDENCY_ORDER = (
    "discount_code_usages", "payments", "vpn_users", "services", "openvpn_profiles", "download_links",
    "tutorial_guides", "service_locations", "groups", "discount_codes", "resellers", "bot_users",
    "admin_users", "app_config", "tutorial_platforms", "tutorial_protocols",
)


class SeedRefused(RuntimeError):
    pass


def _assert_local(url: str) -> None:
    host = urlsplit(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SeedRefused(f"refusing to seed a non-local host: {host!r}")


async def seed_alobot_copy(write_url: str, *, customers: int = 40, seed: int = 1) -> dict[str, int]:
    _assert_local(write_url)
    rng = random.Random(seed)
    now = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
    engine = create_async_engine(write_url, pool_size=1, max_overflow=0)
    counts: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE " + ", ".join(TABLES_IN_DEPENDENCY_ORDER) + " RESTART IDENTITY CASCADE"))

            # Locations, groups, services (the plan matrix AloBot sells from).
            await conn.execute(text(
                "INSERT INTO service_locations (id, title, flag_emoji, is_active, sort_order) VALUES "
                "(1,'آلمان','🇩🇪',true,1),(2,'ترکیه','🇹🇷',true,2)"))
            groups = [
                ("1M-1U", 0), ("1M-2U", 0), ("2M-1U", 0), ("2M-2U", 0), ("Prime-1M-1U", 0), ("Prime-1M-2U", 0),
                ("Junior-1M-1U", 0), ("DE-1M-1U", 0), ("DE-1M-2U", 0), ("TR-1M-1U", 0),
            ]
            for i, (name, price) in enumerate(groups, start=1):
                await conn.execute(text(
                    "INSERT INTO groups (id, ibsng_group_id, name, price, is_trial, synced_at) "
                    "VALUES (:id, :gid, :name, :price, false, :at)"),
                    {"id": i, "gid": str(100 + i), "name": name, "price": price, "at": now})
            await conn.execute(text(
                "INSERT INTO groups (id, ibsng_group_id, name, price, is_trial, trial_category, synced_at) VALUES "
                "(11,'201','Trial',0,true,'normal',:at),(12,'202','Trial-Prime',0,true,'prime',:at)"), {"at": now})
            services = [
                # id, title, category, location, months, users, group, price (Toman)
                (1, "عادی ۱ ماهه ۱ کاربر", "normal", None, 1, 1, "1M-1U", 95000),
                (2, "عادی ۱ ماهه ۲ کاربر", "normal", None, 1, 2, "1M-2U", 150000),
                (3, "عادی ۲ ماهه ۱ کاربر", "normal", None, 2, 1, "2M-1U", 180000),
                (4, "عادی ۲ ماهه ۲ کاربر", "normal", None, 2, 2, "2M-2U", 280000),
                (5, "پرایم ۱ ماهه ۱ کاربر", "prime", None, 1, 1, "Prime-1M-1U", 160000),
                (6, "پرایم ۱ ماهه ۲ کاربر", "prime", None, 1, 2, "Prime-1M-2U", 250000),
                (7, "جونیور ۱ ماهه ۱ کاربر", "junior", None, 1, 1, "Junior-1M-1U", 60000),
                (8, "آلمان ۱ ماهه ۱ کاربر", "fixed", 1, 1, 1, "DE-1M-1U", 140000),
                (9, "آلمان ۱ ماهه ۲ کاربر", "fixed", 1, 1, 2, "DE-1M-2U", 220000),
                (10, "ترکیه ۱ ماهه ۱ کاربر", "fixed", 2, 1, 1, "TR-1M-1U", 120000),
            ]
            for row in services:
                await conn.execute(text(
                    "INSERT INTO services (id, title, category, location_id, duration_months, user_count, group_name, price, is_active, sort_order, created_at, updated_at) "
                    "VALUES (:id, :title, :cat, :loc, :m, :u, :g, :price, true, :id, :at, :at)"),
                    {"id": row[0], "title": row[1], "cat": row[2], "loc": row[3], "m": row[4], "u": row[5], "g": row[6], "price": row[7], "at": now})

            await conn.execute(text(
                "INSERT INTO admin_users (id, telegram_id, level, created_at) VALUES "
                "(1, 111000001, 'full', :at), (2, 111000002, 'sales', :at), (3, 111000003, 'support', :at)"), {"at": now})

            # Discount codes exist before the payments that reference them.
            await conn.execute(text(
                "INSERT INTO discount_codes (id, code, percent, usage_limit, used_count, categories, is_active, is_public, created_at) VALUES "
                "(1, 'WELCOME10', 10, 100, 0, NULL, true, true, :at), "
                "(2, 'VIP25', 25, 20, 0, 'prime,fixed', true, false, :at), "
                "(3, 'OLD5', 5, 10, 10, NULL, false, true, :at)"), {"at": now - dt.timedelta(days=60)})

            # Customers.
            first_names = ["ali", "sara", "reza", "mina", "hamed", "neda", "arash", "leila"]
            for i in range(1, customers + 1):
                tg = 100_000_000 + i
                username = f"{rng.choice(first_names)}_{i}" if rng.random() < 0.6 else None
                first_seen = now - dt.timedelta(days=rng.randint(0, 90), hours=rng.randint(0, 23))
                await conn.execute(text(
                    "INSERT INTO bot_users (id, telegram_id, username, first_seen_at, is_blocked) "
                    "VALUES (:id, :tg, :u, :seen, :blocked)"),
                    {"id": i, "tg": tg, "u": username, "seen": first_seen, "blocked": rng.random() < 0.05})

            await conn.execute(text(
                "INSERT INTO resellers (id, telegram_id, balance, commission_percent, created_at) VALUES "
                "(1, 100000001, 1250000, 15, :at), (2, 100000002, -300000, 10, :at), (3, 100000003, 0, 20, :at)"), {"at": now})

            # Accounts and the payments that bought them.
            vpn_id = 0
            payment_id = 0
            invoice_seq = 0
            usage_id = 0
            used_codes: dict[int, int] = {1: 0, 2: 0}
            for i in range(1, customers + 1):
                tg = 100_000_000 + i
                first_seen = now - dt.timedelta(days=rng.randint(0, 90))
                for _ in range(rng.choice([0, 1, 1, 1, 2, 3])):
                    vpn_id += 1
                    service = rng.choice(services)
                    is_trial = rng.random() < 0.15
                    created = first_seen + dt.timedelta(hours=rng.randint(1, 48))
                    if created > now:
                        created = now - dt.timedelta(hours=1)
                    expires = created + dt.timedelta(days=30 * (service[4] or 1)) if (not is_trial and rng.random() < 0.7) else None
                    group = "Trial" if is_trial else service[6]
                    await conn.execute(text(
                        "INSERT INTO vpn_users (id, telegram_id, ibsng_username, ibsng_group, service_id, is_trial, expires_at, created_at) "
                        "VALUES (:id, :tg, :u, :g, :sid, :trial, :exp, :at)"),
                        {"id": vpn_id, "tg": tg, "u": f"alo.{rng.randint(0, 36**6):x}"[:12], "g": group,
                         "sid": None if is_trial else service[0], "trial": is_trial, "exp": expires, "at": created})
                    if is_trial:
                        continue
                    payment_id += 1
                    invoice_seq += 1
                    method = "crypto" if rng.random() < 0.15 else "card"
                    auto = method == "card" and rng.random() < 0.5
                    amount = Decimal(service[7])
                    original = None
                    code_id = None
                    if rng.random() < 0.2:
                        code_id = rng.choice([1, 2])
                        percent = 10 if code_id == 1 else 25
                        original = amount
                        amount = (amount * (100 - percent) / 100).quantize(Decimal("1"))
                        used_codes[code_id] += 1
                    await conn.execute(text(
                        "INSERT INTO payments (id, telegram_id, method, status, auto_approved, purpose, amount, group_name, service_id, ibsng_username, receipt_file_id, discount_code_id, original_amount, invoice_number, created_at, resolved_at) "
                        "VALUES (:id, :tg, :m, 'approved', :auto, 'purchase', :amount, :g, :sid, :u, :receipt, :code, :orig, :inv, :created, :resolved)"),
                        {"id": payment_id, "tg": tg, "m": method, "auto": auto, "amount": amount, "g": service[6], "sid": service[0],
                         "u": f"alo.{vpn_id:06d}", "receipt": None if method == "crypto" else f"AgAC-receipt-{payment_id}",
                         "code": code_id, "orig": original, "inv": encode_id(invoice_seq),
                         "created": created - dt.timedelta(minutes=rng.randint(3, 40)), "resolved": created})
                    if code_id:
                        usage_id += 1
                        await conn.execute(text(
                            "INSERT INTO discount_code_usages (id, discount_code_id, code, percent, payment_id, telegram_id, original_amount, amount, used_at) "
                            "VALUES (:id, :code_id, :code, :pct, :pid, :tg, :orig, :amount, :at)"),
                            {"id": usage_id, "code_id": code_id, "code": "WELCOME10" if code_id == 1 else "VIP25",
                             "pct": 10 if code_id == 1 else 25, "pid": payment_id, "tg": tg, "orig": original, "amount": amount, "at": created})
                    # Some accounts were renewed or upgraded later.
                    if rng.random() < 0.3:
                        payment_id += 1
                        invoice_seq += 1
                        purpose = rng.choice(["renew", "upgrade"])
                        when = created + dt.timedelta(days=rng.randint(20, 35))
                        if when > now:
                            when = now - dt.timedelta(days=1)
                        await conn.execute(text(
                            "INSERT INTO payments (id, telegram_id, method, status, auto_approved, purpose, target_username, amount, group_name, service_id, ibsng_username, receipt_file_id, invoice_number, created_at, resolved_at) "
                            "VALUES (:id, :tg, 'card', 'approved', false, :purpose, :u, :amount, :g, :sid, :u, :receipt, :inv, :created, :resolved)"),
                            {"id": payment_id, "tg": tg, "purpose": purpose, "u": f"alo.{vpn_id:06d}", "amount": Decimal(service[7]),
                             "g": service[6], "sid": service[0], "receipt": f"AgAC-receipt-{payment_id}", "inv": encode_id(invoice_seq),
                             "created": when - dt.timedelta(minutes=10), "resolved": when})

            # Payments that are not approved: the queue, refusals, and a reversal.
            for status, n in (("pending", 5), ("rejected", 3), ("revoked", 1)):
                for k in range(n):
                    payment_id += 1
                    tg = 100_000_000 + rng.randint(1, customers)
                    service = rng.choice(services)
                    created = now - dt.timedelta(minutes=rng.randint(2, 600) if status == "pending" else rng.randint(1000, 50000))
                    resolved = None if status == "pending" else created + dt.timedelta(minutes=30)
                    await conn.execute(text(
                        "INSERT INTO payments (id, telegram_id, method, status, auto_approved, purpose, amount, group_name, service_id, ibsng_username, receipt_file_id, created_at, resolved_at) "
                        "VALUES (:id, :tg, 'card', :status, :auto, 'purchase', :amount, :g, :sid, :u, :receipt, :created, :resolved)"),
                        {"id": payment_id, "tg": tg, "status": status, "auto": status == "revoked", "amount": Decimal(service[7]),
                         "g": service[6], "sid": service[0], "u": f"alo.p{payment_id:05d}",
                         "receipt": f"AgAC-receipt-{payment_id}" if k % 2 == 0 else None, "created": created, "resolved": resolved})

            await conn.execute(text("UPDATE discount_codes SET used_count = :n WHERE id = 1"), {"n": used_codes[1]})
            await conn.execute(text("UPDATE discount_codes SET used_count = :n WHERE id = 2"), {"n": used_codes[2]})

            # Tutorials, links, profiles, bot settings.
            await conn.execute(text(
                "INSERT INTO tutorial_platforms (id, label, is_active, sort_order) VALUES "
                "(1,'اندروید',true,1),(2,'آیفون',true,2),(3,'ویندوز',true,3),(4,'مک',true,4)"))
            await conn.execute(text(
                "INSERT INTO tutorial_protocols (id, label, is_active, sort_order) VALUES "
                "(1,'OpenVPN',true,1),(2,'Cisco',true,2),(3,'L2TP',true,3)"))
            gid = 0
            for platform in (1, 2, 3, 4):
                for protocol in (1, 2, 3):
                    if platform == 1 and protocol == 3:
                        continue
                    for category in ("normal", "prime"):
                        gid += 1
                        await conn.execute(text(
                            "INSERT INTO tutorial_guides (id, platform_id, protocol_id, category, location_id, body_html, media_type, is_active, sort_order) "
                            "VALUES (:id, :p, :pr, :c, NULL, :body, NULL, true, :id)"),
                            {"id": gid, "p": platform, "pr": protocol, "c": category, "body": f"<b>راهنمای {category}</b> برای اتصال"})
                    await conn.execute(text(
                        "INSERT INTO download_links (id, platform_id, protocol_id, url, created_at, updated_at) VALUES (:id, :p, :pr, :url, :at, :at)"),
                        {"id": platform * 10 + protocol, "p": platform, "pr": protocol, "url": f"https://example.com/dl/{platform}/{protocol}", "at": now})
            await conn.execute(text(
                "INSERT INTO openvpn_profiles (id, name, category, location_id, platform_id, file_id, file_type, text, is_active, created_at) VALUES "
                "(1,'عادی - همه پلتفرم‌ها','normal',NULL,NULL,'BQAC-ovpn-1','ovpn',NULL,true,:at),"
                "(2,'پرایم - اندروید','prime',NULL,1,'BQAC-ovpn-2','ovpn',NULL,true,:at),"
                "(3,'آلمان','fixed',1,NULL,NULL,NULL,'client\\nremote de.example.com 1194',true,:at)"), {"at": now})
            config = {
                "card_number": "6037991234567893", "card_holder": "آلو", "support_username": "alo_support",
                "auto_approve_enabled": "true", "auto_approve_delay_minutes": "3", "reminder_enabled": "true",
                "reminder_bot_mention": "true", "mandatory_channel_enabled": "false", "mandatory_channel_id": "",
                "trial_limit_enabled": "true", "group_chat_id": "-1001234567890",
                "topic_backup": "2", "topic_live_logs": "3", "topic_sell_report": "4", "topic_notifications": "5",
                "topic_ibsng_health": "6", "topic_new_purchase": "7", "topic_trial_service": "8", "topic_bot_start": "9",
            }
            for key, value in config.items():
                await conn.execute(text("INSERT INTO app_config (key, value) VALUES (:k, :v)"), {"k": key, "v": value})

            for table in TABLES_IN_DEPENDENCY_ORDER:
                if table == "app_config":
                    continue
                await conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"))
            for table in TABLES_IN_DEPENDENCY_ORDER:
                counts[table] = (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
    finally:
        await engine.dispose()
    return counts
