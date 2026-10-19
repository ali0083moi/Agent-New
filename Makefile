.PHONY: install dev-backend dev-frontend build test run

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install

dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

test:
	cd backend && .venv/bin/python -m pytest -q
	cd frontend && npm run typecheck

run: build
	cd backend && .venv/bin/python -m app.main
