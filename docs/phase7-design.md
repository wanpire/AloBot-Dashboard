# Phase 7 design — how the dashboard's four actions reach AloBot

Answers to the two questions that had to be settled before any code, with what
was actually read in `vendor/alobot` rather than assumed.

The headline: **three of the four items need no change to AloBot's code at
all.** Only the discount work does, and its diff is small enough to read in
one sitting.

---

## Question 1 — how do block, direct message and top-up reach AloBot?

The three actions turn out to have three different answers, and treating them
as one mechanism would be a mistake.

### Block and unblock: a direct write, no AloBot code

`app/bot/middlewares/blocked_user.py` runs on **every** update and asks the
database `is_blocked(session, user_id)` each time. `app/services/bot_users.py`'s
`block_user` does nothing but set that flag (creating the row if the person
never talked to the bot). There is no cache and no in-memory state.

So the dashboard setting `bot_users.is_blocked` is enforced by AloBot on that
person's very next tap, with **zero lines changed in AloBot**. What it needs is
one grant: `INSERT, UPDATE` on `bot_users` for the write role this project
already uses, which today covers ten other tables.

Two things the screen must say plainly, because they are AloBot's rules and
not ours to change:

- Admins and resellers are exempt in the middleware, so blocking one has no
  effect. The dashboard should refuse rather than pretend it worked.
- Blocking is only the bot door. It does not touch the person's existing VPN
  service, which keeps working until it expires.

### Direct message: the dashboard's outbox, with AloBot's token

A customer has only ever spoken to AloBot's bot, so a message must come from
AloBot's token. Telegram allows this from any process: only `getUpdates`
conflicts between two consumers, and this project is already forbidden from
calling it (there is a test asserting no `getUpdates` anywhere under `app/`).
AloBot polls, and nothing about that changes.

Two ways to do it:

**A. The dashboard sends, using AloBot's token.** No AloBot code at all. The
outbox this project already has does the work it was built for: dedupe keys,
backoff, `403` recognised as "the user blocked the bot", pacing shared with
broadcast.

**B. A queue table AloBot polls.** A new table plus a new background task in
AloBot, beside the three loops (`reminders`, `auto_approve`, `group_scheduler`)
that already run in `app/main.py`. It touches no existing handler: one new
module and one new line.

**Recommended: A.** The reason is the constraint itself. "Additive" code in
AloBot is still code inside the process that serves purchases, sharing its
event loop and its connection pool; a leak or an unhandled exception in a new
loop is a new way for the purchase flow to degrade. A bug in the dashboard
cannot stop a purchase, because it is a different process on a different
database connection.

What A costs, stated honestly:

- **AloBot's production token would live in the dashboard's environment too.**
  Same host, same trust boundary as AloBot's own `.env`, but two copies.
- **One Telegram rate budget, two senders.** An operator's occasional message
  is noise against Telegram's allowance. A **broadcast** is not. This project's
  broadcast feature is built and currently harmless only because it holds a
  throwaway token; pointing it at AloBot's token makes it live and able to
  compete with AloBot's own "your service is ready" messages.

  Mitigation, and it is the part worth your attention: customer-facing sends
  stay **off** by default (`notify/customers_enabled`), the dashboard's pacer
  gets a ceiling well under Telegram's, and **broadcast stays disabled until
  you approve it separately**. Operator alerts and one-to-one replies to a
  payment are what this unlocks; mass sending is a different decision.

If you would rather AloBot's token never leave AloBot, say so and I will build
B instead. It is more code in the live process, which is the trade.

### Reseller top-up: a direct write, and a race worth knowing about

`resellers.balance` is a plain `Numeric(12,2)` with no ledger behind it.

The hazard is in `app/bot/handlers/resellers.py`: a reseller purchase reads the
row, subtracts in Python, and commits.

```python
reseller = await get_reseller(session, telegram_id)
reseller.balance -= cost
await session.commit()
```

That is a read-modify-write with no row lock. A dashboard top-up landing inside
that window is overwritten and the money silently vanishes. The window is a few
milliseconds per reseller purchase, so this is unlikely rather than impossible,
and it is money, so it should be said out loud.

Three ways to handle it:

1. **Write atomically and detect.** The dashboard tops up with
   `UPDATE resellers SET balance = balance + :amount`, which cannot lose a
   concurrent *atomic* write, records every top-up in its own ledger table,
   re-reads afterwards, and raises an operator alert if the balance did not
   move by the expected amount. No AloBot change. Narrows the hazard to
   AloBot's own stale read, and makes it visible when it happens.
