.PHONY: help setup verify verify-fast catalog bootstrap migrate seed \
        backend frontend dev test test-frontend test-live lint \
        deploy-build deploy-release deploy-up deploy-down deploy-logs \
        deploy-runbook deploy-sync

help:
	@echo "pie-portal — Sanketh Quote Builder + Commercial Decision Platform"
	@echo ""
	@echo "  make setup        one command from a bare clone: pie-parser, pinned"
	@echo "                    dev tooling, npm deps, database"
	@echo "  make verify       THE GATE — lint, §1 invariants, 1174 backend tests,"
	@echo "                    frontend build, migrations on an EMPTY database (~4m)"
	@echo "  make verify-fast  the edit loop: skips the frontend build and the"
	@echo "                    empty-database migration check (~2.5m)"
	@echo ""
	@echo "  make dev          run backend (:8000) + frontend (:5173) together"
	@echo "  make backend      run the FastAPI backend on :8000"
	@echo "  make frontend     run the Vite dev server on :5173"
	@echo ""
	@echo "  make test         backend tests only"
	@echo "  make test-frontend  frontend tests only (vitest)"
	@echo "  make test-live    the live contract suites — real AI, real Zoho"
	@echo "  make lint         ruff only"
	@echo "  make bootstrap    create the DB, migrate, seed users + demo data"
	@echo "  make migrate      apply database migrations"
	@echo "  make seed         seed the default org + demo users"
	@echo "  make catalog      build the decoded PIE catalogue from pie-parser"
	@echo ""
	@echo "  Self-hosting (docs/hosting.md) — needs .env.production:"
	@echo "  make deploy-build    build the API and edge images"
	@echo "  make deploy-release  migrations + seed: the deliberate step"
	@echo "  make deploy-up       start Postgres, the API and the TLS edge"
	@echo "  make deploy-down     stop them (volumes survive)"
	@echo "  make deploy-logs     follow the logs"
	@echo "  make deploy-sync     pull every company at once, analyse once"
	@echo ""
	@echo "CI runs 'make verify'. There is no second list of checks anywhere."

# One command from a bare clone. The old `setup` installed runtime deps only and
# left the database, the dev tooling and two environment traps to the reader.
setup:
	./scripts/setup_dev.sh

# The single definition of "verified". scripts/verify.sh is what CI runs and what
# the Claude Code stop-hook checks against, so a change that passes here passes
# there — the two cannot drift, because there is only one of them.
verify:
	./scripts/verify.sh

verify-fast:
	./scripts/verify.sh --fast

bootstrap:
	cd backend && python3 -m app.bootstrap

migrate:
	cd backend && python3 -m alembic upgrade head

seed:
	cd backend && python3 -m app.seed

catalog:
	python3 scripts/build_catalog.py

backend:
	cd backend && python3 -m uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

dev:
	@echo "Starting backend (:8000) and frontend (:5173) — Ctrl-C stops both."
	@( cd backend && python3 -m uvicorn app.main:app --port 8000 ) & \
	 ( cd frontend && npm run dev ) ; \
	 wait

# Parallel by default; each worker gets its own database (backend/tests/conftest.py).
# PYTEST_WORKERS=0 forces the serial path.
test:
	cd backend && python3 -m pytest tests -q -n $${PYTEST_WORKERS:-auto}

test-frontend:
	cd frontend && npm test

# Excluded from `make verify` and from `make test`, for the reason pytest.ini
# gives: these call a real model and a real Zoho book, so they cost money and
# need credentials. Both suites skip themselves — with a reason — when theirs
# are absent, so this is safe to run without a full .env. CI runs them weekly
# from .github/workflows/live.yml.
test-live:
	cd backend && python3 -m pytest tests/live -m live -q -ra

lint:
	python3 -m ruff check .

# ── Self-hosting (docs/hosting.md) ───────────────────────────────────────────
# Every target passes --env-file so the deployment's secrets stay in
# .env.production and never collide with the repo-root .env that
# app/config.py loads for local development.
COMPOSE = docker compose --env-file .env.production

deploy-build:
	$(COMPOSE) build

# Migrations and seeding — the deliberate step. Nothing else runs this, which is
# why `deploy-up` cannot migrate anything (CLAUDE.md §4).
deploy-release:
	$(COMPOSE) --profile release run --rm release

deploy-up:
	$(COMPOSE) up -d
	@echo "Up. Health: docker compose --env-file .env.production exec api \\"
	@echo "             python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())\""

deploy-down:
	$(COMPOSE) down

deploy-logs:
	$(COMPOSE) logs -f --tail=100

# Pull every connected company at once, then analyse the organization once.
# What the nightly cron runs; blocks, and exits non-zero if a pull did not land.
deploy-sync:
	$(COMPOSE) exec -T api python -m app.sync_all $(SYNC_ARGS)

# What a deploy of this range requires, in the order it requires it. Reads the
# migrations that landed; it does NOT guess what production is at.
deploy-runbook:
	python3 scripts/deploy_runbook.py --range $${RANGE:-origin/main..HEAD}
