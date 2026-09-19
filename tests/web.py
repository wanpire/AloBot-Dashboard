"""Shared helpers for tests that drive the web app through HTTP."""

import httpx

from app.db.session import async_session_maker
from app.main import app
from app.services import auth
from app.web.deps import SESSION_COOKIE

PASSWORD = "correct horse battery staple"


def client(**kwargs) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False, **kwargs)


async def make_operator(email="admin@x.io", role="ADMIN", password=PASSWORD):
    async with async_session_maker() as s:
        return await auth.create_operator(s, email=email, display_name=email.split("@")[0], password=password, role=role)


async def login(c: httpx.AsyncClient, email="admin@x.io", password=PASSWORD, totp: str | None = None):
    data = {"email": email, "password": password}
    if totp:
        data["totp"] = totp
    return await c.post("/login", data=data, headers={"Origin": "http://test"})


async def logged_in(role="ADMIN", email=None) -> httpx.AsyncClient:
    """A client holding a real session cookie. The session is opened straight
    in the database (the login route has its own tests) so the returned
    client has not sent anything yet and can still be used as `async with`."""
    email = email or f"{role.lower()}@x.io"
    operator = await make_operator(email=email, role=role)
    async with async_session_maker() as s:
        token = await auth.open_session(s, operator)
    c = client()
    c.cookies.set(SESSION_COOKIE, token)
    return c
