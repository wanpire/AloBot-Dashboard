"""Writing into the AloBot COPY for tests - through the superuser URL, never
through the app's read-only link. Mirrors `scripts/seed_alobot_copy.py`."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import ALOBOT_ADMIN_URL

write_engine = create_async_engine(ALOBOT_ADMIN_URL, pool_size=2, max_overflow=2)

TABLES = (
    "discount_code_usages",
    "payments",
    "vpn_users",
    "services",
    "service_locations",
    "groups",
    "discount_codes",
    "resellers",
    "bot_users",
    "admin_users",
    "app_config",
    "download_links",
    "openvpn_profiles",
    "tutorial_guides",
    "tutorial_platforms",
    "tutorial_protocols",
)


async def truncate_alobot() -> None:
    async with write_engine.begin() as conn:
        await conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))


async def run(sql: str, **params) -> None:
    async with write_engine.begin() as conn:
        await conn.execute(text(sql), params)
