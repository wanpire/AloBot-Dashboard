"""Financial accounts, their identifiers, and the cards customers pay to."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BankCardPrefix, FinancialAccount, FinancialAccountIdentifier, PaymentCard
from app.models.ingest import ACCOUNT_STATUSES, IDENTIFIER_KINDS
from app.services.audit import audit_row


class AccountError(ValueError):
    pass


STATUS_LABELS = {"PENDING": "در انتظار تایید", "ACTIVE": "فعال", "MUTED": "بی‌صدا", "DECLINED": "رد شده"}
STATUS_HINTS = {
    "ACTIVE": "واریزی‌های این حساب در تطبیق پرداخت‌ها شرکت می‌کنند.",
    "MUTED": "پیامک‌هایش ذخیره می‌شود ولی هیچ پرداختی با آن تطبیق داده نمی‌شود.",
    "DECLINED": "این شناسه مال فروشگاه نیست؛ تراکنش‌هایش کنار گذاشته می‌شوند.",
    "PENDING": "از یک پیامک با شناسهٔ ناشناخته ساخته شده و منتظر تصمیم شماست.",
}


def normalize_card(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    return digits if len(digits) == 16 else None


def luhn_ok(number: str) -> bool:
    if not number.isdigit() or len(number) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


async def bank_for_card(session: AsyncSession, number: str) -> str | None:
    rows = (await session.execute(select(BankCardPrefix))).scalars().all()
    best = None
    for row in rows:
        if number.startswith(row.prefix) and (best is None or len(row.prefix) > len(best.prefix)):
            best = row
    return best.bank_name if best else None


async def list_accounts(session: AsyncSession) -> list[dict]:
    accounts = (await session.execute(select(FinancialAccount).order_by(FinancialAccount.status != "PENDING", FinancialAccount.id))).scalars().all()
    idents = (await session.execute(select(FinancialAccountIdentifier).order_by(FinancialAccountIdentifier.id))).scalars().all()
    cards = (await session.execute(select(PaymentCard).order_by(PaymentCard.id))).scalars().all()
    return [
        {
            "account": a,
            "identifiers": [i for i in idents if i.account_id == a.id],
            "cards": [c for c in cards if c.account_id == a.id],
            "inferred_from": next((x for x in accounts if x.id == a.inferred_from_account_id), None),
        }
        for a in accounts
    ]


async def create_account(session: AsyncSession, actor, *, bank_name: str, display_name: str, owner_label: str, identifier_kind: str, identifier_value: str) -> FinancialAccount:
    if not bank_name.strip() or not display_name.strip():
        raise AccountError("نام بانک و نام نمایشی لازم است.")
    account = FinancialAccount(bank_name=bank_name.strip(), display_name=display_name.strip(), owner_label=owner_label.strip() or None, status="ACTIVE")
    session.add(account)
    await session.flush()
    if identifier_value.strip():
        await add_identifier(session, actor, account.id, identifier_kind, identifier_value, commit=False)
    session.add(audit_row(action="account.create", entity_type="financial_account", entity_id=str(account.id), actor_role=actor.role, actor_operator_id=actor.id, after={"bank": account.bank_name, "name": account.display_name}))
    await session.commit()
    return account


async def set_status(session: AsyncSession, actor, account_id: int, status: str) -> FinancialAccount:
    if status not in ACCOUNT_STATUSES:
        raise AccountError("وضعیت نامعتبر است.")
    account = await session.get(FinancialAccount, account_id)
    if account is None:
        raise AccountError("چنین حسابی نیست.")
    before = account.status
    account.status = status
    session.add(audit_row(action="account.status", entity_type="financial_account", entity_id=str(account.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": status}))
    await session.commit()
    return account


async def add_identifier(session: AsyncSession, actor, account_id: int, kind: str, value: str, commit: bool = True) -> FinancialAccountIdentifier:
    if kind not in IDENTIFIER_KINDS:
        raise AccountError("نوع شناسه نامعتبر است.")
    value = re.sub(r"\s", "", value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    if kind.endswith("LAST4") and not (len(value) == 4 and value.isdigit()):
        raise AccountError("چهار رقم آخر باید دقیقاً چهار رقم باشد.")
    if not value:
        raise AccountError("مقدار شناسه خالی است.")
    ident = FinancialAccountIdentifier(account_id=account_id, kind=kind, value=value)
    session.add(ident)
    session.add(audit_row(action="account.identifier.add", entity_type="financial_account", entity_id=str(account_id), actor_role=actor.role, actor_operator_id=actor.id, after={"kind": kind, "value": value}))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise AccountError("این شناسه قبلاً به حساب دیگری وصل شده است.") from None
    if commit:
        await session.commit()
    return ident


async def delete_identifier(session: AsyncSession, actor, account_id: int, identifier_id: int) -> None:
    ident = await session.get(FinancialAccountIdentifier, identifier_id)
    if ident is None or ident.account_id != account_id:
        raise AccountError("چنین شناسه‌ای نیست.")
    session.add(audit_row(action="account.identifier.delete", entity_type="financial_account", entity_id=str(account_id), actor_role=actor.role, actor_operator_id=actor.id, before={"kind": ident.kind, "value": ident.value}))
    await session.delete(ident)
    await session.commit()


async def add_card(session: AsyncSession, actor, account_id: int, card_number: str, holder_name: str) -> PaymentCard:
    number = normalize_card(card_number)
    if number is None:
        raise AccountError("شمارهٔ کارت باید ۱۶ رقم باشد.")
    if not luhn_ok(number):
        raise AccountError("شمارهٔ کارت معتبر نیست (رقم کنترلی Luhn نمی‌خواند).")
    if not holder_name.strip():
        raise AccountError("نام صاحب کارت لازم است؛ همان نامی است که روی فاکتور چاپ می‌شود.")
    card = PaymentCard(account_id=account_id, card_number=number, holder_name=holder_name.strip())
    session.add(card)
    session.add(audit_row(action="card.add", entity_type="financial_account", entity_id=str(account_id), actor_role=actor.role, actor_operator_id=actor.id, after={"card": number, "holder": holder_name.strip()}))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AccountError("این کارت قبلاً ثبت شده است.") from None
    return card


async def set_card_status(session: AsyncSession, actor, card_id: int, status: str) -> PaymentCard:
    if status not in ("ACTIVE", "DISABLED"):
        raise AccountError("وضعیت کارت نامعتبر است.")
    card = await session.get(PaymentCard, card_id)
    if card is None:
        raise AccountError("چنین کارتی نیست.")
    before = card.status
    card.status = status
    session.add(audit_row(action="card.status", entity_type="payment_card", entity_id=card.card_number, actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": status}))
    await session.commit()
    return card
