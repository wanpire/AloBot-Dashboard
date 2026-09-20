.PHONY: up down restart logs migrate revision test check build link-alobot check-alobot-pin copy-alobot-db seed-alobot-copy loadtest

# The running version is stamped from the git commit at build time, so the
# badge in the panel and /health name the build this host is actually running.
export APP_VERSION := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)

up:
	docker compose up -d --build

build:
	docker compose build

down:
	docker compose down

restart:
	docker compose restart dashboard

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose exec dashboard alembic upgrade head

revision:
	docker compose exec dashboard alembic revision --autogenerate -m "$(MSG)"

# Tests need a throwaway Postgres (see CLAUDE.md > Testing).
test:
	python -m pytest -v

# Everything that must pass before deploying: migrations up/down/up, the
# suite, and the production image built and inspected.
check:
	bash scripts/check.sh

# Fire synthetic bank SMS at a DEPLOYED instance (staging only).
loadtest:
	python scripts/loadtest_ingest.py --url $(URL) --device $(DEVICE) --token $(TOKEN) --yes-this-writes-rows

# Point vendor/alobot at the sibling AloBot checkout (read-only reference).
link-alobot:
	bash scripts/link_alobot.sh $(ALOBOT_PATH)

# Verify the linked checkout is at the commit this project was verified against.
check-alobot-pin:
	bash scripts/link_alobot.sh --check

# Restore an AloBot dump into a LOCAL copy with a read-only role (Phase 2).
copy-alobot-db:
	bash scripts/copy_alobot_db.sh $(DUMP) $(ADMIN_URL) $(COPY_DB)

# Fill a LOCAL AloBot copy with synthetic data.
seed-alobot-copy:
	python scripts/seed_alobot_copy.py --url $(URL) --customers $(or $(CUSTOMERS),40)
