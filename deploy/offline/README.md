# Harbor Transfer Portal — offline install kit

Этот каталог является шаблоном release payload для закрытого контура. Финальный kit создаётся из **уже собранных** Docker images командой из корня репозитория:

```bash
./deploy/build-offline-kit.sh 1.0.0
```

Результат:

```text
dist/harbor-transfer-portal-v1.0.0-offline-install.tar.gz
dist/harbor-transfer-portal-v1.0.0-offline-install.tar.gz.sha256
```

## Установка в закрытом контуре

1. Перенесите архив и его `.sha256` в контур.
2. Проверьте внешний checksum архива.
3. Распакуйте архив.
4. Для SOURCE:

```bash
PORTAL_CONTOUR=SOURCE ./install.sh
```

Для TARGET:

```bash
PORTAL_CONTOUR=TARGET ./install.sh
```

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

Release Compose использует стабильное имя проекта `harbor-transfer-portal`. Поэтому named volume `portal-data` имеет одну и ту же Compose identity независимо от versioned каталога, в который распакован kit. Это важно для последующей безопасной upgrade/backup процедуры.

После первого запуска настройте через admin UI **только локальный Harbor этого контура**, CA/credentials, а также SOURCE signing key или TARGET trusted SOURCE public keys.

## Что намеренно не входит в kit

Release payload не содержит `.env`, Harbor credentials, JWT secret, SOURCE private key, TARGET trust material, SQLite DB, receipts/history или другие пользовательские данные.

Этот foundation проверяет packaging/install contract. Полный clean-VM test, backup/upgrade/uninstall и SOURCE → physical bundle → TARGET acceptance E2E выполняются отдельными следующими slices P8.1 (#28).
