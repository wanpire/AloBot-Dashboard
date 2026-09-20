"""Phase 5 task 8: the bot's wording and keyboards, edited here and adopted
by AloBot in Phase 7."""

import pytest

from app.db.session import async_session_maker
from app.services import bot_content
from tests.web import make_operator


async def test_the_main_menu_default_is_alobots_real_menu():
    from pathlib import Path

    source = (Path("vendor/alobot") / "app" / "bot" / "keyboards" / "menus.py").read_text()
    for button in bot_content.MENUS["main"].buttons:
        assert f'callback_data="{button.action}"' in source, button.action
        assert f'text="{button.label}"' in source, button.label


async def test_layout_round_trips_through_our_own_settings_table():
    actor = await make_operator(email="bc2@x.io", role="ADMIN")
    async with async_session_maker() as db:
        before = await bot_content.layout(db, "main")
        assert [b.action for b in before] == [b.action for b in bot_content.MENUS["main"].buttons]
        await bot_content.save_layout(db, actor, "main", [
            {"action": "menu:buy", "label": "🔑 خرید", "style": "success", "row": 1},
            {"action": "menu:myservices", "label": "🛍 سرویس‌های من", "style": "primary", "row": 1},
            {"action": "menu:support", "label": "☎️ پشتیبانی", "style": None, "row": 2},
        ])
        after = await bot_content.layout(db, "main")
    assert [b.action for b in after] == ["menu:buy", "menu:myservices", "menu:support"]
    assert after[0].label == "🔑 خرید" and after[2].style is None


async def test_a_required_button_cannot_be_dropped():
    actor = await make_operator(email="bc3@x.io", role="ADMIN")
    async with async_session_maker() as db:
        with pytest.raises(bot_content.ContentError, match="خرید اشتراک"):
            await bot_content.save_layout(db, actor, "main", [{"action": "menu:support", "label": "پشتیبانی", "style": None, "row": 1}])


async def test_unknown_actions_duplicates_and_bad_styles_are_refused():
    actor = await make_operator(email="bc4@x.io", role="ADMIN")
    required = [{"action": a, "label": "x", "style": None, "row": 1} for a in bot_content.required_actions("main")]
    async with async_session_maker() as db:
        with pytest.raises(bot_content.ContentError, match="ناشناخته"):
            await bot_content.save_layout(db, actor, "main", required + [{"action": "menu:hack", "label": "x", "style": None, "row": 1}])
        with pytest.raises(bot_content.ContentError, match="تکراری"):
            await bot_content.save_layout(db, actor, "main", required + [required[0]])
        with pytest.raises(bot_content.ContentError, match="رنگ"):
            await bot_content.save_layout(db, actor, "main", [{**b, "style": "rainbow"} for b in required])


async def test_a_label_cannot_be_empty_or_longer_than_telegram_allows():
    actor = await make_operator(email="bc5@x.io", role="ADMIN")
    required = [{"action": a, "label": "x", "style": None, "row": 1} for a in bot_content.required_actions("main")]
    async with async_session_maker() as db:
        with pytest.raises(bot_content.ContentError, match="عنوان"):
            await bot_content.save_layout(db, actor, "main", [{**b, "label": " "} for b in required])
        with pytest.raises(bot_content.ContentError, match="بلند"):
            await bot_content.save_layout(db, actor, "main", [{**b, "label": "ب" * 65} for b in required])


async def test_texts_default_until_overridden_and_keep_their_placeholders():
    actor = await make_operator(email="bc6@x.io", role="ADMIN")
    key = bot_content.TEXTS[0].key
    async with async_session_maker() as db:
        assert (await bot_content.texts(db))[key] == bot_content.TEXTS[0].default
        await bot_content.save_text(db, actor, key, bot_content.TEXTS[0].default + " (ویرایش‌شده)")
        assert (await bot_content.texts(db))[key].endswith("(ویرایش‌شده)")
        with pytest.raises(bot_content.ContentError, match="ناشناخته"):
            await bot_content.save_text(db, actor, "NO_SUCH_KEY", "x")


async def test_a_text_that_drops_a_placeholder_is_refused():
    actor = await make_operator(email="bc7@x.io", role="ADMIN")
    spec = next(t for t in bot_content.TEXTS if t.placeholders)
    async with async_session_maker() as db:
        with pytest.raises(bot_content.ContentError, match=spec.placeholders[0]):
            await bot_content.save_text(db, actor, spec.key, "متنی بدون جای‌گذار")


async def test_reset_restores_the_shipped_default():
    actor = await make_operator(email="bc8@x.io", role="ADMIN")
    async with async_session_maker() as db:
        await bot_content.save_text(db, actor, bot_content.TEXTS[0].key, "چیز دیگری")
        await bot_content.reset_text(db, actor, bot_content.TEXTS[0].key)
        assert (await bot_content.texts(db))[bot_content.TEXTS[0].key] == bot_content.TEXTS[0].default
        await bot_content.save_layout(db, actor, "main", [{"action": a, "label": "x", "style": None, "row": 1} for a in bot_content.required_actions("main")])
        await bot_content.reset_layout(db, actor, "main")
        assert [b.action for b in await bot_content.layout(db, "main")] == [b.action for b in bot_content.MENUS["main"].buttons]
