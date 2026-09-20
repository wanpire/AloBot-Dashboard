"""Phase 5 task 3: tutorials, download links, OpenVPN profiles - with the
platform/protocol rules AloBot itself enforces."""

import pytest
from sqlalchemy import text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import content as content_writes
from app.db.session import async_session_maker
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=5, seed=3)
    await link.connect()
    return await make_operator(email="cnt@x.io", role="ADMIN")


async def _one(sql: str, **params):
    async with write_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).first()


def test_the_three_validity_rules_match_alobots_source():
    from pathlib import Path

    source = (Path("vendor/alobot") / "app" / "services" / "tutorials.py").read_text()
    for name in ("is_protocol_valid_for_platform", "is_protocol_valid_for_category", "guide_needs_location"):
        assert f"def {name}" in source
    assert content_writes.is_protocol_valid_for_platform("اندروید", "L2TP") is False
    assert content_writes.is_protocol_valid_for_platform("آیفون", "L2TP") is True
    assert content_writes.is_protocol_valid_for_category("prime", "Cisco") is False
    assert content_writes.is_protocol_valid_for_category("normal", "Cisco") is True
    assert content_writes.is_protocol_valid_for_category(None, "Cisco") is False
    assert content_writes.guide_needs_location("fixed", "L2TP") is True
    assert content_writes.guide_needs_location("fixed", "OpenVPN") is False


async def test_platform_and_protocol_crud(seeded):
    async with async_session_maker() as db:
        pid = await content_writes.create_platform(db, seeded, label="لینوکس", sort_order=5)
        await content_writes.set_platform_active(db, seeded, pid, False)
        assert (await _one("SELECT label, is_active FROM tutorial_platforms WHERE id=:id", id=pid)).is_active is False
        await content_writes.delete_platform(db, seeded, pid)
        assert await _one("SELECT id FROM tutorial_platforms WHERE id=:id", id=pid) is None
        prid = await content_writes.create_protocol(db, seeded, label="WireGuard", sort_order=4)
        assert (await _one("SELECT label FROM tutorial_protocols WHERE id=:id", id=prid)).label == "WireGuard"


async def test_a_platform_with_guides_cannot_be_deleted(seeded):
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="راهنما"):
            await content_writes.delete_platform(db, seeded, 1)


async def test_a_guide_for_android_l2tp_is_refused(seeded):
    android = (await _one("SELECT id FROM tutorial_platforms WHERE label='اندروید'")).id
    l2tp = (await _one("SELECT id FROM tutorial_protocols WHERE label='L2TP'")).id
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="اندروید"):
            await content_writes.save_guide(db, seeded, platform_id=android, protocol_id=l2tp, category="normal", location_id=None, body_html="x")


async def test_a_cisco_guide_outside_normal_is_refused(seeded):
    iphone = (await _one("SELECT id FROM tutorial_platforms WHERE label='آیفون'")).id
    cisco = (await _one("SELECT id FROM tutorial_protocols WHERE label='Cisco'")).id
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="Cisco"):
            await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=cisco, category="prime", location_id=None, body_html="x")


async def test_fixed_l2tp_needs_a_location_and_everything_else_refuses_one(seeded):
    iphone = (await _one("SELECT id FROM tutorial_platforms WHERE label='آیفون'")).id
    l2tp = (await _one("SELECT id FROM tutorial_protocols WHERE label='L2TP'")).id
    openvpn = (await _one("SELECT id FROM tutorial_protocols WHERE label='OpenVPN'")).id
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="لوکیشن"):
            await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=l2tp, category="fixed", location_id=None, body_html="x")
        with pytest.raises(content_writes.ContentError, match="لوکیشن"):
            await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=openvpn, category="fixed", location_id=1, body_html="x")
        await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=l2tp, category="fixed", location_id=1, body_html="<b>راهنما</b>")
    row = await _one("SELECT body_html, location_id FROM tutorial_guides WHERE platform_id=:p AND protocol_id=:pr AND category='fixed'", p=iphone, pr=l2tp)
    assert row.body_html == "<b>راهنما</b>" and row.location_id == 1


async def test_saving_the_same_combination_twice_updates_one_row(seeded):
    iphone = (await _one("SELECT id FROM tutorial_platforms WHERE label='آیفون'")).id
    openvpn = (await _one("SELECT id FROM tutorial_protocols WHERE label='OpenVPN'")).id
    async with async_session_maker() as db:
        await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=openvpn, category="normal", location_id=None, body_html="اول")
        await content_writes.save_guide(db, seeded, platform_id=iphone, protocol_id=openvpn, category="normal", location_id=None, body_html="دوم")
    async with write_engine.connect() as conn:
        rows = (await conn.execute(text("SELECT body_html FROM tutorial_guides WHERE platform_id=:p AND protocol_id=:pr AND category='normal' AND location_id IS NULL"), {"p": iphone, "pr": openvpn})).all()
    assert [r.body_html for r in rows] == ["دوم"]


async def test_download_link_is_set_per_platform_and_protocol_and_must_be_http(seeded):
    android = (await _one("SELECT id FROM tutorial_platforms WHERE label='اندروید'")).id
    openvpn = (await _one("SELECT id FROM tutorial_protocols WHERE label='OpenVPN'")).id
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="لینک"):
            await content_writes.set_download_link(db, seeded, platform_id=android, protocol_id=openvpn, url="javascript:alert(1)")
        await content_writes.set_download_link(db, seeded, platform_id=android, protocol_id=openvpn, url="https://example.com/a.apk")
    assert (await _one("SELECT url FROM download_links WHERE platform_id=:p AND protocol_id=:pr", p=android, pr=openvpn)).url == "https://example.com/a.apk"


async def test_openvpn_profile_needs_a_file_or_text_and_can_be_deactivated(seeded):
    async with async_session_maker() as db:
        with pytest.raises(content_writes.ContentError, match="فایل"):
            await content_writes.create_profile(db, seeded, name="خالی", category=None, location_id=None, platform_id=None, text_body="")
        pid = await content_writes.create_profile(db, seeded, name="متنی", category="normal", location_id=None, platform_id=None, text_body="client\nremote x 1194")
        await content_writes.set_profile_active(db, seeded, pid, False)
        assert (await _one("SELECT is_active, text FROM openvpn_profiles WHERE id=:id", id=pid)).is_active is False
        await content_writes.delete_profile(db, seeded, pid)
    assert await _one("SELECT id FROM openvpn_profiles WHERE id=:id", id=pid) is None
