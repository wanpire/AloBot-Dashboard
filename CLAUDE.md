# alobot-dashboard

## Purpose

`alobot-dashboard` is a standalone web dashboard and bank-SMS payment
verification service for AloBot (the Telegram VPN-sales bot in the sibling
`~/AloBot` repo, GitHub `wanpire/telegram-bot`). It exists because AloBot has
two gaps that no amount of Telegram UI closes:

1. **Card-to-card payments are not verified against the bank.** Today an admin
   eyeballs a receipt photo in Telegram, or a timer auto-approves the payment
   after N minutes with no evidence at all (`app/services/auto_approve.py` in
   AloBot), and a separate "autoreview" queue exists only to catch fraud after
   the fact. This project receives the bank's own SMS from a relay phone,
   parses it, and matches it to the pending payment - exact account, exact
   amount, inside a five-minute window - so a card payment is approved because
   the money actually arrived.
2. **There is no web view of the shop.** Customers, orders, resellers, the
   catalog, discount codes, tutorials, settings and reports all live behind
   ~8,000 lines of Telegram button flows. This project puts them on screens.

It is modelled on the feature set of a reference project (Shikoonet-Platform,
a Persian Telegram shop with the same problem) filtered down to what applies to
AloBot's IBSng-based service. The filtered scope is in `docs/research/`; the
build order is in `docs/PLAN.md`.

## Relationship to AloBot - and the one rule above all others

**AloBot is live with real customers. This project must never put its purchase
flow at risk.** Concretely:

- This project is built, tested and demoed **entirely on its own**, against a
  **copy** of AloBot's database and synthetic data. Until the final phase of
  `docs/PLAN.md` nothing here reads AloBot's production database, holds AloBot's
  production bot token, or changes a line in AloBot's repo.
- Every change that touches AloBot - its source, its compose file, its database
  roles, its network - lives in **the last phase**, needs **its own explicit
  go-ahead** from the owner before any code changes, gets **its own test-suite
  run**, and never restarts the live bot without that being flagged first.
- The single genuine integration point ("this AloBot payment is verified") is
  deferred to that final phase too. It is not small enough to sneak in early.

Deployment pattern is the same as `dns-switcher`: a separate repo, its own
containers, its own Postgres, additively deployed beside AloBot on the same
host, joined to the shared Docker network only when the integration phase says
so.

```
Bank SMS ──▶ Android relay app ──▶ POST /api/v1/sms (this project, public,
                                   behind TLS, device-token auth)
                                       │
                                       ▼
                        parser → transaction candidates → matcher
                                       │
                 ┌─────────────────────┼─────────────────────┐
                 ▼                     ▼                     ▼
       this project's Postgres   AloBot's Postgres      Telegram Bot API
       (claims, transactions,    (READ-ONLY role;        (sendMessage only,
        accounts, devices,        payments, vpn_users,    never getUpdates -
        outbox, operators,        bot_users, services,    AloBot owns polling)
        events, settings)         groups, resellers, …)
                 ▲
                 │  HTMX pages, HTTPS, operator login
            Operator's browser
```

## Scope

**In scope (from `docs/research/part2-relevance-report.md`, "Directly
relevant"):** the SMS verification pipeline and review queue; dashboard
sections that map onto AloBot's existing tables (overview, stats, customers,
orders/subscriptions, resellers, catalog, discount codes, tutorials/links/
profiles, bot texts and keyboards, cron jobs, settings, access, broadcast and
campaign, events); login with password and optional TOTP; the backend patterns
listed under "Golden rules"; and four small bot-side additions (tariff screen,
flood guard, never-bought nudge, button colours) which are AloBot changes and
therefore final-phase.

