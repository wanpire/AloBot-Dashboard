"""The bank vocabulary an operator edits: card prefixes and SMS patterns."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BankCardPrefix, BankSmsPattern
from app.services.audit import audit_row
from app.sms import DbPattern, parse
from app.sms.patterns import PatternRefused, check_pattern
from app.sms.types import ParseResult


class BankError(ValueError):
    pass


async def list_prefixes(session: AsyncSession) -> list[BankCardPrefix]:
    return list((await session.execute(select(BankCardPrefix).order_by(BankCardPrefix.prefix))).scalars().all())


async def upsert_prefix(session: AsyncSession, actor, prefix: str, bank_name: str) -> BankCardPrefix:
    prefix = prefix.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not re.fullmatch(r"[0-9]{4,8}", prefix) or not bank_name.strip():
        raise BankError("پیش‌شماره باید ۴ تا ۸ رقم باشد و نام بانک لازم است.")
    row = await session.get(BankCardPrefix, prefix)
    before = row.bank_name if row else None
    if row is None:
        row = BankCardPrefix(prefix=prefix, bank_name=bank_name.strip(), updated_by=actor.email)
        session.add(row)
    else:
        row.bank_name, row.updated_by = bank_name.strip(), actor.email
    session.add(audit_row(action="bank.prefix.set", entity_type="bank_card_prefix", entity_id=prefix, actor_role=actor.role, actor_operator_id=actor.id, before={"bank": before}, after={"bank": bank_name.strip()}))
    await session.commit()
    return row


async def delete_prefix(session: AsyncSession, actor, prefix: str) -> None:
    row = await session.get(BankCardPrefix, prefix)
    if row is None:
        raise BankError("چنین پیش‌شماره‌ای نیست.")
    session.add(audit_row(action="bank.prefix.delete", entity_type="bank_card_prefix", entity_id=prefix, actor_role=actor.role, actor_operator_id=actor.id, before={"bank": row.bank_name}))
    await session.delete(row)
    await session.commit()


async def list_patterns(session: AsyncSession) -> list[BankSmsPattern]:
    return list((await session.execute(select(BankSmsPattern).order_by(BankSmsPattern.priority, BankSmsPattern.id))).scalars().all())


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def validate_pattern_fields(fields: dict) -> dict:
    pid = (fields.get("id") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pid):
        raise BankError("شناسهٔ الگو: حروف کوچک لاتین، رقم، خط تیره.")
    if not (fields.get("bank_name") or "").strip():
        raise BankError("نام بانک لازم است.")
    for key in ("detect_re", "amount_re"):
        try:
            check_pattern((fields.get(key) or "").strip())
        except PatternRefused as exc:
            raise BankError(f"{key}: {exc}") from None
    for key in ("balance_re", "account_re", "reference_re"):
        if _clean(fields.get(key)):
            try:
                check_pattern(fields[key].strip())
            except PatternRefused as exc:
                raise BankError(f"{key}: {exc}") from None
    unit = (fields.get("amount_unit") or "IRR").strip().upper()
    direction = (fields.get("direction") or "CREDIT").strip().upper()
    if unit not in ("IRR", "TOMAN") or direction not in ("CREDIT", "DEBIT"):
        raise BankError("واحد مبلغ یا جهت نامعتبر است.")
    try:
        priority = int(fields.get("priority") or 100)
    except ValueError:
        raise BankError("اولویت باید عدد باشد.") from None
    return {
        "id": pid, "bank_name": fields["bank_name"].strip(), "detect_re": fields["detect_re"].strip(), "amount_re": fields["amount_re"].strip(),
        "amount_unit": unit, "direction": direction, "balance_re": _clean(fields.get("balance_re")), "account_re": _clean(fields.get("account_re")),
        "reference_re": _clean(fields.get("reference_re")), "priority": priority, "notes": _clean(fields.get("notes")),
    }


async def save_pattern(session: AsyncSession, actor, fields: dict) -> BankSmsPattern:
    clean = validate_pattern_fields(fields)
    row = await session.get(BankSmsPattern, clean["id"])
    if row is None:
        row = BankSmsPattern(**clean, enabled=False, updated_by=actor.email)
        session.add(row)
        action = "bank.pattern.create"
    else:
        for k, v in clean.items():
            setattr(row, k, v)
        row.updated_by = actor.email
        action = "bank.pattern.update"
    session.add(audit_row(action=action, entity_type="bank_sms_pattern", entity_id=clean["id"], actor_role=actor.role, actor_operator_id=actor.id, after=clean))
    await session.commit()
    return row


async def set_pattern_enabled(session: AsyncSession, actor, pattern_id: str, enabled: bool) -> BankSmsPattern:
    row = await session.get(BankSmsPattern, pattern_id)
    if row is None:
        raise BankError("چنین الگویی نیست.")
    row.enabled, row.updated_by = enabled, actor.email
    session.add(audit_row(action="bank.pattern.enabled", entity_type="bank_sms_pattern", entity_id=pattern_id, actor_role=actor.role, actor_operator_id=actor.id, after={"enabled": enabled}))
    await session.commit()
    return row


async def delete_pattern(session: AsyncSession, actor, pattern_id: str) -> None:
    row = await session.get(BankSmsPattern, pattern_id)
    if row is None:
        raise BankError("چنین الگویی نیست.")
    session.add(audit_row(action="bank.pattern.delete", entity_type="bank_sms_pattern", entity_id=pattern_id, actor_role=actor.role, actor_operator_id=actor.id))
    await session.delete(row)
    await session.commit()


def to_db_pattern(row: BankSmsPattern, force_enabled: bool = False) -> DbPattern:
    return DbPattern(
        id=row.id, bank_name=row.bank_name, enabled=row.enabled or force_enabled, priority=row.priority, detect_re=row.detect_re,
        amount_re=row.amount_re, amount_unit=row.amount_unit, direction=row.direction, balance_re=row.balance_re, account_re=row.account_re,
        reference_re=row.reference_re,
    )


def sandbox(row: BankSmsPattern, sample: str) -> ParseResult:
    """Run ONLY this pattern (as if enabled) on the sample, so the operator
    sees what their pattern does before it is switched on."""
    return parse(sample, patterns=[to_db_pattern(row, force_enabled=True)])
