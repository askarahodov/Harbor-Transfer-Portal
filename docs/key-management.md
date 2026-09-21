# Signing и trusted-key management

**Статус:** актуальная инструкция для managed key lifecycle Harbor Transfer Portal v1.

Этот документ описывает admin-managed SOURCE signing identity и TARGET trusted SOURCE public keys. Normative Bundle v1 contract остаётся в [offline-bundle-v1.md](offline-bundle-v1.md); данный workflow не меняет формат bundle и не добавляет private key в переносимый пакет.

## 1. Security boundary

Harbor Transfer Portal использует две разные категории key material:

- **SOURCE signing private key** — secret; существует только на SOURCE и используется для `manifest.sig`;
- **TARGET trusted SOURCE public keys** — public trust anchors; TARGET verifier принимает bundle только если подпись подтверждается хотя бы одним active trusted key.

Normal API/UI никогда не возвращает SOURCE private key после установки.

Admin API:

```text
GET    /api/settings/keys
POST   /api/settings/keys/signing/generate
GET    /api/settings/keys/signing/public
GET    /api/settings/keys/signing/trust-package
PUT    /api/settings/keys/signing
POST   /api/settings/keys/trusted/package?confirm=true
POST   /api/settings/keys/trusted
PUT    /api/settings/keys/trusted/{fingerprint}
PATCH  /api/settings/keys/trusted/{fingerprint}
DELETE /api/settings/keys/trusted/{fingerprint}
```

Все endpoints доступны только роли `admin`. `operator` и `viewer` получают `403` server-side.

TARGET mutations дополнительно требуют явного server-side confirmation: `confirm=true` в JSON для add/replace/enable/disable и `?confirm=true` для remove. UI спрашивает пользователя через confirmation dialog и только после согласия передаёт этот flag backend. Вызов API без подтверждения получает `409 trusted_key_confirmation_required` и не меняет trust store/audit.

## 2. Stable key id / fingerprint

Key id совпадает с fingerprint, который уже использует Bundle package service:

```text
sha256:<64 lowercase hex>
```

Он вычисляется как SHA-256 от raw 32-byte Ed25519 public key.

Один и тот же public key поэтому имеет одинаковый fingerprint:

- на SOURCE после установки private key;
- в TARGET trust list;
- в `signing_key_fingerprint` результата build/verify.

Bundle v1 manifest не получает отдельное поле `key_id`: verifier проверяет подпись против active trusted set и возвращает fingerprint реально совпавшего ключа. Это сохраняет совместимость protocol v1 и поддерживает overlap rotation.

## 3. SOURCE signing identity

В разделе **Настройки → Signing и trust keys** SOURCE показывает только:

- `configured` / `not configured`;
- public fingerprint.

Private key content, filesystem path и raw bytes через normal API/UI не выдаются.

### Автоматическая первичная инициализация

Для нового SOURCE рекомендуемый workflow не требует OpenSSL или ручной работы с private PEM.

Admin в **Настройки → Signing и trust keys** нажимает **Создать signing identity**. Backend:

1. проверяет authenticated роль `admin` и runtime mode `SOURCE`;
2. убеждается, что signing identity ещё не существует;
3. генерирует Ed25519 private key server-side;
4. атомарно сохраняет его в `BUNDLE_SIGNING_PRIVATE_KEY_FILE` с mode `0600`;
5. возвращает только action и public fingerprint;
6. пишет audit event `signing.key.generated` с authenticated actor и runtime metadata.

Повторная генерация существующей identity возвращает `409 signing_key_already_configured` и **не выполняет rotation**.

После генерации рекомендуемый first-run flow — **Скачать trust package**.
`GET /api/settings/keys/signing/trust-package` формирует deterministic
`.htp-trust.tar.gz`, содержащий только:

```text
source-signing-public.pem
identity.json
fingerprint.sha256
```

`identity.json` фиксирует protocol kind/schema, Ed25519 algorithm и fingerprint.
TARGET не доверяет metadata на слово: при импорте он заново парсит public key,
вычисляет fingerprint и сверяет его одновременно с `identity.json` и
`fingerprint.sha256`.

Private key в trust package отсутствует и через normal API не выдаётся.
Отдельный `GET /api/settings/keys/signing/public` сохранён как advanced/manual
вариант для rotation и совместимости.

