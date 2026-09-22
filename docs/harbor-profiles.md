# Harbor profiles

**Статус:** backend management foundation реализована; выбор профиля в transfer workflow ещё не включён.

Harbor Transfer Portal постепенно переходит от одной локальной Harbor-конфигурации к нескольким именованным профилям. Цель — позволить одной installation работать с несколькими доступными Harbor registries без смешивания credentials и без глобального race при переключении.

## Текущий P1 contract

На этом этапе реализованы:

- backward-compatible profile `default`, который представляет существующую single-Harbor конфигурацию;
- дополнительные persistent profiles в server-side settings metadata;
- отдельные server-side credential/CA files для каждого дополнительного profile;
- admin-only CRUD/list/test API;
- audit events без credential/CA content;
- защита default profile от удаления через profile API.

Текущие browse/export/import endpoints **пока продолжают использовать legacy/default Harbor resolution**. Operator selector и привязка операции к profile появятся отдельным slice, чтобы не допустить состояния, когда browse выполнен против одного Harbor, а transfer mutation — против другого.

## Почему нет глобального active profile

Один mutable installation-wide active Harbor небезопасен для многопользовательской работы:

1. operator A выбирает Harbor A;
2. operator B переключает global active profile на Harbor B;
3. уже начатый workflow A может продолжить с другим registry.

Целевой контракт поэтому использует explicit `profile_id` на workflow boundary и immutable profile binding для каждой EXPORT/IMPORT operation.

## Admin API

Базовый prefix:

```text
/api/settings/harbor/profiles
```

Доступ: только `admin`.

Основные endpoints:

- `GET /api/settings/harbor/profiles` — безопасный список profiles;
- `POST /api/settings/harbor/profiles` — создать profile;
- `PATCH /api/settings/harbor/profiles/{profile_id}` — изменить metadata;
- `DELETE /api/settings/harbor/profiles/{profile_id}` — удалить дополнительный profile;
- `PUT /api/settings/harbor/profiles/{profile_id}/credential` — заменить credential;
- `PUT /api/settings/harbor/profiles/{profile_id}/ca` — установить custom CA;
- `DELETE /api/settings/harbor/profiles/{profile_id}/ca` — удалить custom CA;
- `POST /api/settings/harbor/profiles/{profile_id}/test` — проверить соединение.

Credential никогда не возвращается API. Response сообщает только `credential_configured: true|false`. CA content и server-side paths также не возвращаются.

## Default profile и backward compatibility

Profile `default` строится из существующего `HarborSettingsService` и поэтому автоматически видит текущие:

- `HARBOR_URL` / persisted URL override;
- username;
- TLS verify policy;
- legacy managed credential;
- legacy custom CA.

Для существующей installation не требуется ручная миграция Harbor settings.

Default profile нельзя удалить или переименовать через profile API; legacy `/api/settings/harbor` остаётся его management contract на P1.

## Additional profile storage

Без секретов profile metadata сохраняется в `setting_metadata` под versioned key. Credential и CA хранятся отдельно в server-side profile directory рядом с managed Harbor secrets.

Profile metadata содержит только:

- stable id;
- display name;
- base URL;
- username;
- TLS verify flag;
- enabled flag.

Secret content не включается в SQLite metadata и audit events.

## Следующий slice

Перед включением selector в UI необходим единый end-to-end contract:

- explicit profile selection в browser session/workspace;
- Harbor browse API принимает выбранный profile;
- EXPORT/IMPORT operation сохраняет immutable profile id/name/URL-host snapshot;
- orchestrator строит Harbor client по profile, зафиксированному в operation;
- history/audit показывает использованный profile;
- disabled profile нельзя использовать для новой operation;
- существующая operation не drift-ит при последующем редактировании profile.

До завершения этого slice дополнительный profile является управляемой конфигурацией, но не участвует в transfer flow.
