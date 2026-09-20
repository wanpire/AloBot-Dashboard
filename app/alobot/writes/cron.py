"""AloBot's scheduled jobs, and ours beside them.

AloBot's scheduler fires each entry once per day (or week/month) at a time an
admin may override through `app_config` as `sched_cfg_<key>_hour/_minute`.
The table below is its `_SCHEDULE`, pinned by a test against that source.

Our own sweeps have no schedule to show: they run every cycle of the poll
loop and what decides whether something is due is a threshold, not a cadence.
Drawing clock times for them would describe a scheduler that does not exist.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit

# (key, default hour, default minute, cadence) - AloBot's `_SCHEDULE`.
SCHEDULE: tuple[tuple[str, int, int, str], ...] = (
    ("backup_1200", 12, 0, "daily"),
    ("backup_0000", 0, 0, "daily"),
    ("report_daily", 0, 5, "daily"),
    ("report_weekly", 0, 10, "weekly"),
    ("report_monthly", 0, 15, "monthly"),
    ("ibsng_health_0800", 8, 0, "daily"),
    ("ibsng_health_2000", 20, 0, "daily"),
    ("server_health_0205", 2, 5, "daily"),
    ("server_health_0805", 8, 5, "daily"),
    ("server_health_1405", 14, 5, "daily"),
    ("server_health_2005", 20, 5, "daily"),
)

JOB_LABELS = {
    "backup": "بکاپ پایگاه‌داده",
    "report_daily": "گزارش فروش روزانه",
    "report_weekly": "گزارش فروش هفتگی",
    "report_monthly": "گزارش فروش ماهانه",
    "ibsng_health": "بررسی سلامت IBSng",
    "server_health": "بررسی سلامت سرور (دیسک، پایگاه‌داده، Redis)",
}
CADENCE_LABELS = {"daily": "روزانه", "weekly": "هفتگی", "monthly": "ماهانه"}


class CronError(ValueError):
    pass


def label_for(key: str) -> str:
    for prefix, label in JOB_LABELS.items():
        if key.startswith(prefix):
            return label
    return key


async def status(db: AsyncSession) -> list[dict[str, Any]]:
    async with link.session() as read:
        overrides = {r.key: r.value for r in (await read.execute(text("SELECT key, value FROM app_config WHERE key LIKE 'sched\\_cfg\\_%'"))).all()}
    rows = []
    for key, default_hour, default_minute, cadence in SCHEDULE:
        hour_raw, minute_raw = overrides.get(f"sched_cfg_{key}_hour"), overrides.get(f"sched_cfg_{key}_minute")
        hour = int(hour_raw) if hour_raw is not None else default_hour
        minute = int(minute_raw) if minute_raw is not None else default_minute
        rows.append({
            "key": key, "label": label_for(key), "cadence": cadence, "cadence_label": CADENCE_LABELS.get(cadence, cadence),
            "time": f"{hour:02d}:{minute:02d}", "hour": hour, "minute": minute,
            "default_time": f"{default_hour:02d}:{default_minute:02d}",
            "overridden": hour_raw is not None or minute_raw is not None,
        })
    return rows


async def set_time(db: AsyncSession, actor, key: str, *, hour: int, minute: int) -> None:
    if key not in {k for k, *_ in SCHEDULE}:
        raise CronError("کلید زمان‌بندی نامعتبر است.")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CronError("ساعت باید بین ۰ تا ۲۳ و دقیقه بین ۰ تا ۵۹ باشد.")
    async with edit(db, actor, action="cron.set_time", entity_type="app_config", entity_id=key) as a:
        for suffix, value in (("hour", hour), ("minute", minute)):
            await a.execute(
                text("INSERT INTO app_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
                {"k": f"sched_cfg_{key}_{suffix}", "v": str(value)},
            )
        a.audit_after = {"time": f"{hour:02d}:{minute:02d}"}


async def our_sweeps(db: AsyncSession) -> list[dict[str, Any]]:
    """This project's own sweeps. No clock: they run every cycle and read a
    threshold, which is why the screen shows what each one asks instead."""
    from app.core.config import get_settings
    from app.services import settings as settings_service
    from app.services.sweeps import registry

    retention = await settings_service.get(db, "events", "retention_days")
    what = {
        "claims.mirror": "پرداخت‌های کارت در انتظارِ آلوبات را به صف بررسی این داشبورد می‌آورد.",
        "claims.settle": "هر پرداخت باز را با واریزهای بانکی می‌سنجد و جفت یکتا را تایید می‌کند.",
        "outbox.flush": "پیام‌های صف‌شدهٔ تلگرام را می‌فرستد (با تلاش دوباره و احترام به محدودیت نرخ).",
        "events.flush": "هشدارها و خطاهای ساخت‌یافته را در «رویدادها» ثبت می‌کند.",
        "events.prune": f"رویدادهای قدیمی‌تر از {retention} روز را پاک می‌کند.",
        "sessions.prune": "نشست‌های منقضی اپراتورها را پاک می‌کند.",
    }
    interval = get_settings().sweep_interval_seconds
    return [{"name": name, "what": what.get(name, ""), "every_seconds": interval} for name in registry.names()]
