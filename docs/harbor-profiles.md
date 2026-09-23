# Harbor profiles

**Статус:** multi-Harbor backend, immutable operation binding и explicit browser workflow
selection реализованы. Installation-wide active profile сохранён только как legacy fallback.

Harbor Transfer Portal может хранить несколько именованных local Harbor profiles. Admin
управляет profile metadata/secrets в **Настройки**, а operator/admin выбирает конкретный
Harbor непосредственно в SOURCE Export или TARGET Import.

## Текущий end-to-end contract :id=current-contract

Для нового browser workflow действует следующая схема:

```text
GET /api/harbor/profiles
  → пользователь выбирает enabled profile
  → browse/connection получают profile_id
  → export preview/start или import intake получают выбранный profile
  → backend сохраняет immutable id/name/url snapshot в Operation
  → все дальнейшие Harbor actions используют operation-bound profile
  → History показывает safe profile evidence
```

Реализовано:

- backward-compatible profile `default`;
- дополнительные persistent profiles с отдельными server-side credential/CA files;
- admin-only create/update/delete/test и secret-management API;
- safe selectable metadata endpoint `GET /api/harbor/profiles`;
- explicit `profile_id` в Harbor browse/connection;
- explicit `harbor_profile_id` в export preview/start;
- explicit profile binding на import upload/discovery и destination planning;
- immutable `harbor_profile_id/name/url` snapshot в operation;
- verification текущей profile identity против persisted snapshot перед mutation;
- profile-aware Skopeo/Helm/destination validation;
- History/operation API без credential/CA content;
- блокировка unsafe mutation referenced profile;
- запрет удаления profile, если он нужен persisted operation evidence;
- session-local browser preference с восстановлением operation-bound profile после reload.

## SOURCE Export :id=source-export

На шаге выбора SOURCE пользователь сначала выбирает Harbor profile. После этого project,
repository, tag/version и connection status загружаются с query `profile_id`.

При смене profile **до preview** frontend сбрасывает зависимые browse/search/selection
данные. Это исключает смешивание artifact metadata, полученных из разных registries.

Preview и start отправляют тот же `harbor_profile_id`. При создании operation backend
фиксирует:

```text
harbor_profile_id
harbor_profile_name
harbor_profile_url
```

После появления operation selector становится read-only. Worker не использует browser
preference или текущий legacy fallback.

## TARGET Import :id=target-import

TARGET profile выбирается **до intake**. Browser upload и incoming-directory discovery
передают query `profile_id`, поэтому operation получает binding до первого TARGET Harbor
inspection.

Destination plan содержит тот же `harbor_profile_id`. Rebind существующей operation на
другой profile запрещён. TARGET inspect/import и destination validation работают против
profile, связанного с operation.

После reload frontend читает `harbor_profile_id` из operation response и восстанавливает
selector как read-only.

## Почему snapshot immutable :id=immutable-binding

Global mutable selection недостаточна при нескольких пользователях:

1. operator A выбирает Harbor A;
2. operator B меняет административный fallback;
3. уже созданная operation A не должна продолжить против Harbor B.

Поэтому registry identity является частью persisted operation evidence. URL snapshot
служит evidence, но credential/CA не копируются в operation. Для выполнения backend
повторно разрешает profile по id и проверяет, что name/url не drift-нули относительно
snapshot.

Если profile identity изменилась после binding, новая mutation такой operation должна
завершиться fail-closed с profile-binding error.

## Legacy fallback :id=legacy-fallback

Settings по-прежнему хранит один installation-wide active profile. В UI он называется
**Legacy fallback Harbor**.

Fallback нужен для:

- старых API callers без explicit profile id;
- совместимости существующих automation/integration;
- административной диагностики во время перехода.

Новые Export/Import browser workflows fallback не используют как источник selection:
они всегда отправляют выбранный profile explicitly.

Legacy fully-null operations, созданные до migration snapshot, остаются читаемыми и
сохраняют прежнюю fallback semantics для совместимости.

## Safe selectable API :id=selectable-api

Authenticated пользователь получает только enabled safe metadata:

```text
GET /api/harbor/profiles
```

Response содержит:

- stable id;
- display name;
- URL;
- marker default profile.

Username, credential, custom CA content и filesystem paths через этот endpoint не
возвращаются.

Harbor browse/connection принимает:

```text
?profile_id=<stable-id>
```

Неизвестный/disabled profile отклоняется backend.

## Admin UI lifecycle :id=admin-ui-lifecycle

В **Настройки → Harbor profiles** admin управляет дополнительными profiles без доступа к
их сохранённым secret values.

Для каждого дополнительного profile доступны:

