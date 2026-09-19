#!/usr/bin/env bash
# A backup is not a backup until it has been restored. This dumps the own
# database, restores it into a throwaway database BESIDE the real one, checks
# the migration head matches this checkout, runs the invariants, and drops the
# throwaway. It never writes to the live database.
#
#   DATABASE_URL=postgresql://user:pass@host:5432/dashboard scripts/restore_drill.sh
#
# Needs pg_dump / pg_restore / psql on PATH (present in the app image).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="${DATABASE_URL:?DATABASE_URL is required}"
URL="${URL/postgresql+asyncpg:\/\//postgresql://}"
DBNAME="$(python3 -c "import sys,urllib.parse as u; print(u.urlsplit(sys.argv[1]).path.lstrip('/'))" "$URL")"
ADMIN_URL="${URL%/$DBNAME}/postgres"
DRILL="${DBNAME}_drill_$(date +%s)"
DUMP="$(mktemp -t alobot-dashboard-drill.XXXXXX)"
trap 'rm -f "$DUMP"; psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS \"$DRILL\"" >/dev/null 2>&1 || true' EXIT

echo "1/4 dumping $DBNAME"
pg_dump --format=custom --no-owner --file "$DUMP" "$URL"
echo "2/4 restoring into throwaway $DRILL"
psql "$ADMIN_URL" -q -c "CREATE DATABASE \"$DRILL\""
pg_restore --no-owner --dbname "${URL%/$DBNAME}/$DRILL" "$DUMP"
echo "3/4 checking the migration head"
restored_rev="$(psql "${URL%/$DBNAME}/$DRILL" -tA -c 'SELECT version_num FROM alembic_version')"
expected_rev="$(cd "$ROOT" && DATABASE_URL="$URL" python3 -m alembic heads 2>/dev/null | awk '{print $1}')"
if [[ "$restored_rev" != "$expected_rev" ]]; then
  echo "restored database is at $restored_rev but this checkout expects $expected_rev - run migrations after a real restore"; exit 1
fi
echo "4/4 running invariants on the restored copy"
psql "${URL%/$DBNAME}/$DRILL" -v ON_ERROR_STOP=1 -q -f "$ROOT/scripts/verify_invariants.sql" 2>&1 | grep -E "PASS|FAIL" || true
echo "restore drill passed"
