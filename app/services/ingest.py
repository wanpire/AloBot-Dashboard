"""Behind the SMS door: device tokens, dedupe, parsing, persistence, and
resolving which account the bank was talking about.

Everything a request does happens in one transaction: the event row, its
transaction row, a PENDING account for an unknown identifier, and the device
timestamps. A redelivery (the relay app retries on any non-2xx) lands on the
UNIQUE dedupe key and is acknowledged with the original event id.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import current_request_id, get_logger
from app.models import (
    BankSmsPattern, Device, DeviceCredential, FinancialAccount, FinancialAccountIdentifier, SmsEvent, TransactionCandidate,
)
from app.sms import DbPattern, normalize, parse, redact_otp

log = get_logger(__name__)


# ── Device credentials ─────────────────────────────────────────────────────


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_credential(session: AsyncSession, device: Device) -> tuple[str, DeviceCredential]:
    """A fresh 64-hex token, returned ONCE; only its hash and 4-char prefix
    are stored. Any ACTIVE credential the device had is revoked first."""
    await revoke_credentials(session, device.id, flush_only=True)
    token = secrets.token_hex(32)
    credential = DeviceCredential(device_id=device.id, token_hash=_hash(token), token_prefix=token[:4], status="ACTIVE")
    session.add(credential)
    await session.flush()
    return token, credential


async def revoke_credentials(session: AsyncSession, device_id: int, flush_only: bool = False) -> int:
    result = await session.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device_id, DeviceCredential.status == "ACTIVE")
        .values(status="REVOKED", revoked_at=dt.datetime.now(dt.timezone.utc))
    )
    if not flush_only:
        await session.commit()
    return result.rowcount


async def authenticate(session: AsyncSession, device_code: str, api_key: str, now: dt.datetime) -> Device | None:
    """The device named by `device_code` if `api_key` is its ACTIVE token.
    Every failure is the same None; the device's failure timestamp is the
    only thing that distinguishes "known device, wrong key"."""
    device = (await session.execute(select(Device).where(Device.code == device_code))).scalar_one_or_none()
    if device is None:
        hmac.compare_digest(_hash(api_key), _hash("x"))  # same cost as a real check
        return None
    credential = (
        await session.execute(
            select(DeviceCredential).where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.status == "ACTIVE",
                DeviceCredential.token_prefix == api_key[:4],
            )
        )
    ).scalar_one_or_none()
    ok = credential is not None and hmac.compare_digest(credential.token_hash, _hash(api_key)) and device.is_active
    if not ok:
        device.last_auth_failure_at = now
        await session.commit()
        return None
    device.last_seen_at = now
    credential.last_used_at = now
    return device


# ── Timestamps ─────────────────────────────────────────────────────────────


def parse_timestamp(raw: str | int | float | None) -> dt.datetime | None:
    """Android sends epoch milliseconds; iOS Shortcuts sends ISO or epoch
    seconds. None when nothing sensible can be read."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text.isdigit():
        value = int(text)
        if len(text) >= 13:
            value //= 1000
        if 1_000_000_000 <= value <= 4_102_444_800:
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


# ── Ingest ─────────────────────────────────────────────────────────────────


@dataclass
class IngestOutcome:
    event_id: int
    duplicate: bool
    classification: str
    actionable: bool
    transaction_id: int | None = None


def dedupe_key(device_id: int, sender: str, sms_timestamp: dt.datetime, body: str) -> str:
    body_hash = hashlib.sha256(normalize(body).encode()).hexdigest()
    return hashlib.sha256(f"{device_id}|{sender}|{int(sms_timestamp.timestamp() * 1000)}|{body_hash}".encode()).hexdigest()


async def load_patterns(session: AsyncSession) -> list[DbPattern]:
    rows = (await session.execute(select(BankSmsPattern).where(BankSmsPattern.enabled.is_(True)))).scalars().all()
    return [
        DbPattern(
            id=r.id, bank_name=r.bank_name, enabled=True, priority=r.priority, detect_re=r.detect_re, amount_re=r.amount_re,
            amount_unit=r.amount_unit, direction=r.direction, balance_re=r.balance_re, account_re=r.account_re, reference_re=r.reference_re,
        )
        for r in rows
    ]


async def resolve_account(session: AsyncSession, hint: str | None, kind_guess: str, bank_name: str | None) -> tuple[FinancialAccount | None, bool]:
    """(account, created). An identifier nobody registered creates a PENDING
    account so the row is on someone's screen instead of nowhere."""
    if not hint:
        return None, False
    rows = (
        await session.execute(
            select(FinancialAccountIdentifier, FinancialAccount)
            .join(FinancialAccount, FinancialAccount.id == FinancialAccountIdentifier.account_id)
            .where(FinancialAccountIdentifier.value.endswith(hint))
        )
    ).all()
    candidates = [acct for ident, acct in rows if ident.value == hint or ident.kind in ("CARD", "ACCOUNT", "IBAN")]
    if candidates:
        candidates.sort(key=lambda a: (a.status == "DECLINED", a.status != "ACTIVE", a.id))
        return candidates[0], False
    account = FinancialAccount(
        bank_name=bank_name or "نامشخص", display_name=f"ناشناخته ****{hint}", status="PENDING",
        notes="به‌صورت خودکار از یک پیامک بانکی با شناسهٔ ثبت‌نشده ساخته شد.",
    )
    session.add(account)
    await session.flush()
    session.add(FinancialAccountIdentifier(account_id=account.id, kind="CARD_LAST4" if kind_guess == "card" else "ACCOUNT_LAST4", value=hint, label="خودکار"))
    return account, True


