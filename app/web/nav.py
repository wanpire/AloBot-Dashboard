"""The sidebar, grouped by the job. A group is one job and nothing that
belongs to that job lives anywhere else. Labels must not be substrings of
each other (a test holds that) so an operator never reads one twice."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    id: str
    label: str
    icon: str


@dataclass(frozen=True)
class NavGroup:
    label: str
    items: tuple[NavItem, ...]


NAV: tuple[NavGroup, ...] = (
    NavGroup(
        "گزارش‌ها",
        (
            NavItem("overview", "نمای کلی", "home"),
            NavItem("stats", "آمار فروشگاه", "grid"),
            NavItem("finance", "آمار مالی", "bars"),
        ),
    ),
    NavGroup(
        "مشتری و فروش",
        (
            NavItem("customers", "کاربران", "users"),
            NavItem("orders", "سفارشات", "receipt"),
            NavItem("subscriptions", "اشتراک‌های مشتری", "package"),
            NavItem("resellers", "نمایندگان", "users"),
            NavItem("bulk", "ارسال گروهی", "send"),
        ),
    ),
    NavGroup(
        "کاتالوگ",
        (
            NavItem("catalog", "سرویس‌ها", "grid"),
            NavItem("discounts", "کدهای تخفیف", "ticket"),
            NavItem("tutorials", "آموزش و لینک‌ها", "text"),
        ),
    ),
    NavGroup(
        "پول",
        (
            NavItem("payments", "پرداخت‌ها", "money"),
            NavItem("transactions", "تراکنش‌های بانکی", "wallet"),
            NavItem("accounts", "حساب‌ها و کارت‌ها", "wallet"),
            NavItem("banks", "بانک‌ها", "list"),
            NavItem("devices", "دستگاه‌ها", "server"),
        ),
    ),
    NavGroup(
        "ربات",
        (
            NavItem("texts", "متن‌های ربات", "text"),
            NavItem("keyboard", "چیدمان کیبورد", "keyboard"),
            NavItem("botsettings", "تنظیمات ربات", "settings"),
            NavItem("cron", "کارهای زمان‌بندی‌شده", "list"),
        ),
    ),
    NavGroup(
        "سیستم",
        (
            NavItem("settings", "تنظیمات داشبورد", "settings"),
            NavItem("access", "دسترسی‌ها", "users"),
            NavItem("events", "رویدادها", "list"),
        ),
    ),
)

# Sections only an ADMIN is offered. `events` shows stack traces and the
# shop's own failures; `access` edits who may operate the shop.
ADMIN_ONLY: frozenset[str] = frozenset({"events", "access", "settings", "botsettings", "bulk"})

# Sections a READ_ONLY operator may open. The rest either name customers or
# are administrative; the server decides (403), this list only keeps the
# sidebar from offering a door that would refuse.
READABLE_BY_READER: frozenset[str] = frozenset(
    {
        "overview",
        "stats",
        "finance",
        "payments",
        "transactions",
        "accounts",
        "banks",
        "devices",
        "catalog",
        "discounts",
        "tutorials",
        "texts",
        "keyboard",
        "cron",
        "resellers",
    }
)

ALL_IDS: frozenset[str] = frozenset(item.id for group in NAV for item in group.items)


def visible(role: str, page_id: str) -> bool:
    if page_id not in ALL_IDS:
        return False
    if role == "ADMIN":
        return True
    if page_id in ADMIN_ONLY:
        return False
    if role == "READ_ONLY":
        return page_id in READABLE_BY_READER
    return True  # REVIEWER


def visible_nav(role: str) -> list[tuple[str, list[NavItem]]]:
    out = []
    for group in NAV:
        items = [i for i in group.items if visible(role, i.id)]
        if items:
            out.append((group.label, items))
    return out


def page_label(page_id: str) -> str:
    for group in NAV:
        for item in group.items:
            if item.id == page_id:
                return item.label
    return ""
