# ROADMAP — инженерные приоритеты и release gates

> Срез на 10 ноября 2026. **Актуальное состояние всегда проверяйте по [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues), [PR](https://github.com/askarahodov/Harbor-Transfer-Portal/pulls) и CI.** Этот документ не подтверждает production rollout и не заменяет протокол приёмки.

## Что работает в `main`

Harbor SOURCE → подписанный Offline Bundle v1 с `.sha256` и signed handoff → физический перенос → TARGET verify/import. Профили Harbor выбираются отдельно для операций; error-path export hardening [#475](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/475), интерактивная 60-минутная idle-сессия [#476](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/476) и fail-closed import recovery [#477](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/477)/[#478](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/478) объединены и прошли CI. Развёртывание конкретного контура требует отдельной проверки и разрешения.

Первый запуск исходного checkout уже реализован: `./run.sh` (Linux) или `run.cmd` (Windows CMD) создают `.env` при отсутствии и запускают Compose без Python на хосте. Backend сохраняет автоматически сгенерированный `JWT_SECRET` в persistent volume. На **пустой базе** создаётся временный `admin/admin` с обязательной сменой пароля (не менее 12 символов); до смены защищённые API блокируются, после смены старые токены отзываются. Это **не** установка готового offline release kit и **не** настройка действующего Harbor profile. [#481](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/481) и [#482](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/482) завершены; [CI `main` на `a3e3f03e`](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42817246772) — SUCCESS.

**Nexus Docker v2 + classic Helm HTTP** реализован в [Draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451), а не в `main`. Combined CI и disposable Nexus/Harbor E2E на integrated SHA `7b32643b` зелёные; это **не** эквивалент региональной приёмки.

## Активные зависимости

| Приоритет | Задача | Что нужно для закрытия |
| --- | --- | --- |
| P1 | [#434 — Harbor ↔ Nexus handoff](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) | Разрешённый физический SOURCE/TARGET transfer и receipt |
| P1 | [#436 — regional acceptance](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) | Реальные versions/editions, ACL, corporate TLS/CA/proxy, конкурирующие writers, sign-off |
| P2 | [#422 — исторический export RCA](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) | Traceback/deployed SHA и воспроизведение **или явный owner waiver неизвестной причины**; code fix выполнен |
| P2 | [#414 — Nexus integration/qualification](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) | Завершение #434/#436, подтверждённая версия, approval; #451 остаётся Draft |

## Границы релиза

1. Любое изменение проходит scoped tests, независимое ревью и зелёный `Quality Gate` на финальном SHA; детали — [testing.md](docs/testing.md) и [AGENTS.md](AGENTS.md).
2. Для Nexus региональное GO требует независимых SOURCE/TARGET, подписанного physical handoff, реальных репозиториев Docker/Helm с ACL/TLS, verified receipt и подтверждения оператора, безопасности и владельца. Проверять по [региональному runbook (Draft #451)](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/integration/nexus-oss-release-candidate-451/docs/nexus-regional-acceptance-runbook.md).
3. TARGET `overwrite_conflicts=false` **не даёт** атомарного no-overwrite от внешнего writer без дополнительных гарантий на стороне registry. Этот пункт обязателен для #436.
4. Не переносите Nexus в `main` и не отмечайте #434/#436 закрытыми только на основании disposable E2E.
5. Начальный `admin/admin` существует только при **пустой базе** и только для обязательной смены пароля; не публикуйте HTTP bootstrap за пределами loopback без одобренного TLS-терминатора. `run.sh`/`run.cmd` — запуск исходного Compose, не замена offline release kit или региональной приёмки.

Инструкция работы с документацией: [docs/README.md](docs/README.md). Новые задачи и отложенные улучшения фиксируются в Issues, не в отдельном неактуализируемом списке.
