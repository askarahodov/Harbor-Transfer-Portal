# AGENTS.md — правила автономной разработки

Этот файл задаёт порядок работы coding agents **в этом репозитории**. Он дополняет, а не заменяет [CONTRIBUTING.md](CONTRIBUTING.md), [docs/architecture.md](docs/architecture.md), [docs/testing.md](docs/testing.md), [docs/security.md](docs/security.md), [docs/README.md](docs/README.md) и [ROADMAP.md](ROADMAP.md). При конфликте не переопределяйте нормативные protocol/security контракты этим файлом.

## Цель и границы

Harbor Transfer Portal обеспечивает **офлайн** перенос container images / Helm charts через физически изолированные SOURCE и TARGET. Нельзя добавлять прямой SOURCE↔TARGET network flow, переносить private signing keys / противоположные credentials, обходить TLS/ACL или незаметно перезаписывать конфликтующие артефакты. Release-qualified Harbor v1.0.0 в `main` — baseline; Nexus расширение на момент ввода этого файла изолировано в draft PR #451, **NO-GO** для регионального production.

## Перед началом задачи (обязательно)

1. Перечитайте актуальное Issue, PR, branch HEAD, новые commits и связанные review comments. Сверьте связанные ADR и документацию. Не считайте незакоммиченные или чужие изменения своими.
2. Для затронутого кода найдите существующую реализацию, вызовы, тесты, error/empty/cancel/retry paths и trust boundaries. Не исправляйте лишь симптом.
3. Назначьте единственный owner изменения, определите affected files, dependencies, blast radius, acceptance criteria, необходимые проверки и documentation impact. Работайте над **законченной вертикальной задачей**, не создавая искусственных промежуточных PR на каждую строку.
4. Используйте ветку от нужной **актуальной base**. `main` и ветка draft Nexus интеграции имеют разные release boundaries: не переносите неподтверждённые Nexus изменения в `main` ради удобства.
5. При существующей реализации сначала расширяйте её; не добавляйте фреймворк, сервис, abstraction или dependency без необходимости.

## Итерация реализации до полной готовности

**Understand → root cause → design → implement → scoped tests → fix → independent diff review → docs → final verification → PR/checkpoint.**

- Делайте минимальный связный diff; не запускайте unrelated reformat/refactor.
- Передавайте недоверенные входы через явную валидацию; исключайте shell-конкатенацию, path traversal, credential leaks, insecure redirects и TLS bypass.
- Для багфикса добавляйте regression test, демонстрирующий ошибку до исправления, если это технически возможно.
- Не меняйте expected result и не ослабляйте assert/CI gate ради зелёного результата. При неуспехе выясните: product bug, test bug, environment или flaky test, затем исправьте root cause.
- После каждого смыслового блока возвращайтесь к текущему base/HEAD, чтобы не затереть чужие правки.
- Завершайте **полный scope Issue**, а не только happy path: ошибки, отмена, повторы, backward compatibility, права, UI feedback и документация, где применимо.

## Проверки и quality gates

Минимальный набор зависит от blast radius; canonical команды и CI matrix описаны в [docs/testing.md](docs/testing.md).

| Область | Локальные проверки |
| --- | --- |
| Только Markdown/docs | `make docs-check` |
| Backend | `make lint-backend`; `make typecheck-backend`; затронутые pytest, при merge checkpoint `make test-backend` |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| CI scope | `make test-ci-scope` |
| Protocol/security | соответствующие targeted contract/security tests + обязательные CI jobs |
| Registry/runtime/deploy | соответствующие integration/Compose/offline smoke и release gates по `docs/testing.md` |

CI `quality-gate` является обязательным доказательством на merge checkpoint; `skipped` является нормальным только если scope selector обоснованно не выбрал job. Перед release дополнительно проверяются production инфраструктура, rollback, CA/RBAC/secrets, реальный physical handoff и операторская приёмка — зелёный CI **не равен** GO.

## Code review и Definition of Done

Независимо проверьте correctness, code paths, concurrency/idempotency, API compatibility, security, тесты и ограничения изменения. Перед PR проверьте `git diff` и отсутствие токенов/паролей, runtime bundles, личных данных и generated files.

Issue можно считать **инженерно завершённой** только когда:
- требования и все acceptance criteria выполнены;
- regression risk покрыт необходимыми тестами и их реальные результаты зафиксированы;
- соответствующие CI checks зелёные на **итоговом SHA**;
- критические review findings исправлены;
- документация синхронизирована; нет известных BLOCKER/HIGH без явного решения;
- PR готов к ревью/merge, а статус внешней (региональной) приёмки честно отражён.

Закрывать Issue как `completed` лишь после подтверждённого завершения её **полного** scope; если часть зависит от внешних корпоративных контуров — сохраняйте Issue открытой с точным blocker и evidence requirements.

## Автономность и право остановки

Без дополнительного решения владельца допустимо: анализ, изменение кода/тестов/docs в feature branch, локальные targeted checks, создание commits/PR и исправление ошибок CI. Не выполнять автоматически: merge в `main`, production deploy, production DB migrations, destructive data operations, изменение production secrets/IAM/ACL/CA/firewall, ослабление security policy, breaking protocol/API changes и объявление региональной приёмки GO.

После зелёной задачи выбирайте следующую **READY** Issue по приоритету P0 (data/security/production outage) → P1 (core failure) → P2 (feature) → P3 (debt), учитывая dependencies и release boundary. Если работа требует недоступной инфраструктуры/credentials или явно рискованного утверждения — остановитесь и предъявите конкретный blocker, а не имитируйте проверку.

## Checkpoint после законченной задачи

Сообщите кратко: **Completed** (Issue, PR, SHA, diff), **Validation** (точные команды и CI runs), **Findings** (только реальные факты), **Remaining**, **Blockers**, **Next**. Не заявляйте, что тесты запускались, если проверен лишь чужой CI, и не объявляйте NO-GO интеграцию production-ready.