2. **Route through a queue AloBot applies.** No better: AloBot's own
   `add_balance` is the same read-modify-write.
3. **Lock the row in AloBot's deduction** (`SELECT ... FOR UPDATE`, or a single
   `UPDATE ... SET balance = balance - :cost`). This actually fixes it, and it
   modifies the purchase flow, so it is exactly the kind of diff you asked to
   sign off on separately.

**Recommended: 1 now, and 3 offered as its own two-line diff** for you to
accept or refuse on its own merits. I am not folding 3 into this work
unannounced.

---

## Question 2 — where do receipt images live today?

**Only as a Telegram `file_id`.** `payments.receipt_file_id` is a 256-character
string column, set from `message.photo[-1].file_id` at three points in the
payment handlers. AloBot re-sends the photo to admins by that id and never
downloads the bytes: there is no `get_file` or `download_file` anywhere in the
repository.

A `file_id` is only usable by the bot that received it, so showing a receipt in
the dashboard needs AloBot's token, one `getFile` call and one download from
`api.telegram.org`.

Options:

- **A. The dashboard fetches on demand** and streams the image through an
  authenticated route when an operator opens a payment. No AloBot code, no new
  storage, nothing added to the path a receipt arrives on. A handful of API
  calls a day.
- **B. AloBot persists images when they arrive.** This edits the receipt-upload
  path inside the purchase flow, which the constraint rules out. Rejected.
- **C. AloBot fetches on request and stores the bytes for us.** Additive to
  AloBot, but it adds a loop, a table and disk growth to buy nothing that A
  does not already give.

**Recommended: A.** Note it makes the token question above moot in one
direction: if receipts are shown at all, the dashboard holds AloBot's token
regardless, and the remaining argument for a queue-based DM is only the rate
budget, which the pacing ceiling and the broadcast switch already address.

If you reject the token in the dashboard, then receipts need C, and the
"three of four items need no AloBot code" result becomes "one of four".

---

## Question 3, which you did not ask but the answer needs — the discount diff

This is the only item that must touch AloBot's code. The shape, so the eventual
diff is predictable:

- **Migration**: two nullable columns on `discount_codes`, `expires_at` and
  `per_user_limit`, as drafted in `docs/alobot-migrations/0001_*.md`.
- **`app/services/discounts.py`**: two new predicates, and two existing
  functions each gaining one optional keyword argument.
- **`app/bot/handlers/payments.py`**: six call sites gaining one argument
  each. Three call `validate_discount_code`, three call
  `find_best_auto_discount`.

The safety argument, which is why this is worth doing at all: both columns are
nullable with no default, and both new checks return "fine" when the column is
`NULL`. Every discount code that exists today has `NULL` in both, so for all
existing data the decision the code reaches is identical to today's, line for
line. The new behaviour only exists for a code someone deliberately gives an
expiry or a per-customer cap.

One judgement call inside it, worth your view when you read the diff:
`find_best_auto_discount` decides the discounted price shown publicly before
anyone types a code. If it ignores the per-customer limit, a customer who has
used their allowance sees a price and is then refused at checkout. Filtering it
there avoids that, at the cost of three of the six call sites. I would filter.

Counting usage: the check counts rows in `discount_code_usages` rather than a
counter column, and usage is logged at **approval**, not at code entry. So the
per-customer limit inherits the existing behaviour of the total limit, where
two payments pending at once can both pass validation. That is AloBot's
existing semantics and I am not changing it.

---

## What I would do on a yes, and in what order

Every line of this is written against the **copy** of AloBot's database, as
every phase has been. Implementing Phase 7 does not point anything at
production: pointing the dashboard at AloBot's real database, and putting
AloBot's real token in it, are deployment acts that need their own go-ahead and
are tangled up with the staging deployment you deferred.

1. **Block and unblock.** Write grant on `bot_users`, the screen, the two
   exemptions stated honestly. Full dashboard suite.
2. **Receipt images.** Token config, the fetch-and-stream route, the review
   queue showing the image. Full dashboard suite.
3. **Reseller top-up.** Atomic write, a ledger of top-ups, the verification
   alert. Full dashboard suite. The `FOR UPDATE` diff written separately for
   you to accept or refuse.
4. **Discount expiry and per-user limit.** AloBot's migration, service and call
   sites, with tests, as its own change for your sign-off before it merges,
   plus AloBot's own suite. Then the dashboard's editor exposes the fields.

Then the manual walk-through: a real purchase, a renewal and a trial, by hand,
before Phase 7 is called done.
