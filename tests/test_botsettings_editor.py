"""Phase 5 tasks 4, 5, 7: AloBot's own settings, its admins, and its schedule."""

import pytest
from sqlalchemy import text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import admins as admin_writes
from app.alobot.writes import botsettings, cron
from app.db.session import async_session_maker
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=5, seed=3)
    await link.connect()
    return await make_operator(email="cfg@x.io", role="ADMIN")


async def _value(key: str):
    async with write_engine.connect() as conn:
        row = (await conn.execute(text("SELECT value FROM app_config WHERE key=:k"), {"k": key})).first()
    return row.value if row else None


# ── Bot settings ───────────────────────────────────────────────────────────


def test_every_switch_carries_the_exact_vocabulary_its_reader_uses():
    """AloBot does not have one convention: two switches are ON only for the
    exact string "true", two are OFF only for the exact string "false". A
    form that guessed would silently mean the opposite."""
    from pathlib import Path

    source = "\n".join((Path("vendor/alobot") / p).read_text() for p in ("app/services/auto_approve.py", "app/services/reminders.py", "app/services/vpn_users.py", "app/bot/middlewares/channel_membership.py"))
    assert '"auto_approve_enabled")) != "true"' in source
    assert '"reminder_enabled")) == "false"' in source
    assert '"trial_limit_enabled")) == "false"' in source
    assert '"mandatory_channel_enabled")) != "true"' in source
    assert botsettings.spec("auto_approve_enabled").unknown_is is False
    assert botsettings.spec("mandatory_channel_enabled").unknown_is is False
    assert botsettings.spec("reminder_enabled").unknown_is is True
    assert botsettings.spec("trial_limit_enabled").unknown_is is True


async def test_settings_are_written_with_those_exact_words(seeded):
    async with async_session_maker() as db:
        await botsettings.save(db, seeded, {"auto_approve_enabled": False, "reminder_enabled": False, "support_username": " @alo_support ", "auto_approve_delay_minutes": "5"})
    assert await _value("auto_approve_enabled") == "false"
    assert await _value("reminder_enabled") == "false"
    assert await _value("support_username") == "alo_support"
    assert await _value("auto_approve_delay_minutes") == "5"


async def test_an_unlisted_key_is_refused_and_nothing_is_written(seeded):
    before = await _value("card_holder")
    async with async_session_maker() as db:
        with pytest.raises(botsettings.SettingsError, match="ناشناخته"):
            await botsettings.save(db, seeded, {"card_holder": "کسی", "rm -rf": "1"})
    assert await _value("card_holder") == before


async def test_the_card_number_must_be_sixteen_digits_and_pass_luhn(seeded):
    async with async_session_maker() as db:
        with pytest.raises(botsettings.SettingsError, match="کارت"):
            await botsettings.save(db, seeded, {"card_number": "123"})
        with pytest.raises(botsettings.SettingsError, match="کارت"):
            await botsettings.save(db, seeded, {"card_number": "6037991234567890"})
        await botsettings.save(db, seeded, {"card_number": "۶۰۳۷۹۹۱۲۳۴۵۶۷۸۹۳"})
    assert await _value("card_number") == "6037991234567893"


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "9999"])
async def test_the_auto_approve_delay_is_bounded(seeded, bad):
    async with async_session_maker() as db:
        with pytest.raises(botsettings.SettingsError):
            await botsettings.save(db, seeded, {"auto_approve_delay_minutes": bad})


async def test_reading_back_gives_typed_values_with_labels(seeded):
    async with async_session_maker() as db:
        values = await botsettings.current(db)
    assert values["auto_approve_enabled"] is True and values["card_holder"] == "آلو"
    assert botsettings.spec("card_holder").label and botsettings.spec("card_holder").hint


async def test_the_topic_ids_are_shown_but_not_editable(seeded):
    assert botsettings.spec("topic_backup") is None
    async with async_session_maker() as db:
        with pytest.raises(botsettings.SettingsError):
            await botsettings.save(db, seeded, {"topic_backup": "9"})
        readonly = await botsettings.read_only_keys(db)
    assert "topic_backup" in readonly and readonly["topic_backup"] == "2"


# ── Bot admins and the payment-reviewer allow-list ─────────────────────────


