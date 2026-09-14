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
PUT    /api/settings/keys/signing
POST   /api/settings/keys/trusted
PATCH  /api/settings/keys/trusted/{fingerprint}
PUT    /api/settings/keys/trusted/{fingerprint}/replace
DELETE /api/settings/keys/trusted/{fingerprint}
```

Все endpoints доступны только роли `admin`. `operator` и `viewer` получают `403` server-side.

TARGET mutation endpoints `add`, `enable/disable`, `replace` и `remove` дополнительно требуют query parameter `confirm=true`. UI передаёт его только после явного подтверждения оператора. Отсутствующий или `false` confirmation возвращает стабильный `409 key_mutation_confirmation_required` **до** filesystem mutation и audit event.

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

### Установка

Admin выбирает незашифрованный PEM Ed25519 private key. Backend:

1. проверяет contour `SOURCE`;
2. проверяет bounded size;
3. парсит PEM до изменения файла;
4. отклоняет public/RSA/другой key type;
5. нормализует key в PKCS#8 PEM;
6. пишет temporary file в server-controlled directory;
7. выполняет `fsync`, mode `0600` и atomic `os.replace`;
8. возвращает только fingerprint/action;
9. пишет audit event без key material.

Невалидный input не заменяет существующий signing key.

### Rotation

Если signing key уже существует, тот же workflow считается rotation. После успешной atomic replacement новые bundle подписываются новым key.

Перед rotation SOURCE сначала обеспечьте trust overlap на TARGET:

1. сгенерируйте новую Ed25519 key pair в доверенной административной среде;
2. добавьте новый public key на TARGET, не отключая старый;
3. убедитесь, что TARGET показывает оба fingerprint как `active`;
4. ротируйте SOURCE private key;
5. выдержите окно доставки bundle, созданных старым ключом;
6. после завершения окна отключите или удалите старый TARGET public key.

## 4. TARGET trust set

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

`BundlePackageService` продолжает загружать только active `*.pem`; поэтому disable/replace реально меняют verifier enforcement, а не только UI metadata.

## 5. Совместимость с ранее установленными keys

Ранее deployment мог вручную помещать trusted Ed25519 public keys в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR` под произвольным именем `*.pem`.

Managed UI умеет их читать и вычислять fingerprint. При первом enable/disable/replace такой key канонизируется в server-generated fingerprint filename.

Malformed/symlink/non-file entry считается ошибкой key store и не становится доверенным автоматически.

## 6. Add / replace / disable / enable / remove

Все TARGET mutations ниже требуют и UI-confirmation, и server-side `confirm=true`.

### Add

Admin выбирает public PEM и подтверждает добавление. Backend проверяет key и лимит `BUNDLE_MAX_TRUSTED_KEYS`, затем атомарно публикует active key.

Повторный add **того же fingerprint** по-прежнему нормализует managed file и возвращает `replaced` для того же key id. Это не является rotation на новый fingerprint.

### Replace old fingerprint → new public key

Для реальной замены trust identity используется отдельная операция:

```text
PUT /api/settings/keys/trusted/{old_fingerprint}/replace?confirm=true
```

Body содержит новый Ed25519 public PEM. Backend:

1. проверяет наличие старого fingerprint и валидность нового key;
2. отклоняет replacement с тем же fingerprint или fingerprint, уже существующим в trust set;
3. сохраняет enabled/disabled состояние заменяемого key;
4. атомарно публикует новый managed key через temporary file + `os.replace`;
5. **только после успешной публикации нового key** удаляет старый fingerprint;
6. при failure публикации не трогает old key;
7. при failure удаления выполняет safety-first rollback к old canonical key;
8. пишет audit только после успешного завершения.

Replace занимает слот старого key, поэтому разрешён даже когда `BUNDLE_MAX_TRUSTED_KEYS` уже заполнен. Операция не создаёт дополнительный постоянный slot и не позволяет обойти общий лимит.

### Disable

Disable сохраняет public key, но переводит его из active `*.pem` в managed `.disabled`. После этого новые verification attempts больше не доверяют этому key.

### Enable

Enable возвращает key в active `*.pem`; verifier начинает использовать его немедленно, restart backend не требуется.

### Remove

Remove удаляет trusted key из managed trust set. Используйте remove только после окончания overlap window и организационного срока, когда старые bundle больше не должны приниматься.

## 7. Rotation strategies

### Overlap rotation

TARGET поддерживает несколько active Ed25519 public keys одновременно до `BUNDLE_MAX_TRUSTED_KEYS`.

Пример безопасной схемы, когда старые deliveries должны продолжать приниматься:

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

### Direct replace

`old fingerprint → new key` полезен, когда policy требует немедленно прекратить доверие старому fingerprint либо trust set уже заполнен. После successful replace verifier доверяет новому key вместо старого. Поэтому direct replace **не создаёт overlap window** и должен использоваться только когда старые bundle больше не обязаны проходить verification.

Обе схемы не требуют изменения Bundle v1 archive или manifest.

## 8. Limits

`BUNDLE_KEY_MATERIAL_MAX_BYTES` ограничивает один private/public PEM upload и чтение managed key file. Default:

```text
65536
```

Это deployment/security bound и не переносится в generic runtime transfer-policy UI.

Количество trusted keys дополнительно ограничивает `BUNDLE_MAX_TRUSTED_KEYS`. Replace существующего fingerprint сохраняет количество logical trusted keys и поэтому допустим при заполненном лимите.

## 9. Audit

SOURCE events:

```text
signing.key.installed
signing.key.rotated
```

TARGET events:

```text
trust.key.added
trust.key.replaced
trust.key.enabled
trust.key.disabled
trust.key.removed
```

Обычные mutation events содержат action и public fingerprint. `trust.key.replaced` дополнительно содержит:

```text
old_fingerprint
new_fingerprint
```

Private/public PEM contents в audit не записываются. Запрос, отклонённый confirmation guard, audit event не создаёт.

## 10. Backup и restore

SOURCE backup, содержащий `BUNDLE_SIGNING_PRIVATE_KEY_FILE`, является secret backup и должен защищаться как credential/private key material.

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
- обходить server-side confirmation прямым HTTP-вызовом;
- менять расширение disabled key вручную как штатную admin procedure;
- использовать direct replace, если старые deliveries ещё должны проходить verifier — для этого нужен overlap rotation;
- удалять old trust key до завершения overlap window.

Связанные документы:

- [Security/trust model](security.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Package service](package-service.md)
- [Руководство администратора](admin-guide.md)
- [Deployment/runtime Compose](../deploy/README.md)
