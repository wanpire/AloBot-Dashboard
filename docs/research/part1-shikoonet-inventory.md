# Part 1 — Shikoonet-Platform complete feature inventory

Temporary working document. Source: github.com/Shikoonet/Shikoonet-Platform cloned 2026-09-19
(latest push 19:04 UTC). Read in full: docs/STATUS.md (3,844 lines), docs/schema-design.md,
CLAUDE.md, docs/admin-panel-roadmap.md, docs/threat-model.md, docs/configuration-matrix.md; plus
nav.ts, all dashboard API routes, every bot module header, the contracts package (settings,
permissions, cron jobs, report topics, product kinds), migration headers 0001–0082, and the GitHub
issue tracker (4 open, 27 recently closed).

## What the repo is

Four apps in a pnpm monorepo (Node 24 / TypeScript, Postgres 16, 82 migrations):

- `apps/bot` — Telegram bot, long polling, every background sweep runs inside its ~25 s poll loop.
- `apps/ingest-worker` — the only public surface: `POST /api/v1/sms` from an Android SMS relay app.
- `apps/dashboard-worker` — Hono API plus operator login; serves the SPA.
- `apps/admin-web` — React admin panel, 29 sections in 6 sidebar groups.

Seven shared packages: contracts, database, db (Postgres adapter), domain, migrate (MySQL/D1 →
Postgres), seed, sms-parser. Provisioning target: PasarGuard (Marzban fork) panels. Only adapters
that exist: PasarGuard and manual/shelf, although the schema allows more kinds.

Note on freshness: STATUS.md says "last updated 2026-08-22" but carries annotations to 2026-09-18
and migrations run to 2026-09-19. Everything below was cross-checked against source, route lists,
migration headers and the live tracker. The tracker has only 4 open issues today.

---

## A. Bot-side features (inside Telegram)

### Entry and gates
- `/start` with referral payload, user upsert, last-seen tracking.
- Required-channel membership gate (fail-open by design), rules-acceptance gate, "registration open" switch, "shop open" switch, blocked-user gate that still lets a blocked customer send a receipt or press "I paid".
- Flood guard: N messages per minute (default 35, configurable) blocks the user, tells them, reports to the shop group. Admins exempt.

### Menu and presentation
- Inline main menu matching the legacy layout; every keyboard's layout, order, labels and colours editable from the dashboard, with required buttons that cannot be removed.
- Every sentence the bot says is an overridable text key with placeholders.
- Button colours via Bot API 9.4 `style`; premium custom emoji on buttons and in texts; in-bot admin flow for choosing emoji from packs.
- Persistent reply keyboard for navigation. Persian only, Persian digits, Jalali dates.

### Purchase flow
- Category → service (tier) → plan, with badges ("new", "off") and colours per row, admin-controlled row breaks, a shop-wide plan-label template.
- Tariff screen: whole price list in one message.
- Pricing per customer tier (`f`/`n`/`n2`), per-user standing discount percent, reseller-only products.
- Discount codes: percent off, fixed amount off, bonus GB, bonus percent volume, gift codes to wallet. Scopes: product, panel, buy vs renew, first purchase only, resellers only, expiry, max uses, uses per user.
- Per-plan bonus volume percent set by the admin (stacks with codes).
- Order placement with duplicate-tap protection, zero-price plans refused, out-of-stock refused for shelf products.
- Username naming modes for the panel account (customer-typed prefix; panel text + telegram id + purchase number).

### Checkout and payment
- Card-to-card with a rotating card queue ("bakery queue": a card that received money goes to the back), card held N minutes per invoice, invoice expiry tied to the card hold, expired invoice message edited in place.
- Wallet share subtracted at invoice time (price minus balance) so the expected bank amount is exact.
- Copy buttons for card and amount (switchable), "I paid" opens a claim, receipt photo required, one reminder after 5 minutes without a receipt, photo sent before "I paid" is kept.
- Pay fully from wallet.
- After verification: settle → provision → "service ready" with subscription link, locally generated QR image, configs inline or link only (switch), app-download button, per-service delivery note.

### Wallet
- Ledger-based balance, top-up via card-to-card with min/max, gift code redemption, purchases from wallet, referral commission credits, renewal cashback percent, refunds on failed delivery or expired invoice.

### My services
- Paginated list, detail card with text usage bar, derived status, link withheld when dead, QR button.
- Actions: regenerate subscription link, turn service off/on (switchable), add volume, add time (typed number, priced per tier).

### Renewal
- Pick a service, auto-redirect to the matching plan when unambiguous, else the panel's plan list. ADD vs RESET mode per panel, discount codes, cashback, idempotent via a note on the panel account, half-applied renewals recorded, works for disabled and retired-panel services.

