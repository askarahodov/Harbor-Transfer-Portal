# Безопасность Harbor Transfer Portal

**Статус:** актуальная security/trust model для текущей разработки v1.

Этот документ описывает границы доверия, основные угрозы и уже реализованные защитные механизмы Harbor Transfer Portal. Он не переопределяет protocol-critical контракты: точный формат и порядок проверки переносимого пакета задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), архитектурные решения — [ADR](decisions.md).

## 1. Модель безопасности продукта

Harbor Transfer Portal работает в двух независимых сетево изолированных контурах:

- `SOURCE` взаимодействует только со своим локальным Harbor и создаёт подписанный Offline Bundle;
- `TARGET` взаимодействует только со своим локальным Harbor и принимает физически перенесённый Bundle;
- между SOURCE и TARGET нет прямого сетевого соединения;
- одна установка не хранит credentials противоположного контура;
- прямой Harbor-to-Harbor replication через границу изоляции нет.

Главное следствие: **переносимый файл считается недоверенным входом на TARGET независимо от того, откуда физически пришёл носитель**. Доверие появляется только после успешной проверки структуры, protocol version, Ed25519 signature и payload integrity.

## 2. Что защищаем

Критичные assets:

| Asset | Основной риск |
|---|---|
| Harbor credentials | кража доступа к локальному registry |
| `JWT_SECRET` | выпуск/подделка portal access tokens |
| SOURCE signing private key | возможность подписывать поддельные delivery |
| TARGET trusted SOURCE public keys | подмена trust root |
| Offline Bundle payload | tamper/corruption/malicious archive |
| SQLite history/settings | несанкционированное изменение пользователей, metadata и истории |
| Managed CA | подмена TLS trust для local Harbor |
| Operation/report/audit data | утечка внутренней metadata или сокрытие результата операции |

Пароли, tokens, Harbor credentials и private signing key **не являются содержимым Offline Bundle**.

## 3. Границы доверия

### 3.1. Browser ↔ Portal API

Граница аутентификации и RBAC. Frontend помогает пользователю не видеть недоступные действия, но окончательное решение принимает backend.

### 3.2. Portal ↔ Local Harbor

Граница local credential и TLS trust. Каждый экземпляр знает только один effective local Harbor configuration.

### 3.3. Backend ↔ Skopeo / Helm

Граница недоверенных repository/reference/path values, subprocess argv, environment и temporary workspace.

### 3.4. SOURCE ↔ Offline Bundle

Граница canonical manifest, Ed25519 signing, checksums и atomic publication.

### 3.5. Physical media ↔ TARGET

Полученный archive полностью недоверенный до verifier checks. Нельзя считать USB/HDD, имя файла или sidecar сами по себе доказательством подлинности.

### 3.6. Backend ↔ Persistent volume / host

Host-level доступ к `/app/data` является deployment security boundary: имеющий право чтения защищённых файлов потенциально получает доступ к managed Harbor credential или key material.

## 4. Authentication, session и RBAC

Текущая модель использует локальных пользователей и роли:

- `admin`;
- `operator`;
- `viewer`.

`POST /api/auth/login` выдаёт short-lived JWT bearer token. `GET /api/auth/me` повторно проверяет текущего пользователя и роль.

Frontend хранит token только в `sessionStorage` активной browser session согласно [ADR-004](adr/ADR-004-auth-session.md):

- пароль не сохраняется во frontend storage;
- наличие token само по себе не считается подтверждением session — выполняется `/api/auth/me`;
- `401` очищает token/session без автоматического retry тем же token;
- `403` означает отказ в конкретном действии и не очищает session;
- viewer остаётся read-only;
- `/export` и `/import` разрешаются `operator|admin` на уровне route UX, `/settings` и `/users` — `admin`, но backend authorization обязателен независимо от UI.

### Browser security consequence

`sessionStorage` доступен JavaScript, поэтому успешная XSS-атака в активной вкладке потенциально может прочитать token. Нельзя добавлять недоверенный runtime HTML/script, небезопасный `v-html`, CDN script injection или аналогичные источники XSS surface без отдельного security review.

Текущая bearer-header модель не создаёт cookie-based CSRF surface: Authorization header не прикрепляется браузером автоматически как cookie. Если в будущем auth будет переведён на HttpOnly cookie, потребуется отдельное решение для CSRF lifecycle.

## 5. Защита локального входа

Backend ограничивает неуспешные попытки входа одновременно по username и client address.

