# Package service офлайн-пакета v1

`BundlePackageService` реализует protocol-critical границу между уже подготовленными OCI/Helm payload и переносимым `.htp.tar.gz`. Нормативный формат остаётся в `docs/offline-bundle-v1.md` и `docs/schema/manifest-v1.schema.json`.

## Build на SOURCE

Build разрешён только при `PORTAL_CONTOUR=SOURCE`. Сервис принимает несекретные metadata артефактов и пути уже экспортированных payload внутри `BUNDLE_PAYLOAD_ROOT`, затем:

1. создаёт private temporary workspace mode `0700`;
2. snapshot-копирует payload, отклоняя symlink и special files;
3. вычисляет SHA-256 каждого payload-файла и `checksums.sha256`;
4. формирует typed `BundleManifest` и canonical UTF-8 `manifest.json`;
5. подписывает точные canonical bytes Ed25519 private key;
6. создаёт deterministic gzip/tar с нормализованными uid/gid/mtime/mode;
7. проверяет полученный временный archive тем же verifier path с public half signing key;
8. атомарно публикует archive в `BUNDLE_OUTGOING_ROOT`;
9. только после archive публикует `<archive>.sha256` readiness sidecar.

Ошибка до завершения sidecar не оставляет delivery, который выглядит готовым.

## Payload metadata

Для `.tgz` `payload_sha256` — обычный SHA-256 файла, `payload_size` — размер файла.

OCI image-layout является каталогом. `checksums.sha256` по-прежнему содержит отдельную строку для каждого обычного файла внутри каталога. Descriptor каталога использует детерминированный tree checksum: берутся относящиеся к нему checksum-строки в лексикографическом порядке в точном формате `<sha256>  <archive/path>\n`, их UTF-8 concatenation хешируется SHA-256. `payload_size` равен сумме размеров этих файлов. Это не OCI digest: исходный OCI digest отдельно хранится в `source_digest`.

Такое правило связывает подписанный descriptor с полным набором файлов OCI layout, не подменяя transport checksum registry digest-ом.

## Verification на TARGET/SOURCE

Verifier выполняет проверки до extraction и до любых Harbor mutations:

1. размер archive и, если передан, whole-file `.sha256` sidecar;
2. tar member count, total extracted size и compression ratio;
3. path safety, duplicate names, links и special file types;
4. наличие ровно одного `manifest.json`, `manifest.sig`, `checksums.sha256`;
5. raw UTF-8 JSON canonical form и поддерживаемый schema major;
6. Ed25519 signature exact manifest bytes;
7. Draft 2020-12 JSON Schema и typed semantic validation;
8. точное соответствие checksum file set файлам archive;
9. SHA-256 каждого payload-файла;
10. signed descriptor checksum/size metadata и отсутствие undeclared payload.

Только после всех проверок разрешена ручная extraction в новый каталог внутри `BUNDLE_EXTRACT_ROOT`. Используется контролируемая запись файлов, а не небезопасный `tar.extractall()` над непроверенным archive.

## Layout

Container image descriptor должен указывать на каталог внутри `images/`. Helm descriptor должен указывать на обычный `.tgz` внутри `charts/`. Payload roots разных артефактов не могут совпадать или быть вложенными друг в друга.

Имена security-critical файлов не включаются в `checksums.sha256`; `manifest.json` защищён Ed25519 signature. Private key, Harbor credentials и JWT secret в archive не добавляются.

## Ed25519 keys

SOURCE private key задаётся `BUNDLE_SIGNING_PRIVATE_KEY_FILE`. Файл должен быть regular PEM Ed25519 PKCS#8, не symlink, и не иметь group/other permissions; штатный режим — `0600`.

Пример генерации через OpenSSL на административной машине:

```bash
umask 077
openssl genpkey -algorithm ED25519 -out source-signing-private.pem
openssl pkey -in source-signing-private.pem -pubout -out source-signing-public.pem
```

Private key устанавливается только на SOURCE. На TARGET копируется только `source-signing-public.pem` в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR`. Каталог доверия может содержать несколько `*.pem`: verifier пробует каждый Ed25519 public key и возвращает SHA-256 fingerprint фактически совпавшего ключа.

Для v1 ротация выполняется с overlap:

1. SOURCE создаёт новую key pair и защищённо устанавливает новый private key;
2. до переключения на TARGET добавляется новый public key, старый public key остаётся trusted;
3. новые delivery подписываются новым private key;
4. после истечения операционного окна для старых delivery старый public key выводится из trust set.

Private key никогда не копируется в bundle и не выводится verifier API/result.

## Runtime settings

- `BUNDLE_PAYLOAD_ROOT=./data`;
- `BUNDLE_TEMP_ROOT=./data/tmp/bundles`;
- `BUNDLE_OUTGOING_ROOT=./data/outgoing`;
- `BUNDLE_EXTRACT_ROOT=./data/incoming/verified`;
- `BUNDLE_SIGNING_PRIVATE_KEY_FILE=./data/keys/source-signing-private.pem`;
- `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source`;
- `BUNDLE_MAX_ARCHIVE_BYTES`;
- `BUNDLE_MAX_EXTRACTED_BYTES`;
- `BUNDLE_MAX_MEMBER_COUNT`;
- `BUNDLE_MAX_PATH_BYTES`;
- `BUNDLE_MAX_METADATA_BYTES`;
- `BUNDLE_MAX_COMPRESSION_RATIO`;
- `BUNDLE_MAX_TRUSTED_KEYS`.

Лимиты проверяются server-side; их увеличение является административным решением по ёмкости/риску, а не способом обходить malformed bundle.
