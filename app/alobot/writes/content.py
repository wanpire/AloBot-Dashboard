"""Tutorials, download links and OpenVPN profiles.

The three validity rules are AloBot's (`app/services/tutorials.py`) and a
test pins them against that source. They are enforced here because the
dashboard can create a combination the bot would then refuse to show -
a guide nobody can reach is worse than a refusal at the form.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit
from app.alobot.writes.catalog import CATEGORIES


class ContentError(ValueError):
    pass


def is_protocol_valid_for_platform(platform_label: str, protocol_label: str) -> bool:
    """Android 12+ dropped built-in L2TP/IPsec, so that pair is never offered."""
    is_android = "اندروید" in platform_label or "android" in platform_label.lower()
    return not (is_android and "l2tp" in protocol_label.lower())


def is_protocol_valid_for_category(category: str | None, protocol_label: str) -> bool:
    """Cisco AnyConnect is offered under عادی/normal only; an unresolvable
    category fails closed."""
    return not ("cisco" in protocol_label.lower() and category != "normal")


def guide_needs_location(category: str, protocol_label: str) -> bool:
    """Fixed-location + L2TP is the one combination split per location."""
    return category == "fixed" and "l2tp" in protocol_label.lower()


async def _labels(platform_id: int, protocol_id: int) -> tuple[str, str]:
    async with link.session() as read:
        platform = (await read.execute(text("SELECT label FROM tutorial_platforms WHERE id=:id"), {"id": platform_id})).first()
        protocol = (await read.execute(text("SELECT label FROM tutorial_protocols WHERE id=:id"), {"id": protocol_id})).first()
    if platform is None or protocol is None:
        raise ContentError("پلتفرم یا پروتکل انتخاب‌شده وجود ندارد.")
    return platform.label, protocol.label


# ── Platforms and protocols ────────────────────────────────────────────────


async def _create_lookup(db: AsyncSession, actor, table: str, action: str, label: str, sort_order: int) -> int:
    label = label.strip()
    if not label:
        raise ContentError("عنوان لازم است.")
    created: list[int] = []
    async with edit(db, actor, action=action, entity_type=table, entity_id=label) as a:
        row = (await a.execute(text(f"INSERT INTO {table} (label, is_active, sort_order) VALUES (:label, true, :sort) RETURNING id"), {"label": label, "sort": sort_order})).one()
        created.append(row.id)
        a.audit_after = {"id": row.id, "label": label}
    return created[0]


async def create_platform(db: AsyncSession, actor, *, label: str, sort_order: int = 0) -> int:
    return await _create_lookup(db, actor, "tutorial_platforms", "content.platform_create", label, sort_order)


async def create_protocol(db: AsyncSession, actor, *, label: str, sort_order: int = 0) -> int:
    return await _create_lookup(db, actor, "tutorial_protocols", "content.protocol_create", label, sort_order)


async def _set_lookup_active(db: AsyncSession, actor, table: str, action: str, row_id: int, active: bool) -> None:
    async with edit(db, actor, action=action, entity_type=table, entity_id=str(row_id)) as a:
        result = await a.execute(text(f"UPDATE {table} SET is_active=:active WHERE id=:id"), {"active": active, "id": row_id})
        if result.rowcount == 0:
            raise ContentError("چنین ردیفی نیست.")
        a.audit_after = {"is_active": active}


async def set_platform_active(db: AsyncSession, actor, platform_id: int, active: bool) -> None:
    await _set_lookup_active(db, actor, "tutorial_platforms", "content.platform_active", platform_id, active)


async def set_protocol_active(db: AsyncSession, actor, protocol_id: int, active: bool) -> None:
    await _set_lookup_active(db, actor, "tutorial_protocols", "content.protocol_active", protocol_id, active)


async def _delete_lookup(db: AsyncSession, actor, table: str, column: str, action: str, row_id: int) -> None:
    async with link.session() as read:
        guides = (await read.execute(text(f"SELECT count(*) FROM tutorial_guides WHERE {column} = :id"), {"id": row_id})).scalar_one()
        links = (await read.execute(text(f"SELECT count(*) FROM download_links WHERE {column} = :id"), {"id": row_id})).scalar_one()
    if guides or links:
        raise ContentError(f"هنوز {guides} راهنما و {links} لینک به این ردیف وصل است؛ اول آن‌ها را بردارید.")
    async with edit(db, actor, action=action, entity_type=table, entity_id=str(row_id)) as a:
        result = await a.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": row_id})
        if result.rowcount == 0:
            raise ContentError("چنین ردیفی نیست.")
        a.audit_after = {"deleted": True}


async def delete_platform(db: AsyncSession, actor, platform_id: int) -> None:
    await _delete_lookup(db, actor, "tutorial_platforms", "platform_id", "content.platform_delete", platform_id)


async def delete_protocol(db: AsyncSession, actor, protocol_id: int) -> None:
    await _delete_lookup(db, actor, "tutorial_protocols", "protocol_id", "content.protocol_delete", protocol_id)


# ── Guides ─────────────────────────────────────────────────────────────────


async def save_guide(db: AsyncSession, actor, *, platform_id: int, protocol_id: int, category: str, location_id: int | None, body_html: str) -> None:
    """One guide per (platform, protocol, category, location). Saving the
    same combination twice edits that row rather than adding a second one
    the bot would have to choose between."""
    if category not in CATEGORIES:
        raise ContentError("نوع سرویس نامعتبر است.")
    platform_label, protocol_label = await _labels(platform_id, protocol_id)
    if not is_protocol_valid_for_platform(platform_label, protocol_label):
        raise ContentError(f"«{platform_label}» پروتکل «{protocol_label}» را پشتیبانی نمی‌کند (اندروید ۱۲ به بعد L2TP داخلی ندارد).")
    if not is_protocol_valid_for_category(category, protocol_label):
        raise ContentError("Cisco فقط برای سرویس «عادی» ارائه می‌شود.")
    needs_location = guide_needs_location(category, protocol_label)
    if needs_location and location_id is None:
        raise ContentError("راهنمای «لوکیشن ثابت + L2TP» برای هر لوکیشن جداست؛ یک لوکیشن انتخاب کنید.")
    if not needs_location and location_id is not None:
        raise ContentError("فقط ترکیب «لوکیشن ثابت + L2TP» لوکیشن می‌گیرد.")
    if not body_html.strip():
        raise ContentError("متن راهنما خالی است.")
    where_loc = "location_id IS NULL" if location_id is None else "location_id = :location_id"
    params = {"platform_id": platform_id, "protocol_id": protocol_id, "category": category, "location_id": location_id, "body": body_html.strip()}
    async with link.session() as read:
        before = (await read.execute(text(f"SELECT id FROM tutorial_guides WHERE platform_id=:platform_id AND protocol_id=:protocol_id AND category=:category AND {where_loc}"), params)).first()
    async with edit(db, actor, action="content.guide_save", entity_type="tutorial_guide", entity_id=f"{platform_label}/{protocol_label}/{category}/{location_id or '-'}") as a:
        if before is None:
            await a.execute(
                text("INSERT INTO tutorial_guides (platform_id, protocol_id, category, location_id, body_html, is_active, sort_order) "
                     "VALUES (:platform_id, :protocol_id, :category, :location_id, :body, true, 0)"),
                params,
            )
        else:
            await a.execute(text("UPDATE tutorial_guides SET body_html=:body WHERE id=:id"), {"body": params["body"], "id": before.id})
        a.audit_after = {"length": len(params["body"]), "created": before is None}


async def set_guide_active(db: AsyncSession, actor, guide_id: int, active: bool) -> None:
    async with edit(db, actor, action="content.guide_active", entity_type="tutorial_guide", entity_id=str(guide_id)) as a:
        result = await a.execute(text("UPDATE tutorial_guides SET is_active=:active WHERE id=:id"), {"active": active, "id": guide_id})
        if result.rowcount == 0:
            raise ContentError("چنین راهنمایی نیست.")
        a.audit_after = {"is_active": active}


async def delete_guide(db: AsyncSession, actor, guide_id: int) -> None:
    async with edit(db, actor, action="content.guide_delete", entity_type="tutorial_guide", entity_id=str(guide_id)) as a:
        result = await a.execute(text("DELETE FROM tutorial_guides WHERE id=:id"), {"id": guide_id})
        if result.rowcount == 0:
            raise ContentError("چنین راهنمایی نیست.")
        a.audit_after = {"deleted": True}


# ── Download links and OpenVPN profiles ────────────────────────────────────


def _check_url(url: str) -> str:
    url = url.strip()
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ContentError("لینک باید با http:// یا https:// شروع شود.")
    return url


async def set_download_link(db: AsyncSession, actor, *, platform_id: int, protocol_id: int, url: str) -> None:
    url = _check_url(url)
    platform_label, protocol_label = await _labels(platform_id, protocol_id)
    if not is_protocol_valid_for_platform(platform_label, protocol_label):
        raise ContentError(f"«{platform_label}» پروتکل «{protocol_label}» را پشتیبانی نمی‌کند.")
    async with edit(db, actor, action="content.link_set", entity_type="download_link", entity_id=f"{platform_label}/{protocol_label}") as a:
        result = await a.execute(text("UPDATE download_links SET url=:url, updated_at=now() WHERE platform_id=:p AND protocol_id=:pr"), {"url": url, "p": platform_id, "pr": protocol_id})
        if result.rowcount == 0:
            await a.execute(text("INSERT INTO download_links (platform_id, protocol_id, url, created_at, updated_at) VALUES (:p, :pr, :url, now(), now())"), {"url": url, "p": platform_id, "pr": protocol_id})
        a.audit_after = {"url": url}


async def delete_download_link(db: AsyncSession, actor, link_id: int) -> None:
    async with edit(db, actor, action="content.link_delete", entity_type="download_link", entity_id=str(link_id)) as a:
        result = await a.execute(text("DELETE FROM download_links WHERE id=:id"), {"id": link_id})
        if result.rowcount == 0:
            raise ContentError("چنین لینکی نیست.")
        a.audit_after = {"deleted": True}


async def create_profile(db: AsyncSession, actor, *, name: str, category: str | None, location_id: int | None, platform_id: int | None, text_body: str) -> int:
    """A profile the bot hands out after purchase. It must carry something:
    the file upload belongs to Telegram (only the bot's token can produce a
    file_id), so from here a profile is its text."""
    name = name.strip()
    if not name:
        raise ContentError("نام پروفایل لازم است.")
    if not text_body.strip():
        raise ContentError("متن پروفایل خالی است؛ فایل .ovpn فقط از داخل ربات آپلود می‌شود.")
    if category is not None and category not in CATEGORIES:
        raise ContentError("نوع سرویس نامعتبر است.")
    created: list[int] = []
    async with edit(db, actor, action="content.profile_create", entity_type="openvpn_profile", entity_id=name) as a:
        row = (await a.execute(
            text("INSERT INTO openvpn_profiles (name, category, location_id, platform_id, text, is_active, created_at) "
                 "VALUES (:name, :category, :location_id, :platform_id, :text, true, now()) RETURNING id"),
            {"name": name, "category": category, "location_id": location_id, "platform_id": platform_id, "text": text_body.strip()},
        )).one()
        created.append(row.id)
        a.audit_after = {"id": row.id, "name": name}
    return created[0]


async def set_profile_active(db: AsyncSession, actor, profile_id: int, active: bool) -> None:
    async with edit(db, actor, action="content.profile_active", entity_type="openvpn_profile", entity_id=str(profile_id)) as a:
        result = await a.execute(text("UPDATE openvpn_profiles SET is_active=:active WHERE id=:id"), {"active": active, "id": profile_id})
        if result.rowcount == 0:
            raise ContentError("چنین پروفایلی نیست.")
        a.audit_after = {"is_active": active}


async def delete_profile(db: AsyncSession, actor, profile_id: int) -> None:
    async with edit(db, actor, action="content.profile_delete", entity_type="openvpn_profile", entity_id=str(profile_id)) as a:
        result = await a.execute(text("DELETE FROM openvpn_profiles WHERE id=:id"), {"id": profile_id})
        if result.rowcount == 0:
            raise ContentError("چنین پروفایلی نیست.")
        a.audit_after = {"deleted": True}
