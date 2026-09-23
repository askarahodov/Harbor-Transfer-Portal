SHELL := /bin/sh

.PHONY: help doctor images up down logs fmt lint lint-backend typecheck-backend test test-backend test-frontend test-ci-scope dependency-locks-check test-registry-integration test-offline-kit docs-check migrate build compose-config smoke-compose check-foundation

help:
	@printf '%s\n' \
	  'make doctor         Проверить cross-platform dev prerequisites' \
	  'make images         Собрать Docker images из текущего Git revision' \
	  'make up             Пересобрать и recreate локальный стек из текущего Git revision' \
	  'make down           Остановить стек, сохранив persistent volume' \
	  'make logs           Показывать логи локального стека' \
	  'make fmt            Форматировать backend и frontend' \
	  'make lint           Запустить все реализованные линтеры' \
	  'make lint-backend   Запустить только backend Ruff checks' \
	  'make typecheck-backend Запустить backend Mypy static type check' \
	  'make test           Запустить backend и frontend tests' \
	  'make test-backend   Запустить backend tests с application coverage >=70%' \
	  'make test-frontend  Запустить только frontend tests' \
	  'make test-ci-scope  Проверить regression-матрицу scoped CI selection' \
	  'make dependency-locks-check Проверить согласованность dependency lockfiles' \
	  'make test-registry-integration Проверить реальные Skopeo/Helm через local OCI registry' \
	  'make test-offline-kit Проверить packaging/install contract offline release kit' \
	  'make docs-check     Проверить Markdown-ссылки и current documentation contracts' \
	  'make migrate        Применить backend Alembic migrations' \
	  'make build          Собрать backend/frontend artifacts' \
	  'make compose-config Проверить Docker Compose configuration' \
	  'make smoke-compose  Проверить offline kit, собрать стек и выполнить Compose smoke test' \
	  'make check-foundation Проверить базовую структуру репозитория'

doctor:
	python3 tools/dev.py doctor

images:
	python3 tools/dev.py build

up:
	python3 tools/dev.py up

down:
	python3 tools/dev.py down

logs:
	python3 tools/dev.py logs

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

typecheck-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m mypy app

test: test-backend test-frontend

test-backend:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=70

test-frontend:
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm test

test-ci-scope:
	python3 -m unittest tools.test_ci_scope

dependency-locks-check:
	python3 -m unittest tools.test_dependency_locks
	python3 tools/check_dependency_locks.py

test-registry-integration:
	sh deploy/smoke-registry-integration.sh

test-offline-kit:
	sh deploy/smoke-offline-kit.sh

docs-check:
	@test -f tools/check_doc_links.py || { echo 'tools/check_doc_links.py отсутствует'; exit 2; }
	@test -f tools/test_documentation_contracts.py || { echo 'tools/test_documentation_contracts.py отсутствует'; exit 2; }
	python3 -m unittest tools.test_check_doc_links tools.test_documentation_contracts
	python3 tools/check_doc_links.py

migrate:
	@test -f backend/alembic.ini || { echo 'backend/alembic.ini отсутствует'; exit 2; }
	cd backend && python -m alembic upgrade head

build:
	@test -f backend/pyproject.toml || { echo 'backend/pyproject.toml отсутствует'; exit 2; }
	cd backend && python -m build
	@test -f frontend/package.json || { echo 'frontend/package.json отсутствует'; exit 2; }
	cd frontend && npm run build

compose-config:
	python3 tools/dev.py compose-config >/dev/null

smoke-compose: test-offline-kit
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
	@test -f tools/test_documentation_contracts.py
	@test -f tools/dev.py
	@test -f tools/test_dev.py
	@test -f dev.ps1
	@test -f docs/development.md
	@test -f tools/ci_scope.py
	@test -f tools/test_ci_scope.py
	@test -f tools/check_dependency_locks.py
	@test -f tools/test_dependency_locks.py
	@test -f backend/requirements-runtime.lock
	@test -f backend/requirements-dev.lock
	@test -f frontend/package-lock.json
	@test -f backend/integration/registry_smoke.py
	@test -f deploy/compose-registry-integration.yml
	@test -f deploy/smoke-registry-integration.sh
	@test -f deploy/build-offline-kit.sh
	@test -f deploy/smoke-offline-kit.sh
	@test -f deploy/offline/compose.yaml
	@test -f deploy/offline/install.sh
	@test -f deploy/offline/README.md
	@test -f compose.yaml
	@test -d backend
	@test -d frontend
	@test -d docs
	@test -d data
	@test -d deploy
	@! grep -R -n -E '(^|[[:space:]])(\|\|[[:space:]]*true|;[[:space:]]*true)([[:space:]]|$$)' Makefile CONTRIBUTING.md
