# Scope audit — every "directly relevant" item, and where it actually is

`docs/research/part2-relevance-report.md` is the agreed scope: its "directly
relevant" list, and nothing from "unsure" or "not relevant". This walks that
list item by item and says where each one lives, or why it does not exist yet.

Three verdicts are used:

- **Built** — working, with tests, against this project's own database or a
  copy of AloBot's.
- **Phase 7** — cannot be built without touching AloBot, so it waits for the
  final phase and its own go-ahead.
- **Out by design** — in the reference project, deliberately not here, with
  the reason.

## Payment verification

| Item | Verdict | Where |
|---|---|---|
| SMS pipeline: relay app → ingest → parser → transaction candidates | Built | `app/api/ingest.py`, `app/sms/` |
| Auto-verify rule: exact account and amount, five minutes, isolated 1↔1, never auto-reject | Built | `app/services/matcher.py` |
| Claim lifecycle with honest states | Built | `app/models/claims.py`, `app/services/claims.py` |
| Review queue: approve, reject, mark fake, park, template message | Built | `app/services/review.py` |
| Review queue: **reassign / attach a transaction** | Built | the row now offers the nearby unspent credits (`review._attachable`). This was missing until the audit: the matcher only suggests what fits its rule, so a payment that fell outside it could only be verified with no transaction at all, which settles the payment and leaves the credit unclaimed for ever. |
| Financial accounts, cards, prefix→bank table, pattern editor, device management | Built | `app/web/routes/pipeline.py` |
| Receipt reminder five minutes after "I paid" with no photo | Out by design | AloBot creates the payment row only when the receipt photo arrives, so "paid but no receipt" cannot occur. Recorded as a Phase 4 deviation. |
| Unknown-destination discovery with balance-chain inference | Built | `app/services/ingest.py` |
| Continuity mode | Built | `app/services/continuity.py` |
| Financial statistics: automation rate, claim-to-SMS seconds, **income vs bank balances** | Built | `app/services/finance.py`. The balance half was missing until the audit: every bank SMS carries the account balance and it was stored but never shown, so there was nothing to check the counted income against. |
| The receipt image itself | Phase 7 | only AloBot's bot token can fetch that file from Telegram. |

## Dashboard sections

| Item | Verdict | Where |
|---|---|---|
| Overview with headline numbers and queues waiting on a human | Built | `app/web/routes/alobot_pages.py` |
| Overview: IBSng health | Out by design | this project never calls IBSng. AloBot's own CLAUDE.md makes its client the only caller, and that rule is kept. |
| Shop stats, period figures apart from running totals | Built | the stats page |
| Customers: search, card, history, their VPN accounts | Built | the customers and customer pages |
| Customers: block/unblock, direct message | Phase 7 | both write to AloBot: blocking updates `bot_users`, which is not in the ten tables the write role may touch, and a direct message needs AloBot's own bot token. |
| Orders and subscriptions with filters and invoice search | Built | the orders and subscriptions pages |
| Resellers: balance, commission, history | Built | the resellers page |
| Resellers: top-up | Phase 7 | writes to AloBot's `resellers`, outside the write role's scope. |
| Catalog: categories, locations, plan matrix, prices, group binding, bulk price change with preview | Built | `app/alobot/writes/catalog.py` |
| Catalog: IBSng group sync | Out by design | same rule as above; the dashboard reads databases and never calls IBSng. |
| Discount codes: create, expire, enable, usages | Built | `app/alobot/writes/discounts.py` |
| Discount codes: expiry date and per-user limit | Phase 7 | AloBot's table has no such columns. The migration is written down and deliberately not applied: `docs/alobot-migrations/0001_*.md`. |
| Bot content: tutorials, download links, OpenVPN profiles, required channel | Built | `app/alobot/writes/content.py` |
| Bot texts and keyboard layouts editable from the dashboard | Built here, read in Phase 7 | stored in this project's `settings`; AloBot reads them only after the integration phase. |
| Cron jobs page with switches and thresholds | Built | `app/alobot/writes/cron.py` |
| Settings from a typed registry | Built | `app/core/settings_registry.py` |
| Access: dashboard operators, and AloBot's bot admins | Built | `app/web/routes/access.py`, `app/alobot/writes/admins.py` |
| Access: per-action permissions | Out of scope | parked by the owner in the "unsure" list. |
| Broadcast and campaign with progress, failures and pacing | Built | `app/services/broadcast.py`, `app/services/pace.py` |
| Events page with filters, copy as JSON, pruning | Built | `app/web/routes/events.py` |
| Login with password, optional TOTP, lockout, HttpOnly session, password change | Built | `app/services/auth.py` |
| Bell **with counts and sounds**, sidebar badges, Persian digits, Jalali dates | Built | the count was only correct at page load until the audit. The shell now polls `/bell`, updates the badge and the tab title, and beeps when the number grows. The beep is built on the operator's first click, because browsers refuse to make noise before that, and a browser that will not beep still shows the number. |
| Responsive layout | Built | sixteen sections pushed a phone screen sideways until the audit, the payments queue by 755px, which put the row actions off the edge. Cards scroll their own wide content now, and a browser test walks every section at 390px. |

## Backend patterns

Every pattern in the report is in place and has a test: the outbox with dedupe
keys and backoff, conditional-UPDATE transitions in sweeps, the dashboard
reading databases only, money guarantees as partial unique indexes, structured
JSON logs with redaction, rate-limited alerts, fail-closed `ENV_NAME`, the
restore drill, the invariants SQL in the suite, and the write-guard walk over
every route.

## Bot-side additions

The four small ones — tariff screen, flood guard, never-bought nudge and
button colours — are AloBot changes by definition and are Phase 7 tasks.

## What this audit changed

Four gaps, all found by reading the agreed scope against the code rather than
by anything failing:

1. A credit the matcher did not suggest could not be attached to a payment.
2. The bank's own reported balance was stored and never shown.
3. The bell's count was correct only at page load, and never made a sound.
4. Sixteen sections overflowed a phone screen.

Everything else on the "directly relevant" list is either built or blocked on
Phase 7, and nothing on it was dropped silently.
