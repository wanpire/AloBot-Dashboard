# Part 2 — Shikoonet features filtered for relevance to AloBot

Temporary working document. Basis: Part 1 inventory of github.com/Shikoonet/Shikoonet-Platform
(read 2026-09-19) checked against AloBot's actual source (~/AloBot).

## AloBot today, in one paragraph

Card-to-card and NowPayments crypto. Card payments are approved by an admin from a Telegram card,
or auto-approved after N minutes with no bank evidence at all, with a separate "autoreview" queue
for catching fraud after the fact. One card number. No wallet (the menu button is a stub). IBSng
accounts are created inline during approval. A four-tier catalog (عادی / پرایم / لوکیشن ثابت /
جونیور) with plan matrices and locations, trials per tier, resellers with balance and commission,
discount codes, invoices, tutorials and OpenVPN profiles, broadcast and campaign, three admin levels
plus a reviewer allow-list, eight report topics in a forum group, daily/weekly/monthly reports,
backups, IBSng and server health, expiry reminders, DNS admin. No web dashboard of any kind.

**Headline:** the two things Shikoonet has that AloBot lacks entirely are real bank-SMS
verification of card payments and a web dashboard. Almost everything else is either already in
AloBot or PasarGuard-specific.

---

## 1. Directly relevant

### Payment verification (the core of the new project)

| Item | Notes |
|---|---|
| SMS pipeline: Android relay app → ingest endpoint → Persian bank SMS parser → transaction candidates | Replaces the timed auto-approve. Same country, same banks, same SMS formats. |
| Auto-verify rule: exact account, exact amount, bank timestamp within 5 minutes of "I paid", isolated 1↔1 only, never auto-reject | Directly answers the fraud problem the autoreview queue exists for. |
| Claim lifecycle: "I paid" → receipt → waiting for SMS → auto-verified / needs review / parked / messaged | Adds honest states to AloBot's single "pending". |
| Manual review queue with approve, reject, mark fake, park, template message to customer, reassign transaction | Web version of the Telegram approval card. The Telegram card can stay. |
| Financial accounts, payment cards, card-prefix→bank table, SMS pattern editor, relay device management | All part of running the pipeline. |
| Receipt reminder 5 minutes after "I paid" with no photo | Small, bot-side. |
| Unknown-destination account discovery with balance-chain owner inference | Part of the parser; free once the pipeline exists. |
| Continuity mode (timed switch to keep delivering when the SMS phone is down, with those payments queued as unreconciled) | This is what AloBot's auto-approve should become: an explicit incident mode, not the default. |
| Financial statistics page (automation rate, average claim-to-SMS seconds, income vs bank balances) | Only meaningful with the pipeline. |

### Dashboard sections that map straight onto AloBot's existing tables

| Item | Notes |
|---|---|
| Overview page with headline numbers and "queues waiting on a human" | Payments pending, receipts missing, IBSng health. |
| Shop stats (period figures separated from running totals) | AloBot's daily/weekly/monthly reports, on a screen. |
| Customers: search, card, block/unblock with consequences named, direct message, history, their VPN accounts | `bot_users` + `vpn_users`. |
| Orders and subscriptions lists with filters, invoice search | `payments` + `vpn_users` with IBSng expiry. |
| Resellers: balance, commission, top-up, history | AloBot's own reseller model. |
| Catalog: categories, locations, plan matrix, prices, IBSng group binding and sync, bulk price change with preview | `services`, `service_locations`, `groups`. |
| Discount codes: create, expire, enable, view usages; add expiry date and per-user limit | AloBot has codes with category scope, usage limit, public/VIP; expiry and per-user limit are missing. |
| Bot content: tutorials (platform → protocol → guide), download links, OpenVPN profiles, required channel | Web forms instead of 1,151 lines of Telegram FSM. |
| Bot texts and keyboard layouts editable from the dashboard | AloBot texts are hardcoded; requires an AloBot change later. |
| Cron jobs page: every scheduled task with its switch and thresholds | Reminder toggle, auto-approve delay, report times, backup time. |
| Settings page from a typed list of live keys with labels and hints | AloBot's `app_config` already is a key/value table. |
| Access: dashboard operators (admin / reviewer / read-only) and bot admins (support / sales / full) with per-action permissions | Extends AloBot's three tiers. |
| Broadcast and campaign from the web with live progress, failure list with reasons, and rate pacing | AloBot has both flows in Telegram; progress and failures are new. |
| Events page: structured app events, filters, copy as JSON, 30-day prune | Web view of AloBot's "live logs" topic, plus errors. |
| Login with password, optional TOTP, lockout, HttpOnly session, in-panel password change | Needed for any dashboard. |
| Notification bell with counts and sounds, sidebar pending badges, Persian digits and Jalali dates, responsive layout | Table stakes. |

### Bot-side additions (small, each needs an AloBot change)

| Item | Notes |
|---|---|
| Tariff screen (whole price list in one message) | Trivial. |
| Flood guard: N messages per minute → block and report | AloBot has block but no rate guard. |
| Nudge: "started the bot but never bought after N days", once ever | AloBot logs bot starts; this closes the loop. |
| Bot API button colours (`style`) | Button colour requests keep coming; the API now supports it natively. |

### Backend patterns to adopt in the new project

