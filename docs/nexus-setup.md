# Nexus Repository — подготовка к приёмке (Draft)

> **Не инструкция для production на \`main\`.** Nexus реализуется в [Draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451) и остаётся **NO-GO** до [региональной приёмки #436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436). В \`main\` Nexus profiles и classic Helm HTTP не являются выпускаемой функцией. Не переносите credentials или конфигурацию из корпоративных контуров в Issues/документацию.

## 1. Не путайте Docker v2, Helm HTTP и OCI

| Repository/endpoint Nexus | Назначение | Транспорт кандидата Portal |
| --- | --- | --- |
| Docker hosted с connector \`/v2/\` | Container images | Skopeo / Registry v2 |
| Helm hosted с \`index.yaml\`, \`.tgz\`, Components API | Классические Helm charts | Отдельный **legacy Helm HTTP** adapter в Draft #451 |
| OCI repository | Другой механизм хранения | Не подменяет собой classic Helm hosted на проверяемых Nexus 3.69/3.70 |

Для legacy Helm hosted **не** выполняйте \`helm push oci://...\`: этот endpoint не является OCI registry. В отличие от старого описания, classic Helm HTTP adapter уже включён в Draft #451 и прошёл disposable E2E. Это всё ещё **не** производственная приёмка.

## 2. Тестовый Docker hosted

1. Создайте \`docker (hosted)\` и отдельный Docker HTTP/HTTPS connector; TLS наружу обеспечивает Nexus либо контролируемый reverse proxy.
2. Проверьте Docker Bearer Token Realm и ответ \`GET https://docker-registry.example.invalid/v2/\`: без credentials ожидается challenge \`401\`, с валидными — разрешённый доступ. \`403\` означает недостаточные права, \`404\` обычно указывает на ошибочный путь. Конкретный result зависит от proxy/auth configuration.
3. Выделите минимальные scoped права на нужный repository: чтение для SOURCE, необходимые write privileges для TARGET. Успешный read-only \`GET /v2/\` **не доказывает** права на push конкретного тега.
4. Используйте отдельно проверенный профиль с собственными credentials, URL и доверенным CA. Не помещайте private keys, пароли, raw HTTP headers или реальное внутреннее DNS-имя в документацию и CI-артефакты.

Пример **вымышленного** vhost: \`https://docker-registry.example.invalid/v2/\` направляется в Docker connector, а \`https://nexus-ui.example.invalid/\` обслуживает UI. Это **разные** endpoints. Конкретный vhost, CA, proxy headers и Bearer realm необходимо подтвердить на площадке.

## 3. Тестовый classic Helm hosted

Настройте отдельный Helm hosted repository с HTTPS-доступом к \`index.yaml\` и chart \`.tgz\`, и соответствующими scoped правами upload/download для Components API. Для нескольких репозиториев и разных ролей проверьте каждый endpoint отдельно. Не считайте успешное получение \`index.yaml\` доказательством возможности публикации.

## 4. Политики безопасности

- TLS verification остаётся включённым; корпоративный CA должен быть доверенным на стороне Portal и Skopeo/HTTP adapter.
- Секреты привязаны к выбранному профилю, не смешиваются между SOURCE и TARGET.
- TARGET \`overwrite_conflicts=false\` требует **внешней гарантии** отсутствия конкурирующих writer (эксклюзивность или immutable tags/серверный conditional publish). Простая повторная проверка тега до push не атомарна.
- Для physical handoff нужен полный signed bundle + \`.sha256\` + signed \`.htp-handoff.json\`, а после импорта — verified receipt.

## 5. Что доказано автоматически

- [Combined CI/Quality Gate #42806604173](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604173) — SUCCESS.
- [Nexus legacy Docker v2 + classic Helm HTTP 3.69/3.70 и disposable Harbor Core E2E #42806604136](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42806604136) — SUCCESS.
- Обе проверки относятся к интегрированному SHA \`7b32643bb04226b986d98c6ae015311528df01b6\`.

**Не доказано**: реальные региональные версии и редакции, внутренние TLS/CA/DNS/proxy, per-repo ACL, физическая изоляция, media handoff, контроль внешних writer и sign-off. Для них используйте [региональный runbook в Draft #451](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/integration/nexus-oss-release-candidate-451/docs/nexus-regional-acceptance-runbook.md) и [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).

## 6. Диагностика

| Код / симптом | Что проверять |
| --- | --- |
| \`nexus_url_invalid\`, \`nexus_not_configured\` | Правильный Docker v2 connector и профиль |
| \`nexus_auth_failed\` (401) | Учётные данные и auth challenge |
| \`nexus_permission_denied\` (403) | ACL выбранного repository, read/write права |
| \`nexus_tls_failed\` | CA, hostname/SAN, цепочка доверия |
| 404 от \`/v2/\` | Путь или reverse proxy указывает на UI, а не connector |
| Helm HTTP error | Путь \`index.yaml\`, chart \`.tgz\`, Components API и scoped права |

Никогда не исправляйте ошибки доступа отключением TLS verification или выдачей administrator privileges без отдельной санкции.
