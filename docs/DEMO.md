# Demo: the SMS pipeline and the panel, end to end

Twenty minutes, on a staging deployment, against a **copy** of AloBot's
database. Nothing here touches AloBot: the payments on screen are mirrored
read-only from the copy, and no decision this demo makes is sent anywhere.

Every step below was run through before it was written down, and what the
panel and the database actually did is what the "expect" lines say.

## Before the demo

The staging host needs: the stack running, an ADMIN operator, and
`ALOBOT_DATABASE_URL` pointing at a copy with some pending card payments in
it. `RUNBOOK.md` sections 1 and 4 cover both. If the copy is empty:

```bash
python scripts/seed_alobot_copy.py --url <superuser URL of the copy> --customers 40
```

Then prepare the demo itself:

```bash
python scripts/demo_setup.py --url https://<staging host>
```

It creates what is missing and leaves what is there: a bank account holding
the card AloBot collects on, a relay device with a fresh token, and claims
mirrored from the copy. Then it prints, for each open payment, the exact bank
SMS that would settle it. **Keep that output open; the demo is built on it.**

The token is printed once. If the device already existed, the script says so
and prints no token rather than silently cutting off a phone that is using it.

## 1. The shape of the thing (2 minutes)

Log in. Point at three things and move on:

- The **sidebar** groups the shop (customers, orders, catalog) apart from the
  money (payments, bank transactions, accounts, devices).
- The **bell** counts payments waiting on a human. That number is the whole
  point of the project.
- The badge at the bottom of the sidebar is the **git commit this host is
  running**. The same string is in `/health`.

## 2. A payment verifies itself (5 minutes)

Open **پرداخت‌ها**. The queue **در انتظار پیامک بانک** holds payments where
the customer pressed "paid" and the bank has said nothing yet. Pick one and
read its amount aloud.

Now play the bank: paste that payment's `curl` from the setup output.

```
{"ok":true,"duplicate":false,"eventId":2,"classification":"BANK_TRANSACTION",
 "actionable":true,"transactionId":2}
```

The message is classified and stored immediately. Matching is not done in that
request, on purpose: the relay phone retries on any slow answer, so the door
stays fast and a sweep decides a moment later.

Wait for the settle sweep, about 25 seconds, and refresh.

**Expect:** the payment has moved to **تایید خودکار**, verified by `matcher`,
with the candidate credit attached and the time between the customer's click
and the bank's SMS shown - 90 seconds in the prepared data. The finance page's
automation rate has gone up.

Say what just happened: **no human decided this.** The amount matched to the
rial, on the right account, inside five minutes, and exactly one payment and
one credit could have belonged to each other.

## 3. It refuses to guess (5 minutes)

Send a credit that is one rial off. Take any payment's `curl` from the setup
output and change the amount by one, for example `2,200,000` to `2,200,001`.

**Expect:** the SMS is accepted and parsed, the money appears under
**تراکنش‌های بانکی** as an unmatched credit on the right account, and the
payment stays open for a human. Nothing was auto-verified, and - this is the
part worth saying out loud - **nothing was auto-rejected either.** The matcher
never marks a payment fake. A missed auto-approval costs a click; a wrong
decision costs money.

Now send the same SMS twice, verbatim.

**Expect:** the second answer says `"duplicate": true` and no second credit
appears. The relay phone can retry as often as it likes.

Open a payment in **در انتظار بررسی** and show the row: its candidate credits,
each with its amount, its time and how far it sits from the customer's click,
and one button per credit. Approving with a credit spends it, and the same
credit is then never offered to another payment - the database enforces that,
not the screen.

## 4. When the phone dies (3 minutes)

Ask: the relay phone is lost on a Friday afternoon. No SMS arrives, nothing
can be verified, and customers are still paying.

On **پرداخت‌ها**, switch on **حالت تداوم** with a reason and a duration.

**Expect:** a banner on every page, and payments mirrored from now on recorded
as "fulfilled, not reconciled" in their own queue rather than quietly approved.
It verifies nothing. It records that a decision was made without the bank's
word, with a reason and a name against it.

Point out the two safety rails: the duration is capped at six hours and is
enforced when the setting is read, so a switch nobody turned off turns itself
off; and when the phone comes back the backlog flushes and the matcher widens
its window to 24 hours for exactly those payments.

Turn it off before moving on.

## 5. The panel is honest about what it cannot do (3 minutes)

Open **سرویس‌ها** or **تنظیمات ربات**. Both show AloBot's real catalog and
settings, and both carry a read-only banner.

Say plainly: this build can read AloBot and cannot write to it. That is not a
toggle in the UI - the database role it connects with has no write permission
on any AloBot table, and a second flag has to be on as well. Turning either on
is Phase 7 and needs its own go-ahead.

Then show **دسترسی‌ها**: three roles, and a reviewer who can decide payments
but cannot switch continuity mode or touch settings. A control a role may not
use is not drawn at all, and the server refuses it regardless of what was
drawn.

## 6. Operations, briefly (2 minutes)

- `curl -s https://<host>/health` - database, AloBot link, sweep heartbeat,
  version. It turns 503 when the sweeps stall, which is what a monitor should
  watch.
- **رویدادها** - warnings and errors, newest first, no delete control.
- The audit log is append-only in Postgres, and the restore drill fails if
  that trigger has gone missing.

## What to ask for at the end

Sign-off on this staging deployment is the exit criterion for Phase 6. After
that, Phase 7 is the only work that touches AloBot, task by task, each with
its own go-ahead: pointing at AloBot's real database, marking an AloBot
payment as verified, and the four small bot-side additions.