На TARGET admin выбирает **Импортировать SOURCE trust package** и явно
подтверждает enrollment. Повторный импорт той же active identity идемпотентен
(`action=unchanged`); если identity была disabled, повторный import включает
существующий trust slot. SOURCE private key никогда не переносится между контурами.

Export выполняет signing preflight до создания операции и до Skopeo/Helm materialization.
Если identity отсутствует, backend возвращает `409 bundle_signing_key_not_configured`.
SOURCE admin может создать identity прямо из export UX и повторить запуск; operator получает
инструкцию обратиться к admin.

### Установка существующего ключа

Ручной PEM import разрешён только для первичной настройки, когда active signing identity ещё отсутствует. После появления active identity прямой `PUT /api/settings/keys/signing` возвращает `409 signing_rotation_required`.

Это намеренно: уже настроенный SOURCE нельзя ротировать обходя overlap workflow.

### Staged rotation

Rotation состоит из трёх server-side состояний: active, pending и TARGET overlap.

1. SOURCE admin нажимает **Подготовить rotation**.
2. Backend создаёт новый Ed25519 private key в `BUNDLE_PENDING_SIGNING_PRIVATE_KEY_FILE` с mode `0600`; active key не меняется.
3. SOURCE скачивает pending trust package через `GET /api/settings/keys/signing/rotation/trust-package`.
4. TARGET admin импортирует package; старый и новый public fingerprint остаются enabled одновременно.
5. SOURCE admin активирует pending identity, передавая **точный expected fingerprint**.
6. Backend сверяет fingerprint pending key и только затем атомарно заменяет active key.
7. Старый TARGET public key остаётся enabled на overlap/rollback window.
8. После impact-check старый trust можно disable/remove.

Повторный prepare при уже существующем pending key возвращает `409 pending_signing_key_already_configured`. Cancel удаляет только pending key и не затрагивает active identity. Private key bytes ни на одном этапе через API не возвращаются.

## 4. TARGET trust set

### Рекомендуемый first-run bootstrap

Fresh install выполняется так:

```text
SOURCE admin
  → создать signing identity
  → скачать .htp-trust.tar.gz
  → физически перенести package
TARGET admin
  → импортировать SOURCE trust package
  → проверить fingerprint
  → TARGET готов к verification/import
```

Trust package import доступен только роли `admin`, требует `confirm=true`,
ограничен по размеру `BUNDLE_TRUST_PACKAGE_MAX_BYTES` и принимает ровно
allowlist из трёх regular files. Extra entries, duplicates, symlink/hardlink,
malformed metadata, private-key substitution и fingerprint mismatch отклоняются
до изменения trust store.

TARGET принимает только Ed25519 public PEM. Private key, malformed PEM или другой key type отклоняются до изменения trust set.

Managed active key хранится под server-generated именем:

```text
<fingerprint-hex>.pem
```

Disabled key хранится под server-generated именем:

```text
<fingerprint-hex>.disabled
```

Пользователь не задаёт filesystem path или filename.

`BundlePackageService` продолжает загружать только active `*.pem`; поэтому disable реально меняет verifier enforcement, а не только UI metadata.

## 5. Совместимость с ранее установленными keys

Ранее deployment мог вручную помещать trusted Ed25519 public keys в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR` под произвольным именем `*.pem`.

Managed UI умеет их читать и вычислять fingerprint. При первом enable/disable/replace такой key канонизируется в server-generated fingerprint filename.

Malformed/symlink/non-file entry считается ошибкой key store и не становится доверенным автоматически.

## 6. Add / replace / disable / enable / remove

### Add

Admin выбирает public PEM и явно подтверждает добавление. Backend проверяет key и лимит `BUNDLE_MAX_TRUSTED_KEYS`, затем атомарно публикует active key.

### Replace

Replace принимает fingerprint существующего trusted key и новый Ed25519 public PEM. Новый key полностью валидируется до mutation.

Для нового fingerprint backend выполняет немедленный atomic cutover **внутри существующего trust slot**: содержимое файла old key атомарно заменяется новым key, сохраняя active/disabled state slot. Поэтому в любой момент количество unique identities и active `*.pem` не увеличивается до `max+1` и не падает до нуля.

После commit Portal пытается привести filename к canonical `<new-fingerprint>.pem` или `<new-fingerprint>.disabled`. Эта rename-операция является post-commit hygiene: если она не удалась, key уже authoritative по содержимому, остаётся discoverable через fingerprint и операция не возвращает ложный failure.

Failure до atomic commit оставляет old key authoritative. Replace одного key разрешён даже когда trust set уже достиг `BUNDLE_MAX_TRUSTED_KEYS`; обычный `POST add` при том же заполненном лимите продолжает возвращать `409 trusted_key_limit_exceeded`.

Важно: **Replace — это немедленный cutover**, поэтому bundle, подписанные old key, после успешной замены больше не должны проходить verifier. Если old/new deliveries должны сосуществовать во время миграции, используйте плановый overlap workflow из раздела 7, а не Replace.

### Disable

Disable сохраняет public key, но переводит его из active `*.pem` в managed `.disabled`. После этого новые verification attempts больше не доверяют этому key.

### Enable

Enable возвращает key в active `*.pem`; verifier начинает использовать его немедленно, restart backend не требуется.

### Remove

Remove удаляет trusted key из managed trust set. Используйте remove только после окончания overlap window и организационного срока, когда старые bundle больше не должны приниматься.

## 7. Overlap rotation

TARGET поддерживает несколько active Ed25519 public keys одновременно до `BUNDLE_MAX_TRUSTED_KEYS`.

Пример безопасной схемы:

```text
old key active
    ↓
