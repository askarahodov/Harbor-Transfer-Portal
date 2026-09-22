# Harbor profiles

**Статус:** актуальный contract настройки и выбора локального Harbor.

Harbor Transfer Portal может хранить несколько именованных Harbor profiles в одной installation. Это не соединяет SOURCE и TARGET между собой: каждый profile описывает только Harbor, достижимый из текущего изолированного контура.

## Зачем нужны profiles

Profiles позволяют один раз настроить несколько локальных Harbor endpoints, например:

- `Production`;
- `DR`;
- `Lab`;
- существующий backward-compatible `Default`.

В SOURCE Export и TARGET Import оператор выбирает нужный profile до начала transfer. После создания operation выбранный profile **фиксируется в operation snapshot** и не может быть молча заменён изменением UI.

## Данные profile

Именованный profile содержит:

- display name;
- Harbor base URL;
- service account / username;
- TLS verification flag;
- managed credential;
- optional managed CA;
- enabled/disabled state.

Credential и содержимое CA никогда не возвращаются через API/UI. API показывает только boolean status `credential_configured` / `custom_ca_configured`.

## Default profile и upgrade compatibility

Profile `Default` является compatibility projection существующей single-Harbor конфигурации:

- persisted `harbor.url`, `harbor.username`, `harbor.verify_tls`;
- `HARBOR_URL`, `HARBOR_USER`, `HARBOR_VERIFY_TLS` как bootstrap fallback;
- текущий managed credential;
- текущий managed CA / deployment CA fallback.

Поэтому существующая installation после migration продолжает работать без ручного переноса Harbor settings. `Default` нельзя удалить через profile CRUD; он по-прежнему редактируется существующими настройками Harbor.

## Secret storage

Для дополнительных profiles non-secret metadata хранится в SQLite. Secret material хранится в persistent data root отдельно для каждого profile:

```text
data/secrets/
└── harbor-profiles/
    └── <profile-id>/
        ├── credential
        └── ca.pem
```

Profile id генерируется backend и не принимается как произвольный path. Secret/CA writes выполняются atomic replace с restrictive file mode.

## Operation pinning

При старте Export или intake Import backend сохраняет:

```text
harbor_profile_id
harbor_profile_name
harbor_url
```

Эти поля являются audit/history snapshot. Skopeo, Helm и Harbor destination validation получают profile id из operation, а не из текущего dropdown.

Для Import это означает одну lineage:

```text
profile selection
  → intake operation
  → cryptographic preview
  → destination plan
  → TARGET preflight
  → mutation
  → receipt
  → retry
```

Retry наследует profile id исходной operation, но создаёт новый name/URL snapshot из текущей конфигурации этого profile. Это позволяет после terminal failure безопасно исправить endpoint/credential и при этом сохраняет правдивый audit новой retry operation.

## Защита от изменения profile во время transfer

Пока существует non-terminal operation, использующая profile, backend отклоняет:

- изменение profile;
- disable profile;
- удаление profile;
- credential rotation;
- CA replacement/removal.

Ошибка: `harbor_profile_in_use` / HTTP 409.

Это исключает ситуацию, когда preview выполнен против одного Harbor, а mutation — уже против другого endpoint или credentials.

## UI

### Settings

`Настройки → Harbor profiles` позволяет администратору:

- создать profile;
- изменить name/URL/username/TLS;
- enable/disable;
- проверить подключение;
- ротировать credential;
- установить/удалить managed CA;
- удалить profile, если он не используется активной operation.

### SOURCE Export

На шаге выбора артефактов выбирается Harbor profile. Browse проектов/repositories/artifacts, preview и export используют этот profile.

### TARGET Import

Harbor profile выбирается до browser upload или incoming discovery. После intake selector блокируется. Destination mapping и import используют profile, записанный в operation.

## API

Safe profile summaries доступны authenticated users:

```text
GET /api/settings/harbor/profiles
```

Admin management:

```text
POST   /api/settings/harbor/profiles
PATCH  /api/settings/harbor/profiles/{profile_id}
DELETE /api/settings/harbor/profiles/{profile_id}
PUT    /api/settings/harbor/profiles/{profile_id}/credential
PUT    /api/settings/harbor/profiles/{profile_id}/ca
DELETE /api/settings/harbor/profiles/{profile_id}/ca
POST   /api/settings/harbor/profiles/{profile_id}/test
```

Harbor browse endpoints принимают optional `harbor_profile_id`. Export request содержит `harbor_profile_id`. Import upload/discovery принимают `harbor_profile_id` и затем используют persisted operation snapshot.

## Ограничение v1

Profile описывает endpoint текущего контура. Наличие нескольких profiles не превращает Portal в сетевой relay и не отменяет offline physical transfer boundary между SOURCE и TARGET.
