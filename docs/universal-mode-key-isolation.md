# Universal runtime mode: signing/trust key isolation

Этот документ фиксирует security contract для одного Harbor Transfer Portal instance, который может переключать рабочую роль `SOURCE ↔ TARGET` без restart. Переключение роли **не объединяет Harbor-контуры** и не создаёт автоматический cryptographic trust.

## Storage boundary

Один universal instance может одновременно хранить две независимые категории key material:

- SOURCE signing private key: `BUNDLE_SIGNING_PRIVATE_KEY_FILE` (по умолчанию managed key path deployment-а);
- TARGET trusted SOURCE public keys: каталог `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR`.

Private signing key является обычным файлом, symlink запрещён, чтение ограничено configured size bound, а managed write выполняется с mode `0600`. Trusted public keys также читаются только как bounded regular files; symlink/non-file entries fail closed.

Наличие обеих категорий material на одном persistent storage **не делает private key trust anchor-ом**. Bundle signing path загружает только SOURCE private key. Bundle verification path загружает только enabled public keys из TARGET trust store.

## Mode-aware API contract

Key management API остаётся admin-only. Authoritative mode берётся из persistent runtime mode service, а не из bootstrap `PORTAL_CONTOUR` snapshot.

В `SOURCE` разрешены только signing identity status/install/rotation. TARGET trust mutations через прямой API вызов возвращают fail-closed contour error.

В `TARGET` разрешены только trusted public-key status/add/replace/enable/disable/remove. SOURCE private-key install/rotation через прямой API вызов возвращает fail-closed contour error. Private PEM никогда не возвращается через key status/mutation API.

## Runtime switch и concurrency

Security-sensitive key mutation и runtime switch используют один process-local runtime-mode barrier. Для v1 single-backend/SQLite deployment порядок определён однозначно:

1. если key mutation вошла в guard первой, runtime switch ждёт завершения короткой file+audit mutation;
2. если switch завершился первым, key mutation читает новый authoritative mode и fail-closed, если endpoint относится к другой роли.

Switch не копирует, не удаляет, не перегенерирует и не переименовывает key material. Fingerprints и bytes обеих категорий сохраняются между `SOURCE → TARGET → SOURCE`.

Runtime mode audit (`runtime_mode_changed`) остаётся отдельным событием и не считается key rotation. Key mutation audit содержит fingerprint/action + runtime mode/version, но не PEM/private material.

## Frontend boundary

Key controls привязаны к live runtime store. После backend-confirmed switch component немедленно скрывает controls предыдущего mode, очищает прежний key projection и загружает `/settings/keys` заново.

Каждый load имеет generation guard. Запоздавший HTTP response от предыдущего mode не может восстановить signing/trust controls после переключения. Response также обязан сообщать тот же contour, который сейчас является effective runtime mode; mismatch не отображается как usable key state.

## Cryptographic isolation

SOURCE signing:

- `BundlePackageService.build_bundle()` использует только configured signing private key;
- содержимое TARGET trusted-key directory не участвует в выборе signer-а;
- rotation signing key не добавляет соответствующий public key в trust store автоматически.

TARGET verification:

- `verify_bundle()` использует только explicit enabled trusted public keys;
- локальный SOURCE private key, даже если он физически присутствует на том же volume, не используется как implicit trust anchor;
- добавление/disable/replace trusted key не изменяет SOURCE signing private key.

Bundle v1 format и signature contract при этом не меняются.

## Backup/restore и offline kit

Backup universal instance считается sensitive, потому что persistent data может содержать одновременно SOURCE private signing key и TARGET public trust set. Backup/restore должен переносить их как независимые files/state и сохранять restrictive permissions; restore не должен превращать signing identity в trust entry или наоборот.

Release/offline kit не должен содержать пользовательские signing/trusted keys. Keys появляются только в runtime persistent data после явной admin configuration или restore пользовательского backup.

## Regression expectations

Security regression suite должна доказывать минимум:

- mode switch сохраняет signing file и trust directory byte-for-byte;
- opposite-mode key mutation блокируется server-side, независимо от UI;
- TARGET verifier не доверяет local SOURCE private key без explicit public trust entry;
- SOURCE signing fingerprint не зависит от TARGET trust store;
- key audit не содержит PEM/private material и содержит runtime mode context;
- frontend не показывает controls предыдущего mode и игнорирует stale HTTP response после switch;
- existing bounded/symlink/permission/atomic key-management tests остаются зелёными.
