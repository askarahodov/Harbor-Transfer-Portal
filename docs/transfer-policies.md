# Политики переноса и runtime limits

**Статус:** актуальная инструкция для admin-managed transfer policies Harbor Transfer Portal v1.

Этот документ описывает только те transfer policy values, которые текущий backend действительно умеет сохранять и применять. Он не превращает все `.env` parameters в runtime settings и не заменяет deployment/security configuration.

## Доступ

Раздел **«Настройки» → «Политики переноса»** доступен только роли `admin`.

Backend contract:

```text
GET   /api/settings/transfer
PATCH /api/settings/transfer
```

`operator` и `viewer` получают `403` server-side независимо от frontend navigation. При этом `operator` по-прежнему может задавать разрешённые mapping/override только для собственной import operation; это не изменяет global policy.

## Управляемые policy values

| Поле API | Назначение | Применение |
|---|---|---|
| `import_allow_overwrite` | разрешает отдельное подтверждённое overwrite-действие для TARGET conflicts | runtime |
| `import_max_upload_bytes` | максимальный размер browser upload | runtime |
| `bundle_max_archive_bytes` | максимальный размер Bundle archive, включая physical incoming/discovery и verifier | runtime |
| `bundle_max_extracted_bytes` | максимальный допустимый extracted payload | runtime |
| `bundle_max_member_count` | максимальное число archive members | runtime |
| `operation_disk_reserve_bytes` | обязательный свободный disk reserve перед operation | runtime |
| `operation_max_concurrent` | число одновременно выполняемых background operations | **после restart backend** |
| `export_bundle_retention_seconds` | срок хранения completed SOURCE publication | runtime |
| `import_bundle_retention_seconds` | срок хранения failed/partial TARGET bundle для retry | runtime |
| `storage_cleanup_interval_seconds` | интервал фоновой cleanup проверки | runtime |
| `destination_container_image_project` | global fallback TARGET project для container images | для новых destination plans |
| `destination_helm_chart_project` | global fallback TARGET project для Helm charts | для новых destination plans |
| `destination_project_mappings` | global fallback `SOURCE project → TARGET project` | для новых destination plans |

API также возвращает:

- `effective_operation_max_concurrent` — фактически активное значение текущего процесса;
- `restart_required_fields` — поля, сохранённое значение которых ещё не стало effective;
- `destination_mapping_revision` — monotonic revision текущих TARGET mapping defaults.

Mapping defaults по умолчанию пустые. Portal не подставляет скрытые `docker`, `helm` или другие project names. Если итоговый TARGET project не разрешён ни global policy, ни значениями конкретного Import, artifact остаётся `import_destination_unmapped` и import fail-closed.

## TARGET mapping defaults и precedence

Global defaults нужны, чтобы admin один раз задал типичную раскладку TARGET Harbor, но оператор сохранил возможность безопасно уточнить destination для конкретной передачи.

При построении **нового** destination plan effective mapping формируется в таком порядке, от наиболее специфичного к fallback:

1. per-artifact override конкретной import operation;
2. explicit `SOURCE project → TARGET project` mapping текущей import operation;
3. admin-managed `destination_project_mappings`;
4. explicit default project типа текущей import operation;
5. admin-managed default project соответствующего типа.

Global policy не может менять registry host/scheme, Harbor credential, TLS trust или artifact name/tag/version. Она выбирает только нормализованный Harbor project. Backend принимает project names формата `lowercase`, цифры и внутренние `.`, `_`, `-`; authority, URL и repository path вместо project отклоняются.

После resolution обычный destination validator всё равно проверяет, что TARGET project существует и service account имеет write capability. Наличие global default **не обходит** access checks. Missing project или отсутствие write permission блокируют plan до любой mutation Harbor.

### Revision и immutable plan

Каждое фактическое изменение mapping defaults увеличивает `destination_mapping_revision` на единицу. PATCH без изменений revision не увеличивает.

Новый destination plan сохраняет revision, с которой были разрешены defaults. Revision входит в canonical `plan_hash`, поэтому изменение policy нельзя незаметно представить как тот же confirmed plan.

Изменение global policy после построения plan **не переписывает** уже сохранённый plan. Он продолжает содержать прежние final TARGET references, `plan_hash` и `mapping_policy_revision`. Чтобы применить новую policy к READY import operation, оператор явно запускает новую проверку destination plan. Только rebuilt plan получает новую revision.

Для совместимости планы, созданные до появления mapping policy, трактуются как revision `0`. Canonical hash revision `0` сохраняет прежнее представление и не инвалидирует уже сохранённые READY plans после upgrade.

## Default deny для overwrite

Штатное значение:

```text
import_allow_overwrite=false
```

Даже при `true` конфликт не overwrite-ится автоматически. TARGET import всё равно требует:

1. verified preview;
2. классификацию `CONFLICT`;
3. явный `overwrite_conflicts=true` в execute request;
4. достаточную роль;
5. повторную server-side policy проверку.

Изменение policy только разрешает этот путь; оно не превращает overwrite в default action.