Пороговые значения по умолчанию:

| Параметр | Значение | Назначение |
|---|---:|---|
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | 300 | окно подсчёта ошибок |
| `LOGIN_RATE_LIMIT_USERNAME_MAX_FAILURES` | 5 | максимум ошибок для username |
| `LOGIN_RATE_LIMIT_ADDRESS_MAX_FAILURES` | 20 | максимум ошибок для client address |
| `LOGIN_RATE_LIMIT_LOCKOUT_SECONDS` | 900 | lockout после достижения порога |

Счётчики сохраняются в SQLite и переживают обычный restart. Raw username/client address не записываются в `login_throttles`: используются HMAC-SHA256 fingerprints на ключе установки `JWT_SECRET`.

Заблокированная и обычная неверная попытка возвращают одинаковую ошибку `invalid credentials`, чтобы throttling нельзя было использовать для account enumeration.

Приложение не доверяет произвольному пользовательскому `X-Forwarded-For` как доказательству реального адреса клиента.

## 6. Local Harbor credentials

Модель credential определена [ADR-005](adr/ADR-005-harbor-secrets-tls.md).

### Предпочтительное хранение

Credential, установленный portal-managed способом, хранится отдельным file-backed secret:

```text
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
```

Файл создаётся атомарно с restrictive permissions (`0600`). SQLite не используется как постоянное хранилище Harbor password.

### Bootstrap fallback

Поддерживается порядок разрешения:

