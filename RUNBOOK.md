# Runbook

Day-to-day operation of `alobot-dashboard`: setting it up, pointing a relay
phone at it, keeping it healthy, and what to do when something goes wrong.

The panel itself is Persian; this file is for whoever runs the host.

**Before anything else:** this project does not touch AloBot. It reads a
**copy** of AloBot's database and writes only its own. Nothing here restarts
AloBot, changes AloBot's code, or marks an AloBot payment as anything. That
integration is Phase 7 of `docs/PLAN.md` and needs the owner's explicit
go-ahead, task by task.

---

## 1. First-time setup

```bash
git clone <this repo> alobot-dashboard && cd alobot-dashboard
cp .env.example .env
```

Fill in `.env`. Three values have no defaults and the process refuses to start
without them:

| Variable | What it is |
| --- | --- |
| `ENV_NAME` | one of `local`, `test`, `staging`, `production` |
| `DATABASE_URL` | this project's own Postgres |
| `SESSION_SECRET` | 32+ random characters (`openssl rand -hex 32`) |

Leave `ALOBOT_DATABASE_URL` blank until you have a copy of AloBot's database
(section 4). Leave `ALOBOT_DB_WRITES_ENABLED` at `false`: the catalog and
settings screens then render read-only and say so.

```bash
make up                       # builds and starts, stamping the git commit as the version
make migrate                  # applies the migrations
docker compose exec dashboard python scripts/create_operator.py \
    --email you@example.com --name "Your Name" --role ADMIN
```

The password is read from stdin, never from an argument, and must be at least
12 characters.

The app listens on `127.0.0.1:8090` and nothing else. Put nginx or Caddy in
front of it for TLS. The relay phone posts to the same hostname, so that
certificate has to be real, not self-signed.

Check it is up:

```bash
curl -s localhost:8090/health
```

`version` is the git commit the image was built from, and the same string
appears in the panel's sidebar. If they disagree, someone deployed by hand.

## 2. Roles

| Role | Can |
| --- | --- |
| `ADMIN` | everything, including settings, access, devices and continuity mode |
| `REVIEWER` | decide payments, read the rest |
| `READ_ONLY` | read |

A role is given when the operator is created and changed on the access page.
The panel never draws a control a role may not use, and the server refuses it
regardless of what the page drew.

## 3. Backups and the restore drill

A backup nobody has restored is not a backup.

```bash
docker compose exec dashboard scripts/restore_drill.sh
```

It dumps the live database, restores it into a throwaway database beside it,
checks the migration head matches this checkout, runs the money invariants,
and drops the throwaway. It never writes to the live database. Run it after
every deployment and on a schedule.

The Postgres client in the image is pinned to major 16 to match the server. A
newer `pg_dump` emits settings an older server rejects, which is how this
drill broke the first time.

## 4. Pointing at a copy of AloBot's database

The dashboard reads AloBot through a `SELECT`-only role, and before Phase 7
only ever from a **copy**.

```bash
scripts/copy_alobot_db.sh /path/to/alobot-backup.zip
```

It restores AloBot's nightly backup into a local database and creates the
read-only role. It refuses any host that is not local, on purpose. Then set
`ALOBOT_DATABASE_URL` to that copy and restart.

`/health` reports `alobot_db` as `ok`, `unconfigured`, or `incompatible` with
the missing column named. `incompatible` means AloBot's schema moved: update
`ALOBOT_COMMIT`, run `make check-alobot-pin`, and re-run the suite before
trusting any AloBot-backed screen.

Refresh the copy by re-running the script with a newer backup. There is no
live link, by design.

## 5. The relay phone

The phone forwards every bank SMS to this project. One phone, one device
record, one token.

1. In the panel, open **دستگاه‌ها** and create a device. Give it the code the
   phone app sends in its `deviceId` field.
2. The token is displayed **once**. Copy it into the phone app now; it is
   stored only as a hash and cannot be shown again.
3. Point the app at `https://<your host>/api/v1/sms`, POSTing JSON:

```json
{
  "apiKey": "<the device token>",
  "deviceId": "phone-a",
  "message": "<the SMS body, verbatim>",
  "sender": "<the SMS sender>",
  "timestamp": "1789999999000"
}
```

The token goes in the body, not a header: that is the relay app's contract.
Field names are matched loosely (`api_key`, `device_id`, `body`, `from` and
similar all work), so most off-the-shelf SMS-forwarder apps can be pointed at
it without modification.

Answers the phone can get:

| Code | Meaning | What the phone should do |
| --- | --- | --- |
| 200 | stored (`duplicate: true` if it had already arrived) | move on |
| 400 | the body was not the expected JSON | fix the app's template |
| 401 | unknown device, wrong token, revoked token, or disabled device | re-issue the token |
| 413 | body over the size cap | nothing; a bank SMS is never this large |
| 429 | per-device or per-IP rate limit | back off and retry |
| 503 | the server is saturated | retry after the `Retry-After` seconds |

Confirm it works by sending yourself a real bank SMS and watching it appear
under **تراکنش‌های بانکی** within seconds. A message no parser understood is
listed there under **خوانده‌نشده‌ها**, with its text and a coverage table per
sender; that view is where a new bank's format gets noticed.

**OTP messages are detected and never stored.** Promotional messages are
classified and ignored. Only bank transaction messages become candidates.