## Связанные limits

Backend валидирует комбинацию целиком:

```text
import_max_upload_bytes <= bundle_max_archive_bytes <= bundle_max_extracted_bytes
```

`import_max_upload_bytes` ограничивает только browser upload. Штатный air-gap fallback через физически скопированный archive + `.sha256` в incoming directory может быть больше browser limit, но не может превышать `bundle_max_archive_bytes`. После discovery тот же archive всё равно проходит обычную schema/signature/checksum и archive/extracted/member verification.

Это позволяет держать browser upload консервативным и переносить крупные bundle через USB/HDD без искусственного повышения browser limit.

Дополнительные bounds:

- upload/archive: от 1 MiB до 1 TiB;
- extracted: от 1 MiB до 2 TiB;
- archive members: 4…1 000 000;
- disk reserve: 0…1 TiB;
- concurrent operations: 1…32;
- SOURCE/TARGET retention: от 1 часа до 365 суток;
- cleanup interval: от 1 минуты до 24 часов.

Невалидная комбинация отклоняется целиком с `422`; частичное сохранение не выполняется.

## Runtime и restart semantics

Следующие поля применяются к общему `Settings` object сразу после успешного PATCH и поэтому используются последующими import/verifier/operation paths без restart:

- overwrite policy;
- browser upload limit;
- archive/extracted/member limits;
- disk reserve;
- SOURCE/TARGET retention;
- cleanup interval.

Retention service читает runtime-effective retention values при каждой cleanup итерации. Новый cleanup interval используется следующими итерациями periodic task; restart backend не требуется.

Destination mapping defaults не копируются в process `Settings`: planner читает persistent snapshot при каждом новом `build_destination_plan`. Поэтому они также не требуют restart, но намеренно не воздействуют на уже сохранённый plan.

`operation_max_concurrent` отличается: `OperationManager` создаёт semaphore при startup. Поэтому новое значение сохраняется в `SettingMetadata`, но текущий semaphore не пересоздаётся на лету.

После изменения UI показывает необходимость restart. При следующем startup backend загружает persisted transfer policy **до** `OperationManager.startup()`, и новый concurrency limit становится effective.

## Persistence и precedence

`.env` остаётся bootstrap/default source для numeric/boolean transfer limits, включая retention. Admin override хранится в SQLite `SettingMetadata` под namespace `transfer.*` и имеет приоритет для поддерживаемых policy fields.

Для retention bootstrap defaults:

```text
EXPORT_BUNDLE_RETENTION_SECONDS=604800
IMPORT_BUNDLE_RETENTION_SECONDS=604800
STORAGE_CLEANUP_INTERVAL_SECONDS=3600
```

В admin UI они представлены в понятных единицах: SOURCE/TARGET retention — в сутках, cleanup interval — в минутах. API и persistent store продолжают использовать секунды, чтобы не менять backend lifecycle contract.

Destination mapping policy хранится в том же persistent store под namespace `transfer.destination_mapping.*`; вместе с остальной SQLite БД она попадает в штатный backup/restore lifecycle.

Через этот механизм нельзя менять:

- `JWT_SECRET`;
- Harbor URL/registry host через mapping policy;
- Harbor credential;
- SOURCE private signing key;
- filesystem paths;
- executable paths;
- TLS CA contents;
- login/auth security settings.

Такие значения остаются в специализированных secure workflows или deployment configuration.

## Audit

Каждое фактическое изменение создаёт `AuditEvent`:

```text
transfer.policy.updated
```

Metadata содержит:

- `changed_fields`;
- безопасные `before`/`after` numeric/boolean/mapping values;
- old/new `destination_mapping_revision`, когда изменена mapping policy;
- `restart_required_fields`.

Secret material в transfer policy store отсутствует. Mapping audit не содержит Harbor credential, token, CA private material или signing keys.

PATCH без фактического изменения не создаёт audit event и не увеличивает mapping revision.

## Что намеренно не реализовано

### Hot reload concurrency

Backend не пытается динамически заменять semaphore во время активных операций. Это снижает риск гонок и неоднозначного поведения уже запущенных workers.

## Операционный checklist

Перед увеличением limits или изменением destination defaults оцените:

1. размер переносимых payload;
2. одновременное наличие archive, staged/extracted workspace и final payload;
3. число параллельных операций;
4. обязательный disk reserve;
5. backup/retention capacity;
6. ожидаемую длительность verifier/Skopeo/Helm операций;
7. существование новых TARGET projects и write permissions service account;
8. какие READY destination plans уже подтверждены и должны остаться на прежней revision.

Не увеличивайте limits только для прохождения неожиданно большого или malformed bundle и не меняйте global mapping только для обхода access/conflict validation конкретного Import.

Связанные документы:

- [Руководство администратора](admin-guide.md)
- [OperationManager](operation-manager.md)
- [TARGET import orchestration](import-orchestration.md)
- [Security/trust model](security.md)
- [Deployment/runtime Compose](../deploy/README.md)
