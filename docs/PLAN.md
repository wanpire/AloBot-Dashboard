# Phased plan and task breakdown

Each phase is independently deliverable and verifiable. Each task is written so
it can be handed over as its own prompt later. Phases 0–6 never touch AloBot's
repo, deployment, database or bot token; Phase 7 is the only one that does, and
every one of its tasks needs its own explicit go-ahead.

Legend: **[own]** only this project · **[copy]** needs a copied AloBot database ·
**[test-bot]** needs a throwaway Telegram bot token, never AloBot's.

---

## Phase 0 — Skeleton ✅ (this scaffold)

Package layout, fail-closed config, two engines, Alembic on the own database,
`/health`, compose with loopback port and isolated db network, AloBot link
script with commit pin, config and health tests, the brief, this plan.

---

## Phase 1 — Foundation: identity, safety rails, first screen ✅

Goal: an operator can log in to an empty but real dashboard, and every
cross-cutting guarantee the later phases rely on already exists.

1. **[own] Own-database models and first migration:** `operators`,
   `operator_sessions`, `settings` (scope, key, value jsonb, updated_by),
   `audit_logs` (append-only via trigger), `app_events`, `bot_notifications`
   (outbox: dedupe_key UNIQUE, attempt_count, next_attempt_at, status
   PENDING/SENT/FAILED/DEAD). Alembic migration verified upgrade → downgrade →
   upgrade against a real Postgres.
2. **[own] Structured JSON logger with redaction deny-list** (token, secret,
   password, otp, sms body keys) and a test that writes a fake token and greps
   the output for it. Request id per request, carried into audit and events.
3. **[own] Operator authentication:** password (scrypt or argon2 via
   `passlib`-free stdlib), optional TOTP (RFC 6238, tested against the RFC
   vectors), lockout of 5 tries / 15 min counted inside the UPDATE, HttpOnly
   session cookie (`Secure` outside relaxed envs), 12 h idle / 30 d absolute,
   in-panel password change requiring the current password, CLI to create the
   first operator with the password read from stdin.
4. **[own] Roles and route guards:** ADMIN / REVIEWER / READ_ONLY; per-route
   write guard; `tests/test_write_guards.py` walks `app.routes` and asserts
   every non-GET route refuses READ_ONLY, with a named allowlist for the
   exceptions (login, logout, password change).
5. **[own] Origin guard and CSRF** for every mutating request; per-IP login rate
   limit keyed on a configured trusted-proxy header, off with a boot warning
   when unset.
6. **[own] Base layout:** RTL Jinja2 shell, sidebar with groups (گزارش‌ها ·
   مشتری و فروش · کاتالوگ · پول · ربات · سیستم), Persian digit and Jalali date
   filters with tests against `Intl`-equivalent fixtures, HTMX wiring, version
   badge from `APP_VERSION`, empty overview page.
7. **[own] Settings page (own settings only):** typed registry of live keys
   (label, hint, kind, on/off vocabulary) drawn as a form; server refuses
   writes to unlisted keys; every write audited.
8. **[own] Access page:** list, create, edit role, deactivate operators;
   guards "cannot demote yourself" and "last active ADMIN stays" live in the
   UPDATE and the UI only reports the server's sentence.
9. **[own] Events page:** `app_events` with level/time/text filters,
   copy-as-JSON, 30-day prune sweep, ADMIN only.
10. **[own] Sweep runner:** one asyncio loop inside the app (or a second
    process, decided here) that runs registered sweeps every ~25 s with a
    heartbeat file the container health check reads, so "alive but not
    sweeping" is unhealthy.
11. **[own] `verify_invariants.sql` scaffold** run by a test on every suite,
    plus `scripts/restore_drill.sh` (pg_dump → restore into a throwaway db →
    invariants pass).

Exit criteria: login works in a real browser, every write route is guarded by
the test, logs are JSON with no secret leakage, restore drill passes.

**Done 2026-09-20** — 109 tests; restore drill verified inside the image.
One deviation: task 3's CLI and task 10's heartbeat both landed; task 5's
CSRF is the origin guard alone (no token), which is sufficient because every
form is same-origin and the session cookie is SameSite=Lax.

---

## Phase 2 — AloBot data window (read-only) and the read-only screens ✅

Goal: every AloBot-backed screen renders from a **copy** of AloBot's database.

1. **[copy] `scripts/copy_alobot_db.sh`:** takes an AloBot `pg_dump` (AloBot's
   own nightly backup zip already contains one) and restores it into a local
   `alobot_copy` database; creates the `dashboard_ro` role with SELECT only;
   documents the exact commands. Never points at production.