async def test_admin_can_be_added_promoted_and_removed(seeded):
    async with async_session_maker() as db:
        await admin_writes.add(db, seeded, telegram_id=555000111, level="support")
        assert (await _value("x")) is None
        await admin_writes.set_level(db, seeded, telegram_id=555000111, level="sales")
        async with write_engine.connect() as conn:
            row = (await conn.execute(text("SELECT level FROM admin_users WHERE telegram_id=555000111"))).first()
        assert row.level == "sales"
        await admin_writes.remove(db, seeded, telegram_id=555000111)
        async with write_engine.connect() as conn:
            assert (await conn.execute(text("SELECT id FROM admin_users WHERE telegram_id=555000111"))).first() is None


@pytest.mark.parametrize("level", ["owner", "", "FULL"])
async def test_only_alobots_three_tiers_are_accepted(seeded, level):
    async with async_session_maker() as db:
        with pytest.raises(admin_writes.AdminError, match="سطح"):
            await admin_writes.add(db, seeded, telegram_id=555000222, level=level)


async def test_the_last_full_admin_cannot_be_removed_or_demoted(seeded):
    async with async_session_maker() as db:
        async with write_engine.begin() as conn:
            await conn.execute(text("DELETE FROM admin_users WHERE level='full' AND telegram_id <> 111000001"))
        with pytest.raises(admin_writes.AdminError, match="آخرین"):
            await admin_writes.remove(db, seeded, telegram_id=111000001)
        with pytest.raises(admin_writes.AdminError, match="آخرین"):
            await admin_writes.set_level(db, seeded, telegram_id=111000001, level="sales")


async def test_the_reviewer_allow_list_is_stored_the_way_alobot_reads_it(seeded):
    async with async_session_maker() as db:
        assert await admin_writes.reviewers(db) == []
        await admin_writes.toggle_reviewer(db, seeded, 111000001)
        await admin_writes.toggle_reviewer(db, seeded, 111000002)
        assert await _value("payment_review_admin_ids") == "111000001,111000002"
        await admin_writes.toggle_reviewer(db, seeded, 111000001)
        assert await _value("payment_review_admin_ids") == "111000002"
        await admin_writes.reset_reviewers(db, seeded)
        assert await _value("payment_review_admin_ids") == ""
        assert await admin_writes.reviewers(db) == []


async def test_a_reviewer_must_be_an_admin(seeded):
    async with async_session_maker() as db:
        with pytest.raises(admin_writes.AdminError, match="ادمین"):
            await admin_writes.toggle_reviewer(db, seeded, 999999999)


# ── Cron ───────────────────────────────────────────────────────────────────


def test_the_schedule_keys_and_defaults_come_from_alobots_own_table():
    from pathlib import Path

    source = (Path("vendor/alobot") / "app" / "services" / "group_scheduler.py").read_text()
    for key, hour, minute, _cadence in cron.SCHEDULE:
        assert f'("{key}", {hour}, {minute},' in source, key
    block = source[source.index("_SCHEDULE = ("):source.index("SCHEDULE_KEYS")]
    entries = [line for line in block.splitlines() if line.strip().startswith('("')]
    assert len(cron.SCHEDULE) == len(entries), "AloBot added or removed a scheduled job"


async def test_effective_times_start_at_the_defaults_and_follow_an_override(seeded):
    async with async_session_maker() as db:
        rows = {r["key"]: r for r in await cron.status(db)}
        assert rows["report_daily"]["time"] == "00:05" and rows["report_daily"]["overridden"] is False
        await cron.set_time(db, seeded, "report_daily", hour=7, minute=30)
        rows = {r["key"]: r for r in await cron.status(db)}
    assert rows["report_daily"]["time"] == "07:30" and rows["report_daily"]["overridden"] is True
    assert await _value("sched_cfg_report_daily_hour") == "7"
    assert await _value("sched_cfg_report_daily_minute") == "30"


@pytest.mark.parametrize(("key", "hour", "minute"), [("nope", 1, 0), ("report_daily", 24, 0), ("report_daily", 1, 60), ("report_daily", -1, 0)])
async def test_a_bad_key_or_time_is_refused(seeded, key, hour, minute):
    async with async_session_maker() as db:
        with pytest.raises(cron.CronError):
            await cron.set_time(db, seeded, key, hour=hour, minute=minute)


async def test_our_own_sweeps_are_listed_beside_alobots(seeded):
    async with async_session_maker() as db:
        ours = await cron.our_sweeps(db)
    names = {s["name"] for s in ours}
    assert {"claims.mirror", "claims.settle", "outbox.flush"} <= names
