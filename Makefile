SHELL := /bin/sh

.PHONY: help up down logs fmt lint lint-backend test test-backend test-frontend migrate build check-foundation

help:
	@printf '%s\n' \
	  'make up             Start local stack' \
	  'make down           Stop local stack' \
	  'make logs           Follow local stack logs' \
	  'make fmt            Format backend and frontend' \
	  'make lint           Run all implemented linters' \
	  'make lint-backend   Run backend Ruff checks only' \
	  'make test           Run backend and frontend tests' \
	  'make test-backend   Run backend tests only' \
	  'make test-frontend  Run frontend tests only' \
	  'make migrate        Apply backend Alembic migrations' \
	  'make build          Build backend/frontend artifacts' \
	  'make check-foundation Validate repository scaffolding'

up:
	@test -f compose.yaml || { echo 'compose.yaml is not implemented yet'; exit 2; }
	docker compose up -d

down:
	@test -f compose.yaml || { echo 'compose.yaml is not implemented yet'; exit 2; }
	docker compose down

logs:
	@test -f compose.yaml || { echo 'compose.yaml is not implemented yet'; exit 2; }
	docker compose logs -f

fmt:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml is not implemented yet'; exit 2; }
	cd backend && python -m ruff format .
	@test -f frontend/package.json || { echo 'frontend/package.json is not implemented yet'; exit 2; }
	cd frontend && npm run format

lint: lint-backend
	@test -f frontend/package.json || { echo 'frontend/package.json is not implemented yet'; exit 2; }
	cd frontend && npm run lint

lint-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml is not implemented yet'; exit 2; }
	cd backend && python -m ruff check .

test: test-backend test-frontend

test-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml is not implemented yet'; exit 2; }
	cd backend && python -m pytest

test-frontend:
	@test -f frontend/package.json || { echo 'frontend/package.json is not implemented yet'; exit 2; }
	cd frontend && npm test

migrate:
	@test -f backend/alembic.ini || { echo 'backend/alembic.ini is not implemented yet'; exit 2; }
	cd backend && python -m alembic upgrade head

build:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml is not implemented yet'; exit 2; }
	cd backend && python -m build
	@test -f frontend/package.json || { echo 'frontend/package.json is not implemented yet'; exit 2; }
	cd frontend && npm run build

check-foundation:
	@test -f README.md
	@test -f CONTRIBUTING.md
	@test -f .gitignore
	@test -f .editorconfig
	@test -f .env.example
	@test -f docs/decisions.md
	@test -d backend
	@test -d frontend
	@test -d docs
	@test -d data
	@test -d deploy
	@! grep -R -n -E '(^|[[:space:]])(\|\|[[:space:]]*true|;[[:space:]]*true)([[:space:]]|$$)' Makefile CONTRIBUTING.md
