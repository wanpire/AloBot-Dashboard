"""Phase 1 tasks 3 and 5 over HTTP: the login door, sessions, origin guard, rate limit."""

import pytest

from app.core.config import get_settings
from tests.web import PASSWORD, client, logged_in, login, make_operator


async def test_anonymous_page_request_is_redirected_to_login():
    async with client() as c:
        r = await c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_login_page_renders_in_persian_rtl():
    async with client() as c:
        r = await c.get("/login")
    assert r.status_code == 200
    assert 'dir="rtl"' in r.text and "ورود" in r.text


async def test_wrong_password_shows_one_generic_error_and_sets_no_cookie():
    await make_operator()
    async with client() as c:
        r = await login(c, password="wrong")
    assert r.status_code == 200
    assert "ایمیل یا رمز عبور اشتباه است" in r.text
    assert "set-cookie" not in r.headers


async def test_right_password_sets_an_httponly_cookie_and_opens_the_overview():
    await make_operator()
    async with client() as c:
        r = await login(c)
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        home = await c.get("/")
    assert home.status_code == 200
    assert "admin" in home.text  # display name in the header


async def test_locked_account_is_told_so():
    await make_operator()
    async with client() as c:
        for _ in range(5):
            await login(c, password="wrong")
        r = await login(c)
    assert r.status_code == 200 and "قفل" in r.text


async def test_logout_revokes_the_session():
    c = await logged_in()
    async with c:
        r = await c.post("/logout", headers={"Origin": "http://test"})
        assert r.status_code == 303
        again = await c.get("/")
    assert again.status_code == 303 and again.headers["location"] == "/login"


async def test_mutating_request_from_a_foreign_origin_is_refused():
    await make_operator()
    async with client() as c:
        r = await c.post("/login", data={"email": "admin@x.io", "password": PASSWORD}, headers={"Origin": "http://evil.test"})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


async def test_mutating_request_without_origin_or_referer_is_refused_outside_relaxed_envs(monkeypatch):
    await make_operator()
    settings = get_settings()
    monkeypatch.setattr(settings, "env_name", type(settings.env_name).production)
    try:
        async with client() as c:
            r = await c.post("/login", data={"email": "admin@x.io", "password": PASSWORD})
        assert r.status_code == 403
    finally:
        monkeypatch.setattr(settings, "env_name", type(settings.env_name).test)


async def test_password_change_needs_the_current_password_and_keeps_this_session():
    c = await logged_in()
    async with c:
        bad = await c.post(
            "/password",
            data={"current": "wrong", "new": "another long password", "confirm": "another long password"},
            headers={"Origin": "http://test"},
        )
        assert bad.status_code == 200 and "رمز فعلی" in bad.text
        ok = await c.post(
            "/password",
            data={"current": PASSWORD, "new": "another long password", "confirm": "another long password"},
            headers={"Origin": "http://test"},
        )
        assert ok.status_code == 303
        still = await c.get("/")
    assert still.status_code == 200


async def test_login_is_rate_limited_per_ip_when_a_trusted_header_is_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_ip_header", "X-Real-IP")
    await make_operator()
    async with client() as c:
        statuses = []
        for _ in range(25):
            r = await c.post(
                "/login",
                data={"email": "admin@x.io", "password": "wrong"},
                headers={"Origin": "http://test", "X-Real-IP": "203.0.113.9"},
            )
            statuses.append(r.status_code)
        other = await c.post(
            "/login",
            data={"email": "admin@x.io", "password": "wrong"},
            headers={"Origin": "http://test", "X-Real-IP": "203.0.113.10"},
        )
    assert statuses.count(429) == 5 and statuses[:20] == [200] * 20
    assert other.status_code == 200  # a different address has its own bucket


async def test_a_forged_header_does_not_pick_the_bucket_when_no_trusted_header_is_set(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_ip_header", "")
    await make_operator()
    async with client() as c:
        statuses = [
            (
                await c.post(
                    "/login",
                    data={"email": "admin@x.io", "password": "wrong"},
                    headers={"Origin": "http://test", "X-Forwarded-For": f"10.0.0.{i}"},
                )
            ).status_code
            for i in range(25)
        ]
    assert 429 not in statuses  # limiter is OFF, not "everyone in one bucket"


async def test_totp_is_asked_for_after_enrolment():
    from app.core.security import totp_code
    import datetime as dt
    from app.db.session import async_session_maker
    from app.services import auth

    op = await make_operator()
    async with async_session_maker() as s:
        op = await auth.get_operator_by_email(s, "admin@x.io")
        secret = await auth.enrol_totp(s, op)
        now = dt.datetime.now(dt.timezone.utc)
        assert await auth.confirm_totp(s, op, totp_code(secret, int(now.timestamp())), now=now)
    async with client() as c:
        r = await login(c)
        assert r.status_code == 200 and "کد دومرحله‌ای" in r.text
        r2 = await login(c, totp=totp_code(secret, int(dt.datetime.now(dt.timezone.utc).timestamp()) + 30))
    assert r2.status_code == 303
