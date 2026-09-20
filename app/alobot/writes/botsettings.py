"""AloBot's own settings (`app_config`), as a typed registry.

**Each switch carries its own vocabulary, because AloBot has no single
convention.** `auto_approve_enabled` and `mandatory_channel_enabled` are read
as `!= "true"` - ON only for that exact word, so anything unrecognised is
OFF. `reminder_enabled` and `trial_limit_enabled` are read as `== "false"` -
OFF only for that exact word, so anything unrecognised is ON. A form that
wrote one convention for all four would silently mean the opposite on half
of them, and nothing would look broken.

Keys not listed here cannot be written at all. Three families are managed by
their own screens instead: `category_enabled:*` (catalog), `sched_cfg_*`
(cron), `payment_review_admin_ids` (access). The `topic_*` ids are shown
read-only: the bot creates those forum topics itself and a hand-typed
thread id points reports at nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit
from app.services.accounts import luhn_ok, normalize_card

READ_ONLY_PREFIXES = ("topic_",)


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class BotSettingSpec:
    key: str
    label: str
    hint: str
    kind: str  # bool | int | text | card | handle | chat_id
    on: str = "true"
    off: str = "false"
    unknown_is: bool = True
    min: int | None = None
    max: int | None = None


REGISTRY: tuple[BotSettingSpec, ...] = (
    BotSettingSpec("card_number", "شمارهٔ کارت", "کارتی که مشتری در فاکتور کارت‌به‌کارت می‌بیند. تغییرش روی فاکتورهای تازه اثر می‌گذارد؛ برای تطبیق خودکار، همین کارت باید در «حساب‌ها و کارت‌ها» هم ثبت شده باشد.", "card"),
    BotSettingSpec("card_holder", "نام صاحب کارت", "زیر شمارهٔ کارت روی فاکتور چاپ می‌شود.", "text"),
    BotSettingSpec("support_username", "آیدی پشتیبانی", "بدون @ ذخیره می‌شود؛ دکمهٔ «پشتیبانی» ربات به همین می‌رسد.", "handle"),
    BotSettingSpec("auto_approve_enabled", "تایید خودکار پرداخت کارت‌به‌کارت", "تایید بدون مدرک بانکی، صرفاً با گذشت زمان. با راه‌افتادن تطبیق پیامک بانکی این باید خاموش شود.", "bool", unknown_is=False),
    BotSettingSpec("auto_approve_delay_minutes", "تاخیر تایید خودکار (دقیقه)", "چند دقیقه پس از ثبت پرداخت، اگر ادمین تصمیمی نگرفته باشد.", "int", min=1, max=1440),
    BotSettingSpec("reminder_enabled", "یادآوری انقضای سرویس", "پیام یادآوری پیش از پایان سرویس مشتری.", "bool", unknown_is=True),
    BotSettingSpec("reminder_bot_mention", "نام ربات در یادآوری", "متنی که در پیام یادآوری به‌عنوان نام ربات می‌آید.", "text"),
    BotSettingSpec("mandatory_channel_enabled", "عضویت اجباری کانال", "تا وقتی کاربر عضو کانال نشده، ربات جز /start و پشتیبانی چیزی نشان نمی‌دهد.", "bool", unknown_is=False),
    BotSettingSpec("mandatory_channel_id", "کانال اجباری", "شناسهٔ عددی کانال یا @username آن. ربات باید در کانال ادمین باشد وگرنه گیت بی‌صدا باز می‌ماند.", "text"),
    BotSettingSpec("trial_limit_enabled", "محدودیت سرویس تست", "هر شناسهٔ تلگرام فقط یک سرویس تست در هر ۳۰ روز.", "bool", unknown_is=True),
    BotSettingSpec("group_chat_id", "گروه گزارش‌دهی", "گروه فورومی که بکاپ، گزارش فروش و سلامت به تاپیک‌هایش می‌رود.", "chat_id"),
)

_BY_KEY = {s.key: s for s in REGISTRY}


def spec(key: str) -> BotSettingSpec | None:
    return _BY_KEY.get(key)


def _coerce(s: BotSettingSpec, raw: Any) -> str:
    if s.kind == "bool":
        return s.on if raw in (True, 1, "1", "true", "on", "yes") else s.off
    if s.kind == "card":
        number = normalize_card(str(raw))
        if number is None or not luhn_ok(number):
            raise SettingsError("شمارهٔ کارت باید ۱۶ رقم و معتبر باشد (رقم کنترلی Luhn).")
        return number
    if s.kind == "handle":
        return str(raw).strip().lstrip("@")
    if s.kind in ("int", "chat_id"):
        value = str(raw).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not re.fullmatch(r"-?\d+", value):
            raise SettingsError(f"«{s.label}» باید یک عدد باشد.")
        number = int(value)
        if s.kind == "int" and ((s.min is not None and number < s.min) or (s.max is not None and number > s.max)):
            raise SettingsError(f"«{s.label}» باید بین {s.min} و {s.max} باشد.")
        return str(number)
    return str(raw).strip()


async def current(db: AsyncSession) -> dict[str, Any]:
    """Typed values for the form: booleans resolved through each switch's own
    vocabulary, everything else as stored."""
    async with link.session() as read:
        stored = {r.key: r.value for r in (await read.execute(text("SELECT key, value FROM app_config"))).all()}
    out: dict[str, Any] = {}
    for s in REGISTRY:
        raw = stored.get(s.key)
        if s.kind == "bool":
            out[s.key] = s.unknown_is if raw is None or raw not in (s.on, s.off) else raw == s.on
        else:
            out[s.key] = raw
    return out


async def read_only_keys(db: AsyncSession) -> dict[str, str]:
    async with link.session() as read:
        rows = (await read.execute(text("SELECT key, value FROM app_config ORDER BY key"))).all()
    return {r.key: r.value for r in rows if r.key.startswith(READ_ONLY_PREFIXES)}


async def save(db: AsyncSession, actor, values: dict[str, Any]) -> int:
    """Validate everything first, then write. An unlisted key refuses the
    whole save rather than writing the rest."""
    prepared: dict[str, str] = {}
    for key, raw in values.items():
        s = spec(key)
        if s is None:
            raise SettingsError(f"کلید «{key}» در فهرست تنظیمات قابل ویرایش نیست (ناشناخته یا فقط‌خواندنی).")
        prepared[key] = _coerce(s, raw)
    async with link.session() as read:
        before = {r.key: r.value for r in (await read.execute(text("SELECT key, value FROM app_config WHERE key = ANY(:keys)"), {"keys": list(prepared)})).all()}
    changed = {k: v for k, v in prepared.items() if before.get(k) != v}
    if not changed:
        return 0
    async with edit(db, actor, action="botsettings.save", entity_type="app_config", entity_id=",".join(sorted(changed)), before={k: before.get(k) for k in changed}) as a:
        for key, value in changed.items():
            await a.execute(text("INSERT INTO app_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"), {"k": key, "v": value})
        a.audit_after = changed
    return len(changed)