### Free trial
- Per-panel trial size, per-user quota, created `on_hold` on the panel and flipped active on first connection.

### Support, help, referral, reseller
- Support: "message this handle" (no ticketing, by decision).
- Help articles and client-app list from dashboard content tables.
- Referral: invite link, 10 % of the referred customer's first purchase to the referrer, summary screen.
- Reseller application with one open request per user; applicant messaged from the dashboard.

### Shelf delivery
- For products with no automated panel (AI accounts, Spotify, OpenVPN, manual) a shelf of pre-made accounts or links is the delivery; a row is held for its invoice; no stock means no charge. For automated panels it is a fallback after 10 minutes of panel failure.

### Background sweeps (inside the poll loop, each switchable from the dashboard)
- Settle verified payments, provision paid orders, reclaim stalled provisioning.
- Sync usage and subscription link from every panel every 10 minutes (never writes status or expiry from the panel).
- Expiry warning (days), low-volume warning (GB), "bought but never connected" reminder, "started but never bought" nudge.
- Downgrade an ended account onto a fallback group so the link still works and shows "expired".
- Remove expired / remove volume-exhausted accounts from the panel, with a report-only dry-run mode defaulting on.
- Expire unpaid invoices, receipt reminder, reseller meter reading.
- Nightly report after the Tehran day actually ends. Broadcast drain with shared pacing and 429 handling.

### Reports to a Telegram group with topics
- Ten topic kinds (purchases, renewals/add-ons, payments, other, trials, errors, referral commissions, nightly, cron notices, backup placeholder); topic ids in settings; all sent through the outbox.

### In-bot admin panel
- `/panel` (silent for non-admins). Roles OWNER / ADMIN / SUPPORT with 12 per-action permissions: view claims, approve with transaction, approve without transaction, reject, view stats, look up user, adjust wallet, block, set discount, message user, bulk credit, bulk message. Every action audited.

---

## B. Dashboard-side features (29 sections, 6 groups)

### Reports
- Dashboard: six headline numbers plus the queues waiting on a human.
- Shop stats: stocks and flows separated, period filter.
- Financial stats: bank-side analytics, automation rate, average seconds from claim to bank transaction.

### Customer and sales
- Customers: server-side pagination and search; card with wallet adjustment (idempotency key, preview, reason, negative allowed with confirmation), block/unblock with consequences named, standing discount with before/after, direct message, make reseller, history.
- Referrals: one row per referrer with counts and earnings, expandable.
- Orders, Subscriptions (usage column, "not yet read" distinct from zero), Transactions (wallet ledger).
- Requests: reseller applications, approve/reject once, message templates, "new" badge.
- Resellers: franchises with tiers and per-tier discount, meter readings, status.
- Bulk: broadcast text or copy a channel post (photos, formatting, buttons), live progress, failure list with reasons, bulk wallet credit, trial-quota reset, reach count.

### Catalog
- Panels: add/edit with sealed password, test connection, read groups and inbounds from the panel, create groups, move members, hidden users, honest status.
- Services and plans: create/edit/delete/merge/status, product kinds with kind-specific fields, badges, colours, row layout, per-plan bonus volume, bulk price change with preview from the same SQL.
- Categories with badges and layout.
- Discount codes: create, expire, enable/disable, redemptions.
- Stock shelf: single row or bulk CSV, retire, per-plan counts including empty shelves, secrets visible to ADMIN only.

### Money (payment hub)
- Payments tabs: deposits, needs review, awaiting receipt, parked, messaged, continuity, bot auto-verified (segmented new / renewal / top-up), manual verified, declined, all. Free-text search, bank reference search, customer type.
- Review dialog: receipt image, wallet share, approve, reject, verify manually, mark fake, park, template message, change account, reassign transaction, reopen/revert manual verification, fulfil without payment, retry provisioning.
- Today. Transactions: decline/restore income (single, bulk, all), assign account, classify as reseller income, create account from unknown transaction, assignment history, comments.
- Expenses: typed ledger (expense / reseller income / correction), categories, foreign currency with rate, recurring bills, paying account and bank fee, void, CSV, history.
- Bank books: monthly statement per account against the bank's own balance, off-books tagging, manual movements the bank never texted, opening balances, CSV.
- Accounts: lifecycle (accept, decline, mute, deactivate, restore), identifiers, backfill, rerun assignment with preview, move references, payment cards with holder name and per-card analytics.
- Banks: card prefix → bank, bank SMS patterns (additive only, sandboxed regex test), Luhn test, SMS test.
- Devices: relay phones, credentials shown once, rotate/revoke with confirmation, rename, deactivate, move references, delete preview.
- Continuity mode: timed switch (max 6 h) to keep delivering when the SMS channel is down.