add new key
    ↓
old + new active
    ↓
rotate SOURCE private key
    ↓
старые и новые deliveries могут быть проверены
    ↓
disable old key
    ↓
только new key active
    ↓
remove old key после окончания rollback/delivery window
```

Плановая overlap rotation и **Replace** решают разные задачи: overlap сохраняет доверие к старым deliveries на период миграции, а Replace атомарно переключает один trust slot на новую identity без временного роста trust-set count.

Это не требует изменения Bundle v1 archive или manifest.

## 8. Limits

`BUNDLE_KEY_MATERIAL_MAX_BYTES` ограничивает один private/public PEM upload и чтение managed key file. Default:

```text
65536
```

Это deployment/security bound и не переносится в generic runtime transfer-policy UI.

Trust package целиком ограничен `BUNDLE_TRUST_PACKAGE_MAX_BYTES`; default — `131072` bytes.

Количество trusted keys дополнительно ограничивает `BUNDLE_MAX_TRUSTED_KEYS`. Atomic trust-slot replace не занимает дополнительный slot; обычный add занимает.

## 9. Audit

SOURCE events:

```text
signing.key.generated
signing.key.installed
signing.trust_package.exported
signing.rotation.prepared
signing.rotation.trust_package.exported
signing.rotation.activated
signing.rotation.cancelled
```

TARGET events:

```text
trust.source_identity.imported
trust.key.added
trust.key.replaced
trust.key.enabled
trust.key.disabled
trust.key.removed
```

Audit metadata содержит только безопасные identifiers/actions. Для replace сохраняются старый и новый public fingerprint; PEM contents не записываются.

Отказ из-за отсутствующего server-side confirmation не создаёт mutation audit event, потому что trust set не изменился.

## 10. Backup и restore

SOURCE backup, содержащий `BUNDLE_SIGNING_PRIVATE_KEY_FILE` и/или `BUNDLE_PENDING_SIGNING_PRIVATE_KEY_FILE`, является secret backup и должен защищаться как credential/private key material. Offline lifecycle qualification проверяет восстановление active key, pending key и TARGET overlap trust set с restrictive permissions.

TARGET trust directory не содержит private secrets, но определяет security trust policy и также должен входить в consistent configuration backup.

После restore проверьте через admin UI:

- SOURCE fingerprint совпадает с ожидаемой signing identity;
- TARGET active/disabled fingerprints соответствуют утверждённому trust set;
- test bundle, подписанный active SOURCE key, проходит TARGET verification.

## 11. Что не следует делать

Не следует:

- передавать SOURCE private key на TARGET;
- сохранять private key в Git, issue, PR, logs или screenshots;
- принимать public key из того же недоверенного канала только потому, что по нему пришёл bundle;
- давать пользователю возможность задавать key filename/path;
- обходить server-side `confirm=true` собственным неинтерактивным клиентом без отдельного операторского решения;
- использовать Replace вместо overlap rotation, если старые deliveries ещё должны оставаться валидными;
- менять расширение disabled key вручную как штатную admin procedure;
- удалять old trust key до завершения overlap window.

Связанные документы:

- [Security/trust model](security.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Package service](package-service.md)
- [Руководство администратора](admin-guide.md)
- [Deployment/runtime Compose](../deploy/README.md)