**Explicitly out of scope for now (owner's decision):** wallet ledger, referral
program, multiple destination cards, stock shelf, auto-deleting expired IBSng
accounts, usage bars, per-action admin permissions, DNS-switcher screens,
expenses and bank books, changing how AloBot stores money. Each will be decided
on its own later. **Design so none of them is foreclosed; do not scaffold any
of them.** Everything PasarGuard-specific from the reference project is gone
for good.

## Stack

Python 3.12, FastAPI + uvicorn, Jinja2 templates with HTMX for partial updates
(server-rendered, RTL-first, no frontend build step), SQLAlchemy 2.x async +
asyncpg, Alembic, httpx, Pydantic v2, pytest + pytest-asyncio. Persian digits
and Jalali dates on every screen an operator reads.

## Two databases, deliberately separate

- **Own Postgres** (`DATABASE_URL`, container `alobot-dashboard-db`): every
  table this project creates. Managed by this repo's Alembic.
- **AloBot's Postgres** (`ALOBOT_DATABASE_URL`): reached through a Postgres role
  with `SELECT` only. Tables are **reflected at startup**, never re-declared
  here (`app/alobot/README.md`), and a boot-time compatibility check compares
  the columns this project depends on against the AloBot commit in
  `ALOBOT_COMMIT`. Blank URL means AloBot-backed pages show "not connected".
  Writes to AloBot tables (catalog, discount codes, tutorials, settings) are
  enabled only in the final phase, by a write-capable role scoped to those
  tables **and** `ALOBOT_DB_WRITES_ENABLED=true`; the role is the guard, the
  flag is what lets the UI say "read-only" honestly.

Why not one database with two schemas: every connection and migration would
then touch AloBot's production server, which is exactly what the isolation rule
forbids before the final phase.

## Money

AloBot stores Toman as `Numeric(12,2)`. Bank SMS report Rial. This project's
own tables store **integer Rial** (`bigint`), and the Toman↔Rial conversion
lives in exactly one helper in `app/alobot`. This is internal to this project
and changes nothing about AloBot; the owner has parked the question of AloBot's
own storage unit.

## AloBot's source - read access, no runtime coupling

`vendor/alobot` is a gitignored symlink to the sibling AloBot checkout
(`make link-alobot ALOBOT_PATH=/path/to/AloBot`; default `../AloBot`, which is
`~/AloBot` on the dev Mac and `/home/peyman/telegram-bot` on the server). It is
there so anyone working here can read AloBot's real handlers, services, models
and IBSng client instead of guessing. **Nothing under `app/` imports from it**:
importing AloBot's package would drag in its `Settings` (bot token, IBSng
credentials) and its Redis/aiogram runtime. AloBot's data is reached through
the read-only database link above; AloBot's IBSng client is not needed here
because this project never calls IBSng (see Golden rules).

`ALOBOT_COMMIT` records the AloBot commit this project was last verified
against; `make check-alobot-pin` warns when the link has moved past it, which
is the cue to re-run the schema compatibility check. **Never write under
`vendor/alobot`.** AloBot changes happen in AloBot's repo, in the final phase,
with their own go-ahead.

## Golden rules

- **Auto-verify only an isolated 1↔1 pair.** Exactly one pending claim and
  exactly one bank credit that could belong to each other: same account, same
  amount to the Rial, `|bank_timestamp − paid_clicked_at| ≤ 5 min`. Anything
  ambiguous goes to a human. **Never auto-reject, never auto-mark fake.** A
  missed auto-approval costs an operator a click; a wrong one costs money.
- **One bank transaction settles at most one claim, one claim settles at most
  once - enforced by partial unique indexes in Postgres**, never by code alone.
  Verified in `migrations/verify_invariants.sql` on every test run.
- **The dashboard reads databases and nothing else.** It never calls IBSng
  (AloBot's CLAUDE.md already makes AloBot's client the only caller) and never
  calls dns-switcher. The one outbound call it makes is Telegram `sendMessage`
  through the outbox, and only with a test bot token until the final phase.
- **Every customer-facing message goes through the outbox** (`bot_notifications`):
  dedupe key, retry with backoff, DEAD state, "user blocked the bot" detected
  from the error code. Never a direct send from a request handler.
- **State transitions are conditional UPDATEs in sweeps**, with the expected
  status in the `WHERE`; a transition that changed zero rows lost a race and
  rolls back. No callbacks that assume they are the only writer.
