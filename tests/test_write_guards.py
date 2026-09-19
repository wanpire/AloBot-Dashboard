"""Phase 1 task 4: every write route refuses READ_ONLY, counted from the
router's own route table so a route added without a guard fails here."""

import re

from fastapi.routing import APIRoute

from app.main import app
from tests.web import logged_in

# Routes a signed-in READ_ONLY operator must be able to POST to, and why.
ALLOWLIST = {
    "/login": "the door itself",
    "/logout": "leaving is always allowed",
    "/password": "changing your own password is not a write to shop data",
}


def _walk(routes):
    """Included routers are nested objects with their own `.routes`; flatten."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif getattr(route, "original_router", None) is not None:  # FastAPI >= 0.140 nests included routers
            yield from _walk(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _walk(route.routes)


def write_routes():
    for route in _walk(app.routes):
        methods = {m for m in route.methods if m not in ("GET", "HEAD", "OPTIONS")}
        if methods:
            yield route, sorted(methods)


def test_there_are_write_routes_to_guard():
    assert list(write_routes()), "no write routes found - the walk is broken"


async def test_every_write_route_refuses_read_only():
    c = await logged_in("READ_ONLY")
    failures = []
    async with c:
        for route, methods in write_routes():
            if route.path in ALLOWLIST:
                continue
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            for method in methods:
                r = await c.request(method, path, headers={"Origin": "http://test"})
                if r.status_code != 403:
                    failures.append(f"{method} {route.path} -> {r.status_code}")
    assert not failures, "unguarded write routes:\n" + "\n".join(failures)


async def test_every_write_route_refuses_anonymous():
    from tests.web import client

    failures = []
    async with client() as c:
        for route, methods in write_routes():
            if route.path in ("/login",):
                continue
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            for method in methods:
                r = await c.request(method, path, headers={"Origin": "http://test"})
                if r.status_code not in (401, 303):
                    failures.append(f"{method} {route.path} -> {r.status_code}")
    assert not failures, "\n".join(failures)
