# Signing identity и trusted SOURCE keys

Статус: **актуальный component document** для задачи #110 / P6.2.3.

Harbor Transfer Portal использует Ed25519 для аутентичности `manifest.json` в Offline Bundle v1. SOURCE хранит signing private key, TARGET — только доверенные SOURCE public keys. Private key никогда не переносится в bundle и не должен попадать на TARGET.

## SOURCE signing identity

Admin в `/settings` видит только:

- `configured / not configured`;
- public fingerprint вида `sha256:<64 hex>`.

Для установки или ротации выбирается PEM Ed25519 **private key** размером не более 64 KiB и подтверждается security-sensitive действие. Backend полностью парсит key до изменения filesystem, затем записывает canonical PKCS#8 PEM атомарно в `BUNDLE_SIGNING_PRIVATE_KEY_FILE`.

Гарантии:

- private key file имеет mode `0600`;
- parent key directory приводится к mode `0700`;
- API после установки не возвращает PEM/private bytes;
- UI не prefill-ит и не умеет скачать private key;
- audit содержит только action и новый public fingerprint;
- ошибка валидации нового key не повреждает уже установленный key;
- `BundlePackageService` продолжает использовать тот же configured private-key path при создании bundle.

Ротация SOURCE не означает автоматического удаления старого trust на TARGET. Перед переключением SOURCE убедитесь, что public key новой identity уже доставлен на TARGET по доверенному организационному каналу, если требуется overlap migration.

## TARGET trust set

Admin в `/settings` может:

- добавить Ed25519 public key;
- видеть stable fingerprint и `enabled/disabled` status;
- временно отключить и снова включить key;
- заменить key с explicit confirmation;
- удалить key с explicit confirmation.

Backend принимает только PEM Ed25519 **public key**. Private PEM, RSA/ECDSA, malformed и oversized input отклоняются. Пользователь не задаёт filesystem path или filename.

Managed filename строится сервером из fingerprint:

```text
ed25519-<sha256 hex>.pem
ed25519-<sha256 hex>.pem.disabled
```

Enabled files сохраняют расширение `.pem`, поэтому существующий `BundlePackageService` deterministic loader видит их через `*.pem`. Disabled files имеют `.pem.disabled` и не попадают в verifier trust set. Это позволяет держать старый и новый SOURCE public key одновременно во время overlap rotation без изменения Bundle Protocol v1.

## Fingerprint / key id

Fingerprint совпадает с package-service semantics:

1. Ed25519 public key сериализуется как 32 raw public bytes;
2. вычисляется SHA-256;
3. результат публикуется как `sha256:<lowercase hex>`.

Private key material не участвует в API identity и не сохраняется в audit metadata.

## Replace и fail-safe semantics

TARGET replace сначала валидирует и устанавливает новый public key, затем удаляет старый. Если процесс прервётся между этими действиями, безопасный остаточный state — overlap old+new, а не потеря trust set.

SOURCE rotation использует same-directory temporary file + `fsync` + `os.replace()`: до успешной атомарной замены старый private key остаётся на месте.

## Authorization и contour boundary

Все `/api/settings/keys...` endpoints доступны только `admin`.

- `operator` и `viewer` получают server-side `403`;
- SOURCE mutations trusted-public-key set отклоняются;
- TARGET mutation SOURCE private key отклоняется;
- UI contour checks являются только UX; security enforcement выполняется backend.

## Audit

Persisted audit events не содержат key bytes:

```text
keys.source_signing.installed
keys.source_signing.rotated
keys.trusted.added
keys.trusted.enabled
keys.trusted.disabled
keys.trusted.replaced
keys.trusted.removed
```

Metadata содержит только fingerprint/key-state identifiers, необходимые для расследования изменения trust boundary.

## Operational rotation

Безопасная overlap-ротация:

1. на SOURCE подготовить новую Ed25519 identity вне Portal согласно процедуре площадки;
2. по доверенному каналу передать **public** key на TARGET;
3. TARGET admin добавляет новый public key, не удаляя старый;
4. убедиться, что TARGET показывает оба fingerprints как `enabled`;
5. SOURCE admin ротирует private signing identity;
6. проверить новую тестовую delivery на TARGET;
7. после завершения in-flight старых deliveries старый TARGET public key можно `disable`, затем удалить по change procedure.

Нельзя добавлять неизвестный public key только для того, чтобы сделать `bundle_signature_untrusted` зелёным.

## Regression coverage

Backend tests проверяют:

- mode `0600` и отсутствие private content в API/audit;
- Ed25519 type validation и private-as-public rejection;
- deterministic fingerprint compatibility с PackageService;
- overlap add/enable/disable/remove/replace;
- verifier acceptance с managed TARGET trust и rejection после disable;
- contour/RBAC и explicit confirmation.

Frontend tests проверяют SOURCE/TARGET-specific states и отсутствие private-key presentation.