2. **[copy] `app/alobot` reflection layer:** reflect `payments`, `vpn_users`,
   `bot_users`, `admin_users`, `resellers`, `services`, `service_locations`,
   `groups`, `discount_codes`, `discount_code_usages`, `tutorial_*`,
   `download_links`, `openvpn_profiles`, `app_config`; typed query functions
   per screen; Toman↔Rial helper with tests.
3. **[copy] Schema compatibility check at boot** against the column set
   recorded for `ALOBOT_COMMIT`; mismatch disables AloBot pages with a named
   reason; `make check-alobot-pin` hooked to it.
4. **[copy] Synthetic seed** for the copy database (customers, payments in every
   status, VPN users with expiries, resellers, catalog) so demos never need
   real data; refuses to run against any host but localhost.
5. **[copy] Overview page:** headline numbers (today's approved sales, pending
   card payments, active VPN users, trials today, expiring in 3 days) and the
   "waiting on a human" queues.
6. **[copy] Shop stats page:** period figures (24 h / 7 d / 30 d / custom
   Jalali range) separated from running totals, matching AloBot's own report
   numbers exactly (test compares against AloBot's `sales_reports` logic
   re-implemented in SQL).
7. **[copy] Customers list and card:** search by telegram id / username, their
   VPN accounts with expiry, their payments, blocked state. Read-only.
8. **[copy] Orders and subscriptions lists:** filters by status, method,
   category, date; invoice-number search using AloBot's sqids alphabet (the
   alphabet is read from `vendor/alobot`, recorded, and tested).
9. **[copy] Resellers list:** balance, commission, their purchases. Read-only.
10. **[copy] Catalog, discount codes, tutorials/links/profiles, bot settings
    (`app_config`) as read-only screens** with an honest "read-only until
    integration" banner driven by `ALOBOT_DB_WRITES_ENABLED`.

Exit criteria: every screen above renders against the seeded copy with zero
writes attempted (a test asserts the AloBot engine saw only SELECTs).

**Done 2026-09-20** — 134 tests. The test copy is built from AloBot's own
migrations (`vendor/alobot`, pinned commit) and read through the SELECT-only
role, so the suite runs on the exact schema and privileges production will
have.

---

## Phase 3 — SMS ingest and Persian bank SMS parsing ✅ (corpus pending)

Goal: the relay phone can post, the bank's SMS become structured transactions.

1. **[own] Models + migration:** `devices`, `device_credentials` (hash + 4-char
   prefix, one ACTIVE per device by partial unique index), `financial_accounts`
   (bank, identifier, status PENDING/ACTIVE/MUTED/DECLINED), `payment_cards`
   (number, holder name, account), `bank_card_prefixes`, `bank_sms_patterns`,
   `sms_events` (redacted body, sender, timestamps, dedupe key UNIQUE),
   `transaction_candidates` (direction, amount_irr, account, balance,
   reference, bank_timestamp, disposition).
2. **[own] `POST /api/v1/sms`:** device token in the body (the relay app's
   contract), constant-time hash compare, one generic 401, body size cap
   applied before parsing, per-device and per-IP rate limits, dedupe over
   device+sender+timestamp+normalised body, Android epoch-ms and iOS Shortcuts
   timestamps accepted. Security tests: enumeration, oversize, chunked body,
   replay.
3. **[own] Parser package:** normaliser (Persian/Arabic digits, whitespace,
   separators), classifier (credit / debit / balance / OTP / promo / unknown),
   one parser module per bank format present in the owner's real SMS corpus
   (Mellat, Melli, Saman, Parsian, Keshavarzi, Shahr, Gardeshgari, generic
   "internet transfer", generic "compact"), OTP detection with redaction before
   persistence, additive DB patterns that may only add a bank name never
   override an amount. **Written from scratch in Python; the reference
   project's TypeScript parser is read for behaviour only, never copied.**
   Corpus-driven tests: one fixture file per bank, real messages with numbers
   altered.
4. **[own] Unknown destination handling:** an unmapped account identifier
   creates a PENDING `financial_accounts` row; balance-chain inference proposes
   the likely owner; nothing auto-activates.
5. **[own] Devices page:** create device, credential shown once, rotate/revoke
   with confirmation naming what stops, rename, deactivate/reactivate, last
   seen / last success.
6. **[own] Accounts and cards pages:** account lifecycle actions with
   consequences named, identifiers, cards with holder name, Luhn check on
   entry.
7. **[own] Banks page:** prefix→bank table (longest prefix wins), SMS pattern
   editor with a sandboxed test against a pasted message and a regex time
   budget, "test card" and "test SMS" tools.
8. **[own] Transactions page:** list, filters, unparsed list with CSV, reparse
   dry-run/apply, assign account, decline/restore income.
9. **[own] Coverage report:** which senders/banks parsed, which did not, over a
   period.

