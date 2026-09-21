# Offline lifecycle: backup, restore, upgrade и uninstall

Этот документ относится к release kit Harbor Transfer Portal и описывает эксплуатационные операции без доступа в интернет.

## Backup

В каталоге установленного kit выполните:

```bash
./backup.sh
```

Для явного каталога установки:

```bash
./backup.sh /opt/harbor-transfer-portal-v1.0.0-offline-install
```

`backup.sh`:

1. проверяет regular non-symlink `.env`, Docker Engine/Compose и текущий local backend image;
2. проверяет существование стабильного named volume `harbor-transfer-portal_portal-data`;
3. если Portal запущен, кратковременно ставит Compose services на pause;
4. читает volume через **уже локальный** `harbor-transfer-portal-backend:<current-version>` с `--pull never --network none`;
5. сохраняет `.env`, snapshot `/app/data`, metadata и внутренний `CHECKSUMS.sha256`;
6. создаёт внешний `.sha256` для backup archive;
7. снимает pause, если Portal был запущен.

По умолчанию backup создаётся в `./backups`. Другой каталог можно задать только явно:

```bash
PORTAL_BACKUP_DIR=/secure/offline-backups ./backup.sh
```

Backup **содержит секреты**: JWT secret, portal-managed Harbor credential/CA, SOURCE private signing key или TARGET trust material могут находиться в `.env` и `/app/data`. Поэтому backup не является переносимым публичным release artifact. Храните его как secret backup с ограниченным доступом. Release builder никогда не включает каталог `backups/` в install archive.

Runtime role и `runtime.portal_mode_version` хранятся в SQLite внутри `/app/data`, поэтому корректный backup сохраняет authoritative runtime mode вместе с остальным persistent state. `PORTAL_CONTOUR` в `.env` остаётся bootstrap metadata, а не источником текущей роли после инициализации базы.

## Restore / recovery

Restore — отдельная destructive recovery-операция. Используйте release kit **той же версии**, что указана в backup metadata. Backup версии `1.0.0` нельзя напрямую восстанавливать kit версии `1.1.0`: сначала восстановите `1.0.0`, проверьте health/readiness и runtime mode, затем выполните обычный upgrade.

Пример:

```bash
cd /opt/harbor-transfer-portal-v1.0.0-offline-install
./restore.sh /secure/offline-backups/harbor-transfer-portal-backup-v1.0.0-YYYYMMDDTHHMMSSZ.tar.gz --confirm-restore
```

До любой Docker mutation `restore.sh`:

1. требует explicit `--confirm-restore`;
2. принимает backup archive и `.sha256` только как regular non-symlink files;
3. проверяет внешний SHA-256 archive;
4. проверяет strict allowlist outer archive: `.env`, `backup-metadata.txt`, `portal-data.tar.gz`, `CHECKSUMS.sha256`;
5. проверяет внутренние checksums;
6. проверяет `product=harbor-transfer-portal`, matching release version и stable volume identity;
7. проверяет bootstrap contour metadata из backup `.env`, не подменяя им authoritative runtime mode восстановленной SQLite;
8. проверяет persistent-data tar на absolute/`..` traversal paths и запрещает symlink/special-file members;
9. проверяет host/release architecture.

После validation script загружает только bundled backend/frontend images matching-версии, проверяет exact refs/architecture, атомарно восстанавливает `.env`, останавливает существующие containers, заменяет содержимое `harbor-transfer-portal_portal-data`, возвращает ownership `10001:10001` и стартует Compose с `--no-build --pull never --wait`.

Операции очистки/restoration volume выполняются через локальный backend image с `--pull never --network none`; network helper image не нужен.

После restore backend читает runtime role и revision из восстановленной SQLite DB. Значение `PORTAL_CONTOUR` из `.env` не должно переопределять уже сохранённый mode. Проверьте `/api/runtime` или mode indicator в UI до новых export/import operations.

### Ограничение rollback

Restore не пытается вычислить Alembic downgrade и не является способом смешать application version и DB state разных release. Для recovery используйте matching version kit + backup. После успешного health-check переходите вперёд штатным `upgrade.sh`.

Если restore уже перешёл к destructive phase и затем завершается ошибкой, не запускайте Portal наугад. Сохраните исходный backup, устраните инфраструктурную причину и повторите matching-version restore.

## Upgrade

