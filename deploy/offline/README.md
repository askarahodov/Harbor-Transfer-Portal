# Harbor Transfer Portal — offline install kit

Этот каталог является шаблоном release payload для закрытого контура. Финальный kit создаётся из **уже собранных release images** в контролируемой build/release среде:

```bash
./deploy/build-release-images.sh 1.0.0
./deploy/build-offline-kit.sh 1.0.0
```

Результат:

```text
dist/harbor-transfer-portal-v1.0.0-offline-install.tar.gz
dist/harbor-transfer-portal-v1.0.0-offline-install.tar.gz.sha256
```

Для успешного CI `push` в `main` тот же архив, который прошёл clean-host qualification для SOURCE и TARGET, сохраняется как GitHub Actions artifact `harbor-transfer-portal-offline-install-<commit-sha>`. Artifact содержит только versioned release archive и его внешний `.sha256`; перед переносом используйте оба файла из одного artifact.

Packaging fail-closed проверяет canonical product version и OCI `org.opencontainers.image.version`/`revision` labels, поэтому stale или несовместимые images нельзя молча включить в текущий release archive.

## Установка в закрытом контуре

1. Перенесите архив и его `.sha256` в контур.
2. Проверьте внешний checksum архива.
3. Распакуйте архив.
4. Для **первого startup новой базы** задайте bootstrap role, например SOURCE:

```bash
PORTAL_CONTOUR=SOURCE ./install.sh
```

или TARGET:

```bash
PORTAL_CONTOUR=TARGET ./install.sh
```

`PORTAL_CONTOUR` — bootstrap default, а не постоянная фиксация роли installation. После первого startup authoritative `SOURCE`/`TARGET` mode и monotonic revision сохраняются в SQLite. Для дальнейшего переключения используйте runtime switcher в UI; rebuild/restart не требуется. Изменение `PORTAL_CONTOUR` после инициализации не должно переопределять сохранённый mode.

Один и тот же release kit поддерживает обе роли. Runtime switch **не выбирает другой Harbor**: installation по-прежнему работает только со своим настроенным local Harbor и не создаёт network path в противоположный изолированный контур. Полный contract: `docs/runtime-mode.md` внутри release kit.

Installer:

- сначала проверяет `CHECKSUMS.sha256` всего внутреннего payload;
- проверяет Docker Engine, Docker Compose v2, architecture и свободное место;
- загружает `images/backend.tar` и `images/frontend.tar` через `docker load`;
- проверяет, что после `docker load` существуют **точные** локальные image references этой версии и что их architecture совпадает с release;
- не выполняет `docker build`, package-manager install или network download;
- запускает Compose с `--no-build --pull never`, а release Compose дополнительно задаёт `pull_policy: never` для обоих сервисов;
- создаёт `.env` только если его ещё нет и генерирует случайный JWT secret;
- существующий `.env` должен быть обычным файлом, а не symlink;
- при повторном запуске сохраняет существующий `.env`; kit другой версии не заменяет его молча;
- запускает image-only `compose.yaml` и ждёт health/readiness.

Release Compose использует стабильное имя проекта `harbor-transfer-portal`. Поэтому named volume `portal-data` имеет одну и ту же Compose identity независимо от versioned каталога, в который распакован kit. Это важно для безопасного lifecycle.

### Browser transport после установки

Raw HTTP listener Portal по умолчанию публикуется только на loopback:

```text
PORTAL_HTTP_BIND=127.0.0.1
PORTAL_HTTP_PORT=8080
PORTAL_BROWSER_SCHEME=http
```

`http://127.0.0.1:8080` предназначен для bootstrap/diagnostics и как локальный upstream site-managed TLS terminator. Для authenticated remote browser use настройте HTTPS reverse proxy на площадке и затем задайте в `.env`:

```text
PORTAL_BROWSER_SCHEME=https
```

После изменения перезапустите Compose. TLS certificate/private key остаются вне release kit и управляются площадкой. Backend не доверяет client-supplied `X-Forwarded-Proto` как доказательству HTTPS; security-sensitive browser attributes определяются только trusted deployment setting.

Если TLS terminator расположен не на host Portal, задайте `PORTAL_HTTP_BIND` адресом выделенного доверенного интерфейса и ограничьте firewall так, чтобы raw HTTP listener был доступен только terminator. Не используйте широкую HTTP-публикацию как замену HTTPS.

Полная модель: `docs/browser-transport.md` внутри release kit.

После первого запуска настройте через admin UI **только local Harbor этой installation**, CA/credentials и требуемое key material. В universal installation SOURCE private signing key и TARGET trusted public keys могут сосуществовать в persistent storage, но active semantics жёстко разделяются текущим runtime mode.

Полный штатный пользовательский перенос выполняется через browser: в SOURCE role выбираются артефакты и скачивается bundle + `.sha256`, файлы физически переносятся в принимающий контур, затем Portal в TARGET role выполняет verify/preview/import через UI. Пошаговая процедура находится прямо в release kit: `docs/user-guide.md`.

## Backup

Для защищённого snapshot установки:

```bash
./backup.sh
```

