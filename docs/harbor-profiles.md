# Harbor profiles

**Статус:** управление несколькими profiles и installation-wide active profile реализовано;
immutable per-operation profile binding остаётся отдельным следующим slice.

Harbor Transfer Portal поддерживает backward-compatible default Harbor profile и
дополнительные именованные profiles. Admin управляет ими через **Настройки → Harbor
profiles**.

## Текущий contract :id=current-contract

Реализовано:

- profile `default`, представляющий существующую single-Harbor configuration;
- дополнительные persistent profiles с отдельными server-side credential/CA files;
- admin-only create/update/delete/list/test API;
- выбор одного authoritative **active profile** для installation;
- selector active profile в Settings;
- audit events без credential/CA content;
- защита default/active profile от unsafe mutations;
- блокировка смены active profile при blocking transfer operations.

Harbor browse и transfer services получают конфигурацию через
`HarborSettingsService.resolve()/build_client()`, то есть используют server-side active
profile. Browser не передаёт arbitrary registry URL или credential в export/import
request.

Export operation creation дополнительно проходит под общей profile boundary с activation:
active profile нельзя переключить между preview/resolution и созданием blocking export
operation.

## Текущая safety boundary :id=active-profile-safety

Installation-wide active profile — промежуточный v1 contract, а не финальная multi-user
модель.

Backend запрещает activation другого profile, пока существуют non-terminal blocking
operations. Mutation credential/CA/metadata active profile также блокируется в этот период.
Это предотвращает обычный drift уже выполняющейся operation после ручного переключения
profile.

Однако operation record пока не хранит immutable profile id/name/URL-host snapshot.
Поэтому target architecture по-прежнему требует explicit operation binding, особенно для
полного multi-user concurrency contract.

## Почему целевой contract не должен оставаться global-active :id=target-contract

Один mutable installation-wide selection потенциально создаёт race между пользователями:

1. operator A начинает workflow против Harbor A;
2. operator B меняет selection на Harbor B;
3. если operation не зафиксировала profile, дальнейшие шаги могут разрешить Harbor B.

Текущие blocking guards уменьшают этот риск, но целевая модель должна быть сильнее:

- browser/workspace передаёт explicit `profile_id`;
- backend валидирует его server-side;
- EXPORT/IMPORT operation сохраняет immutable profile snapshot;
- orchestrator строит Harbor client из snapshot operation, а не из текущего global state;
- history/audit показывает использованный profile;
- редактирование/disable profile не меняет уже созданную operation.

Этот следующий slice отслеживается существующим multi-Harbor epic.

## Admin API :id=admin-api

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
- `POST /api/settings/harbor/profiles/{profile_id}/test` — проверить соединение;
- `PUT /api/settings/harbor/profiles/{profile_id}/activate` — сделать enabled profile active.

Credential никогда не возвращается API. Response сообщает только
`credential_configured: true|false`. CA content и server-side paths также не возвращаются.

## Default profile и backward compatibility :id=default-profile

Profile `default` строится из существующего `HarborSettingsService` и автоматически
использует текущие:

- `HARBOR_URL` / persisted URL override;
- username;
- TLS verify policy;
- legacy managed credential;
- legacy custom CA.

Для существующей installation ручная миграция Harbor settings не требуется.

Default profile нельзя удалить или переименовать через profile API; legacy
`/api/settings/harbor` остаётся его management contract.

## Additional profile storage :id=storage

Без secret values profile metadata сохраняется в `setting_metadata`. Credential и CA
дополнительного profile хранятся отдельно в server-side profile directory рядом с managed
Harbor secrets.

Profile metadata содержит:

- stable id;
- display name;
- base URL;
- username;
- TLS verify flag;
- enabled flag.

Secret content не включается в SQLite metadata и audit events.

## Следующий slice :id=next-slice

До завершения immutable operation binding active-profile model следует считать
installation-wide управляемым selector, а не per-user selection.

Следующий architecture slice:

- explicit profile selection в browser workspace;
- Harbor browse API принимает выбранный profile;
- EXPORT/IMPORT operation сохраняет immutable profile id/name/URL-host snapshot;
- orchestrator использует profile, зафиксированный в operation;
- history/audit показывает использованный profile;
- disabled profile нельзя использовать для новой operation;
- существующая operation не drift-ит при последующем редактировании profile.

Связанные документы:

- [Настройки Portal](settings.md)
- [Frontend](frontend.md)
- [Руководство администратора](admin-guide.md)
- [Security](security.md)