Рекомендуемый сценарий — распаковать новый versioned kit рядом с предыдущим и запустить `upgrade.sh` из нового каталога, передав путь к старой установке:

```bash
cd /opt/harbor-transfer-portal-v1.1.0-offline-install
./upgrade.sh /opt/harbor-transfer-portal-v1.0.0-offline-install
```

Upgrade выполняет операции в таком порядке:

1. проверяет `CHECKSUMS.sha256` нового kit, architecture и Docker prerequisites;
2. проверяет старый `.env` и текущую `PORTAL_VERSION`;
3. **до любой mutation** проверяет, что предыдущая installation сама является recoverable matching-version kit: `restore.sh`, release metadata, bundled images и checksums присутствуют и валидны;
4. создаёт обязательный pre-upgrade backup;
5. только после backup загружает bundled backend/frontend images новой версии через `docker load`;
6. проверяет exact local image references и architecture;
7. копирует прежнюю конфигурацию, атомарно меняя только `PORTAL_VERSION`;
8. запускает новый Compose с `--no-build --pull never --wait`.

`PORTAL_CONTOUR`, JWT secret, Harbor settings и остальные строки старой `.env` сохраняются без регенерации. Для уже инициализированной installation `PORTAL_CONTOUR` остаётся bootstrap fallback; authoritative runtime mode сохраняется в SQLite persistent state. Stable Compose project name `harbor-transfer-portal` сохраняет identity named volume между versioned каталогами.

### Transactional rollback

Если startup новой версии завершается ошибкой, `upgrade.sh` **не ограничивается возвратом строки PORTAL_VERSION**. Он удаляет activation `.env` нового kit и автоматически запускает verified `restore.sh` из предыдущего matching-version kit с обязательным pre-upgrade backup.

Таким образом rollback возвращает:

- предыдущую `.env`;
- SQLite schema/state до возможной forward Alembic migration;
- authoritative runtime mode;
- Harbor credential/CA;
- SOURCE active и pending signing private keys;
- TARGET overlap trust set;
- остальные данные `/app/data`.

Rollback выполняется matching-version local images с `--pull never --network none`; network helper или registry не нужны.

`upgrade.sh` имеет три явных terminal outcome:

```text
UPGRADE_OK       новая версия прошла startup/health
ROLLBACK_OK      новая версия упала, предыдущая версия и snapshot автоматически восстановлены
ROLLBACK_FAILED  новая версия упала и автоматический matching-version restore тоже не завершился
```

При `ROLLBACK_OK` команда upgrade всё равно возвращает non-zero: upgrade не состоялся, но предыдущая installation снова является active. Новый kit не оставляет активный `.env`.

При `ROLLBACK_FAILED` не запускайте ни старую, ни новую версию вручную против текущего volume. Сохраните указанный pre-upgrade backup, устраните инфраструктурную причину и повторите recovery из указанного matching-version предыдущего kit.

Не удаляйте pre-upgrade backup до завершения функциональной проверки новой версии.

## Safe uninstall

Обычный uninstall:

```bash
./uninstall.sh
```

или для другого installation directory:

```bash
./uninstall.sh /opt/harbor-transfer-portal-v1.1.0-offline-install
```

По умолчанию команда выполняет Compose `down`, но **не удаляет**:

- named volume `harbor-transfer-portal_portal-data`;
- `.env`;
- release directory;
- backup archives.

Это позволяет повторно поднять установку или перейти к контролируемому recovery без потери persistent state.

### Destructive purge volume

Удаление persistent volume требует одновременно option и точного explicit confirmation:

```bash
PORTAL_CONFIRM_PURGE=DELETE_PORTAL_DATA ./uninstall.sh --purge-data
```

`--purge-data` без confirmation завершается до destructive Compose invocation. Даже подтверждённый purge не удаляет `.env` и release files автоматически: оператор должен обработать host-side secrets/files отдельно по своей retention policy.

Перед purge рекомендуется создать и проверить backup.

## Offline boundary

Lifecycle scripts не скачивают helper images, packages или binaries. Backup и restore используют current/matching local backend image с отключённой сетью, upgrade использует только bundled images, а Compose startup выполняется с `--no-build --pull never`.

Clean-host installation qualification и полный isolated SOURCE → physical bundle → TARGET acceptance **реализованы** как fail-able CI gates baseline v1.0.0. Они проверяют offline install/restart persistence и end-to-end transfer соответственно; актуальная test-selection policy описана в [testing.md](testing.md).
