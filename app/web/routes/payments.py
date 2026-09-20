from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.labels import PURPOSE_LABELS, label
from app.core.config import get_settings
from app.models import PaymentClaim
from app.services import continuity, finance, receipts, review
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()
DECIDER = require_role("ADMIN", "REVIEWER")
ADMIN = require_role("ADMIN")

REASON_LABELS = {
    "UNMAPPED_CARD": "کارت مقصد به هیچ حسابی وصل نیست", "ACCOUNT_NOT_ACTIVE": "حساب مقصد فعال نیست",
    "AMBIGUOUS_TRANSACTIONS": "چند واریز هم‌مبلغ در بازه", "AMBIGUOUS_CLAIMS": "چند پرداخت برای یک واریز",
    "NO_TRANSACTION_AFTER_10M": "واریزی پیدا نشد", "AWAITING_BANK_SMS": "در انتظار پیامک بانک",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _int(value: str | None, default: int) -> int:
    try:
        return max(1, int(value)) if value else default
    except ValueError:
        return default


async def _page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    qp = request.query_params
    tab = qp.get("tab") or "review"
    if tab not in review.TAB_LABELS:
        tab = "review"
    purpose = qp.get("purpose") or None
    q = qp.get("q") or None
    data = await review.list_claims(db, tab=tab, q=q, purpose=purpose, page=_int(qp.get("page"), 1))
    return render(
        request, "payments.html", page_id="payments", status_code=status_code, tab=tab, tabs=review.TAB_LABELS, counts=await review.tab_counts(db),
        q=q or "", purpose=purpose, purposes=PURPOSE_LABELS, label=label, reasons=REASON_LABELS, templates=review.REVIEW_TEMPLATES,
        continuity_state=await continuity.state(db, _now()),
        # Without AloBot's token a receipt cannot be fetched at all, so the
        # page says so rather than drawing an image that will not load.
        receipts_enabled=bool(get_settings().alobot_bot_token),
        **data, **ctx,
    )


@router.get("/payments")
async def payments(request: Request, operator=Depends(page("payments")), db: AsyncSession = Depends(get_db)):
    return await _page(request, db, notice=request.query_params.get("notice"))


async def _act(request: Request, db: AsyncSession, fn, *args):
    try:
        await fn(db, *args, now=_now())
    except review.ReviewError as exc:
        return await _page(request, db, 400, error=str(exc))
    tab = request.query_params.get("tab") or "review"
    return RedirectResponse(f"/payments?tab={tab}&notice=saved", status_code=303)


@router.get("/payments/{claim_id}/receipt")
async def receipt(claim_id: int, operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    """The receipt photo itself. The route takes a payment, never a file id:
    otherwise the panel would be a way to read any file AloBot's bot has ever
    been sent. Nothing is stored; see app/services/receipts.py."""
    claim = await db.get(PaymentClaim, claim_id)
    if claim is None or not claim.receipt_file_id:
        return PlainTextResponse("این پرداخت رسیدی ندارد.", status_code=404)
    try:
        found = await receipts.fetch(claim.receipt_file_id)
    except PermissionError as exc:
        return PlainTextResponse(str(exc), status_code=503)
    except receipts.ReceiptError as exc:
        return PlainTextResponse(str(exc), status_code=502)
    return Response(
        found.content,
        media_type=found.content_type,
        # A customer's bank receipt: the browser may hold it, nothing else may.
        headers={"cache-control": "private, max-age=300", "content-disposition": "inline"},
    )


@router.post("/payments/{claim_id}/approve")
async def approve(request: Request, claim_id: int, transaction_id: int = Form(...), operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.approve_with_transaction, operator, claim_id, transaction_id)


@router.post("/payments/{claim_id}/verify-manual")
async def verify_manual(request: Request, claim_id: int, note: str = Form(""), operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.verify_manually, operator, claim_id, note)


@router.post("/payments/{claim_id}/reject")
async def reject(request: Request, claim_id: int, note: str = Form(""), operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.reject, operator, claim_id, note)


@router.post("/payments/{claim_id}/fake")
async def fake(request: Request, claim_id: int, note: str = Form(""), operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.mark_fake, operator, claim_id, note)


@router.post("/payments/{claim_id}/park")
async def park(request: Request, claim_id: int, operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.park, operator, claim_id)


@router.post("/payments/{claim_id}/unpark")
async def unpark(request: Request, claim_id: int, operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.unpark, operator, claim_id)


@router.post("/payments/{claim_id}/reopen")
async def reopen(request: Request, claim_id: int, operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.reopen, operator, claim_id)


@router.post("/payments/{claim_id}/message")
async def message(request: Request, claim_id: int, template: str = Form(""), operator=Depends(DECIDER), db: AsyncSession = Depends(get_db)):
    return await _act(request, db, review.message, operator, claim_id, template)


@router.post("/payments/continuity")
async def continuity_on(request: Request, minutes: str = Form("60"), reason: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await continuity.activate(db, operator, minutes=int(minutes) if minutes.isdigit() else 0, reason=reason, now=_now())
    except continuity.ContinuityError as exc:
        return await _page(request, db, 400, error=str(exc))
    return RedirectResponse("/payments?tab=continuity&notice=continuity_on", status_code=303)


@router.post("/payments/continuity/off")
async def continuity_off(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    await continuity.deactivate(db, operator, now=_now())
    return RedirectResponse("/payments?tab=continuity&notice=continuity_off", status_code=303)


@router.get("/finance")
async def finance_page(request: Request, operator=Depends(page("finance")), db: AsyncSession = Depends(get_db)):
    days = min(_int(request.query_params.get("days"), 30), 3660)
    since = _now() - dt.timedelta(days=days)
    return render(request, "finance.html", page_id="finance", days=days, since=since, **await finance.summary(db, since))
