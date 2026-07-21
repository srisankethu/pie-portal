.PHONY: help setup catalog backend frontend dev test

help:
	@echo "pie-portal — Sanketh Quote Builder"
	@echo "  make setup      install backend + frontend deps"
	@echo "  make catalog    build the decoded PIE catalogue from the pie-parser submodule"
	@echo "  make backend    run the FastAPI backend on :8000"
	@echo "  make frontend   run the Vite dev server on :5173 (proxies /api -> :8000)"
	@echo "  make dev        run backend + frontend together"
	@echo "  make test       run backend tests"

setup:
	./scripts/setup_pie_parser.sh
	python3 -m pip install -r backend/requirements.txt
	cd frontend && npm install

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

test:
	cd backend && python3 -m pytest tests -q
