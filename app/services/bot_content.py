"""The bot's wording and its keyboards, edited here.

**AloBot does not read any of this yet.** Its texts and menus are hardcoded
in its own source today; Phase 7 wires it to read these rows and fall back to
its shipped strings when a row is absent. Until then this screen is a draft
contract - which is why the defaults below are AloBot's REAL strings, copied
from `app/bot/keyboards/menus.py` in the linked checkout and pinned by a
test, rather than wording invented here.

Rows live in this project's own `settings` table under scope `bot`, as
`keyboard:<menu>` and `text:<KEY>`. A menu is stored whole or not at all: a
button means something by where it sits relative to the others, so there is
no merge and therefore no question about where a button added by a later
release lands in a layout saved last year. Absent means "use the default",
and the screen shows the two states differently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.services.audit import audit_row

SCOPE = "bot"
STYLES = (None, "primary", "success", "danger")
MAX_LABEL = 64


class ContentError(ValueError):
    pass


@dataclass(frozen=True)
class MenuButton:
    action: str
    label: str
    style: str | None = None
    row: int = 1
    required: bool = False
    hint: str = ""


@dataclass(frozen=True)
class Menu:
    id: str
    label: str
    hint: str
    buttons: tuple[MenuButton, ...]


# AloBot's real main menu (`main_menu` in its keyboards/menus.py), including
# the native Bot API 9.4 styles it already uses.
MENUS: dict[str, Menu] = {
    "main": Menu(
        id="main",
        label="منوی اصلی",
        hint="اولین چیزی که مشتری بعد از /start می‌بیند. دکمه‌های نمایندگی و پنل مدیریت را خود ربات بر اساس نقش کاربر اضافه می‌کند و این‌جا نمی‌آیند.",
        buttons=(
            MenuButton("menu:renew", "♻️ تمدید سرویس", "success", 1, False, "تمدید سرویس‌های موجود"),
            MenuButton("menu:buy", "🔑 خرید اشتراک", "success", 1, True, "مسیر خرید؛ بدون آن مشتری راهی برای خرید ندارد"),
            MenuButton("menu:myservices", "🛍 سرویس‌های من", "primary", 2, True, "فهرست اکانت‌ها؛ بدون آن مشتری به اطلاعات اکانتش نمی‌رسد"),
            MenuButton("menu:trial", "🎁 سرویس تست", "success", 2, False, "اکانت تست رایگان"),
            MenuButton("menu:support", "☎️ پشتیبانی", "primary", 3, True, "راه تماس با پشتیبانی؛ بدون آن مشتریِ گیرکرده راهی ندارد"),
            MenuButton("menu:tutorials", "📚 آموزش", "primary", 3, False, "آموزش‌های اتصال"),
        ),
    ),
}


@dataclass(frozen=True)
class TextSpec:
    key: str
    label: str
    default: str
    hint: str
    placeholders: tuple[str, ...] = ()


TEXTS: tuple[TextSpec, ...] = (
    TextSpec("WELCOME", "خوش‌آمدگویی", "به ربات خوش آمدید 👋\nاز منوی زیر یکی را انتخاب کنید.", "پیام /start، بالای منوی اصلی."),
    TextSpec("SUPPORT", "صفحهٔ پشتیبانی", "برای پشتیبانی به {support} پیام بدهید.", "زیر دکمهٔ «پشتیبانی».", ("{support}",)),
    TextSpec("EXPIRY_REMINDER", "یادآوری انقضا", "سرویس {username} تا {days} روز دیگر تمام می‌شود.", "پیام یادآوری پیش از پایان سرویس.", ("{username}", "{days}")),
    TextSpec("PAYMENT_RECEIVED", "تایید پرداخت", "✅ پرداخت شما تایید شد و سرویس در حال آماده‌سازی است.", "پس از تایید پرداخت کارت‌به‌کارت."),
)
_TEXT_BY_KEY = {t.key: t for t in TEXTS}


def required_actions(menu_id: str) -> list[str]:
    return [b.action for b in MENUS[menu_id].buttons if b.required]


def _menu(menu_id: str) -> Menu:
    if menu_id not in MENUS:
        raise ContentError("چنین کیبوردی نیست.")
    return MENUS[menu_id]


async def _row(db: AsyncSession, key: str) -> Setting | None:
    return await db.get(Setting, (SCOPE, key))


async def layout(db: AsyncSession, menu_id: str) -> list[MenuButton]:
    menu = _menu(menu_id)
    row = await _row(db, f"keyboard:{menu_id}")
    if row is None or not row.value:
        return list(menu.buttons)
    known = {b.action: b for b in menu.buttons}
    return [MenuButton(action=b["action"], label=b["label"], style=b.get("style"), row=b.get("row", 1), required=known[b["action"]].required, hint=known[b["action"]].hint) for b in row.value if b["action"] in known]


async def is_customised(db: AsyncSession, menu_id: str) -> bool:
    row = await _row(db, f"keyboard:{menu_id}")
    return row is not None and bool(row.value)


async def save_layout(db: AsyncSession, actor, menu_id: str, buttons: list[dict[str, Any]]) -> None:
    menu = _menu(menu_id)
    known = {b.action: b for b in menu.buttons}
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for button in buttons:
        action = str(button.get("action", ""))
        if action not in known:
            raise ContentError(f"دکمهٔ ناشناخته: {action}")
        if action in seen:
            raise ContentError(f"دکمهٔ تکراری: {known[action].label}")
        seen.add(action)
        label = str(button.get("label", "")).strip()
        if not label:
            raise ContentError(f"عنوان دکمهٔ «{known[action].label}» خالی است.")
        if len(label) > MAX_LABEL:
            raise ContentError(f"عنوان دکمه بلندتر از {MAX_LABEL} نویسه است.")
        style = button.get("style") or None
        if style not in STYLES:
            raise ContentError("رنگ دکمه باید یکی از پیش‌فرض، آبی، سبز یا قرمز باشد.")
        try:
            row_index = int(button.get("row", 1))
        except (TypeError, ValueError):
            raise ContentError("شمارهٔ ردیف باید عدد باشد.") from None
        cleaned.append({"action": action, "label": label, "style": style, "row": max(1, row_index)})
    missing = [known[a].label for a in required_actions(menu_id) if a not in seen]
    if missing:
        raise ContentError(f"این دکمه‌ها را نمی‌توان برداشت: {'، '.join(missing)}.")
    await _store(db, actor, f"keyboard:{menu_id}", cleaned, action="botcontent.keyboard_save", entity_id=menu_id)


async def reset_layout(db: AsyncSession, actor, menu_id: str) -> None:
    _menu(menu_id)
    await _store(db, actor, f"keyboard:{menu_id}", None, action="botcontent.keyboard_reset", entity_id=menu_id)


async def texts(db: AsyncSession) -> dict[str, str]:
    out = {}
    for spec in TEXTS:
        row = await _row(db, f"text:{spec.key}")
        out[spec.key] = row.value if row is not None and row.value else spec.default
    return out


async def save_text(db: AsyncSession, actor, key: str, value: str) -> None:
    spec = _TEXT_BY_KEY.get(key)
    if spec is None:
        raise ContentError(f"متن ناشناخته: {key}")
    value = value.strip()
    if not value:
        raise ContentError("متن خالی است.")
    for placeholder in spec.placeholders:
        if placeholder not in value:
            raise ContentError(f"جای‌گذار {placeholder} باید در متن بماند، وگرنه ربات نمی‌تواند مقدارش را بگذارد.")
    await _store(db, actor, f"text:{key}", value, action="botcontent.text_save", entity_id=key)


async def reset_text(db: AsyncSession, actor, key: str) -> None:
    if key not in _TEXT_BY_KEY:
        raise ContentError(f"متن ناشناخته: {key}")
    await _store(db, actor, f"text:{key}", None, action="botcontent.text_reset", entity_id=key)


async def _store(db: AsyncSession, actor, key: str, value: Any, *, action: str, entity_id: str) -> None:
    row = await _row(db, key)
    before = row.value if row is not None else None
    if value is None:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(Setting(scope=SCOPE, key=key, value=value, updated_by=actor.email))
    else:
        row.value, row.updated_by = value, actor.email
    db.add(audit_row(action=action, entity_type="bot_content", entity_id=entity_id, actor_role=actor.role, actor_operator_id=actor.id, before=before, after=value))
    await db.commit()


def menu_defaults(menu_id: str) -> list[dict[str, Any]]:
    return [asdict(b) for b in _menu(menu_id).buttons]