### Rotating or revoking a token

Both live behind **عملیات** on the device's row and both need the confirmation
box ticked, because both stop the phone immediately:

- **Rotate** issues a new token and kills the old one the same instant. The
  phone sends nothing until you paste the new token into it.
- **Revoke** kills the token without issuing another. Use it when a phone is
  lost.

Rotate on a schedule, and whenever a token has been pasted anywhere it should
not have been.

## 6. Daily operation

The bell in the top bar counts payments waiting on a human. The queues are on
the **پرداخت‌ها** page:

- **در انتظار پیامک بانک** — the customer pressed "paid", the bank SMS has
  not arrived. The matcher waits 10 minutes before calling it suspicious.
- **در انتظار بررسی** — the matcher would not decide alone: two claims for
  one credit, an amount that does not match, a credit outside the window.
  Each row shows its candidate credits with the time difference.
- **تایید خودکار** — matched and verified with no human involved. The
  finance page reports what share of payments this is.

The rule the matcher follows: it auto-verifies only an isolated one-to-one
pair, same account, same amount to the rial, within five minutes. **It never
auto-rejects and never marks anything fake.** A missed auto-approval costs a
click; a wrong one costs money.

A credit that has already settled a payment is never offered to another one.
The database enforces this, not the screen.

## 7. When the SMS phone dies

If the relay phone is lost, broken or offline, no bank SMS arrives, so nothing
can be verified and every payment piles up in **در انتظار پیامک بانک**.

**Continuity mode** (ADMIN only, on the payments page) says "keep serving
customers, reconcile later". While it is on, mirrored payments are recorded as
`FULFILLED_UNRECONCILED` and a banner appears on every page.

- It takes a reason and a duration between 5 minutes and 6 hours. The expiry
  is enforced when the setting is read, so a forgotten switch turns itself
  off.
- It does not verify anything. It records that a decision was made without
  bank evidence, and those payments stay in their own queue.
- When the phone is back: the SMS backlog flushes, the matcher extends its
  window to 24 hours for these claims, and what it cannot match stays for a
  human.

Turn it off as soon as the phone is back. Every switch is audited with its
reason and who threw it.

## 8. Reading what happened

- **رویدادها** (ADMIN) shows warnings and errors the app raised, newest
  first, copyable as JSON. There is no delete control.
- The audit log records every operator action with who, when, before and
  after. It is append-only: a Postgres trigger refuses updates and deletes,
  and the restore drill fails if that trigger is missing.
- Application logs are JSON, one line per event:
  `docker compose logs -f dashboard`. Tokens, passwords, session secrets and
  raw SMS bodies are redacted by key name at any depth.

Events worth alerting on:

| Event | Means |
| --- | --- |
| `ingest.saturated` | the pool could not keep up and messages were shed as 503 |
| `ingest.unauthorized` | a token was wrong; repeated, it is someone probing |
| `audit.unrecorded` | an AloBot edit committed but its audit row did not |
| `settle.lost_race` | two sweeps met on one claim; harmless, one won |

Set **چت هشدارهای سیستم** in settings to a Telegram chat id to have operator
alerts delivered there. Customer-facing messages are off by default and stay
off until Phase 7, because AloBot still talks to the customer itself.

## 9. Health and what breaks

`curl -s localhost:8090/health` answers with `ok`, the environment, the
version, the database, the AloBot link and the sweep heartbeat.

| Symptom | Cause | Do |
| --- | --- | --- |
| `/health` 503, `sweeps.ok` false | the sweep loop stopped or stalled | check the logs for a sweep raising, restart the container |
| `alobot_db: incompatible` | AloBot's schema moved past `ALOBOT_COMMIT` | re-link, re-pin, run the suite; AloBot pages are stale until then |
| `alobot_db: unconfigured` | no copy configured | section 4 |
| Phone gets 503s | ingest saturated | expected under a backlog flush; if sustained, see below |
| Phone gets 401s | token rotated, revoked, or device disabled | re-issue and paste into the phone |
| Nothing auto-verifies | no destination card registered, or the account is not ACTIVE | check حساب‌ها و کارت‌ها |

**Sustained 503s from ingest.** Throughput is bounded by the two commits each
message costs, not by the connection pool: the load test measured a *bigger*
pool as slower. Before touching `DB_POOL_SIZE`, raise `max_connections` in
`docker-compose.yml`, and re-measure with:

```bash
make loadtest URL=https://<staging host> DEVICE=phone-a TOKEN=<token>
```

That writes rows. Staging only.

## 10. Deploying a change

```bash
bash scripts/check.sh    # the gate: migrations up/down/up, the whole suite, the image
make up                  # rebuild and restart, version stamped from the git commit
make migrate
curl -s localhost:8090/health
```

`check.sh` needs the throwaway test Postgres and a linked `vendor/alobot`; it
never touches a deployment.

## 11. What needs the owner's go-ahead

Everything in Phase 7 of `docs/PLAN.md`: any change to AloBot's repo, its
compose file, its database roles or its network; pointing
`ALOBOT_DATABASE_URL` at AloBot's real database; enabling
`ALOBOT_DB_WRITES_ENABLED`; putting AloBot's production bot token in this
project; and restarting AloBot for any reason.

Each of those is its own task with its own approval, its own run of AloBot's
test suite, and no restart of the live bot without flagging it first.
