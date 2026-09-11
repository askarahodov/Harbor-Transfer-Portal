# ADR-005: TLS, пользовательский CA и хранение credential локального Harbor

- **Статус:** принято
- **Дата:** 2026-09-11

## Контекст

Каждый экземпляр Harbor Transfer Portal работает только со своим локальным Harbor. SOURCE не должен знать credential TARGET и наоборот. При этом URL, service account, TLS-политика и credential должны изменяться администратором без перезапуска приложения и без появления пароля/токена в SQLite, API-ответах, audit metadata или frontend state после сохранения.

Docker Compose v1 использует persistent volume `/app/data`, поэтому portal-owned файлы могут сохраняться между перезапусками контейнера. Для bootstrap также требуется совместимость с deployment-managed environment/file secrets.

## Рассмотренные варианты

### Хранить credential в SQLite

Удобно для CRUD, но превращает обычную БД истории и настроек в хранилище секрета, усложняет backup/доступ и противоречит требованию предпочесть file-backed secret. В v1 отклонено.

### Хранить credential только в environment

Просто для bootstrap, но rotation требует изменения deployment и рестарта. Кроме того, process environment не является предпочтительным постоянным хранилищем секрета.

### File-backed credential + безопасные DB overrides

Credential хранится отдельным файлом с режимом `0600`; SQLite содержит только URL, username и TLS-флаг. Такой вариант поддерживает runtime rotation и не смешивает секрет с обычными настройками.

## Решение

1. В `setting_metadata` сохраняются только non-secret overrides: `harbor.url`, `harbor.username`, `harbor.verify_tls`.
2. Credential, установленный через admin API, атомарно записывается в `HARBOR_MANAGED_SECRET_FILE` (по умолчанию `./data/secrets/harbor-password`) с режимом `0600`.
3. Bootstrap fallback разрешён в порядке: managed credential file → `HARBOR_PASSWORD_FILE` → `HARBOR_PASSWORD`. API сообщает только `credential_configured: true/false` и никогда не возвращает источник или значение.
4. `PATCH /api/settings/harbor` физически не содержит поля credential. Rotation выполняется только `PUT /api/settings/harbor/credential`, поэтому изменение URL/username/TLS не может случайно очистить credential.
5. TLS verification по умолчанию включена. `verify_tls=false` требует явного admin action и отображается в UI как предупреждение; автоматического fallback на insecure TLS нет.
6. Пользовательский CA загружается как PEM/CRT через portal API, валидируется стандартным SSL loader и атомарно сохраняется в `HARBOR_MANAGED_CA_FILE` с режимом `0600`. Пользователь не задаёт server-side path через API.
7. Deployment-provided `HARBOR_CA_FILE` остаётся bootstrap fallback, если managed CA отсутствует.
8. Effective Harbor configuration разрешается на каждый запрос из DB overrides + file/env bootstrap, поэтому изменения применяются без рестарта backend.
9. Изменения settings/credential/CA создают `AuditEvent`. Metadata содержит только `changed_fields`; старые/новые значения credential и CA содержимое туда не записываются.
10. API одного экземпляра содержит только один набор полей локального Harbor; секций SOURCE Harbor + TARGET Harbor одновременно не существует.

## Security consequences

- Backup SQLite не содержит Harbor credential, но полный backup установки должен отдельно учитывать защищённый каталог `data/secrets`.
- Тот, кто может читать backend persistent volume, потенциально может прочитать managed credential; права каталога/файла и host-level доступ остаются deployment security boundary.
- Environment secret поддерживается только как bootstrap compatibility path и не считается предпочтительным вариантом для новой установки.
- CA является публичным trust material, но хранится в том же закрытом каталоге, чтобы исключить подмену через произвольные пользовательские пути.
- Harbor connection errors нормализуются; upstream body/credential не возвращаются frontend.

## Совместимость и миграции

Добавляется таблица `audit_events`. Существующие `HARBOR_URL`, `HARBOR_USER`, `HARBOR_PASSWORD`, `HARBOR_VERIFY_TLS`, `HARBOR_CA_FILE` продолжают работать как bootstrap fallback, поэтому существующие Compose-инсталляции не требуют немедленной миграции конфигурации.

## Проверка

Обязательные tests проверяют admin-only mutation, отсутствие credential в API/audit, сохранение credential при PATCH, atomic file mode `0600`, explicit TLS disable, managed CA, sanitized connection-test failures и frontend no-prefill behavior.
