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
    # settings) are gated TWICE. This URL must name a role that is allowed
    # to write those tables and nothing else; blank means no write engine
    # exists at all, which is the state production stays in until Phase 7.
    alobot_write_database_url: str = ""

    # ...and this flag must also be true. The role is the real guard; the
    # flag is what lets the UI say "read-only" honestly instead of failing
    # on save, and what an operator can turn off without touching Postgres.
    alobot_db_writes_enabled: bool = False

    # Random secret for signing session cookies. Generate with
    # `openssl rand -hex 32`.
    session_secret: str

    log_level: str = "INFO"

    # Name of the header the reverse proxy OVERWRITES with the client's real
    # address (nginx: `proxy_set_header X-Real-IP $remote_addr`). Blank means
    # "no proxy tells us the address", and the per-IP login limiter is OFF
    # with a boot warning - never "everyone in one bucket" and never a header
    # the visitor can type themselves.
    trusted_proxy_ip_header: str = ""

    # The connection pool for this project's OWN database. Tunable because
    # the right number depends on the host, and measured by the ingest load
    # test rather than guessed. The pools that reach AloBot's Postgres are
    # NOT tunable here: they are budgeted in app/db/session.py.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # How often the shell asks the bell for its number, in seconds. 0 turns
    # the polling off entirely and leaves the count as it was at page load.
    bell_poll_seconds: int = 30

    # The public SMS door. Body cap in bytes (a bank SMS is a few hundred),
    # per-device and per-IP request limits per minute.
    ingest_max_body_bytes: int = 8192
    ingest_device_rate_per_minute: int = 600
    ingest_ip_rate_per_minute: int = 120

    # Telegram bot token used ONLY to send (the outbox). Blank disables sending
    # with a boot warning. A THROWAWAY test bot until the integration phase;
    # AloBot's production token only ever lands here in Phase 7.
    telegram_bot_token: str = ""

    # The in-process sweep loop (outbox, prunes, later the matcher). Off in
    # tests, which drive sweeps directly. /health reports the loop stale
    # when it is expected and the heartbeat is older than 90 s.
    run_sweeps: bool = True
    sweep_interval_seconds: float = 25.0
    heartbeat_path: str = "/tmp/alobot-dashboard-heartbeat"

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
