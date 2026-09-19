import pytest
from pydantic import ValidationError

from app.core.config import EnvName, Settings


def test_env_name_is_required_and_has_no_default(monkeypatch):
    monkeypatch.delenv("ENV_NAME", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+asyncpg://x", session_secret="a" * 32)


def test_unknown_env_name_is_refused(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env_name="prod", database_url="postgresql+asyncpg://x", session_secret="a" * 32)


def test_short_session_secret_is_refused():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env_name="local", database_url="postgresql+asyncpg://x", session_secret="short")


@pytest.mark.parametrize(
    ("env", "relaxed"),
    [(EnvName.local, True), (EnvName.test, True), (EnvName.staging, False), (EnvName.production, False)],
)
def test_only_local_and_test_relax_guards(env, relaxed):
    s = Settings(_env_file=None, env_name=env, database_url="postgresql+asyncpg://x", session_secret="a" * 32)
    assert s.is_relaxed_env is relaxed


def test_alobot_writes_default_off(monkeypatch):
    monkeypatch.delenv("ALOBOT_DATABASE_URL", raising=False)
    s = Settings(_env_file=None, env_name="local", database_url="postgresql+asyncpg://x", session_secret="a" * 32)
    assert s.alobot_db_writes_enabled is False
    assert s.alobot_database_url == ""
