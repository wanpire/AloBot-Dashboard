from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import FixedWindowLimiter
from app.services import auth
from app.web.deps import SESSION_COOKIE, current_operator, get_db
from app.web.guards import client_ip
from app.web.templating import render

router = APIRouter()
log = get_logger(__name__)

# 20 login attempts a minute per address, checked BEFORE any hashing so a
# guesser cannot make us pay scrypt for free.
login_limiter = FixedWindowLimiter(limit=20, window_seconds=60)

ERR_BAD = "ایمیل یا رمز عبور اشتباه است."
ERR_LOCKED = "این حساب به‌خاطر تلاش‌های ناموفق تا {until} قفل است."
ERR_TOTP = "کد دومرحله‌ای را وارد کنید."
ERR_TOTP_BAD = "کد دومرحله‌ای اشتباه است."


@router.get("/login")
async def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    totp: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    if ip is not None and not login_limiter.hit(ip):
        log.warning("login.rate_limited", ip=ip)
        return render(request, "login.html", status_code=429, error="تعداد تلاش‌ها زیاد است؛ کمی بعد دوباره امتحان کنید.")
    result = await auth.authenticate(db, email, password, totp=totp or None)
    if result.ok:
        token = await auth.open_session(
            db, result.operator, ip=ip, user_agent=request.headers.get("user-agent")
        )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=auth.SESSION_ABSOLUTE_DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=not get_settings().is_relaxed_env,
            path="/",
        )
        log.info("login.ok", operator=result.operator.email, role=result.operator.role)
        return response
    log.warning("login.refused", reason=result.reason, email=email.strip().lower()[:254])
    if result.reason == "locked":
        from app.web.format import jalali_datetime

        error = ERR_LOCKED.format(until=jalali_datetime(result.locked_until))
    elif result.reason == "totp_required":
        return render(request, "login.html", email=email, needs_totp=True, notice=ERR_TOTP)
    elif result.reason == "bad_totp":
        return render(request, "login.html", email=email, needs_totp=True, error=ERR_TOTP_BAD)
    else:
        error = ERR_BAD
    return render(request, "login.html", email=email, error=error)


@router.post("/logout")
async def logout(request: Request, operator=Depends(current_operator), db: AsyncSession = Depends(get_db)):
    await auth.revoke_session(db, request.state.session_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/password")
async def password_page(request: Request, operator=Depends(current_operator)):
    return render(request, "password.html")


@router.post("/password")
async def password_submit(
    request: Request,
    current: str = Form(""),
    new: str = Form(""),
    confirm: str = Form(""),
    operator=Depends(current_operator),
    db: AsyncSession = Depends(get_db),
):
    if len(new) < 12:
        return render(request, "password.html", error="رمز جدید باید دست‌کم ۱۲ نویسه باشد.")
    if new != confirm:
        return render(request, "password.html", error="تکرار رمز جدید یکسان نیست.")
    ok = await auth.change_password(db, operator, current=current, new=new, keep_token=request.state.session_token)
    if not ok:
        return render(request, "password.html", error="رمز فعلی اشتباه است.")
    log.info("password.changed", operator=operator.email)
    return RedirectResponse("/?notice=password_changed", status_code=303)
