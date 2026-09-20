#!/usr/bin/env bash
# Restore an AloBot pg_dump into a LOCAL database and create the SELECT-only
# role the dashboard connects with. This is how every phase before the final
# integration sees AloBot data: from a copy, never from production.
#
#   scripts/copy_alobot_db.sh <dump.sql|dump.dump|dump.zip> [ADMIN_URL] [COPY_DB]
#
# ADMIN_URL defaults to this project's own Postgres superuser
# (postgresql://dashboard:dashboard@localhost:5432/postgres); COPY_DB to
# alobot_copy. AloBot's nightly backup zip (app/services/backups.py in AloBot)
# contains a plain-SQL pg_dump; a .zip is unpacked first.
set -euo pipefail
DUMP="${1:?path to AloBot dump}"
ADMIN_URL="${2:-postgresql://dashboard:dashboard@localhost:5432/postgres}"
COPY_DB="${3:-alobot_copy}"
HOST="$(python3 -c "import sys,urllib.parse as u; print(u.urlsplit(sys.argv[1]).hostname or '')" "$ADMIN_URL")"
# The guard is about the DESTINATION: this restores INTO the dashboard's own
# Postgres and must never be pointed at AloBot's. `postgres` is the service
# name of this project's own db container in docker-compose.yml, which is how
# the destination is addressed from inside the app container on a server;
# AloBot's own database is not reachable under that name.
case "$HOST" in localhost|127.0.0.1|::1|host.docker.internal|postgres) ;; *) echo "refusing non-local host $HOST"; exit 2;; esac

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
if [[ "$DUMP" == *.zip ]]; then
  unzip -q "$DUMP" -d "$work"
  DUMP="$(find "$work" -name '*.sql' -o -name '*.dump' | head -1)"
  [[ -n "$DUMP" ]] || { echo "no .sql/.dump inside the zip"; exit 1; }
fi

psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS \"$COPY_DB\"" -c "CREATE DATABASE \"$COPY_DB\""
DB_URL="${ADMIN_URL%/postgres}/$COPY_DB"
if [[ "$DUMP" == *.dump ]]; then
  pg_restore --no-owner --no-privileges --dbname "$DB_URL" "$DUMP"
else
  psql "$DB_URL" -q -v ON_ERROR_STOP=1 -f "$DUMP" >/dev/null
fi
psql "$DB_URL" -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro') THEN
    CREATE ROLE dashboard_ro LOGIN PASSWORD 'ro';
  END IF;
END \$\$;
GRANT CONNECT ON DATABASE "$COPY_DB" TO dashboard_ro;
GRANT USAGE ON SCHEMA public TO dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro;
SQL
echo "AloBot copy ready: ${DB_URL/dashboard:dashboard@/dashboard_ro:ro@} (read-only role dashboard_ro)"
echo "alembic head in the copy: $(psql "$DB_URL" -tA -c 'SELECT version_num FROM alembic_version')"