Exit criteria: a fixture corpus of at least 100 real-shape messages parses with
zero false amounts; a synthetic relay client posts end to end and the rows
appear on the Transactions page; ingest survives a 130-request burst with the
configured limit.

**Done 2026-09-20** — 192 tests. The corpus criterion is deferred by the
owner: real bank messages arrive after the first deployment (the shop does not
yet have the SMS formats). The provisional corpus in `tests/sms_corpus/`
covers the generic parser and the pattern engine; bank-specific parsers are
added when real messages exist. End-to-end relay post and the burst were run
against the real container.

---

## Phase 4 — Claims, matching, review queue, notifications

Goal: a pending AloBot card payment becomes a claim, the matcher decides, an
operator reviews what it could not decide, the customer hears about it.

1. **[own+copy] `payment_claims` and `reconciliation_matches` models:** claim
   keyed on AloBot `payment_id` (UNIQUE), `telegram_id`, `expected_amount_irr`,
   `paid_clicked_at` (AloBot `payments.created_at`), `receipt_file_id`,
   status PENDING / MATCH_SUGGESTED / AUTO_VERIFIED / MANUAL_VERIFIED /
   REJECTED / FAKE / FULFILLED_UNRECONCILED, `parked_at`, `messaged_at`,
   `suspect_reason`. Match rows with **partial unique indexes: one
   CONFIRMED/AUTO_VERIFIED match per transaction, one per claim.** Both proven
   in `verify_invariants.sql` with `assert_rejects`.
2. **[copy] Claim mirror sweep:** reads AloBot's pending card payments through
   the read-only link and upserts claims idempotently; never writes to AloBot.
3. **[own] Matcher (pure function, no DB handle):** builds the claim↔credit
   bipartite graph, auto-verifies only isolated 1↔1 components, WAIT for 10 min
   after receipt while no SMS, SUGGEST otherwise with a named reason
   (`UNMAPPED_CARD`, `ACCOUNT_NOT_ACTIVE`, `AMBIGUOUS_TRANSACTIONS`,
   `AMBIGUOUS_CLAIMS`, `NO_TRANSACTION_AFTER_10M`, …). Tests: order
   independence, "closest in time is not a tiebreaker", two claims × two
   transactions all stay suggested.
4. **[own] Settle sweep:** writes the match and the claim transition as one
   conditional UPDATE; losers roll back; the "verified" fact is recorded on the
   claim (consumed by AloBot in Phase 7).
5. **[own] Payments page:** tabs — deposits, needs review, awaiting receipt,
   parked, messaged, continuity, auto-verified (segmented purchase / renewal /
   upgrade), manually verified, rejected, all; free-text search; bank reference
   search; sidebar pending badge.
6. **[own] Review dialog and actions:** receipt image (from AloBot's
   `receipt_file_id` through the test bot's `getFile`), approve with a chosen
   transaction, verify manually (recorded as manual, never as bank-verified),
   reject, mark fake, park, reopen/revert, reassign transaction, change account,
   send a template message. Every action idempotent by client key, audited,
   two-step confirmation inside the page.
7. **[own] Continuity mode:** timed switch (5 min – 6 h), claims opened while on
   are marked FULFILLED_UNRECONCILED and get a 24 h matching window; banner on
   every page while active; expires by itself.
8. **[own] Financial stats page:** automation rate, average seconds from claim
   to bank credit, income by account and day, unmatched credits.
9. **[test-bot] Outbox sender:** Telegram `sendMessage` via httpx with a test
   bot token, shared pacing, 429 honouring, permanent-rejection detection
   (403 blocked), DEAD after 8 attempts; template messages to customers;
   receipt reminder 5 min after "I paid" with no photo (dedupe
   `receipt-nudge:<claim>`); alert to the operators' chat on `notify.dead` and
   parser failures, rate-limited by a UNIQUE key.
10. **[own] Notification bell:** unread counts per tab, recent list, seen
    state, optional sound.

Exit criteria: end-to-end demo on the copy database — seed a pending payment,
post a matching SMS, watch the claim auto-verify; post two same-amount SMS and
watch both go to review; a test asserts the invariants suite passes after every
scenario.

---

## Phase 5 — Write screens on AloBot's tables (against the copy only)

Goal: the shop can be administered from the web. All writes go to the **copy**
database through a write-capable role that exists only there; production stays
read-only until Phase 7.

1. **[copy] Catalog editor:** categories on/off, locations CRUD, plan matrix
   binding (group + price), bulk price change with a preview generated from
   the same SQL as the apply, IBSng group list (from AloBot's synced `groups`
   table, never from IBSng directly).
2. **[copy] Discount codes:** create (public/VIP, percent, category scope,
   usage limit), enable/disable, delete-if-unused, usages view; **add expiry
   date and per-user limit as new columns — recorded as an AloBot migration for
   Phase 7, not applied to the copy's schema here beyond a feature flag.**
3. **[copy] Tutorials, download links, OpenVPN profiles editors** with the
   platform/protocol validity rules copied from AloBot's `tutorials.py` and
   tested against `vendor/alobot`.
4. **[copy] Bot settings editor** (`app_config`): card details, support handle,
   channel gate, trial toggle, auto-approve toggle and delay, reminder toggle,
   report group id — from a typed registry with labels and hints; unknown keys
   refused.
5. **[copy] Bot admins page:** AloBot `admin_users` levels, reviewer allow-list.
6. **[own+test-bot] Broadcast and campaign from the web:** text or channel-post
   copy with optional button, audience preview and reach count, live progress,
   per-recipient failure list with reasons, shared pacing with the outbox,
   client-minted batch id so a double submit is one broadcast.
7. **[copy] Cron jobs page:** AloBot's scheduled tasks (reminders, reports,
   backup, health) with their switches and times as stored in `app_config`,
   plus this project's own sweeps with their thresholds.
