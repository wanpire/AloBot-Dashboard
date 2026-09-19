"""Single source of truth for configuration, loaded from `.env`.

`ENV_NAME` has no default on purpose. A process that cannot say which
environment it is in must not start, because every production guard in
this project (session cookie flags, write access to AloBot tables, real
bot token) keys off it. A typo silently turning production into "local"
is the failure this refuses to allow.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvName(str, Enum):
    local = "local"
    test = "test"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Required, no default - see module docstring.
    env_name: EnvName

    # This project's own Postgres (claims, SMS transactions, accounts,
    # devices, outbox, operators, events). Always required.
    database_url: str

    # AloBot's Postgres, reached through a READ-ONLY role. Blank means
    # "no AloBot link" - every page that reads AloBot data shows a clear
    # "not connected" state instead of failing. Until the final
    # integration phase this must point at a COPY of AloBot's database,
    # never production.
    alobot_database_url: str = ""

    # Writes to AloBot tables (catalog, discount codes, tutorials,
    # settings) stay off until the final integration phase explicitly
    # flips this. The DB role behind `alobot_database_url` is the real
    # guard; this flag is the application-level one that lets the UI
    # say "read-only" honestly instead of failing on save.
    alobot_db_writes_enabled: bool = False

    # Random secret for signing session cookies. Generate with
    # `openssl rand -hex 32`.
    session_secret: str

    log_level: str = "INFO"

    # Reported by /health and the version badge. Set at build/deploy
    # time from the git commit; "dev" when unset.
    app_version: str = "dev"

    @field_validator("session_secret")
    @classmethod
    def _secret_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters (openssl rand -hex 32)")
        return value

    @property
    def is_relaxed_env(self) -> bool:
        """Only local and test relax production guards - an allowlist, so
        staging on the internet is treated exactly like production."""
        return self.env_name in (EnvName.local, EnvName.test)


@lru_cache
def get_settings() -> Settings:
    return Settings()
