.PHONY: up down restart logs migrate revision test link-alobot check-alobot-pin

up:
	docker compose up -d --build

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

# Point vendor/alobot at the sibling AloBot checkout (read-only reference).
link-alobot:
	bash scripts/link_alobot.sh $(ALOBOT_PATH)

# Verify the linked checkout is at the commit this project was verified against.
check-alobot-pin:
	bash scripts/link_alobot.sh --check
