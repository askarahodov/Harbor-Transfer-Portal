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

`operator` и `viewer` получают `403` server-side независимо от frontend navigation.

## Управляемые policy values

| Поле API | Назначение | Применение |
|---|---|---|
| `import_allow_overwrite` | разрешает отдельное подтверждённое overwrite-действие для TARGET conflicts | runtime |
| `import_max_upload_bytes` | максимальный browser/incoming intake size | runtime |
| `bundle_max_archive_bytes` | максимальный размер Bundle archive для verifier | runtime |
| `bundle_max_extracted_bytes` | максимальный допустимый extracted payload | runtime |
| `bundle_max_member_count` | максимальное число archive members | runtime |
| `operation_disk_reserve_bytes` | обязательный свободный disk reserve перед operation | runtime |
| `operation_max_concurrent` | число одновременно выполняемых background operations | **после restart backend** |

API также возвращает:

- `effective_operation_max_concurrent` — фактически активное значение текущего процесса;
- `restart_required_fields` — поля, сохранённое значение которых ещё не стало effective.

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

Это предотвращает конфигурацию, в которой Portal принимает upload, который затем заведомо не может пройти archive limit, либо разрешает archive больше допустимого extracted budget.

Дополнительные bounds:

- upload/archive: от 1 MiB до 1 TiB;
- extracted: от 1 MiB до 2 TiB;
- archive members: 4…1 000 000;
- disk reserve: 0…1 TiB;
- concurrent operations: 1…32.

Невалидная комбинация отклоняется целиком с `422`; частичное сохранение не выполняется.

## Runtime и restart semantics

Следующие поля применяются к общему `Settings` object сразу после успешного PATCH и поэтому используются последующими import/verifier/operation paths без restart:

- overwrite policy;
- upload limit;
- archive/extracted/member limits;
- disk reserve.

`operation_max_concurrent` отличается: `OperationManager` создаёт semaphore при startup. Поэтому новое значение сохраняется в `SettingMetadata`, но текущий semaphore не пересоздаётся на лету.

После изменения UI показывает необходимость restart. При следующем startup backend загружает persisted transfer policy **до** `OperationManager.startup()`, и новый concurrency limit становится effective.

## Persistence и precedence

`.env` остаётся bootstrap/default source. Admin override хранится в SQLite `SettingMetadata` под namespace `transfer.*` и имеет приоритет для поддерживаемых policy fields.

Это относится только к перечисленным transfer policies. Через этот механизм нельзя менять:

- `JWT_SECRET`;
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
- безопасные `before`/`after` numeric/boolean values;
- `restart_required_fields`.

Secret material в transfer policy store отсутствует.

PATCH без фактического изменения не создаёт audit event.

## Что намеренно не реализовано

### Retention

Current product пока не имеет полного automatic retention/cleanup lifecycle. Поэтому admin UI не предлагает декоративную retention policy, которая ничего не удаляет и не гарантирует lifecycle.

Retention должен появиться только вместе с tested cleanup behavior, защитой READY/active workspaces, receipts/history policy и эксплуатационной документацией.

### Hot reload concurrency

Backend не пытается динамически заменять semaphore во время активных операций. Это снижает риск гонок и неоднозначного поведения уже запущенных workers.

## Операционный checklist

Перед увеличением limits оцените:

1. размер переносимых payload;
2. одновременное наличие archive, staged/extracted workspace и final payload;
3. число параллельных операций;
4. обязательный disk reserve;
5. backup/retention capacity;
6. ожидаемую длительность verifier/Skopeo/Helm операций.

Не увеличивайте limits только для прохождения неожиданно большого или malformed bundle.

Связанные документы:

- [Руководство администратора](admin-guide.md)
- [OperationManager](operation-manager.md)
- [TARGET import orchestration](import-orchestration.md)
- [Security/trust model](security.md)
- [Deployment/runtime Compose](../deploy/README.md)
