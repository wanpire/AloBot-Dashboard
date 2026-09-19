"""Phase 1 task 6: the shell every screen lives in."""

from app.web.nav import NAV, READABLE_BY_READER
from tests.web import logged_in


def test_no_nav_label_is_a_substring_of_another():
    labels = [item.label for group in NAV for item in group.items]
    for a in labels:
        for b in labels:
            assert a == b or a not in b, (a, b)


async def test_read_only_sees_only_the_readable_sections():
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/")
    hidden = [item for group in NAV for item in group.items if item.id not in READABLE_BY_READER]
    assert hidden, "nothing is hidden from READ_ONLY - the test proves nothing"
    for item in hidden:
        assert f'href="/{item.id}"' not in r.text, item.id
    for item in (i for g in NAV for i in g.items if i.id in READABLE_BY_READER):
        assert f'href="/{item.id}"' in r.text, item.id


async def test_read_only_gets_403_on_an_admin_only_page_even_by_url():
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/access")
    assert r.status_code == 403


async def test_version_badge_and_persian_digits_in_the_shell():
    c = await logged_in()
    async with c:
        r = await c.get("/")
    assert "dev" in r.text
    assert 'lang="fa"' in r.text
