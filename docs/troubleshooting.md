# Troubleshooting Harbor Transfer Portal

**Статус:** актуальное руководство по диагностике текущих v1 primitives, SOURCE/TARGET transfer flow и Compose runtime.

Формат каждого раздела: **симптом → вероятная причина → диагностика → безопасное решение → эскалация**.

Этот документ не заменяет [security.md](security.md) и [admin-guide.md](admin-guide.md). SOURCE export и TARGET import orchestration/UI уже реализованы; финальная isolated cross-contour release acceptance и offline install qualification остаются задачей #28.

## 1. Базовый диагностический порядок

Перед изменением security/configuration соберите минимальный контекст без секретов:

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=100 frontend
```

Проверьте endpoints:

```text
/api/health
/api/ready
/healthz
```

Для проблем локального Harbor администратор должен сначала использовать UI **«Настройки локального Harbor» → «Проверить подключение»**.

Не публикуйте в issue/chat:

- Harbor password/token;
- bearer JWT;
- SOURCE private signing key;
- содержимое managed secret;
- полный `.env`;
- raw logs до проверки redaction/internal metadata.

## 2. Harbor отклоняет credential

### Симптом

Connection test возвращает:

```text
harbor_auth_failed
Harbor отклонил учётные данные
```

Skopeo/Helm primitives могут возвращать:

```text
skopeo_auth_failed
helm_auth_failed
```

### Вероятная причина

- password/token был изменён в Harbor, но не ротирован в Portal;
- настроен неверный username/service account;
- managed credential отсутствует;
- deployment fallback содержит устаревший secret.

### Диагностика

В Settings проверьте:

- Harbor URL;
- username;
- `credential_configured`/визуальный статус credential;
- connection test.

Не пытайтесь вывести текущее managed credential: API намеренно не возвращает его значение.

### Безопасное решение

1. убедитесь, что service account существует в **локальном Harbor этого контура**;
2. установите/ротируйте credential через admin Settings;
3. снова выполните connection test;
4. только после успешной проверки отзывайте старый credential, если использовалось overlap-окно.

Не помещайте credential в Harbor URL и не сохраняйте его в Git.

### Когда эскалировать

Если credential достоверно рабочий в Harbor, но `harbor_auth_failed` остаётся, приложите sanitized backend logs, Harbor URL host без userinfo и сведения о типе service account — без секрета.

## 3. Harbor отвечает Forbidden

### Симптом

Connection test возвращает:

```text
harbor_forbidden
Harbor запретил доступ порталу
```

### Вероятная причина

Credential валиден, но учётной записи недостаточно прав для запрошенной Harbor API operation.

### Диагностика

Сверьте scope service account:

- SOURCE: read/pull разрешённых проектов/artifacts для browse/export;
- TARGET: read + push/write разрешённых target repositories для preview/import;
- не используйте глобального Harbor Administrator как автоматическое «исправление».

### Безопасное решение

Выдайте минимально необходимые project/repository permissions согласно политике площадки и повторите штатный SOURCE/TARGET flow. Не расширяйте права шире необходимого только ради прохождения одного запроса.

### Когда эскалировать

Если отказ возникает только на конкретном project/repository, передайте разработчику operation context и Harbor resource path без credentials.

## 4. TLS/x509/private CA

### Симптом

Возможные коды:

```text
harbor_tls_failed
skopeo_tls_failed
helm_tls_failed
```

или x509/certificate/TLS-handshake ошибка в sanitized logs.

### Вероятная причина

- Harbor использует private CA, которого Portal ещё не доверяет;
- CA bundle неверный/неполный;
- сертификат Harbor истёк или не соответствует hostname;
- после ротации PKI в Portal остался старый managed CA.

### Диагностика

В admin Settings:

1. убедитесь, что `Проверять TLS-сертификат Harbor` включено;
2. проверьте статус custom CA;
3. повторите connection test.

### Безопасное решение

Загрузите корректный PEM/CRT CA bundle через admin Settings и оставьте TLS verification включённой.

`HARBOR_VERIFY_TLS=false` **не является штатным исправлением** x509-проблемы. Silent insecure fallback отсутствует и не должен добавляться.

### Когда эскалировать

Если корректный CA установлен, но все три слоя (`Harbor client`, Skopeo, Helm) ведут себя по-разному, приложите sanitized error code каждого слоя и certificate metadata без private key.

## 5. Harbor недоступен или timeout

### Симптом

Connection test:

```text
harbor_unavailable
```

Skopeo/Helm:

```text
skopeo_timeout
helm_timeout
```

### Вероятная причина

- DNS/route/local firewall внутри контура;
- Harbor не запущен или перегружен;
- неверный Harbor URL;
- timeout слишком мал для текущей операции;
- storage/registry backend Harbor деградировал.

### Диагностика

Проверьте:

```bash
docker compose ps
docker compose logs --tail=200 backend
```

Затем admin connection test.

Параметры:

```text
HARBOR_CONNECT_TIMEOUT_SECONDS
HARBOR_READ_TIMEOUT_SECONDS
SKOPEO_TIMEOUT_SECONDS
HELM_TIMEOUT_SECONDS
```

### Безопасное решение

Сначала устраните доступность local Harbor/network/storage. Увеличивать timeout разумно только после подтверждения, что операция корректна, но объективно требует больше времени.

### Когда эскалировать

Если connection test стабилен, а timeout повторяется только на конкретном большом artifact, приложите размер/тип artifact, phase и безопасный error code.

## 6. Skopeo export/import failure

### Симптом

Возможные коды service primitive:

```text
skopeo_command_failed
skopeo_auth_failed
skopeo_tls_failed
skopeo_not_found
skopeo_timeout
skopeo_digest_mismatch
skopeo_payload_digest_mismatch
skopeo_payload_invalid
```

### Вероятная причина

- auth/TLS/Harbor availability;
- source artifact отсутствует;
- OCI payload повреждён;
- digest после copy не совпал;
- Skopeo завершился ненулевым exit code;
- operation была отменена/прервана.

### Диагностика

Определите phase операции: source inspect, export/copy, payload inspect, TARGET push или TARGET verification.

Смотрите structured operation state и sanitized backend logs. Не используйте raw stderr как пользовательский контракт.

### Безопасное решение

- auth/TLS исправляйте через Harbor Settings;
- `skopeo_not_found` — перепроверьте выбранный repository/reference;
- `skopeo_payload_invalid`/`skopeo_payload_digest_mismatch` — не продолжайте import; payload должен быть заново сформирован/проверен;
- `skopeo_digest_mismatch` после TARGET push — не объявляйте delivery успешной, требуется расследование target artifact.

Не повторяйте команду с `--tls-verify=false` автоматически и не подменяйте expected digest текущим observed digest только ради зелёного результата.

### Когда эскалировать

Digest mismatch или повторяемый `skopeo_command_failed` при исправном Harbor — security/correctness incident для transfer flow; приложите operation id, safe artifact reference и error code.

## 7. Helm pull/push failure

### Симптом

Возможные коды:

```text
helm_command_failed
helm_auth_failed
helm_tls_failed
helm_not_found
helm_timeout
helm_source_not_found
helm_credential_incomplete
helm_chart_metadata_invalid
helm_chart_metadata_mismatch
helm_target_exists
helm_target_not_visible
```

### Вероятная причина

- Harbor credential/TLS;
- chart/version отсутствует;
- `.tgz` повреждён или metadata не совпадает с selection;
- TARGET уже содержит эту chart/version;
- Harbor не показывает artifact после push.

### Диагностика

Определите phase: source inspect, registry login, pull, package validation, push, target verification.

### Безопасное решение

- `helm_chart_metadata_mismatch` — не переименовывайте `.tgz` как workaround; package name/version должны реально совпадать;
- `helm_target_exists` — не перезаписывайте автоматически; решение принадлежит conflict policy;
- `helm_target_not_visible` — не считать push успешным до независимого подтверждения Harbor;
- auth/TLS решать через local Harbor settings.

### Когда эскалировать

Если `helm push` завершился успешно, но `helm_target_not_visible` стабильно повторяется, приложите repository/name/version, Harbor version и safe logs без credentials.

## 8. Whole-file `.sha256` не совпадает

### Симптом

```text
bundle_sidecar_checksum_mismatch
```

### Вероятная причина

- archive повреждён при копировании;
- `.sha256` относится к другому bundle;
- bundle был изменён после генерации sidecar;
- копирование ещё не завершено.

### Диагностика

Сравните pair filename и источник доставки. Не редактируйте sidecar вручную для «исправления» результата.

### Безопасное решение

Отклоните эту копию и перенесите заново **оба** файла из доверенного SOURCE output:

```text
<delivery>.htp.tar.gz
<delivery>.htp.tar.gz.sha256
```

### Когда эскалировать

Если повторный перенос того же SOURCE output снова даёт mismatch, проверьте SOURCE storage/media path и зафиксируйте SHA-256 до/после физического переноса.

## 9. Payload checksum/metadata mismatch

### Симптом

```text
bundle_payload_checksum_mismatch
bundle_payload_metadata_mismatch
bundle_payload_layout_mismatch
```

### Вероятная причина

Payload или checksum metadata внутри archive были повреждены/изменены либо archive сформирован несовместимым producer.

### Диагностика

Verifier уже выполняет проверку до controlled extraction/Harbor mutation. Дополнительный ручной `tar extract` непроверенного archive не нужен и небезопасен.

### Безопасное решение

Отклоните bundle. Не пересчитывайте `checksums.sha256` на TARGET и не редактируйте подписанный manifest.

### Когда эскалировать

Если bundle был только что создан текущим SOURCE и self-verification прошёл, а TARGET его отклоняет, это protocol compatibility defect: приложите versions/SHA/fingerprint и error code, но не secret material.

## 10. Signature invalid/untrusted

### Симптом

```text
bundle_signature_invalid
bundle_signature_untrusted
```

### Вероятная причина

- `manifest.sig` повреждён;
- TARGET не доверяет public key, которым SOURCE подписал manifest;
- SOURCE key был ротирован без overlap на TARGET;
- bundle/manifest подменён.

### Диагностика

На TARGET откройте admin **Settings → Signing и trust keys** и проверьте список trusted SOURCE fingerprints и их active/disabled state. Filesystem source of truth остаётся:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Сверьте ожидаемый SOURCE public-key fingerprint по доверенному организационному каналу.

### Безопасное решение

- при корректной ротации добавьте правильный SOURCE **public** key через admin Settings и выдержите overlap со старым ключом;
- disabled key не участвует в verifier trust set;
- private key на TARGET не переносится;
- если происхождение bundle не подтверждено — отклоните его.

Не добавляйте неизвестный public key в trust set только потому, что после этого подпись становится «зелёной».

### Когда эскалировать

Неожиданный `bundle_signature_untrusted` для ранее доверенного SOURCE следует рассматривать как возможную ошибку key lifecycle или security incident.

## 11. Неподдерживаемая или невалидная Bundle Schema

### Симптом

```text
bundle_schema_unsupported
bundle_schema_invalid
bundle_manifest_invalid
```

### Вероятная причина

- producer использует другой major protocol;
- manifest повреждён;
- bundle создан старой/несовместимой реализацией;
- schema/typed semantics нарушены.

### Диагностика

Текущий verifier поддерживает major `1.x`. Не меняйте `schema_version` вручную.

### Безопасное решение

Используйте совместимые SOURCE/TARGET версии или предусмотренную в будущем миграцию protocol. Bundle должен быть заново сформирован producer, который соблюдает normative [Offline Bundle Protocol v1](offline-bundle-v1.md).

### Когда эскалировать

Если обе стороны заявляют один current v1 protocol, но current SOURCE bundle получает `bundle_schema_invalid` на current TARGET, это protocol regression.

## 12. Недостаточно диска

### Симптом

Operation завершается:

```text
operation_insufficient_disk
```

или package limit сообщает превышение configured archive/extracted size.

### Вероятная причина

Свободного места меньше суммы required workspace + `OPERATION_DISK_RESERVE_BYTES`.

### Диагностика

Проверьте host/volume capacity и настройки:

```text
OPERATION_DISK_RESERVE_BYTES
OPERATION_MAX_CONCURRENT
BUNDLE_MAX_ARCHIVE_BYTES
BUNDLE_MAX_EXTRACTED_BYTES
```

Не удаляйте вручную `data/secrets`, `data/keys`, SQLite или `READY` workspace как «лишние файлы».

### Безопасное решение

- освободите место по документированной backup/cleanup процедуре;
- увеличьте storage capacity;
- при необходимости уменьшите concurrency;
- пересмотрите limits только после capacity/security оценки.

Автоматическая retention/auto-delete policy не считается реализованной, пока для неё нет отдельного tested lifecycle.

### Когда эскалировать

Если свободного места достаточно, но preflight стабильно считает иначе, приложите filesystem/volume topology и значения limits без secrets.

## 13. Incoming bundle без `.sha256`

### Текущее поведение

TARGET incoming discovery реализован. `POST /api/imports/discover` сканирует только `*.htp.tar.gz` непосредственно в `IMPORT_DISCOVERY_ROOT` и claim-ит archive только при наличии обычного соседнего `<bundle>.sha256`.

Archive без sidecar **игнорируется** и не создаёт import operation. Это защищает от обработки ещё копируемого или не полностью доставленного bundle.

### Диагностика

Для transfer-media flow:

1. скопируйте в configured incoming directory готовую пару archive + `.sha256`;
2. откройте `/import`;
3. нажмите **«Обнаружить готовые пакеты»**;
4. проверьте, что появилась новая import operation.

Если operation не появилась, убедитесь, что имена пары совпадают и оба объекта являются обычными файлами в корне discovery directory.

### Безопасное решение

Если есть archive без sidecar:

- не пытайтесь запускать его как «почти готовый» bundle;
- дождитесь/повторите копирование пары файлов с SOURCE/носителя;
- не генерируйте TARGET-side sidecar как замену SOURCE readiness marker;
- после появления корректной пары повторите **«Обнаружить готовые пакеты»**.

## 14. Artifact conflict на TARGET

### Симптом

Verified preview показывает `CONFLICT`: TARGET reference/version уже существует, но содержит другой digest.

### Текущее поведение

Preview классифицирует artifacts как `NEW`, `SAME`, `CONFLICT`, `UNKNOWN` или `ERROR`.

Default policy:

- `NEW` → import;
- `SAME` → `SKIPPED` без повторной mutation;
- `CONFLICT` → execute заблокирован;
- `UNKNOWN/ERROR` → execute заблокирован fail-closed.

Overwrite возможен только когда одновременно:

1. admin включил server-side `IMPORT_ALLOW_OVERWRITE`/runtime policy;
2. operator/admin явно подтвердил conflict overwrite в TARGET wizard.

### Диагностика

В preview сравните exact SOURCE expectation и текущий TARGET digest для каждого conflicting artifact. Проверьте, не менялся ли target repository независимо от Portal между preview и execute.

### Безопасное решение

Не включайте overwrite только ради «зелёного» статуса. Сначала выясните происхождение другого TARGET digest. Если overwrite действительно разрешён организационной политикой, включите его административно и используйте отдельное подтверждаемое действие **«Импортировать с подтверждённым overwrite»** только для осознанного сценария.

`UNKNOWN` и `ERROR` overwrite не обходят и должны оставаться блокирующими.

### Когда эскалировать

Если preview классифицирует известный одинаковый digest как `CONFLICT` либо меняет classification без изменения TARGET, это correctness defect conflict inspection.

## 15. Post-import digest mismatch

### Симптом

Container path может вернуть:

```text
skopeo_digest_mismatch
```

а import operation завершится `FAILED`; per-artifact outcome и receipt/history сохраняют безопасный результат ошибки.

### Вероятная причина

Observed TARGET digest после push не совпал с expected manifest/source digest либо TARGET artifact нельзя подтвердить после mutation.

### Диагностика

Откройте `/history` или текущий import wizard и проверьте:

- operation id/status;
- artifact reference;
- expected/source digest;
- observed TARGET digest, если он безопасно доступен;
- safe error code.

TARGET import orchestration выполняет независимую post-import verification; успешный subprocess exit code сам по себе не делает operation успешной.

### Безопасное решение

Не помечайте artifact/operation успешными вручную. Не заменяйте expected digest observed значением и не скрывайте mismatch как warning. Зафиксируйте receipt/history и расследуйте состояние TARGET Harbor.

### Когда эскалировать

Всегда, если mismatch воспроизводится: это correctness/security-critical отклонение transfer result.

## 16. Операция прервана restart/shutdown

### Симптом

После backend restart/shutdown operation имеет:

```text
operation_interrupted_restart
operation_interrupted_shutdown
```

### Причина

Baseline v1 не возобновляет середину Skopeo/Helm subprocess после перезапуска.

Любая уже захваченная non-terminal operation, кроме `READY`, безопасно завершается `FAILED`. `READY` — единственный wait state, workspace которого может сохраняться с освобождением stale worker ownership.

### Диагностика

Проверьте persisted operation через History/UI или:

```text
GET /api/operations/{id}
```

и backend logs.

### Безопасное решение

Не редактируйте status в SQLite вручную. Сначала оцените persisted terminal state и per-artifact outcomes, затем повторите SOURCE export или TARGET intake/import штатным UI/API, если требуется новая операция. Для `READY` import operation используйте сохранённый preview/workspace, если он остаётся валиден.

### Когда эскалировать

Если после restart операция остаётся навсегда non-terminal без worker, это regression OperationManager reconciliation.

## 17. Operation worker failed

### Симптом

```text
operation_worker_failed
```

### Причина

Worker получил неожиданное исключение. В persisted operation намеренно сохраняется безопасный общий текст, а не raw exception/subprocess output.

### Диагностика

Используйте server-side sanitized logs и operation id. Не требуйте от UI раскрыть raw exception.

### Безопасное решение

Исправляется root cause конкретного service/orchestrator error. Не преобразовывайте неожиданные ошибки в `COMPLETED` или `SKIPPED`.

### Когда эскалировать

`operation_worker_failed` требует developer investigation, если нет очевидной инфраструктурной причины.

## 18. Login не работает

### Симптом

```text
invalid credentials
```

или:

```text
authentication is not configured
```

### Вероятная причина

- неверный local username/password;
- user отключён;
- login throttling после серии ошибок;
- `JWT_SECRET` не настроен.

### Диагностика

Для `authentication is not configured` проверьте deployment `JWT_SECRET` (не выводя его значение).

Для repeated login failures учтите default lockout policy из [security.md](security.md).

### Безопасное решение

Используйте bootstrap/admin user lifecycle из [admin-guide.md](admin-guide.md). Не отключайте rate limiting ради подбора пароля.

## 19. Frontend показывает не тот contour

### Симптом

UI показывает SOURCE/TARGET, не соответствующий площадке.

### Вероятная причина

Неверный `PORTAL_CONTOUR` deployment configuration.

### Диагностика

Проверьте `.env`, `docker compose config` и `/api/health`.

### Безопасное решение

Не используйте рабочий persistent volume как способ регулярно переключать один экземпляр между SOURCE и TARGET. Исправьте deployment role до выполнения transfer operations.

## 20. После restore backend не стартует

### Симптом

Backend unhealthy, migration/permission errors после восстановления `/app/data`.

### Вероятная причина

- ownership файлов не UID/GID `10001`;
- secret/key permissions повреждены;
- backup несовместим с application/migration version;
- восстановлена неполная DB/secrets/key state.

### Диагностика

```bash
docker compose logs --tail=300 backend
```

Проверьте процедуру restore в [admin-guide.md](admin-guide.md), включая ownership и migration compatibility.

### Безопасное решение

Восстанавливайте согласованный backup всего installation state, а не только SQLite. Не выполняйте случайный Alembic downgrade как first-response workaround.

## 21. Что приложить к эскалации

Полезно:

- Portal commit/release identifier;
- contour SOURCE/TARGET;
- operation id;
- safe error code;
- phase/status;
- Harbor version;
- artifact repository/reference без credentials;
- bundle delivery id + SHA-256 + signing-key fingerprint, если это protocol issue;
- sanitized relevant log fragment;
- факт наличия private CA и TLS verification state без содержимого secret/private key.

Не прикладывать:

- passwords/tokens;
- `JWT_SECRET`;
- SOURCE private key;
- `data/secrets` contents;
- полный backup;
- raw environment dump.

## 22. Связанные документы

- [User Guide](user-guide.md)
- [Admin Guide](admin-guide.md)
- [Security/trust model](security.md)
- [Deployment/runtime Compose](../deploy/README.md)
- [Import orchestration](import-orchestration.md)
- [Export orchestration](export-orchestration.md)
- [Key management](key-management.md)
- [OperationManager](operation-manager.md)
- [Skopeo service](skopeo-service.md)
- [Helm OCI service](helm-oci-service.md)
- [Package service](package-service.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Architecture](architecture.md)

Стабильный user-facing error code или изменение SOURCE/TARGET workflow должно обновлять этот документ в той же implementation итерации.