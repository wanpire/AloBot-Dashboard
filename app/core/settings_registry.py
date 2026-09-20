"""Every setting the dashboard itself reads, with a name a person understands.

The registry is the contract: the page draws its form from it, the service
refuses a write to anything not in it, and code reads values through
`app.services.settings.get` which falls back to `default`. A key that is not
here cannot be set, so an operator never edits a row nothing reads.

AloBot's own settings (`app_config` in AloBot's database) are a different
registry, built in Phase 5; nothing here touches them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SettingKind = Literal["text", "int", "bool", "chat_id"]


@dataclass(frozen=True)
class SettingSpec:
    scope: str
    key: str
    label: str
    hint: str
    kind: SettingKind
    default: Any = None
    min: int | None = None
    max: int | None = None

    @property
    def form_name(self) -> str:
        return f"{self.scope}.{self.key}"


REGISTRY: tuple[SettingSpec, ...] = (
    SettingSpec(
        "general",
        "shop_name",
        "نام فروشگاه",
        "در عنوان صفحه‌ها و پیام‌های داشبورد به کار می‌رود.",
        "text",
        default="آلو",
    ),
    SettingSpec(
        "events",
        "retention_days",
        "نگهداری رویدادها (روز)",
        "رویدادهای قدیمی‌تر از این تعداد روز به‌صورت خودکار پاک می‌شوند.",
        "int",
        default=30,
        min=1,
        max=365,
    ),
    SettingSpec(
        "notify",
        "customers_enabled",
        "ارسال پیام به مشتریان",
        "تا فاز یکپارچه‌سازی خاموش می‌ماند: ربات آلوبات خودش به مشتری خبر می‌دهد. روشن که شود، این داشبورد نتیجهٔ تایید بانکی را از طریق توکن ربات به مشتری می‌فرستد.",
        "bool",
        default=False,
    ),
    SettingSpec(
        "alerts",
        "operator_chat_id",
        "چت هشدارهای سیستم",
        "شناسهٔ چت تلگرامی که هشدارهای سیستم به آن فرستاده می‌شود. خالی یعنی بدون هشدار تلگرامی (ارسال از فاز ۴ فعال می‌شود).",
        "chat_id",
        default=None,
    ),
)

_BY_KEY = {(s.scope, s.key): s for s in REGISTRY}


def spec_for(scope: str, key: str) -> SettingSpec | None:
    return _BY_KEY.get((scope, key))
