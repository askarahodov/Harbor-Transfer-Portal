# ROADMAP — инженерные приоритеты и границы релиза

> Snapshot на 2026-11-09. **GitHub Issues/PR/CI и actual branch SHA — источник истины о текущем состоянии.** Обновляйте этот документ при смене приоритетов или статуса release; не используйте как доказательство успешной приёмки.

## Product baseline

Harbor Transfer Portal v1.0.0 поддерживает локальный **SOURCE → signed offline bundle + SHA-256 sidecar + signed handoff → physical transfer → TARGET verification/import** для Harbor. `main` — release-qualified baseline, но фактический production rollout требует локального approval. Nexus Docker/legacy HTTP Helm добавляется отдельно в [draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451). До подтверждённой региональной приёмки это **NO-GO**, не функция `main`.

## Активные workstreams (приоритет по безопасности и dependencies)

| Priority | Issue / workstream | Complete означает | Зависимости и граница |
| --- | --- | --- | --- |
| P1 | [#422 — export worker failure](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) | Исправлена root cause потери причины ошибки; regression tests воспроизводят реальный сбой; scoped CI и независимый review зелёные | Не смешивать с Nexus release; доказательство production incident отдельно |
| P1 | [#436 — regional Nexus acceptance](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) | Проверены фактические regional Nexus versions/editions, Docker/Helm repositories/ACL, TLS/CA, offline handoff; evidence и owner sign-off в runbook | Требует доступ из утверждённых сетей и реального оператора; одноразовый CI Nexus не заменяет |
| P1 | [#434 — Harbor↔Nexus interop](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) | Реальный SOURCE→TARGET physical handoff и receipt с разрешёнными endpoints, failure cases и operator sign-off | Зависит от внешних контуров и release boundary #451 |
| P2 | [#414 — Nexus contract/integration tests](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) | Проверено покрытие реальных Docker/Helm контрактов и отмечены недостающие edge cases; добавлены regression tests там, где отсутствуют | Только в соответствующей Nexus integration branch; не подменять regional acceptance |
| P2 | [#451 — integration release candidate](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451) | Итоговый SHA проверен combined CI/E2E и review; outstanding risks исправлены; региональное GO оформлено до merge/deploy | Сейчас draft/NO-GO; **не merge** автоматически |

Приоритеты P1 не означают, что недоступная региональная инфраструктура должна блокировать независимое исправление #422. Не закрывайте #434/#436, пока отсутствует фактическое evidence. Точный следующий READY выбирается после проверки свежего GitHub state.

## Режим работы AI agent

1. Синхронизировать Issue/PR/branch/CI и docs, выбрать самый высокий **незаблокированный** приоритет.
2. Выполнить **всю связную задачу**: реализация, edge cases, регрессионные тесты, документация и independent review.
3. Довести требуемый CI до green **на окончательном commit SHA** и сделать checkpoint по [AGENTS.md](AGENTS.md).
4. Открыть PR в корректную base branch; не merge автоматически.
5. Следующую READY задачу брать только при отсутствии blocker и конфликтующего редактирования.

## Критерии milestone

### M1 — AI-ready repository

- [x] Существуют `CONTRIBUTING.md`, нормативная `docs/`, scoped CI, dependency locks, documentation checker и `quality-gate`.
- [ ] `AGENTS.md` + `ROADMAP.md` приняты в `main` через проверенный PR.
- [ ] Для практического пилота выполнена одна **цельная** задача с regression test, review и зелёным CI.
- [ ] Подтверждены branch protection / merge rules в GitHub Settings (API может ограничивать чтение).

### M2 — надёжный одиночный агент

- [ ] Issue → branch → complete implementation → tests → PR → CI → review выполняется на 3–5 реальных задачах без ручного переписывания требований.
- [ ] Failures CI приводят к root-cause repair и повторной проверке итогового SHA, без ослабления gates.
- [ ] Разрушительные/production действия явно требуют решения владельца.

### M3 — coordinator / multi-agent

Только после M2: выбор READY tasks по dependencies; отдельные branches/worktrees для независимых областей; исключение concurrent edits одних файлов; PR lifecycle и checkpoint для человека. Не внедрять мультиагентную платформу, пока не стабилен одиночный цикл.

## Непреложные release gates

Региональный Nexus GO требует документального evidence по фактическим versions, permissions, HTTPS/CA, per-repo Docker/Helm, физическому переносу, immutable bundle/signatures/digests, TARGET receipt и Harbor regression. Форма и процедуры: [regional acceptance runbook](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/integration/nexus-oss-release-candidate-451/docs/nexus-regional-acceptance-runbook.md). Зелёные [CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml) и [Nexus disposable E2E](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/nexus-legacy-e2e.yml) обязательны для соответствующего scope, но **недостаточны** для корпоративного GO.

Ссылки на текущие принципы: [docs/architecture.md](docs/architecture.md), [docs/testing.md](docs/testing.md), [docs/security.md](docs/security.md), [docs/deferred-tasks.md](docs/deferred-tasks.md).