8. **[own] Bot texts and keyboard layout editors** stored in **this project's**
   settings table (AloBot reads them only after Phase 7), with required buttons
   that cannot be removed and Bot API button `style` per button.

Exit criteria: a browser walk of every write screen against the seeded copy,
each save read back from the database not from the response; the write-guard
test still passes with every new route.

---

## Phase 6 — Hardening, demo, staging deployment beside AloBot (still isolated)

1. **[own] Browser end-to-end suite** (Playwright or the Chrome MCP) that opens
   every section, presses every write control as READ_ONLY and REVIEWER and
   expects refusal, and walks the payment scenarios of Phase 4.
2. **[own] Load test of ingest** (burst and sustained) with pool sizing
   confirmed against AloBot's connection ceiling.
3. **[own] Production image:** no dev tooling, `APP_VERSION` from the git
   commit at build time, CI-style check script.
4. **[own] RUNBOOK.md:** first-time setup, creating the first operator, relay
   phone setup (app URL, device token), rotating a device token, restore drill,
   reading the audit log, what to do when the SMS phone dies (continuity mode).
5. **[copy] Staging deployment on the production host** in its own compose
   project, pointed at a **copy** of AloBot's database restored from the
   nightly backup, behind TLS on its own hostname/port. AloBot's containers,
   network and database are not touched; the AloBot copy is refreshed by a
   script, not a live link.
6. **[own] Demo script** the owner can follow end to end on staging.

Exit criteria: owner sign-off on the staging demo.

---

## Phase 7 — AloBot integration (LAST; each task needs its own go-ahead)

Every task here changes AloBot's repo, deployment, database or token. Rules for
each: explicit go-ahead before code, AloBot's own `make test` run, no live
restart without flagging it, and a rollback path written down first.

1. **Read-only production link:** create the `dashboard_ro` role on AloBot's
   Postgres (SELECT on the listed tables only); join this project's container
   to AloBot's compose network as an *external* network (AloBot's compose file
   unchanged) or add the network to AloBot's compose — decided at go-ahead time;
   point `ALOBOT_DATABASE_URL` at production; run the compatibility check.
2. **Production bot token in the outbox** (sending only; AloBot keeps polling).
   Verify no `getUpdates` is ever issued by this project (test + grep).
3. **AloBot change — verified-by-bank approval:** AloBot's auto-approve loop
   approves a card payment when this project reports it verified (internal
   HTTP endpoint with a shared secret, same pattern as dns-switcher) instead of
   when the timer expires; the timer becomes the continuity-mode fallback,
   default off. Own migration if a column is needed. Own go-ahead.
4. **AloBot change — capture "I paid" and receipt honestly:** ensure
   `payments.created_at` is the "I paid" moment and the receipt file id is
   stored (already true today; verify and pin with a test).
5. **AloBot change — bot-side additions:** tariff screen, flood guard (N per
   minute, block and report), never-bought nudge (sent by this project's
   outbox reading `bot_users`; AloBot untouched if the timing data suffices),
   Bot API button `style` on the main menu. One PR each.
6. **AloBot change — read texts/keyboard overrides** from this project (via
   the internal API or a shared read-only table), falling back to today's
   hardcoded strings when unavailable.
7. **Write role for catalog / discount codes / tutorials / `app_config`:**
   create `dashboard_rw` scoped to those tables; flip
   `ALOBOT_DB_WRITES_ENABLED=true`; apply the discount-code expiry /
   per-user-limit migration in AloBot's repo.
8. **Retire the copy database on staging** and the test bot token; final
   restore drill; update `ALOBOT_COMMIT`.

Exit criteria: a real card payment on the live bot is approved because the bank
SMS matched, with the operator watching the review queue; the old timer is off;
AloBot's test suite green; rollback verified once on staging.