- **`ENV_NAME` fails closed.** No default, four allowed values, and only
  `local`/`test` relax any guard (allowlist, not `!= production`).
- **Secrets never reach logs.** Bot token, device tokens, session secrets, raw
  SMS bodies and OTPs are on the logger's redaction deny-list; OTP SMS are
  never stored at all. The structured logger is the only logger.
- **Every write route has a role guard, and a test counts them** over the
  router's own route table so a guard cannot be forgotten silently.
- **Idempotency everywhere money or messages move**: claims keyed on AloBot's
  payment id, outbox rows on a dedupe key, admin actions on a client-minted
  key, so a double-click or a retried sweep lands on the same row.

## Configuration

See `.env.example`. `ENV_NAME`, `DATABASE_URL` and `SESSION_SECRET` are
required; the process refuses to start without them. `ALOBOT_DATABASE_URL` and
`ALOBOT_DB_WRITES_ENABLED` are the two AloBot-facing knobs and both default to
"off". Later phases add the ingest limits, the bot token, TOTP settings and the
Telegram report chat - each is added to `Settings` and `.env.example` in the
phase that needs it, never earlier.

## Project layout

```
app/
  core/        config (fail-closed), logging, security helpers
  db/          own engine + AloBot read-only engine, declarative base
  models/      this project's own ORM models
  alobot/      reflected read-only access to AloBot tables, compat check, money
  api/         JSON routes (ingest, internal API); operator pages are in web/
  web/         Jinja2 templates, static assets, HTMX page routes
  services/    business logic: parser, matcher, outbox, sweeps, review actions
alembic/       migrations for the OWN database only
scripts/       link_alobot.sh, later copy-alobot-db, restore drill, seeds
tests/         pytest; real Postgres, never SQLite (partial indexes matter)
docs/
  PLAN.md      phased plan and task breakdown - the build order
  research/    the reference-project inventory and the relevance filter
vendor/alobot  gitignored symlink to AloBot's checkout (read-only reference)
ALOBOT_COMMIT  the AloBot commit this project was verified against
```

## Testing

Real Postgres only: the money guarantees are partial unique indexes and
SQLite cannot represent them. `docker-compose.yml` never publishes the db port,
so run a throwaway one for tests:

```bash
docker run -d --rm --name alobot-dashboard-test-db \
  -e POSTGRES_USER=dashboard -e POSTGRES_PASSWORD=dashboard -e POSTGRES_DB=dashboard_test \
  -p 5432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+asyncpg://dashboard:dashboard@localhost:5432/dashboard_test make test
```

`tests/conftest.py` sets `ENV_NAME=test`, `RUN_SWEEPS=false` and the other
required variables before anything under `app` is imported, applies the real
migrations once per session (never `create_all`), and truncates every table
after each test. The invariants script runs as part of the suite. AloBot-backed tests (from Phase 2)
run against a second throwaway database restored from a **copy** of AloBot's
schema, never against AloBot's own database.

## Status

Phase 0 (scaffold): package layout, fail-closed configuration, the two
engines, Alembic wired to the own database, `/health`, compose with a
loopback-only port and an isolated db network, the AloBot link script with a
commit pin.

Phase 1 (foundation) - done, 109 tests:

- Migration `0001`: `operators`, `operator_sessions`, `settings`,
  `audit_logs` (append-only trigger), `app_events`, `bot_notifications`
  (dedupe_key UNIQUE, closed status set, partial index on due rows).
- `app/core/logging.py`: one JSON logger, redaction by key name at any depth,
  request id on every line; `app/services/events.py` copies WARNING+ into
  `app_events` through a buffered sink the sweep loop flushes.
- `app/services/auth.py`: scrypt passwords, RFC 6238 TOTP (tested on the RFC
  vectors), lockout counted inside the UPDATE, sessions with a 12 h idle
  window that slides only when stale and a 30 d absolute cap, in-panel
  password change that keeps the current session; `scripts/create_operator.py`
  reads the password from stdin.
