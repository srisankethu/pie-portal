.PHONY: help setup verify verify-fast catalog bootstrap migrate seed \
        backend frontend dev test lint

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
	@echo "  make lint         ruff only"
	@echo "  make bootstrap    create the DB, migrate, seed users + demo data"
	@echo "  make migrate      apply database migrations"
	@echo "  make seed         seed the default org + demo users"
	@echo "  make catalog      build the decoded PIE catalogue from pie-parser"
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

lint:
	python3 -m ruff check .
