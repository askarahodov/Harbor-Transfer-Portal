SHELL := /bin/sh

.PHONY: help up down logs fmt lint lint-backend test test-backend test-frontend build compose-config smoke-compose check-foundation

help:
	@printf '%s\n' \
	  'make up             Build and start local stack' \
	  'make down           Stop local stack (persistent volume is preserved)' \
	  'make logs           Follow local stack logs' \
	  'make fmt            Format backend and frontend' \
	  'make lint           Run all implemented linters' \
	  'make lint-backend   Run backend Ruff checks only' \
	  'make test           Run backend and frontend tests' \
	  'make test-backend   Run backend tests only' \
	  'make test-frontend  Run frontend tests only' \
	  'make build          Build backend/frontend artifacts' \
	  'make compose-config Validate Docker Compose configuration' \
	  'make smoke-compose  Build/restart stack and verify persistence' \
	  'make check-foundation Validate repository scaffolding'

up:
	@test -f .env || { echo '.env is required; copy .env.example to .env first'; exit 2; }
	docker compose up -d --build

down:
	docker compose down

logs:
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

build:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml is not implemented yet'; exit 2; }
	cd backend && python -m build
	@test -f frontend/package.json || { echo 'frontend/package.json is not implemented yet'; exit 2; }
	cd frontend && npm run build

compose-config:
	@test -f .env || { echo '.env is required; copy .env.example to .env first'; exit 2; }
	docker compose config >/dev/null

smoke-compose:
	./deploy/smoke-compose.sh

check-foundation:
	@test -f README.md
	@test -f CONTRIBUTING.md
	@test -f .gitignore
	@test -f .editorconfig
	@test -f .env.example
	@test -f docs/decisions.md
	@test -f compose.yaml
	@test -d backend
	@test -d frontend
	@test -d docs
	@test -d data
	@test -d deploy
	@! grep -R -n -E '(^|[[:space:]])(\|\|[[:space:]]*true|;[[:space:]]*true)([[:space:]]|$$)' Makefile CONTRIBUTING.md