- Roles ADMIN / REVIEWER / READ_ONLY. `app/web/nav.py` is the one place that
  says who sees which section; `page()` and `require_role()` in
  `app/web/deps.py` enforce it; `tests/test_write_guards.py` walks every
  write route in the router and asserts READ_ONLY is refused, with a named
  allowlist for login, logout and password change.
- `app/web/guards.py`: origin guard on every mutating request (Origin or
  Referer must match Host; missing is refused outside local/test); per-IP
  login limit behind `TRUSTED_PROXY_IP_HEADER`, OFF with a boot warning when
  unset - never "everyone in one bucket".
- RTL Jinja2/HTMX shell (`base.html`), Persian digit / Jalali filters
  (`app/web/format.py`, pinned to known calendar points incl. a leap year),
  version badge; settings page from the typed registry in
  `app/core/settings_registry.py` (unknown keys refused, every change
  audited in the same transaction); access page whose self and last-admin
  guards live inside the UPDATE with the active admins locked first; events
  page (ADMIN only, no delete control, copy-as-JSON).
- `app/services/sweeps.py`: registry + loop, one failing sweep never stops
  the rest, heartbeat file touched after a full cycle; `/health` reports the
  heartbeat age and turns 503 when the loop is expected and stale.
- `scripts/verify_invariants.sql` runs on every test run (and fails the suite
  if the audit trigger is dropped); `scripts/restore_drill.sh` dumps,
  restores beside the live db, checks the migration head, runs the
  invariants, drops the copy - verified inside the image against Postgres 16
  (the image pins `postgresql-client-16` because a 17 client's dump broke a
  16 restore).

Phase 2 (AloBot data window, read-only) - done:

- `scripts/copy_alobot_db.sh` restores an AloBot dump into a LOCAL database
  and creates the SELECT-only `dashboard_ro` role; refuses non-local hosts.
  `scripts/seed_alobot_copy.py` fills a local copy with deterministic
  synthetic data covering every payment status, method and purpose AloBot
  produces; also refuses non-local hosts.
- `app/alobot/link.py` reflects the tables in `app/alobot/compat.py` at boot
  and runs the compatibility check; `link.available` is the one question
  every AloBot page asks, and `/health` reports `alobot_db` as ok /
  unconfigured / incompatible with the missing column named.
- `app/alobot/queries.py` holds every read as a plain SELECT over the
  reflected tables; `tests/test_alobot_pages.py` records every statement the
  AloBot engine executes while walking all screens and asserts they are all
  SELECTs (and the role could not write anyway - a test proves that too).
- `app/alobot/money.py` (Toman↔Rial, refuses sub-Rial precision),
  `app/alobot/codes.py` (AloBot's sqids order codes, alphabet pinned against
  `vendor/alobot` by a test), `app/alobot/time.py` (Tehran day bounds).
