# Runtime transfer policies

**Статус:** current behavior для P6.2.2 / issue #109.

Администратор управляет безопасно изменяемыми transfer limits через web UI **«Настройки» → «Политики переноса»** или admin-only API:

```text
GET   /api/settings/transfer
PATCH /api/settings/transfer
```

`operator` и `viewer` не имеют доступа к чтению или изменению этих настроек. Backend остаётся authoritative security boundary: UI не может ослабить server-side validation.

## Поддерживаемые параметры

| Параметр | Default | Диапазон | Применение |
|---|---:|---:|---|
| `import_allow_overwrite` | `false` | boolean | hot |
| `import_max_upload_bytes` | 50 GiB | 1 MiB … 1 TiB | hot |
| `bundle_max_archive_bytes` | 50 GiB | 1 MiB … 1 TiB | hot |
| `bundle_max_extracted_bytes` | 100 GiB | 1 MiB … 2 TiB | hot |
| `bundle_max_member_count` | 100000 | 4 … 1000000 | hot |
| `operation_max_concurrent` | 2 | 1 … 32 | restart required |

Дополнительные отношения проверяются атомарно:

- `import_max_upload_bytes <= bundle_max_archive_bytes`;
- `bundle_max_extracted_bytes >= bundle_max_archive_bytes`.

Если комбинация некорректна, PATCH отклоняется целиком; частично сохранённой конфигурации не остаётся.

## Overwrite policy

`import_allow_overwrite=false` остаётся безопасным default. Конфликтующий TARGET artifact не перезаписывается автоматически.

При `true` глобальная политика лишь **разрешает** отдельному import flow предложить overwrite. Сам import всё равно требует явного `overwrite_conflicts=true` и существующего confirmation flow. Таким образом включение настройки не превращает все конфликты в безусловную перезапись.

## Hot settings

После успешного PATCH backend сразу обновляет effective runtime settings для новых export/import orchestration instances. Это влияет на реальные enforcement paths:

- browser upload проверяется против `import_max_upload_bytes`;
- physical incoming/discovery не ограничивается browser upload limit и проверяет размер archive против `bundle_max_archive_bytes`;
- overwrite проверяется server-side через `import_allow_overwrite`;
- Bundle Protocol package verifier/builder использует archive/extracted/member limits.

Это разделение позволяет держать browser upload консервативным и при этом принимать более крупный bundle через штатный air-gap incoming/media path в пределах archive limit.

Значения сохраняются в SQLite metadata и переживают restart.

## Operation concurrency

`operation_max_concurrent` не меняется у уже работающего `OperationManager`: semaphore создаётся на lifecycle backend и его небезопасно заменять во время активных operations.

После сохранения нового значения API возвращает:

- `operation_max_concurrent` — желаемое persisted значение;
- `operation_max_concurrent_active` — значение, действующее в текущем backend process;
- `restart_required_fields: ["operation_max_concurrent"]` — пока значения отличаются.

После штатного restart backend persisted concurrency загружается до создания `OperationManager`, становится active и warning исчезает.

## Audit

Каждое фактическое изменение создаёт `transfer.settings.updated`. Audit metadata содержит только:

- имена изменённых полей;
- old/new значения policy/limit;
- `apply_mode=hot|restart_required`.

В generic transfer settings store намеренно отсутствуют JWT secret, Harbor credential, SOURCE signing private key и другие secrets.

## Retention

Retention auto-delete не представлен как настройка, потому что tested lifecycle автоматического удаления в текущем продукте ещё не реализован. UI явно сообщает об этом вместо декоративного параметра, который ни на что не влияет.

## Операционная процедура

1. Войдите как `admin` и откройте `/settings`.
2. Измените только нужные policy/limit values.
3. Нажмите **«Сохранить политики переноса»**.
4. Если показан restart-required warning, запланируйте штатный restart backend/Compose.
5. После restart снова откройте Settings и убедитесь, что desired и active concurrency совпадают.

Не снижайте limits для обхода security validation и не включайте overwrite как постоянный default без явного эксплуатационного решения.