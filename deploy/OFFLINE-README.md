# Harbor Transfer Portal — offline installation kit

Этот каталог поставки предназначен для установки Harbor Transfer Portal в изолированном контуре без доступа к PyPI, npm, apt, Docker Hub, `get.helm.sh` или другому внешнему registry во время установки.

## Состав

- `images/backend.tar` — заранее собранный backend image с Python runtime, Skopeo и Helm;
- `images/frontend.tar` — заранее собранный frontend/Nginx image;
- `compose.yaml` — release Compose без `build:` и с `pull_policy: never`;
- `.env.example` — безопасный шаблон без реальных credentials/secrets;
- `install.sh` — проверка kit и установка через `docker load` + `docker compose up --no-build`;
- `release.json` — версия, architecture, source commit, image checksums и версии Skopeo/Helm;
- `VERSION`, `ARCHITECTURE` — машинно-простые release markers;
- `SHA256SUMS` — checksums всех файлов внутри kit.

Рядом с архивом поставляется `<archive>.sha256` для проверки самого tar.gz до распаковки.

## Требования

Поддерживается Linux `amd64` или `arm64`, соответствующий `ARCHITECTURE` конкретного kit.

На целевой VM нужны:

- Docker Engine 24.0+;
- Docker Compose plugin 2.20+;
- стандартные Linux utilities: `sha256sum`, `awk`, `sed`, `sort`, `od`, `du`, `df`.

Python, Node.js, Skopeo и Helm на host не нужны.

## Проверка архива до распаковки

На носителе должны находиться archive и его `.sha256` sidecar:

```bash
sha256sum -c harbor-transfer-portal-v1.0.0-offline-install.tar.gz.sha256
tar -xzf harbor-transfer-portal-v1.0.0-offline-install.tar.gz
cd harbor-transfer-portal-v1.0.0-offline-install
```

Подставьте фактическую release version.

## Установка SOURCE

```bash
./install.sh SOURCE
```

## Установка TARGET

```bash
./install.sh TARGET
```

Один и тот же release payload используется для обеих ролей. Роль фиксируется в локальном `.env`.

Installer сначала проверяет `SHA256SUMS`, host architecture, Docker/Compose и свободное место, затем создаёт `.env` только при его отсутствии, генерирует локальный JWT secret, загружает заранее собранные images и запускает Compose без build/pull.

## Повторный запуск

Повторный:

```bash
./install.sh SOURCE
```

или:

```bash
./install.sh TARGET
```

безопасен для **той же версии и того же contour**: существующий `.env` не перезаписывается, named volume `portal-data` сохраняется.

Если `PORTAL_VERSION` в существующем `.env` отличается от версии kit, installer прекращает работу. Upgrade нельзя подменять обычным reinstall — используйте отдельную документированную upgrade procedure.

## Первичная настройка

После успешного health-check installer печатает команду для создания bootstrap admin. Пароль администратора передаётся только через временную environment variable процесса CLI и не сохраняется в release kit.

Далее через web UI:

1. настройте локальный Harbor только этого контура;
2. оставьте TLS verification включённой;
3. при private PKI загрузите local CA;
4. SOURCE — настройте Ed25519 signing private key;
5. TARGET — установите только trusted SOURCE public key(s).

Harbor credentials, JWT secrets, user data, SQLite DB и SOURCE private signing key **не входят** в release archive.

## Air-gap invariant

Штатная установка не должна выполнять:

- `docker compose build`;
- `docker pull`;
- `npm install`;
- `pip install`;
- `apt-get`;
- загрузку Helm/Skopeo из интернета.

Если `docker load` не создал ожидаемые local image references, installer завершится ошибкой вместо попытки скачать image.

## Проверка версии

```bash
cat VERSION
cat ARCHITECTURE
cat release.json
```

`release.json` фиксирует полный Git source commit и фактические версии Skopeo/Helm внутри backend image.
