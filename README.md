# alobot-dashboard

Web dashboard and bank-SMS payment verification service for AloBot, deployed as
a standalone sibling container. Read `CLAUDE.md` for what it is and the rules it
lives by, and `docs/PLAN.md` for the build order.

```bash
cp .env.example .env            # fill ENV_NAME, SESSION_SECRET (openssl rand -hex 32)
make link-alobot                # read-only symlink to ../AloBot for reference
make up && make migrate         # own Postgres + app on 127.0.0.1:8090
curl -s localhost:8090/health
```

Tests need a throwaway Postgres; see `CLAUDE.md` › Testing.
