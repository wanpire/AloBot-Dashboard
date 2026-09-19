"""The screens that run the SMS pipeline: devices, accounts and cards,
banks, transactions and coverage. Every write is ADMIN-only and every
refusal is the service's own sentence rendered with a 400."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingest import ACCOUNT_STATUSES, DISPOSITIONS, IDENTIFIER_KINDS
from app.services import accounts as accounts_service
from app.services import banks as banks_service
from app.services import devices as devices_service
from app.services import transactions as tx_service
from app.services.accounts import STATUS_HINTS, STATUS_LABELS, bank_for_card, luhn_ok, normalize_card
from app.sms import parse
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()
ADMIN = require_role("ADMIN")

IDENTIFIER_LABELS = {"CARD": "شمارهٔ کامل کارت", "CARD_LAST4": "۴ رقم آخر کارت", "ACCOUNT": "شمارهٔ کامل حساب", "ACCOUNT_LAST4": "۴ رقم آخر حساب", "IBAN": "شبا", "OTHER": "دیگر"}
DISPOSITION_LABELS = {"ACTIONABLE": "قابل تطبیق", "DECLINED_INCOME": "درآمد نیست", "IGNORED": "نادیده"}


def _int(value: str | None, default: int) -> int:
    try:
        return max(1, int(value)) if value else default
    except ValueError:
        return default


# ── Devices ────────────────────────────────────────────────────────────────


async def _devices_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    return render(request, "devices.html", page_id="devices", status_code=status_code, devices=await devices_service.list_devices(db), **ctx)


@router.get("/devices")
async def devices(request: Request, operator=Depends(page("devices")), db: AsyncSession = Depends(get_db)):
    return await _devices_page(request, db, notice=request.query_params.get("notice"))


@router.post("/devices")
async def devices_create(request: Request, code: str = Form(""), display_name: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        device, token = await devices_service.create_device(db, operator, code=code, display_name=display_name)
    except devices_service.DeviceError as exc:
        return await _devices_page(request, db, 400, error=str(exc))
    return await _devices_page(request, db, new_token=token, token_device=device)


@router.post("/devices/{device_id}/rotate")
async def devices_rotate(request: Request, device_id: int, confirm: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    if confirm != "1":
        return await _devices_page(request, db, 400, error="چرخاندن توکن باید تایید شود: توکن فعلی گوشی همان لحظه از کار می‌افتد و توکن تازه باید روی گوشی وارد شود.")
    try:
        device, token = await devices_service.rotate(db, operator, device_id)
    except devices_service.DeviceError as exc:
        return await _devices_page(request, db, 400, error=str(exc))
    return await _devices_page(request, db, new_token=token, token_device=device)


@router.post("/devices/{device_id}/revoke")
async def devices_revoke(request: Request, device_id: int, confirm: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    if confirm != "1":
        return await _devices_page(request, db, 400, error="ابطال توکن باید تایید شود: از این لحظه هیچ پیامک بانکی از این گوشی نمی‌رسد و پرداخت‌ها تایید خودکار نمی‌شوند.")
    try:
        await devices_service.revoke(db, operator, device_id)
    except devices_service.DeviceError as exc:
        return await _devices_page(request, db, 400, error=str(exc))
    return RedirectResponse("/devices?notice=revoked", status_code=303)


@router.post("/devices/{device_id}/active")
async def devices_active(request: Request, device_id: int, active: str = Form("1"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await devices_service.set_active(db, operator, device_id, active == "1")
    except devices_service.DeviceError as exc:
        return await _devices_page(request, db, 400, error=str(exc))
    return RedirectResponse("/devices?notice=saved", status_code=303)


@router.post("/devices/{device_id}/rename")
async def devices_rename(request: Request, device_id: int, display_name: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await devices_service.rename(db, operator, device_id, display_name)
    except devices_service.DeviceError as exc:
        return await _devices_page(request, db, 400, error=str(exc))
    return RedirectResponse("/devices?notice=saved", status_code=303)


# ── Accounts and cards ─────────────────────────────────────────────────────


async def _accounts_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    return render(
        request, "accounts.html", page_id="accounts", status_code=status_code, groups=await accounts_service.list_accounts(db),
        statuses=ACCOUNT_STATUSES, status_labels=STATUS_LABELS, status_hints=STATUS_HINTS, kinds=IDENTIFIER_KINDS, kind_labels=IDENTIFIER_LABELS, **ctx,
    )


@router.get("/accounts")
async def accounts(request: Request, operator=Depends(page("accounts")), db: AsyncSession = Depends(get_db)):
    return await _accounts_page(request, db, notice=request.query_params.get("notice"))


@router.post("/accounts")
async def accounts_create(
    request: Request, bank_name: str = Form(""), display_name: str = Form(""), owner_label: str = Form(""),
    identifier_kind: str = Form("ACCOUNT_LAST4"), identifier_value: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db),
):
    try:
        await accounts_service.create_account(db, operator, bank_name=bank_name, display_name=display_name, owner_label=owner_label, identifier_kind=identifier_kind, identifier_value=identifier_value)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=created", status_code=303)


@router.post("/accounts/{account_id}/status")
async def accounts_status(request: Request, account_id: int, status: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await accounts_service.set_status(db, operator, account_id, status)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=saved", status_code=303)


@router.post("/accounts/{account_id}/identifiers")
async def accounts_identifier_add(request: Request, account_id: int, kind: str = Form(""), value: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await accounts_service.add_identifier(db, operator, account_id, kind, value)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=saved", status_code=303)


@router.post("/accounts/{account_id}/identifiers/{identifier_id}/delete")
async def accounts_identifier_delete(request: Request, account_id: int, identifier_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await accounts_service.delete_identifier(db, operator, account_id, identifier_id)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=saved", status_code=303)


@router.post("/accounts/{account_id}/cards")
async def accounts_card_add(request: Request, account_id: int, card_number: str = Form(""), holder_name: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await accounts_service.add_card(db, operator, account_id, card_number, holder_name)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=saved", status_code=303)


@router.post("/cards/{card_id}/status")
async def cards_status(request: Request, card_id: int, status: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await accounts_service.set_card_status(db, operator, card_id, status)
    except accounts_service.AccountError as exc:
        return await _accounts_page(request, db, 400, error=str(exc))
    return RedirectResponse("/accounts?notice=saved", status_code=303)


# ── Banks ──────────────────────────────────────────────────────────────────


async def _banks_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    return render(
        request, "banks.html", page_id="banks", status_code=status_code, prefixes=await banks_service.list_prefixes(db),
        patterns=await banks_service.list_patterns(db), **ctx,
    )


@router.get("/banks")
async def banks(request: Request, operator=Depends(page("banks")), db: AsyncSession = Depends(get_db)):
    return await _banks_page(request, db, notice=request.query_params.get("notice"))


@router.post("/banks/prefixes")
async def banks_prefix(request: Request, prefix: str = Form(""), bank_name: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await banks_service.upsert_prefix(db, operator, prefix, bank_name)
    except banks_service.BankError as exc:
        return await _banks_page(request, db, 400, error=str(exc))
    return RedirectResponse("/banks?notice=saved", status_code=303)


@router.post("/banks/prefixes/{prefix}/delete")
async def banks_prefix_delete(request: Request, prefix: str, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await banks_service.delete_prefix(db, operator, prefix)
    except banks_service.BankError as exc:
        return await _banks_page(request, db, 400, error=str(exc))
    return RedirectResponse("/banks?notice=saved", status_code=303)


@router.post("/banks/patterns")
async def banks_pattern_save(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    try:
        await banks_service.save_pattern(db, operator, {k: str(v) for k, v in form.items()})
    except banks_service.BankError as exc:
        return await _banks_page(request, db, 400, error=str(exc), draft={k: str(v) for k, v in form.items()})
    return RedirectResponse("/banks?notice=saved", status_code=303)


@router.post("/banks/patterns/{pattern_id}/enabled")
async def banks_pattern_enabled(request: Request, pattern_id: str, enabled: str = Form("0"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await banks_service.set_pattern_enabled(db, operator, pattern_id, enabled == "1")
    except banks_service.BankError as exc:
        return await _banks_page(request, db, 400, error=str(exc))
    return RedirectResponse("/banks?notice=saved", status_code=303)


@router.post("/banks/patterns/{pattern_id}/delete")
async def banks_pattern_delete(request: Request, pattern_id: str, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await banks_service.delete_pattern(db, operator, pattern_id)
    except banks_service.BankError as exc:
        return await _banks_page(request, db, 400, error=str(exc))
    return RedirectResponse("/banks?notice=saved", status_code=303)


@router.post("/banks/patterns/{pattern_id}/test")
async def banks_pattern_test(request: Request, pattern_id: str, sample: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    from app.models import BankSmsPattern

    row = await db.get(BankSmsPattern, pattern_id)
    if row is None:
        return await _banks_page(request, db, 404, error="چنین الگویی نیست.")
    result = banks_service.sandbox(row, sample)
    return await _banks_page(request, db, sandbox={"pattern_id": pattern_id, "sample": sample, "result": result})


@router.post("/banks/test-card")
async def banks_test_card(request: Request, card_number: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    number = normalize_card(card_number)
    outcome = {
        "input": card_number,
        "number": number,
        "luhn": luhn_ok(number) if number else False,
        "bank": await bank_for_card(db, number) if number else None,
    }
    return await _banks_page(request, db, card_test=outcome)


@router.post("/banks/test-sms")
async def banks_test_sms(request: Request, sample: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    from app.services.ingest import load_patterns

    result = parse(sample, patterns=await load_patterns(db))
    return await _banks_page(request, db, sms_test={"sample": sample, "result": result})


# ── Transactions and coverage ──────────────────────────────────────────────


async def _transactions_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    qp = request.query_params
    view = qp.get("view") or "list"
    data: dict = {"view": view, "disposition_labels": DISPOSITION_LABELS, "dispositions": DISPOSITIONS}
    if view == "unparsed":
        data["events"] = await tx_service.unparsed_events(db)
    elif view == "coverage":
        days = _int(qp.get("days"), 7)
        data["days"] = days
        data["coverage"] = await tx_service.coverage(db, dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days))
    else:
        filters = {"direction": qp.get("direction") or None, "disposition": qp.get("disposition") or None, "q": qp.get("q") or None}
        account_id = qp.get("account_id")
        filters["account_id"] = int(account_id) if account_id and account_id.isdigit() else None
        data["filters"] = filters
        data.update(await tx_service.list_transactions(db, page=_int(qp.get("page"), 1), **filters))
    return render(request, "transactions.html", page_id="transactions", status_code=status_code, **data, **ctx)


@router.get("/transactions")
async def transactions(request: Request, operator=Depends(page("transactions")), db: AsyncSession = Depends(get_db)):
    return await _transactions_page(request, db, notice=request.query_params.get("notice"))


@router.post("/transactions/{transaction_id}/account")
async def transactions_account(request: Request, transaction_id: int, account_id: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await tx_service.assign_account(db, operator, transaction_id, int(account_id) if account_id.isdigit() else None)
    except tx_service.TransactionError as exc:
        return await _transactions_page(request, db, 400, error=str(exc))
    return RedirectResponse("/transactions?notice=saved", status_code=303)


@router.post("/transactions/{transaction_id}/disposition")
async def transactions_disposition(request: Request, transaction_id: int, disposition: str = Form(""), note: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await tx_service.set_disposition(db, operator, transaction_id, disposition, note)
    except tx_service.TransactionError as exc:
        return await _transactions_page(request, db, 400, error=str(exc))
    return RedirectResponse("/transactions?notice=saved", status_code=303)


@router.post("/transactions/reparse")
async def transactions_reparse(request: Request, mode: str = Form("dry-run"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    outcome = await tx_service.reparse_unparsed(db, operator, apply=(mode == "apply"))
    request.scope["query_string"] = b"view=unparsed"
    return await _transactions_page(request, db, reparse={"mode": mode, **outcome})