### Bot
- Bot: connect a token from the UI, live username vs configured, report group and topic creation.
- Texts, Keyboard layouts (per menu, reset), Content (help articles, apps, required channels), Cron jobs (switches, thresholds, dry-run).

### System
- Settings: only live keys, each with label, hint and exact on/off vocabulary; imported keys read-only; secrets refused.
- Access: panel operators (ADMIN / REVIEWER / READ_ONLY) and bot admins with per-permission checkboxes; self-demotion and last-admin guards.
- Events: structured app events with filters and copy-as-JSON, admin only, 30-day prune.
- Import: Mirzabot MySQL dump import with check → dry run → apply, live progress, undo.

### Cross-cutting
- Login with scrypt password, optional TOTP, lockout inside the UPDATE, HttpOnly session, in-panel password change, origin guard, per-IP login rate limit.
- Role-aware UI: sections and write buttons hidden per role, enforced server-side and counted by a test over all 114 write routes.
- Notification bell with counts, sounds, seen state; sidebar badges; command palette; responsive card layouts; version badge; SMS coverage and unparsed list with reparse dry-run.

---

## C. Backend and architecture patterns

- **SMS auto-verify pipeline.** Android relay → `POST /api/v1/sms` (device token in body, stored hashed, per-device and per-IP rate limits, body cap, dedupe over device+sender+timestamp+normalised body) → parser registry (14 Persian bank parsers plus additive DB patterns, OTP redaction, direction, amount, account identifier, balance, reference) → transaction candidate → matcher. Auto-verify only for an isolated 1↔1 claim/transaction pair: exact account, exact amount, bank timestamp within 5 min of "I paid" (24 h for continuity-fulfilled claims), never auto-reject, 10 min WAIT after receipt, everything else to manual review. Uniqueness enforced by partial unique indexes. Unknown destination numbers create a pending account and infer the owner from the bank's balance chain.
- **Generic product model.** `provisioning_providers(kind, config jsonb, sealed secret)` → `products` → `product_plans` → `orders` → `subscriptions`. One adapter interface (provision, renew, act, sync) per kind; `manual` fallback; account name derived from order public id for idempotent retries; preflight tool for group ids.
- **Money in the schema.** All amounts bigint IRR; wallet balance is a trigger-derived sum of an append-only ledger with idempotency keys; CHECK ties order totals; `audit_logs` append-only via trigger.
- **Order state machine driven by sweeps.** AWAITING_PAYMENT → PAID → PROVISIONING → COMPLETED/FAILED, every transition a conditional UPDATE, one subscription per order by index, losers roll back.
- **Telegram exactly-once.** Update claimed in the same transaction as its effects; replies after commit; poison updates dead-lettered before ack; one poller per token via advisory lock; heartbeat file for health check; bot exits when the dashboard changes the token.
- **Outboxes.** `bot_notifications` with dedupe keys, backoff, DEAD state, permanent-rejection detection; `webhook_deliveries`; broadcasts with SENDING state; `WITH … AS MATERIALIZED` to make LIMIT a real cap.
- **Settings as a typed contract.** One `(scope, key, value)` table; one list declares live keys, labels, and exact on/off strings; bot, API and form derive from it. "Failed to read" distinguished from "admin chose zero".
- **Dashboard never calls a panel.** The bot syncs; the dashboard reads rows.
- **Card queue.** FIFO on money landing, hold per invoice, saturation hands out the soonest-free card.
- **Two-layer RBAC.** Panel operators (prefix read guard + per-route write guard) and bot admins (per-action permission map, owner always allowed).
- **Observability with zero new dependencies.** JSON logger with redaction deny-list, `app_events`, Telegram alerts through the outbox rate-limited by a UNIQUE key, request ids, `/version` from the build commit.
- **Operations.** Schema ledger with sha256 drift detection; containers refuse to boot on a stale schema but allow rollback; one-shot migrate container; restore drill that runs the invariant suite; fail-closed `ENV_NAME`; production image stripped of dev tooling and checked by CI; Playwright walks every section; import tool with dry run and undo.

---

## D. Not counted as built (their own docs or tracker say so)

Support tickets and departments (declined) · lottery wheel and dice (declined) · payment gateways
beyond card-to-card (declined) · mini-app (not chosen) · PNG usage card (text bar chosen) · second
language · per-card daily/monthly cap (deferred) · incident queue with ownership · replaying the
dead-letter queue · shelf-empty alert · hiding out-of-stock products · re-showing a sold password ·
file attachments on delivery (open #377) · personal dashboard accounts with per-section access
groups and TOTP from the UI (open #363) · linking an unmatched deposit to an expired same-card
same-amount invoice (open #275) · real-send proof of channel-post forwarding (open #94) · adapters
for classic Marzban, Marzneshin, Hiddify, X-UI, WireGuard (schema allows, none written) · off-site
backups.
