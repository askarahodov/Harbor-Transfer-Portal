SHELL := /bin/sh

.PHONY: help up down logs fmt lint lint-backend test test-backend test-frontend docs-check ci-scope-check migrate build compose-config smoke-compose check-foundation

help:
	@printf '%s\n' \
	  'make up             Собрать и запустить локальный стек' \
	  'make down           Остановить стек, сохранив persistent volume' \
	  'make logs           Показывать логи локального стека' \
	  'make fmt            Форматировать backend и frontend' \
	  'make lint           Запустить все реализованные линтеры' \
	  'make lint-backend   Запустить только backend Ruff checks' \
	  'make test           Запустить backend и frontend tests' \
	  'make test-backend   Запустить только backend tests' \
	  'make test-frontend  Запустить только frontend tests' \
	  'make docs-check     Проверить локальные Markdown-ссылки и docs checker tests' \
	  'make ci-scope-check Проверить path-aware CI selection regression' \
	  'make migrate        Применить backend Alembic migrations' \
	  'make build          Собрать backend/frontend artifacts' \
	  'make compose-config Проверить Docker Compose configuration' \
	  'make smoke-compose  Собрать стек и выполнить Compose smoke test' \
	  'make check-foundation Проверить базовую структуру репозитория'

up:
	@test -f .env || { echo 'Требуется .env; сначала скопируйте .env.example в .env'; exit 2; }
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

fmt:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m ruff format .
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm run format

lint: lint-backend
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm run lint

lint-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m ruff check .

test: test-backend test-frontend

test-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m pytest

test-frontend:
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm test

docs-check:
	@test -f tools/check_doc_links.py || { echo 'tools/check_doc_links.py отсутствует'; exit 2; }
	python3 -m unittest tools.test_check_doc_links
	python3 tools/check_doc_links.py

ci-scope-check:
	@test -f tools/ci_scope.py || { echo 'tools/ci_scope.py отсутствует'; exit 2; }
	python3 -m unittest tools.test_ci_scope

migrate:
	@test -f backend/alembic.ini || { echo 'backend/alembic.ini отсутствует'; exit 2; }
	cd backend && python -m alembic upgrade head

build:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m build
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm run build

compose-config:
	@test -f .env || { echo 'Требуется .env; сначала скопируйте .env.example в .env'; exit 2; }
	docker compose config >/dev/null

smoke-compose:
	./deploy/smoke-compose.sh

check-foundation:
	@test -f README.md
	@test -f CONTRIBUTING.md
	@test -f .gitignore
	@test -f .editorconfig
	@test -f .env.example
	@test -f docs/README.md
	@test -f docs/decisions.md
	@test -f tools/check_doc_links.py
	@test -f compose.yaml
	@test -d backend
	@test -d frontend
	@test -d docs
	@test -d data
	@test -d deploy
	@! grep -R -n -E '(^|[[:space:]])(\|\|[[:space:]]*true|;[[:space:]]*true)([[:space:]]|$$)' Makefile CONTRIBUTING.md
