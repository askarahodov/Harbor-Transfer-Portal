# Offline lifecycle: backup, upgrade и uninstall

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

## Upgrade

Рекомендуемый сценарий — распаковать новый versioned kit рядом с предыдущим и запустить `upgrade.sh` из нового каталога, передав путь к старой установке:

```bash
cd /opt/harbor-transfer-portal-v1.1.0-offline-install
./upgrade.sh /opt/harbor-transfer-portal-v1.0.0-offline-install
```

Upgrade выполняет операции в таком порядке:

1. проверяет `CHECKSUMS.sha256` нового kit, architecture и Docker prerequisites;
2. проверяет старый `.env` и текущую `PORTAL_VERSION`;
3. создаёт обязательный pre-upgrade backup;
4. только после backup загружает bundled backend/frontend images через `docker load`;
5. проверяет exact local image references и architecture;
6. копирует прежнюю конфигурацию, атомарно меняя только `PORTAL_VERSION`;
7. запускает новый Compose с `--no-build --pull never --wait`.

`PORTAL_CONTOUR`, JWT secret, Harbor settings и остальные строки старой `.env` сохраняются без регенерации. Stable Compose project name `harbor-transfer-portal` сохраняет identity named volume между versioned каталогами.

Если startup новой версии завершается ошибкой, `upgrade.sh` возвращает `.env` к предыдущей `PORTAL_VERSION` и сообщает путь к backup. Это **не является полным automatic rollback**: backend startup мог уже применить forward Alembic migration к SQLite. Если migration не backwards compatible, возврат старого image может потребовать восстановление pre-upgrade backup по утверждённой disaster-recovery процедуре.

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

Lifecycle scripts не скачивают helper images, packages или binaries. Backup использует текущий local backend image с отключённой сетью, upgrade использует только bundled images, а Compose startup выполняется с `--no-build --pull never`.

Full clean-VM installation acceptance, restore qualification и полный SOURCE → physical bundle → TARGET acceptance E2E остаются отдельными release gates задачи #28.