| Item | Notes |
|---|---|
| Outbox for every customer notification: dedupe key, retry with backoff, dead state, detection of "user blocked the bot" | AloBot sends directly and loses messages on Telegram hiccups. |
| Order/claim transitions as conditional UPDATEs with sweeps, not callbacks; idempotent provisioning | IBSng create is inline in approval today; if IBSng is down the approval fails in front of the admin. |
| Dashboard reads the database only; the bot is the only process that talks to IBSng and Telegram | Keeps the dashboard fast and keeps one IBSng client, which is also AloBot's own CLAUDE.md rule. |
| Money guarantees in partial unique indexes, not application code | One SMS settles one payment, one payment settles once. |
| Structured JSON logs with a redaction deny-list, alerts through the outbox, rate-limited by a UNIQUE key | Zero new dependencies. |
| Fail-closed environment name, restore drill, invariant SQL run in tests | Cheap, and the project handles real money. |
| Per-route write guard counted by a test over all routes | Prevents the "route 115 added without a guard" failure. |

---

## 2. Unsure — decision needed

| Item | What it is | Why it is a decision |
|---|---|---|
| Multiple destination cards with a rotating queue and per-invoice hold | Each invoice names a card, cards rotate, a card is held for N minutes | AloBot has one card. Matching is only exact when two buyers cannot get the same amount on the same card within 5 minutes. With one card, same-amount collisions go to manual review. Business decision: add cards or accept collisions. |
| The SMS relay phone itself | An Android phone with the bank SIM running the open-source relay app, posting to our ingest | Not code. Someone has to own that phone. Without it, nothing in the "payment verification" block works. |
| Wallet as an append-only ledger | Top-up, pay from wallet, refunds, gift codes, admin adjustments with audit | AloBot's wallet is a stub. Shikoonet's design is a ready blueprint, but it is a large AloBot-side feature, not a dashboard feature. |
| Bulk wallet credit | Credit every customer at once | Only if wallet. |
| Referral program with 10 percent of first purchase | Invite link, commission via ledger, referrals page | AloBot has none. Needs wallet or reseller-style balance to pay out. |
| Reseller self-application from the bot, approved from the dashboard with template messages | AloBot adds resellers manually | Small, but changes who can become a reseller. |
| Per-customer standing discount percent | Applied to every order | Fits AloBot's VIP codes idea; unclear if wanted. |
| Expenses ledger and bank books (monthly statement per account against the bank's balance, off-books tagging) | Accounting on top of the SMS feed | Only useful if you want the dashboard to be the shop's books, not just its verifier. |
| Stock shelf | Pre-made accounts sold when the panel is down | IBSng creates accounts on demand; a shelf of pre-created IBSng users could cover IBSng outages, which AloBot already monitors. |
| Delete expired accounts from IBSng after N days, with a report-only dry-run mode | AloBot's IBSng client has `delete_user` | Policy: do you want the bot deleting accounts at all. |
| Move expired accounts to a fallback IBSng group | "Expired" behaviour customers can see | Depends on whether IBSng expiry already does what you want. |
| "Bought but never connected" reminder | Needs last-connection data from IBSng | Depends on what `get_user_info` returns. |
| Usage bar and low-volume warnings | Volume-based | Only if your IBSng groups are traffic-capped rather than time-only. |
| Customer-controlled service on/off | Maps to IBSng `lock_user` | Small; unclear if wanted. |
| Per-action admin permissions (12 checkboxes) instead of tiers | Finer than support/sales/full | Nice, not urgent. |
| Persistent reply keyboard and premium emoji on buttons | Cosmetic | Premium emoji needs the bot owner to have Telegram Premium. |
| DNS switcher section in the dashboard | Not in Shikoonet; AloBot has a DNS module | Natural extra section, but it would make the dashboard call dns-switcher, breaking the "reads DB only" rule. |
| Money stored as integer rials | AloBot stores Numeric Toman | Changing it touches AloBot's schema. Leave AloBot alone; the new project can store rials internally. |

---

## 3. Not relevant

- Everything PasarGuard-specific: group ids and inbounds, subscription links and link regeneration, panel groups, hidden users, syncing links and usage every 10 minutes, QR codes, "configs shown or link only", on_hold trial accounts, renewal modes ADD vs RESET, panel preflight and openapi tools, multi-panel management with sealed passwords. AloBot has one IBSng and credentials are usernames and passwords.
- Generic product kinds (VPN, AI account, Spotify, manual) and the adapter registry. AloBot sells one thing through one client.
- Add volume, add time, bonus-GB and bonus-percent discount codes, per-plan bonus volume. Volume is a PasarGuard concept.
- Reseller tiers as franchises with a metered PasarGuard admin. AloBot resellers are balance and commission.
- Username naming modes. AloBot already lets buyers pick a username.
- Renewal auto-match, trial per panel, renewal cashback. AloBot has its own renew, upgrade, and trial flows.
- Report topics and nightly report. AloBot already has eight topics and three report cadences.
- Mirzabot MySQL import, undo, legacy settings vocabulary, Faoxima comparisons, the whole `legacy/` directory.
- Telegram exactly-once machinery (update claims, advisory-lock singleton, poison-update dead letters). AloBot runs on aiogram with Redis FSM; different runtime.
- Payment gateways. AloBot already has NowPayments; Shikoonet declined gateways.
- Support tickets, lottery wheel, mini-app. Declined on their side too.
- Coolify, GitHub Actions gates, CodeRabbit, draft-first PR policy, schema ledger with sha256, image stripping. Process and their hosting, not product.
- Cloudflare tunnel history, D1 to Postgres port, hub-cloudflare.

---

## Two notes before deciding

1. Nothing in the "directly relevant" block requires changing AloBot's running deployment to start:
   the SMS pipeline, review queue, and every dashboard page can be built and tested against a copy of
   AloBot's schema. The one integration point (marking an AloBot payment as verified) can be wired
   in a later, explicitly flagged phase.
2. Items marked "needs an AloBot change" are small and additive, but they are AloBot commits, so
   they will sit in their own phase in the Part 4 plan.

**Next:** reply with which unsure items are in, which directly relevant items to cut, and Part 3
(sibling project skeleton) follows.
