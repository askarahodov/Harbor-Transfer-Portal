# ADR-005: TLS, custom CA и хранение credential локального Harbor

- **Статус:** принято
- **Дата:** 2026-09-11

## Контекст

Harbor Transfer Portal разворачивается отдельно в контурах `SOURCE` и `TARGET`. Каждая установка имеет право обращаться только к своему локальному Harbor и не должна хранить credential противоположного контура.

Для следующих сервисов — Harbor REST, Skopeo и Helm — нужен единый источник URL, service account, TLS policy и custom CA. При этом admin UI должен позволять менять runtime-настройки, не возвращая существующий password/token в браузер и не сохраняя его в SQLite как обычное значение конфигурации.

Docker Compose v1 использует один persistent volume `/app/data`, backend работает непривилегированным пользователем и должен сохранять настройки после обычного restart.

## Рассмотренные варианты

### Все значения в `.env`

Просто и удобно для bootstrap, но admin UI не может безопасно менять process environment уже запущенного контейнера. Кроме того, password/token становится environment secret, который проще случайно раскрыть через диагностику или неправильный deployment tooling.

### Все значения в SQLite

Даёт простой CRUD API, но помещает credential в ту же БД, где хранятся обычные metadata и операции. Это увеличивает blast radius резервных копий/диагностики и провоцирует возврат secret поля через ORM/API.

### Внешний secret manager

Предпочтителен в инфраструктуре, где он уже существует, но не может быть обязательной runtime-зависимостью v1: портал должен работать автономно в air-gap установке без отдельного Vault/KMS/оркестратора.

### File-backed secret + SQLite metadata overlay

Разделяет секретные и обычные значения, работает внутри текущей Compose-модели и позволяет admin UI безопасно ротировать credential без чтения старого значения.

## Решение

1. Одна установка содержит конфигурацию только **одного локального Harbor**. Контракт SOURCE/TARGET credentials не объединяется.
2. Bootstrap допускает `HARBOR_URL`, `HARBOR_USER`, `HARBOR_VERIFY_TLS`, `HARBOR_CA_FILE` и credential через `HARBOR_PASSWORD_FILE`. `HARBOR_PASSWORD` остаётся совместимым environment fallback, но не является предпочтительным способом хранения.
3. Runtime non-secret overrides (`URL`, username, TLS verification policy и CA mode) сохраняются в `setting_metadata` SQLite.
4. Runtime credential хранится в server-owned `/app/data/secrets/harbor-password`; runtime custom CA — в `/app/data/secrets/harbor-ca.crt`.
5. Managed files публикуются через temporary file + `fsync` + atomic replace и создаются с mode `0600`; образ создаёт `/app/data/secrets` с mode `0700`.
6. API никогда не возвращает password/token, содержимое CA или путь credential. Вместо password/token read endpoint сообщает только `credential_configured`.
7. Credential rotation имеет отдельный endpoint. PATCH обычных non-secret настроек не может неявно очистить существующий credential.
8. Custom CA загружается как PEM/CRT содержимое и сохраняется в фиксированный server-owned path. Web-клиент не передаёт произвольный filesystem path.
9. TLS verification включена по умолчанию. Отключение возможно только явным admin-действием, фиксируется audit event и warning; автоматического fallback на insecure TLS нет.
10. Harbor base URL не может содержать userinfo/password, query, fragment или subpath. Credential передаётся только через отдельный secret contract.
11. Эффективную runtime-конфигурацию использует общий Harbor client dependency, поэтому browse API и последующие Skopeo/Helm сервисы должны опираться на один и тот же resolver, а не создавать собственные копии настроек.
12. Security-sensitive изменения создают persistent audit events только с actor, event type/result и именами изменённых полей. Значения password/token/CA в audit не записываются.

## Приоритет значений

### Non-secret

Runtime SQLite override имеет приоритет над bootstrap env/config.

### Credential

1. managed runtime file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` bootstrap fallback.

### Custom CA

Runtime CA override имеет приоритет над bootstrap `HARBOR_CA_FILE`. Явное удаление runtime CA сохраняет режим `none`, чтобы bootstrap CA не включился обратно неожиданно в рамках того же runtime policy.

## Последствия и компромиссы

- Persistent volume теперь содержит security-sensitive managed files и должен входить в защищённую backup/restore процедуру.
- Администратор не может «посмотреть текущий пароль» в UI — только заменить его. Это намеренное свойство.
- Environment bootstrap secret остаётся менее предпочтительным, но нужен для совместимости и минимального первого запуска.
- V1 не требует внешнего secret manager; при интеграции с Vault/KMS в будущем понадобится новый ADR или замена этого решения с сохранением API non-disclosure contract.
- Модель audit событий в этой задаче минимальна и предназначена для traceability Harbor settings. Полный history/audit API, retention и structured logging принадлежат P6.1 (#21).

## Проверка

Обязательные проверки покрывают:

- admin-only authorization для read/mutations/test connection;
- отсутствие password/token/path в safe response;
- сохранение credential при изменении URL/TLS;
- atomic managed credential с mode `0600`;
- custom CA validation, install и clear;
- запрет credential в Harbor URL;
- sanitized Harbor auth/unavailable errors;
- audit metadata без secret values;
- frontend отсутствие secret prefill и отдельное действие credential rotation;
- явный warning при отключении TLS verification.

## Совместимость и миграции

Добавляется Alembic migration для минимальных `audit_events`; существующая `setting_metadata` используется без изменения схемы. Bootstrap `HARBOR_*` остаются совместимыми с текущими установками, а runtime overrides применяются только после явного admin-изменения.
