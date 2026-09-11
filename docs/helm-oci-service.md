# Helm OCI service

`HelmOciService` — единая backend-граница для переноса Helm chart в OCI-репозиториях локального Harbor.

## Контракт

Сервис принимает структурированную ссылку `repository + name + version`; вызывающий код не передаёт shell-команды, registry host или OCI transport строкой. Registry всегда берётся из effective local Harbor settings.

Поддержаны операции:

- `pull_chart()` — фиксирует Harbor artifact digest, выполняет `helm pull` в контролируемый workspace и валидирует полученный `.tgz`;
- `validate_package()` — проверяет безопасные пути и типы members tar archive, затем использует `helm show chart` и сравнивает `name/version` с ожидаемыми;
- `inspect_target()` — возвращает `absent`, `same_digest`, `conflicting_digest` или `present`;
- `push_chart()` — выполняет безопасный preflight, валидирует package, отправляет его только в локальный Harbor и повторно проверяет наличие artifact через Harbor API.

## Credential и subprocess

Helm запускается только через `asyncio.create_subprocess_exec(*argv)`. `shell=True` не используется.

Credential не передаётся аргументом командной строки: `helm registry login` получает пароль через `--password-stdin`. Значение credential входит в redaction set и не включается в `HelmServiceError`.

Для каждой операции создаётся отдельный временный каталог mode `0700`. В него направляются:

- `HOME`;
- `HELM_CONFIG_HOME`;
- `HELM_CACHE_HOME`;
- `HELM_DATA_HOME`;
- `HELM_REGISTRY_CONFIG`.

Helm subprocess **не наследует весь environment backend-процесса**. Передаются только контролируемые Helm paths, `PATH` и безопасные locale-переменные. Поэтому `JWT_SECRET`, `HARBOR_PASSWORD` и другие portal secrets не попадают в дочерний процесс только из-за присутствия в environment backend.

После операции временный каталог удаляется. Это исключает совместное mutable Helm-состояние между задачами.

stdout/stderr читаются потоково и сохраняются только до `HELM_OUTPUT_LIMIT_BYTES`. Timeout и asyncio cancellation завершают child process.

## TLS, custom CA и HTTP

Сервис использует Harbor transport policy из настроек портала.

- verification включена по умолчанию для `https://`;
- custom CA копируется только во временный controlled path и передаётся через `--ca-file`;
- `--insecure` / `--insecure-skip-tls-verify` появляются только если администратор явно отключил TLS verification для HTTPS;
- если администратор явно настроил `http://` Harbor, Helm получает `--plain-http`; это не является автоматическим fallback после TLS-ошибки;
- silent fallback с HTTPS на insecure/plain HTTP отсутствует.

Production-развёртывание должно предпочитать HTTPS с валидным корпоративным CA. `http://` предназначен только для явно выбранного локального режима, например disposable integration registry.

## Workspace и package validation

`HELM_WORKSPACE_ROOT` задаёт единственный допустимый корень для входных и выходных `.tgz`.

До вызова Helm archive просматривается без распаковки. Разрешены только обычные файлы и каталоги. Отклоняются absolute paths, `..`, backslash/NUL, symlink/hardlink, FIFO/device и другие special members. После этого `helm show chart` обязан успешно прочитать package, а `name/version` должны точно совпасть с ожидаемой ссылкой.

Имя файла `.tgz` не считается источником metadata: после `helm pull` сервис принимает ровно один package в пустом destination и извлекает metadata через Helm.

## Digest guarantee

Для Helm OCI сервис не обещает ту же гарантию сохранения manifest digest, что Skopeo service для container image.

До SOURCE pull и при TARGET inspection записывается digest Harbor OCI artifact, если Harbor его предоставляет. Сам `.tgz` идентифицируется отдельным SHA-256 checksum. После push Harbor независимо подтверждает новый artifact и его digest.

Если TARGET уже содержит выбранную версию, `inspect_target()` позволяет отличить совпадающий digest от конфликта. `push_chart()` безопасно отказывается перезаписывать существующую версию: окончательная conflict/idempotency policy принадлежит import engine.

`digest_matches_source` в результате push — наблюдаемая metadata, а не доказательство идентичности transport representation. Bundle checksum + chart `name/version` + Harbor metadata являются явными проверяемыми уровнями v1.

## Runtime settings

- `HELM_BINARY=helm`
- `HELM_TIMEOUT_SECONDS=300`
- `HELM_OUTPUT_LIMIT_BYTES=65536`
- `HELM_WORKSPACE_ROOT=./data/packages`
- `HELM_TEMP_ROOT=./data/tmp/helm`

Disposable-registry integration test не включён в обычный backend unit job: текущий CI не поднимает отдельный OCI registry lifecycle. Такая проверка должна выполняться в integration workflow export/import engine без обращения к публичному интернету.
