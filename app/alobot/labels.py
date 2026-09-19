"""AloBot's own vocabulary, in the words the shop uses. Mirrors the labels in
AloBot's `app/services/catalog.py` and payment handlers."""

CATEGORY_LABELS = {"normal": "عادی", "prime": "پرایم", "fixed": "لوکیشن ثابت", "junior": "جونیور"}
STATUS_LABELS = {"pending": "در انتظار", "approved": "تایید شده", "rejected": "رد شده", "revoked": "لغو شده"}
METHOD_LABELS = {"card": "کارت‌به‌کارت", "crypto": "کریپتو", "admin": "ادمین"}
PURPOSE_LABELS = {"purchase": "خرید", "renew": "تمدید", "upgrade": "ارتقا"}
ADMIN_LEVEL_LABELS = {"support": "پشتیبانی", "sales": "فروش", "full": "مدیر کامل"}

# AloBot's app_config keys, as the bot names them, with what each one means.
BOT_SETTING_LABELS = {
    "card_number": "شمارهٔ کارت",
    "card_holder": "نام صاحب کارت",
    "support_username": "آیدی پشتیبانی",
    "auto_approve_enabled": "تایید خودکار پرداخت کارت‌به‌کارت",
    "auto_approve_delay_minutes": "تاخیر تایید خودکار (دقیقه)",
    "reminder_enabled": "یادآوری انقضا",
    "reminder_bot_mention": "نام ربات در یادآوری",
    "mandatory_channel_enabled": "عضویت اجباری کانال",
    "mandatory_channel_id": "کانال اجباری",
    "trial_limit_enabled": "محدودیت سرویس تست",
    "group_chat_id": "گروه گزارش‌دهی",
    "topic_backup": "تاپیک بکاپ",
    "topic_live_logs": "تاپیک لاگ زنده",
    "topic_sell_report": "تاپیک گزارش فروش",
    "topic_notifications": "تاپیک اعلان‌ها",
    "topic_ibsng_health": "تاپیک سلامت IBSng",
    "topic_new_purchase": "تاپیک خرید جدید",
    "topic_trial_service": "تاپیک سرویس تست",
    "topic_bot_start": "تاپیک استارت ربات",
}


def label(mapping: dict[str, str], key: str | None) -> str:
    if key is None:
        return "—"
    return mapping.get(key, key)
