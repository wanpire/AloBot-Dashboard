# app/alobot — the read-only window onto AloBot's data

Everything that touches AloBot's database goes through this package and
nothing else. Rules:

1. **Reflect, never declare.** AloBot's tables are loaded with
   `Table(..., autoload_with=...)` at startup. This project keeps no
   copy of AloBot's SQLAlchemy models, so a column AloBot adds or
   renames is seen here immediately instead of silently diverging.
2. **Read-only by role, not by discipline.** The connection uses a
   Postgres role with `SELECT` only. The application flag
   `ALOBOT_DB_WRITES_ENABLED` exists so the UI can *say* a screen is
   read-only; the role is what enforces it.
3. **Compatibility check at boot.** `app.alobot.compat` compares the
   reflected columns this project depends on against the AloBot commit
   recorded in `ALOBOT_COMMIT` and refuses to serve AloBot-backed pages
   on a mismatch, with a message naming the column.
4. **Money crosses the boundary once.** AloBot stores Toman as
   `Numeric(12,2)`; bank SMS and this project's own tables use integer
   Rial. The conversion lives in exactly one helper here.

Built in Phase 2 (see `docs/PLAN.md`). This file is the contract; the
code arrives with that phase.
