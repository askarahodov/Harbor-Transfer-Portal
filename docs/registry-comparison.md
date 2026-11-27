# Harbor и Nexus: возможности и границы поддержки

> **Статус:** v1.1.0 включает Harbor SOURCE/TARGET и Nexus Docker Registry v2 + classic Helm HTTP ([PR #503](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/503)). Региональная приёмка подтверждена владельцем (owner-attested, Windows); независимые evidence и sign-offs фиксируются в [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) по [шаблонам](acceptance-evidence-templates.md).

## Фактическая матрица

| Функция | Harbor (v1.1.0) | Nexus (v1.1.0) |
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

На предыдущей интеграционной сборке (`7b32643bb04226b986d98c6ae015311528df01b6`) выполнены [CI / Quality Gate](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604173) и [disposable Nexus 3.69/3.70 + Harbor 2.14.6 E2E](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604136). Оба — **SUCCESS**; проверки выполнялись на одноразовых CI-сервисах, не в корпоративных изолированных SOURCE/TARGET.

Фактическая региональная проверка включает версии/редакции, ACL, TLS/CA/proxy, физический перенос, подпись/digest, receipt, отказные сценарии и sign-off операторов/безопасности/владельца. Владелец подтвердил прогон на Windows; артефакты и подписи прикладываются по шаблонам приёмки.

## Правильные источники

- [Настройка тестового Nexus-кандидата](nexus-setup.md)
- [Архитектура текущей версии](architecture.md) и [протокол Offline Bundle v1](offline-bundle-v1.md)
- [Runbook региональной приёмки](nexus-regional-acceptance-runbook.md)
- [Issue #414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) — инженерный статус