Backup включает `.env` и persistent `/app/data`, поэтому он **содержит секреты**. SQLite внутри `/app/data` содержит authoritative runtime mode и `runtime.portal_mode_version`; следовательно backup сохраняет не только bootstrap `.env`, но и фактически выбранную runtime role. Script использует только текущий локальный backend image с `--pull never --network none`, создаёт внутренний и внешний SHA256 и, если Portal запущен, делает snapshot во время краткого Compose pause.

По умолчанию backup пишется в `./backups`; внешний защищённый каталог можно задать через `PORTAL_BACKUP_DIR`.

## Restore / recovery

Restore выполняйте **kit той же версии, из которой был создан backup**. Например, backup `1.0.0` сначала восстанавливается через kit `1.0.0`; только после проверки health выполняется обычный upgrade на следующую версию.

```bash
./restore.sh /secure/backups/harbor-transfer-portal-backup-v1.0.0-YYYYMMDDTHHMMSSZ.tar.gz --confirm-restore
```

`restore.sh` fail closed до изменения установки:

- проверяет внешний `.sha256` backup archive;
- принимает только ожидаемый allowlist файлов backup и проверяет внутренний `CHECKSUMS.sha256`;
- проверяет product, version, stable volume identity и bootstrap contour metadata;
- отклоняет symlinked backup/`.env`, unsafe archive paths и unsupported member types;
- требует явный `--confirm-restore`;
- загружает только bundled images matching-версии и не выполняет pull/build;
- очищает и восстанавливает persistent volume через локальный backend image с `--pull never --network none`;
- возвращает ownership `/app/data` к UID/GID `10001` и запускает Compose с `--no-build --pull never --wait`.

После restore backend читает authoritative runtime mode и revision из восстановленной SQLite DB. `PORTAL_CONTOUR` из `.env` остаётся bootstrap fallback и **не должен** менять уже сохранённую роль. После recovery проверьте `/api/runtime` или mode indicator в UI до начала новых export/import operations.

Restore не является механизмом автоматического downgrade Alembic migrations. Для recovery версии X используйте matching kit X, подтвердите health/runtime mode и только затем запускайте штатный `upgrade.sh`.

## Upgrade

Распакуйте новый versioned kit рядом со старым и из нового каталога выполните:

```bash
./upgrade.sh /path/to/previous-install
```

Upgrade сначала проверяет новый payload и создаёт обязательный pre-upgrade backup. Только после этого он загружает bundled images, переносит прежнюю `.env`, меняя только `PORTAL_VERSION`, и запускает Compose с `--no-build --pull never --wait`.

При startup failure `.env` возвращается к предыдущей версии, но автоматический database rollback **не обещается**: Alembic migration могла уже изменить SQLite schema. Сохраняйте pre-upgrade backup до завершения acceptance новой версии.

Runtime mode переживает штатный upgrade как часть SQLite persistent state. Если обновляется старая installation, где runtime metadata ещё отсутствует, первый startup после migration bootstrap-ит mode один раз из `PORTAL_CONTOUR`; после этого persisted mode authoritative.

## Uninstall

Безопасный вариант:

```bash
./uninstall.sh
```

Он удаляет containers/network, но сохраняет `.env` и named volume `harbor-transfer-portal_portal-data`.

Destructive purge volume требует двойного явного действия:

```bash
PORTAL_CONFIRM_PURGE=DELETE_PORTAL_DATA ./uninstall.sh --purge-data
```

Даже purge не удаляет host-side `.env` и release files автоматически.

Подробная процедура и ограничения: `docs/offline-lifecycle.md` внутри release kit.

## Что намеренно не входит в kit

Release payload не содержит `.env`, Harbor credentials, JWT secret, SOURCE private key, TARGET trust material, SQLite DB, receipts/history, backup archives или другие пользовательские данные.

Release payload включает version metadata, checksums, changelog/release notes и on-site documentation. User secrets создаются или настраиваются только в локальной установке.

## Автоматическая release qualification

Offline release contract не основан только на документации. CI содержит отдельные fail-able gates:

- deterministic offline-kit/lifecycle smoke проверяет checksums, no-pull/no-build semantics, idempotent install, backup/restore/upgrade/uninstall и fail-closed version identity;
- **Offline release — clean-host install qualification** собирает immutable kit, удаляет release images из runner и устанавливает один и тот же archive с predictable bootstrap role; проверяет health, Alembic, Skopeo/Helm, version identity и сохранение persistent state после rerun/restart;
- runtime lifecycle tests отдельно проверяют migration/backfill, authoritative persisted mode после restart, SQLite backup/restore mode+revision, concurrent switch serialization и SOURCE → TARGET → SOURCE без process restart;
- **Acceptance — isolated SOURCE → TARGET transfer** проверяет реальный image + Helm chart через SOURCE export, signed physical bundle boundary и отдельный TARGET registry, включая receipt/history/report, replay `SKIPPED`, conflict default-deny и tamper rejection до mutation.

Эти gates входят в общий CI `quality-gate` для соответствующего release/transfer scope. Они уже реализованы и являются частью v1 release qualification, а не будущей работой.
