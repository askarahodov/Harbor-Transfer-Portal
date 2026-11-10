# Harbor и Nexus: возможности и границы поддержки

> **Статус на 10.11.2026:** Harbor SOURCE/TARGET — поддерживаемый baseline ветки `main`. Nexus Docker Registry v2 и classic Helm HTTP реализованы **только в [Draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451)**. Региональная приёмка [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) не завершена (**NO-GO**). Наличие функционала в тестовой ветке не означает готовность к установке или выпуску.

## Фактическая матрица

| Функция | Harbor (`main`) | Nexus (только Draft #451) |
| --- | --- | --- |
| Docker/OCI container images | Skopeo, Harbor API и Registry v2 | Skopeo + Docker hosted Registry v2 |
| Helm charts | Harbor OCI через Helm | **Classic Helm hosted HTTP**: index.yaml / tarball / Components API, отдельный адаптер |
| Helm OCI в legacy Nexus 3.69/3.70 | Не относится к Harbor | **Не использовать**; classic Helm hosted не является OCI registry |
| Профили и ACL | Именованные Harbor profiles, scoped credentials | Отдельные Nexus profile/connector/репозиторий и scoped privileges (candidate) |
| Transport | Signed offline bundle, sidecar, signed handoff | Тот же protocol, но без регионального GO |
| External writer / tag conflicts | Preflight и fail-closed на известных конфликтах | Preflight **не даёт** атомарного no-overwrite при параллельном стороннем writer |

Portal переносит образы и Helm charts; он **не переносит** Maven, npm, PyPI или произвольные компоненты Nexus. Не следует выводить поддержку формата из того, что Nexus умеет хранить его самостоятельно.

## Важные различия

- **Harbor**: проекты, repositories, robot accounts, Harbor REST API `/api/v2.0`; текущий поддерживаемый путь описан в [Harbor profiles](harbor-profiles.md) и [руководстве оператора](user-guide.md).
- **Nexus**: Docker hosted registry работает через отдельный endpoint/vhost с `/v2/`, а не обязательно через web UI Nexus. Classic Helm hosted — это HTTP-репозиторий с `index.yaml` и `.tgz`, а не `helm push oci://`.
- Для каждого реального SOURCE/TARGET нужны конкретные repository URLs, scoped privileges и trusted TLS/CA. Пример конфигурации не является доказательством доступа.
- Нельзя обещать, что `overwrite_conflicts=false` предотвращает overwrite при наличии **другого** писателя в том же TARGET registry. До регионального GO нужен проверенный режим эксклюзивного writer/immutability или эквивалентная гарантия.

## Что проверено и чего не хватает

На integrated SHA `7b32643bb04226b986d98c6ae015311528df01b6` выполнены [CI / Quality Gate](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604173) и [disposable Nexus 3.69/3.70 + Harbor 2.14.6 E2E](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604136). Оба — **SUCCESS**; проверки выполнялись на одноразовых CI-сервисах, не в корпоративных изолированных SOURCE/TARGET.

Фактическая региональная проверка включает версии/редакции, ACL, TLS/CA/proxy, физический перенос, подпись/digest, receipt, отказные сценарии и sign-off операторов/безопасности/владельца. До её прохождения #451 остаётся Draft / NO-GO.

## Правильные источники

- [Настройка тестового Nexus-кандидата](nexus-setup.md)
- [Архитектура текущей версии](architecture.md) и [протокол Offline Bundle v1](offline-bundle-v1.md)
- [Runbook региональной приёмки (ветка Draft #451)](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/integration/nexus-oss-release-candidate-451/docs/nexus-regional-acceptance-runbook.md)
- [Issue #414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) — инженерный статус
