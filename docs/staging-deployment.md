# Phase 6 task 5 — staging deployment on the production host

**Status: approved and deployed.** The three questions below were answered by
the owner; the answers are recorded here and the deployment followed them.

| Question | Answer |
|---|---|
| The copy's data | Restore AloBot's nightly backup **as it is**. It stays on the host the data already lives on. |
| Telegram bot for operator alerts | A dedicated throwaway bot, `@AlonetPanelbot`, separate from AloBot's own. Its token lives only in the server's `.env` (mode 600) - never in git, never in a log, never on a screen in the panel. |
| Hostname | `panel.alonet.co`. |

One correction found while deploying: AloBot's nightly backup is built in
memory and sent to Telegram, never written to disk, so there is no backup
file on the host to copy. The copy is therefore taken with the same
`pg_dump -Fc` that AloBot's own backup job runs, against the same database,
which is a read-only snapshot and no more invasive than what already happens
every night.

Everything else in Phase 6 is finished and runs on a laptop. This task is the
first thing in this project that touches the machine AloBot runs on, and it
wants a copy of real customer data, so it is written up here rather than
carried out.

## What it is

A second, independent compose project on the same host: its own containers,
its own Postgres, its own network, its own hostname behind TLS. It sits
beside AloBot the way `dns-switcher` does.

## What it does not touch

Stated plainly, because this is the whole reason the phase is ordered this
way:

- AloBot's containers are not stopped, restarted, rebuilt or reconfigured.
- AloBot's compose file, `.env`, source and database roles are not edited.
- AloBot's docker network is not joined. The dashboard talks to its own
  Postgres and to nothing of AloBot's over the network.
- AloBot's live database is not read. The dashboard points at a **restored
  copy** in its own Postgres container, refreshed by re-running a script.
- AloBot's bot token is not used. Outbound Telegram stays on a throwaway
  bot, and customer-facing messages stay off.
- `ALOBOT_DB_WRITES_ENABLED` stays `false`, so every AloBot-backed editor
  renders read-only.

If any of those has to change, it is Phase 7 and it needs its own go-ahead.

## What it costs the host

Two containers: the app and a Postgres. Idle memory is small; the real cost
is disk, roughly the size of AloBot's database twice over (the dump plus the
restored copy). Worth checking free space before starting.

One TCP port bound to `127.0.0.1` only, plus one reverse-proxy virtual host
for TLS. No port is published on `0.0.0.0`, and a test in the suite enforces
that in the compose file.

## The decision that is actually yours

The copy is restored from AloBot's nightly backup, so it contains **real
customers**: Telegram ids, usernames, payment amounts and IBSng usernames.
Three ways to go, and this needs an answer before anything is restored:

1. **Restore the backup as it is.** The demo shows real orders and real
   payment histories, which is what makes it convincing. The data does not
   leave the host it already lives on, and the dashboard reads it through a
   `SELECT`-only role. This is the recommendation, unless anyone besides you
   will be watching the demo.
2. **Restore and then scrub.** Same shape, identifiers replaced. Costs a
   scrubbing script that does not exist yet, and a demo that is slightly less
   persuasive.
3. **Synthetic only.** `scripts/seed_alobot_copy.py` fills a copy with
   generated data and no real customer appears anywhere. Safest, least
   convincing, and already working - it is what the test suite uses.

The second question is smaller: **a throwaway Telegram bot token**, so the
outbox has somewhere to send operator alerts during the demo. Not AloBot's
token. A fresh bot from BotFather takes a minute and can be deleted after.

And the third: **the hostname** the panel should answer on, so the
reverse-proxy vhost and its certificate can be set up.

## The steps, once approved

Nothing here runs without that go-ahead.

1. Check free disk against twice the size of AloBot's database.
2. Create a directory for the project and clone this repo into it. It is a
   separate checkout; AloBot's directory is not touched.
3. Write its `.env`: `ENV_NAME=staging`, its own `DATABASE_URL` on its own
   Postgres, a fresh `SESSION_SECRET`, `ALOBOT_DB_WRITES_ENABLED=false`, the
   throwaway bot token, and `ALOBOT_DATABASE_URL` left blank for now.
4. `bash scripts/check.sh` on the host, then `make up` and `make migrate`.
5. Create the first ADMIN operator with `scripts/create_operator.py`.
6. Copy AloBot's most recent nightly backup file into the staging directory
   and restore it with `scripts/copy_alobot_db.sh`, which creates the
   `SELECT`-only role. Reading a backup file is the only contact with
   anything of AloBot's, and it is a read of a file.
7. Set `ALOBOT_DATABASE_URL` to the copy, restart, and confirm `/health`
   reports `alobot_db: ok`.
8. Add the reverse-proxy vhost and its certificate for the chosen hostname,
   forwarding to the loopback port.
9. Register the relay phone: create the device, put its token in the phone
   app, and point the app at `https://<hostname>/api/v1/sms`.
10. `python scripts/demo_setup.py --url https://<hostname>` and walk
    `docs/DEMO.md` end to end.

## Rolling it back

`docker compose down -v` in the staging directory, delete the directory,
remove the vhost. AloBot is unaffected because it was never involved: there
is no shared network, no shared database and no shared configuration to
unpick.

## After sign-off

Sign-off on the demo is the exit criterion for Phase 6. Phase 7 is then the
only work left, and every task in it needs its own go-ahead, its own run of
AloBot's test suite, and no restart of the live bot without that being
flagged first.