1. managed credential file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` environment fallback.

Environment credential сохраняется для bootstrap compatibility, но не является предпочтительным постоянным production storage для новой установки.

API сообщает только, настроен ли credential, и не возвращает его значение или источник.

### URL validation

Harbor base URL должен быть origin без embedded credential, query, fragment и произвольного subpath. Конструкция вида `user:password@host` запрещена как способ спрятать secret в non-secret setting.

## 7. TLS и private CA

Harbor TLS verification включена по умолчанию.

Правила:

- нет автоматического fallback на insecure TLS;
- `verify_tls=false` допускается только как явное административное действие и должно быть заметно оператору/администратору;
- custom CA проходит server-side validation;
- portal-managed CA хранится в контролируемом файле `HARBOR_MANAGED_CA_FILE`;
- deployment-managed `HARBOR_CA_FILE` остаётся bootstrap fallback;
- пользователь не передаёт произвольный server-side path через API.

Skopeo и Helm получают effective TLS policy из тех же локальных Harbor settings. Они не должны самостоятельно принимать решение «если сертификат невалиден — повторить insecure».

Отключение TLS verification допустимо только как осознанное диагностическое/исключительное действие, а не штатное решение x509-проблемы.

## 8. Skopeo subprocess boundary

`backend/app/services/skopeo_service.py` является единственной backend-границей запуска Skopeo для container images.

Security properties:

- service принимает structured `ImageReference`, а не произвольную command/transport строку;
- registry host берётся только из local Harbor settings;
- subprocess запускается через `asyncio.create_subprocess_exec(*argv)`;
- shell не используется;
- credential не передаётся через `--creds` в argv;
- на время операции создаётся Docker-compatible `auth.json` во временном каталоге `0700`;
- auth file имеет mode `0600`;
- credential/base64 auth и username включены в redaction set;
- output capture ограничен `SKOPEO_OUTPUT_LIMIT_BYTES`;
- timeout/cancellation завершают child process;
- payload path обязан оставаться внутри `SKOPEO_PAYLOAD_ROOT`;
- custom CA передаётся через controlled temporary cert-dir;
- source/payload/target digest checks выполняются отдельно от exit code subprocess.

Успешный `skopeo copy` без последующей digest verification не считается доказательством успешной доставки container image.

Подробности: [skopeo-service.md](skopeo-service.md).

## 9. Helm OCI subprocess boundary

`HelmOciService` — единая backend-граница работы с Helm OCI.

Security properties:

- service принимает structured `repository + name + version`;
- local registry определяется Harbor settings;
- `shell=True` не используется;
- `helm registry login` получает password через `--password-stdin`, а не command-line argument;
- `HELM_CONFIG_HOME`, `HELM_CACHE_HOME`, `HELM_DATA_HOME` и registry config изолированы per operation;
- temporary directory создаётся с mode `0700` и удаляется после операции;
- stdout/stderr bounded;
- credential redacted;
- timeout/cancellation завершают child process;
- package path обязан быть внутри `HELM_WORKSPACE_ROOT`;
- chart tar просматривается до Helm processing, а unsafe paths/links отклоняются;
- `name/version` подтверждаются через `helm show chart`;
- silent insecure TLS fallback отсутствует.

Helm chart `.tgz` и Harbor OCI artifact digest имеют разные уровни идентичности; transport checksum `.tgz` не следует выдавать за гарантию полного OCI digest preservation.

Подробности: [helm-oci-service.md](helm-oci-service.md).

## 10. Offline Bundle: authenticity и integrity

Bundle v1 использует два независимых механизма.

### Ed25519 signature — authenticity

`manifest.sig` является подписью точных canonical UTF-8 bytes `manifest.json`.

Подпись отвечает на вопрос:

> manifest подписан приватным ключом, соответствующим одному из заранее доверенных SOURCE public keys?

### SHA-256 — integrity

`checksums.sha256` и descriptor checksum/size metadata обнаруживают повреждение или изменение payload.

Checksum отвечает на вопрос:

> payload совпадает с байтами, для которых была рассчитана контрольная сумма?

**SHA-256 сам по себе не подтверждает, кто создал файл.** Злоумышленник, способный заменить и payload, и checksum, может пересчитать SHA-256. Поэтому checksum не заменяет подпись.

## 11. Ключи подписи и доверия

### SOURCE private key

Настраивается через:

```text
BUNDLE_SIGNING_PRIVATE_KEY_FILE=./data/keys/source-signing-private.pem
```

Штатный admin workflow позволяет установить/ротировать Ed25519 private key через browser. Требования реализации:

- принимается bounded key material (`BUNDLE_KEY_MATERIAL_MAX_BYTES`);
- private key проверяется как Ed25519 до замены;
- normal form — PEM PKCS#8;
- file replacement атомарный, restrictive permissions штатно `0600`;
- private key существует только на SOURCE;
- private key не включается в bundle;
- API/UI после submission возвращает только configured status/fingerprint и никогда не экспортирует key material;
- audit фиксирует install/rotate без PEM.

### TARGET trust set

TARGET хранит доверенные SOURCE public keys в:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Admin UI/API позволяет list/add/replace/enable/disable/remove только валидные Ed25519 **public** keys по стабильному `sha256:` fingerprint. Private/malformed/oversized input отклоняется, filesystem filename создаётся server-side, а user-controlled path не принимается.

Enabled keys представлены active `*.pem` и потребляются тем же `BundlePackageService.verify_bundle()` path. Disabled key исключён из active trust set, поэтому его состояние реально влияет на verification. Verifier возвращает fingerprint совпавшего public key, но не key material.

### Rotation v1

Рекомендуемая последовательность:

1. создать новую SOURCE key pair;
2. заранее добавить новый public key на TARGET, сохранив старый enabled;
3. переключить SOURCE на новый private key;
4. выдержать окно доставки старых bundle;
5. disable старый TARGET key и подтвердить ожидаемый migration state;
6. после завершения окна удалить старый public key из TARGET trust set.

Так old/new SOURCE deliveries могут сосуществовать во время controlled overlap. Private key не переносится вместе с delivery.

Подробности: [key-management.md](key-management.md).

## 12. Безопасная проверка archive

TARGET не должен выполнять обычный `tar extract` над непроверенным archive.

Verifier до controlled extraction отклоняет как минимум:

- absolute paths;
- `..` path traversal;
- backslash/NUL в archive path;
- non-canonical aliases вроде `./` или повторных `/`;
- normalized duplicate/file-directory collisions;
- symlink;
- hardlink;
- device nodes;
- FIFO и другие unsupported special members;
- duplicate security-critical entries;
- security-critical path, представленный не regular file;
- unexpected top-level layout;
- undeclared/overlapping payload roots.

`manifest.json`, `manifest.sig` и `checksums.sha256` должны присутствовать ровно один раз и быть обычными файлами.

Extraction разрешается только после успешных verification checks в новый controlled directory. Реализация не использует небезопасный `tar.extractall()` над непроверенным archive.

## 13. Resource exhaustion / decompression limits

Bundle verifier/key-management применяют server-side upper bounds:

- `BUNDLE_MAX_ARCHIVE_BYTES`;
- `BUNDLE_MAX_EXTRACTED_BYTES`;
- `BUNDLE_MAX_MEMBER_COUNT`;
- `BUNDLE_MAX_PATH_BYTES`;
- `BUNDLE_MAX_METADATA_BYTES`;
- `BUNDLE_MAX_COMPRESSION_RATIO`;
- `BUNDLE_MAX_TRUSTED_KEYS`;
- `BUNDLE_KEY_MATERIAL_MAX_BYTES`.

Поддерживаемые transfer limits управляются через bounded admin policy API/UI; часть bootstrap/default values по-прежнему задаётся deployment configuration. Лимиты являются security/capacity control. Их увеличение должно быть административным решением по ёмкости и риску, а не способом «пропустить» malformed bundle.

Skopeo/Helm дополнительно ограничивают execution timeout и retained stdout/stderr.

## 14. Verification order до registry mutation

Нормативный порядок задаётся Bundle Protocol. В укрупнённом виде TARGET проверяет:

1. whole-file archive/sidecar limit/checksum, когда sidecar передан;
2. archive member/path/type/resource safety;
3. supported schema major и canonical manifest;
4. Ed25519 signature;
5. checksum syntax/file set и payload checksums;
6. typed/schema/semantic manifest constraints;
7. signed descriptor checksum/size metadata;
8. только после этого разрешается controlled extraction и последующая import orchestration.

Registry mutation не происходит до завершения package verification. Перед execute import orchestration дополнительно повторно проверяет exact bundle identity и target state для защиты от TOCTOU.

Точный контракт: [offline-bundle-v1.md](offline-bundle-v1.md), [package-service.md](package-service.md) и [import-orchestration.md](import-orchestration.md).

## 15. Conflict и overwrite policy

Безопасный baseline v1 реализован следующим образом:

- target artifact отсутствует → `NEW`, можно импортировать;
- target reference уже содержит тот же expected digest → `SAME`, идемпотентный `SKIPPED`;
- тот же tag/version указывает на другой digest → `CONFLICT`, execute заблокирован по умолчанию;
- `UNKNOWN`/`ERROR` → mutation блокируется fail-closed.

Conflict overwrite возможен только если одновременно:

1. admin явно разрешил server-side `IMPORT_ALLOW_OVERWRITE`/runtime policy;
2. operator/admin выбрал explicit overwrite execute;
3. UI отдельно подтвердил перечисленные conflicting artifacts.

Включение global policy само по себе не запускает overwrite. `UNKNOWN/ERROR` этой policy не обходятся. Overwrite approval и operation context сохраняются для audit/receipt/history.

## 16. Secrets и redaction

Нельзя помещать в source code, Git, bundle, обычные отчёты или frontend state:

- Harbor password/token;
- `JWT_SECRET`;
- SOURCE private signing key;
- Authorization bearer token;
- содержимое private key;
- credential-bearing URL.

Service errors не должны возвращать raw upstream stderr/body, если там потенциально могут находиться credential или sensitive internal metadata.

Skopeo/Helm command runners имеют bounded output и redaction sets. Harbor connection errors/settings API также возвращают sanitized error model. Audit/report fixtures защищены secret-leak regression tests.

## 17. Persistent data и backup security

SQLite backup не является полным backup security state.

Отдельно существуют:

- SQLite database;
- `data/secrets` с managed Harbor credential/CA;
- SOURCE signing private key;
- TARGET trusted public keys;
- receipts/history/report metadata;
- incoming/outgoing packages согласно организационной retention policy.

Backup/restore должен сохранять необходимые trust/secret files с их permissions и не складывать секреты в публичный release archive.

Current Compose procedure закреплена в [admin-guide.md](admin-guide.md). Финальная clean-VM/offline release и restore/rollback qualification остаётся задачей #28.

## 18. Offline/network security

Runtime в изолированном контуре не должен иметь скрытую зависимость от internet/CDN.

Допустимо, что controlled build/release pipeline получает build dependencies. Финальная offline installation должна загружать заранее собранные images/artifacts, а не выполнять internet build внутри закрытого контура.

Frontend не должен загружать fonts/scripts/UI assets с CDN во время runtime.

SOURCE/TARGET не должны использовать друг друга как remote API/registry endpoint.

## 19. Health/readiness и information exposure

Health/readiness endpoints предназначены для эксплуатационной проверки и не должны раскрывать:

- Harbor password;
- JWT secret;
- private signing key;
- raw bearer token;
- other secret configuration.

Contour identity (`SOURCE|TARGET`) не является секретом: она нужна UI и оператору, чтобы не перепутать контур.

## 20. Audit, structured logging, history и reports

Persisted audit/history/report workstream реализован и не зависит от парсинга raw container logs как product state.

Security properties:

- security/admin actions attributable к actor/system actor;
- request/operation/delivery context используется для correlation;
- passwords/tokens/Harbor credentials/private keys не должны попадать в logs/audit/report;
- UI читает persisted operation/history state, а не log text;
- overwrite approval сохраняется с actor context;
- History предоставляет read-only operation/artifact result projection по role policy;
- terminal operations имеют CSV/PDF reports из persisted state;
- TARGET import сохраняет canonical receipt отдельно от rendered report;
- report paths/filenames генерируются server-side;
- CSV защищается от formula injection для опасных leading values;
- PDF runtime не должен обращаться к internet/CDN за fonts/assets.

Финальная offline/release qualification этих возможностей остаётся частью #28, но application audit/history/report capabilities уже реализованы.

## 21. Реализовано и ещё требуется

| Security area | Текущий статус |
|---|---|
| Local users / JWT / RBAC foundation | реализовано |
| Frontend session guards | реализовано |
| Login rate limiting | реализовано |
| Managed Harbor credential/CA + TLS policy | реализовано |
| Skopeo argv/authfile/TLS/path/redaction boundary | реализовано |
| Helm argv/password-stdin/workspace/TLS/package validation boundary | реализовано |
| Bundle canonical manifest/signature/checksum verification | реализовано |
| Safe archive path/type/resource validation | реализовано |
| Managed SOURCE signing / TARGET trust-key lifecycle | реализовано |
| TARGET conflict/overwrite authorization UX | реализовано, default deny + explicit confirmation |
| Audit/log correlation + History API/UI | реализовано |
| CSV/PDF reports + TARGET receipt UX | реализовано |
| Admin user/policy/key management | реализовано |
| Automatic product retention/cleanup | не реализовано; требуется отдельный tested lifecycle при необходимости |
| Final offline install/release hardening | запланировано в #28 |

## 22. Known v1 limitations / non-goals

- Портал не защищает от администратора/host operator, который имеет полный root-доступ к backend persistent volume и key/secret files. Это deployment trust boundary.
- Физическая защита USB/HDD и организационный процесс допуска носителя находятся вне приложения; приложение проверяет цифровое содержимое после поступления.
- `sessionStorage` bearer token остаётся доступен JavaScript при успешной XSS-атаке активной вкладки.
- Автоматическая retention/cleanup не реализована и не должна подразумеваться существующими policy settings.
- Финальный offline-install/clean-VM/cross-contour release security acceptance ещё не выполнен до #28.
- Портал не создаёт сетевой DLP/antivirus pipeline для произвольных файлов: Bundle Protocol разрешает только ожидаемую структуру и типы payload.
- Отключение TLS verification не является исправлением PKI; это явное исключение с ухудшением защиты.

## 23. Security checklist для изменений

Перед merge security-sensitive изменения проверить:

- не появился ли secret/token/private key в diff, fixture, log или error response;
- не добавлен ли `shell=True` или command string с user-controlled data;
- не расширилась ли filesystem path boundary без validation;
- не допускается ли archive extraction до verifier checks;
- не ослаблены ли signature/checksum/schema tests;
- не появился ли silent TLS disable/fallback;
- не превратился ли frontend role guard в единственный authorization control;
- не допускается ли conflict overwrite без явной policy;
- documentation impact обновлён вместе с поведением.

## 24. Связанные документы

- [Архитектура](architecture.md)
- [User Guide](user-guide.md)
- [Admin Guide](admin-guide.md)
- [Troubleshooting](troubleshooting.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Package service](package-service.md)
- [Import orchestration](import-orchestration.md)
- [Key management](key-management.md)
- [Transfer policies](transfer-policies.md)
- [Skopeo service](skopeo-service.md)
- [Helm OCI service](helm-oci-service.md)
- [ADR-004: auth/session](adr/ADR-004-auth-session.md)
- [ADR-005: Harbor secrets/TLS](adr/ADR-005-harbor-secrets-tls.md)
- [ADR-009: OCI image-layout](adr/ADR-009-oci-layout-payload.md)
- [Deployment](../deploy/README.md)
- [Testing/CI](testing.md)

При противоречии этого overview с normative Bundle Protocol или принятым ADR приоритет имеет специализированный нормативный документ; расхождение должно быть исправлено в документации, а не интерпретироваться молча.