- Screens: overview (queues waiting on a human + today's figures), shop
  stats (period figures separated from the «هم‌اکنون» snapshot), customers
  (search + card with accounts and payments), orders (filters + invoice-code
  search), subscriptions (expiry or an honest «نامشخص», since AloBot reads
  expiry live from IBSng and this dashboard never calls IBSng), resellers,
  catalog, discount codes, tutorials/links/profiles, bot settings - the last
  four carry the read-only banner until the integration phase.
- Tests build the AloBot copy from AloBot's OWN Alembic migrations in
  `vendor/alobot` (pinned commit) into a throwaway `alobot_copy_test`
  database and connect through `dashboard_ro`, so the suite exercises the
  exact schema and the exact privileges production will have.

Phase 3 (SMS ingest and parser) - done, with one open gap:

- Migration `0002`: `devices`, `device_credentials` (one ACTIVE per device by
  partial unique index), `financial_accounts` (+ identifiers, unique per
  kind/value), `payment_cards` (16 digits, unique), `bank_card_prefixes`,
  `bank_sms_patterns`, `sms_events` (dedupe_key UNIQUE, OTP-redacted body),
  `transaction_candidates` (one per event, non-negative amounts).
- `POST /api/v1/sms` (`app/api/ingest.py`): device token in the body (the
  relay app's contract), body capped in bytes before parsing, one generic
  401 for every auth failure, per-device limit and per-IP limit behind the
  trusted-proxy header, dedupe over device+sender+timestamp+normalised body,
  Android epoch-ms and iOS Shortcuts timestamps. Matching is NOT done here -
  the handler persists and answers; Phase 4's sweep matches.
- `app/sms/`: normaliser, OTP and promotional detection, a generic Persian
  bank-transaction parser (direction from phrases and signs, amount after
  «مبلغ» or the direction word or as a signed number, balance, account
  last-four, reference), balance-only, unknown; then operator patterns
  (`app/sms/patterns.py`) which are additive only, never see an OTP, and are
  refused at the form if they blow a regex time budget on bait input.
- Unknown identifiers create a PENDING account; the balance chain proposes
  the owner (`inferred_account_id`) and nothing auto-activates.
- Screens: devices (token shown once, rotate/revoke need a ticked
  confirmation naming what stops), accounts and cards (statuses with their
  consequences spelled out, Luhn on entry), banks (prefix→bank, patterns with
  a sandbox that runs only that pattern, test-card and test-SMS tools),
  transactions (filters, assign account, decline/restore income, unparsed
  list with dry-run/apply reparse, coverage per sender).

**Open gap, owner's decision:** the bank-specific parsers wait for real
messages. `tests/sms_corpus/` is provisional synthetic text written from
common conventions; the owner supplies real SMS (amounts and names altered)
after the first deployment, and each bank's format lands either as a parser
in `app/sms/parsers.py` or as an operator pattern. Until then the generic
parser and the pattern editor are the coverage.

Phase 4 (claims, matcher, review queue, notifications) - done:

- Migration `0003`: `payment_claims` (one per AloBot payment id; THIS
  project's decision in `status`, AloBot's own in `alobot_status`) and
  `reconciliation_matches` with the two partial unique indexes that make
  "one credit settles one claim, once" a database fact (in the invariants).
- `app/services/matcher.py` is pure and is the only place that decides:
  isolated 1↔1, same account, same amount to the Rial, ±5 min (24 h for a
  continuity claim), WAIT for 10 min before saying "no transaction", never
  auto-reject, time distance is never a tiebreaker.
- Sweeps: `claims.mirror` (AloBot's pending card payments → claims, target
  card from AloBot's `app_config.card_number` → our `payment_cards` → account),
  `claims.settle` (match row + conditional claim UPDATE in one transaction;
  the race loser rolls back), `outbox.flush` (Telegram sendMessage with
  backoff, 429 honoured, 403 = DEAD at once, batch cap via `AS MATERIALIZED`).
- `app/services/review.py`: approve with a chosen credit (CONFIRMED match),
  verify without one (needs a note), reject, fake, park, message a template,
  reopen (frees the credit); each a conditional transition. Queues are SQL
  conditions in one place (`TAB_CONDITIONS`), the bell counts «در انتظار
  بررسی», the sidebar shows it.
- Continuity mode: one settings row with an expiry the read enforces,
  5 min - 6 h, reason required, ADMIN only, banner on every page; claims
  mirrored while it is on are FULFILLED_UNRECONCILED.
- Financial stats: automation rate, average «پرداخت کردم»→credit seconds,
  credits per account, unmatched credits, declined income.
- Customer-facing sends are gated by `notify/customers_enabled` (default off)
  because AloBot still tells the customer itself until Phase 7; operator
  alerts go to `alerts/operator_chat_id`, rate-limited by a UNIQUE key per
  event per Tehran hour. The bot token is a throwaway until Phase 7.

Two deliberate deviations from the plan: the receipt reminder is not built,
because AloBot creates the payment row only when the receipt photo arrives, so
"paid but no receipt" cannot occur; and the receipt image itself is shown in
Phase 7, since only AloBot's bot token can fetch that file.

Next: Phase 5 in `docs/PLAN.md` - write screens on AloBot's tables, against
the copy.