async def infer_owner_by_balance_chain(
    session: AsyncSession, *, exclude_account_id: int, direction: str, amount: int, balance: int, before: dt.datetime
) -> int | None:
    """The one ACTIVE account whose last texted balance, plus or minus this
    movement, lands exactly on this text's balance. Zero or several → None."""
    expected_previous = balance - amount if direction == "CREDIT" else balance + amount
    accounts = (await session.execute(select(FinancialAccount).where(FinancialAccount.status == "ACTIVE", FinancialAccount.id != exclude_account_id))).scalars().all()
    matches: list[int] = []
    for account in accounts:
        last = (
            await session.execute(
                select(TransactionCandidate.balance_irr)
                .where(TransactionCandidate.account_id == account.id, TransactionCandidate.balance_irr.is_not(None), TransactionCandidate.bank_timestamp < before)
                .order_by(TransactionCandidate.bank_timestamp.desc(), TransactionCandidate.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last == expected_previous:
            matches.append(account.id)
    return matches[0] if len(matches) == 1 else None


async def persist_candidate(session: AsyncSession, event: SmsEvent, result, sms_timestamp: dt.datetime) -> tuple[TransactionCandidate, bool]:
    """The transaction row for a parsed event, its account resolved (a
    PENDING one created if nobody registered the identifier), and the
    balance-chain guess when it was created. Returns (row, actionable)."""
    account, created = await resolve_account(session, result.account_hint, str(result.evidence.get("account_kind", "account")), result.bank_name)
    candidate = TransactionCandidate(
        sms_event_id=event.id, account_id=account.id if account else None, direction=result.direction,
        amount_irr=result.amount_irr, balance_irr=result.balance_irr, reference=result.reference,
        account_hint=result.account_hint, bank_name=result.bank_name or (account.bank_name if account and not created else None),
        bank_timestamp=sms_timestamp, confidence=result.confidence, parser_id=result.parser_id or "unknown",
        parser_version=result.parser_version or "0", evidence=result.evidence or None,
    )
    if created and result.amount_irr is not None and result.balance_irr is not None and result.direction in ("CREDIT", "DEBIT"):
        inferred = await infer_owner_by_balance_chain(
            session, exclude_account_id=account.id, direction=result.direction, amount=result.amount_irr,
            balance=result.balance_irr, before=sms_timestamp,
        )
        if inferred is not None:
            candidate.inferred_account_id = inferred
            account.inferred_from_account_id = inferred
    session.add(candidate)
    await session.flush()
    actionable = (
        result.classification == "BANK_TRANSACTION" and result.direction == "CREDIT" and result.amount_irr is not None
        and account is not None and account.status == "ACTIVE"
    )
    return candidate, actionable


async def ingest(
    session: AsyncSession,
    device: Device,
    *,
    sender: str,
    message: str,
    timestamp_raw: Any,
    checksum: str | None,
    now: dt.datetime,
) -> IngestOutcome:
    warnings: list[str] = []
    sms_timestamp = parse_timestamp(timestamp_raw)
    if sms_timestamp is None:
        sms_timestamp, _ = now, warnings.append("timestamp_unparsed_used_received_at")
    key = dedupe_key(device.id, sender, sms_timestamp, message)
    existing = (await session.execute(select(SmsEvent.id, SmsEvent.classification).where(SmsEvent.dedupe_key == key))).first()
    if existing:
        return IngestOutcome(event_id=existing.id, duplicate=True, classification=existing.classification, actionable=False)

    result = parse(message, sender=sender, patterns=await load_patterns(session))
    stored_body = redact_otp(message) if result.classification == "OTP" else message
    event = SmsEvent(
        device_id=device.id, sender=sender[:64], body=stored_body, body_hash=hashlib.sha256(normalize(message).encode()).hexdigest(),
        dedupe_key=key, checksum=(checksum or None), sms_timestamp=sms_timestamp, classification=result.classification,
        parser_id=result.parser_id, parser_version=result.parser_version, warnings=(warnings + result.warnings) or None,
        request_id=current_request_id(),
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(select(SmsEvent.id, SmsEvent.classification).where(SmsEvent.dedupe_key == key))).first()
        return IngestOutcome(event_id=existing.id, duplicate=True, classification=existing.classification, actionable=False)

    transaction_id = None
    actionable = False
    if result.matched:
        candidate, actionable = await persist_candidate(session, event, result, sms_timestamp)
        transaction_id = candidate.id
        device.last_success_at = now
    await session.commit()
    log.info("ingest.stored", event_id=event.id, classification=result.classification, actionable=actionable, device=device.code)
    if result.classification == "UNKNOWN":
        log.warning("ingest.unparsed", event_id=event.id, sender=sender[:64], device=device.code)
    return IngestOutcome(event_id=event.id, duplicate=False, classification=result.classification, actionable=actionable, transaction_id=transaction_id)