- редактирование display name, URL, username и TLS verification;
- отдельная ротация credential;
- установка/замена custom CA через PEM;
- удаление custom CA без чтения текущего PEM обратно в browser;
- connection test только для enabled profile;
- enable/disable;
- удаление profile, если backend допускает это по operation evidence.

Disabled profile остаётся видимым в management UI, но исключается из
`GET /api/harbor/profiles`, поэтому operator/admin не может выбрать его для нового
Export/Import workflow.

Profile, который сейчас является **Legacy fallback Harbor**, нельзя отключить. Сначала
admin выбирает другой enabled fallback. Для operation-bound profiles backend независимо
применяет mutation guards: non-terminal operation блокирует metadata/credential/CA
mutation, а persisted historical evidence блокирует delete.

Editor никогда не prefill-ит credential или CA content. Пустые secret/CA fields означают
«не менять», а не «прочитать текущее значение».
## Admin API :id=admin-api

Базовый prefix:

```text
/api/settings/harbor/profiles
```

Доступ: только `admin`.

Основные endpoints:

- `GET /api/settings/harbor/profiles` — management list;
- `POST /api/settings/harbor/profiles` — создать profile;
- `PATCH /api/settings/harbor/profiles/{profile_id}` — изменить metadata/enabled state;
- `DELETE /api/settings/harbor/profiles/{profile_id}` — удалить дополнительный profile;
- `PUT /api/settings/harbor/profiles/{profile_id}/credential` — заменить credential;
- `PUT /api/settings/harbor/profiles/{profile_id}/ca` — установить custom CA;
- `DELETE /api/settings/harbor/profiles/{profile_id}/ca` — удалить custom CA;
- `POST /api/settings/harbor/profiles/{profile_id}/test` — проверить соединение;
- `PUT /api/settings/harbor/profiles/{profile_id}/activate` — изменить legacy fallback.

Credential никогда не возвращается API. CA content и server-side paths также не
возвращаются.

## Mutation safety :id=mutation-safety

Для profile, связанного с non-terminal operation, backend блокирует изменения, которые
могли бы поменять execution identity, включая metadata/credential/CA mutation.

Terminal operation сохраняет historical snapshot. Profile, на который существует
persisted operation evidence, нельзя удалить, иначе history потеряла бы ссылку на
authoritative profile identity.

Legacy fallback можно менять независимо от новых pinned operations; он блокируется только
legacy non-terminal rows, которые действительно ещё зависят от global fallback.

## Default profile и backward compatibility :id=default-profile

Profile `default` представляет прежнюю single-Harbor configuration и использует:

- `HARBOR_URL` / persisted URL override;
- username;
- TLS verify policy;
- legacy managed credential;
- legacy custom CA.

Existing installation не требует ручного переноса settings. Migration добавляет nullable
snapshot columns; старые operation rows остаются валидными.

Default profile нельзя удалить через profile API. Legacy `/api/settings/harbor` остаётся
его bootstrap/management contract.

## Storage и secrets :id=storage

Profile metadata хранится server-side. Credential и custom CA дополнительных profiles
хранятся в отдельных managed files.

Operation snapshot содержит только safe evidence:

- id;
- display name;
- URL.

В operation/history/audit/bundle не должны попадать:

- password/token;
- raw credential;
- CA content;
- private key material;
- filesystem secret path.

## Browser preference :id=browser-preference

Frontend хранит последний выбранный profile id в `sessionStorage` только для удобства.
Это не authority.

При открытии workflow:

1. frontend запрашивает текущий safe selectable list;
2. использует сохранённый id, если profile ещё доступен;
3. иначе выбирает `default`, если он доступен;
4. иначе первый enabled profile;
5. при восстановлении persisted operation backend snapshot имеет приоритет над preference.

Если доступных profiles нет, transfer workflow остаётся fail-closed и предлагает
обратиться к администратору.

## History :id=history

Operation list/detail показывает safe Harbor evidence. Для новых rows доступны profile
name и URL snapshot. Это позволяет доказуемо ответить, против какого registry выполнялась
операция, даже если legacy fallback позже изменился.

Legacy rows без snapshot явно отображаются как legacy/no snapshot.

## Проверки :id=tests

Regression должен покрывать как минимум:

- browse A/B передаёт разные `profile_id`;
- смена SOURCE profile очищает старый browse/selection state;
- export preview/start используют один profile;
- import upload/discovery привязывают выбранный TARGET profile;
- destination plan не может rebind operation;
- reload/resume восстанавливает operation-bound profile;
- profile mutation/deletion guards;
- history не раскрывает secrets и показывает safe snapshot;
- existing default deployment продолжает работать;
- backend/frontend/security/integration scoped CI остаётся зелёным.

Связанные документы:

- [Настройки Portal](settings.md)
- [Frontend](frontend.md)
- [Пользовательское руководство](user-guide.md)
- [History UI](history-ui.md)
- [Security](security.md)
