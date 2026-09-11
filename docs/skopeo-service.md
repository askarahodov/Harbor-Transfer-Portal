# Skopeo service: контейнерные образы

## Назначение

`backend/app/services/skopeo_service.py` — единственная backend-граница для запуска Skopeo при переносе контейнерных образов. Export/import engine не должен собирать команды Skopeo самостоятельно.

Сервис реализует решение ADR-009: payload контейнерного образа хранится как каталог OCI image-layout, а multi-architecture содержимое копируется с `--all`.

## Безопасная модель вызова

Публичный API принимает `ImageReference(repository, reference)`, а не произвольную строку `docker://...`. Repository и tag/digest проходят строгую проверку. Registry host всегда выводится из настроек **локального Harbor текущего экземпляра**.

Subprocess запускается только через `asyncio.create_subprocess_exec(*argv)`. Shell не используется. Пользовательские значения не интерполируются в shell-строку.

Credential локального Harbor не передаётся через `--creds`. На время одной операции создаётся Docker-compatible `auth.json`:

- во временном каталоге Skopeo;
- каталог имеет режим `0700`;
- auth file имеет режим `0600`;
- в argv присутствует только путь к auth file;
- после операции временный каталог удаляется.

Username, credential и base64 auth value входят в список redaction runner'а. Ошибки service API содержат стабильный код и безопасное сообщение, но не raw stderr.

## TLS и пользовательский CA

TLS-политика берётся из effective Harbor settings (#12):

- verification включена по умолчанию;
- при `verify_tls=true` Skopeo получает `--tls-verify=true` / `--src-tls-verify=true` / `--dest-tls-verify=true`;
- custom CA копируется во временный cert-dir и передаётся через соответствующий `--cert-dir` flag;
- `false` передаётся только если admin явно отключил verification;
- автоматического fallback на `--tls-verify=false` нет.

## Export

`export_image()` выполняет последовательность:

1. inspect source image в локальном Harbor и фиксирует source digest;
2. `skopeo copy --all --preserve-digests docker://... oci:<payload>:image`;
3. проверяет минимальную структуру OCI image-layout (`oci-layout`, `index.json`, `blobs/sha256`);
4. независимо inspect'ит локальный OCI payload;
5. сравнивает payload digest с source digest.

Успешный exit code `copy` без digest verification не считается успешным export.

## Import

`import_image()`:

1. принимает только каталог внутри настроенного `SKOPEO_PAYLOAD_ROOT`;
2. проверяет структуру OCI image-layout;
3. inspect'ит payload и сравнивает его digest с manifest expectation **до push**;
4. выполняет `skopeo copy --all --preserve-digests oci:... docker://...`;
5. независимо inspect'ит TARGET Harbor;
6. сравнивает observed target digest с expected digest.

Digest mismatch является отдельной ошибкой и не маскируется как общий subprocess failure.

## Idempotency primitives

`inspect_target(..., expected_digest=...)` возвращает одно из состояний:

- `absent` — target reference отсутствует;
- `same_digest` — target уже содержит ожидаемый digest;
- `conflicting_digest` — reference существует, но digest другой;
- `present` — reference существует, если expected digest не передан.

Решение «skip / overwrite / fail» остаётся ответственностью import policy/engine.

## Progress

Сервис публикует структурированные step events (`SkopeoPhase`): inspect source, export, inspect target, import, target verification. UI не должен разбирать stdout/stderr Skopeo. Фиктивные byte percentages не генерируются.

## Timeout, cancellation и output

Параметры runtime:

- `SKOPEO_TIMEOUT_SECONDS` — максимальное время одной команды;
- `SKOPEO_OUTPUT_LIMIT_BYTES` — максимальный объём сохраняемой части каждого stdout/stderr;
- `SKOPEO_PAYLOAD_ROOT` — разрешённый root для OCI payload;
- `SKOPEO_TEMP_ROOT` — root для краткоживущих auth/CA файлов.

При timeout child process принудительно завершается. При отмене asyncio task child process также завершается, после чего `CancelledError` пробрасывается вызывающему task manager.

Reader продолжает дренировать pipe после достижения output limit, но перестаёт накапливать данные в памяти. Это исключает deadlock на полном pipe и ограничивает объём retained output.

## Тестирование

Unit tests используют injectable command runner и проверяют:

- точный argv export/import/inspect;
- отсутствие credential в argv;
- отсутствие `shell=True`;
- authfile mode `0600`;
- TLS/custom CA flags;
- stdout/stderr redaction и bounded capture;
- timeout/cancellation termination;
- source/payload/target digest checks;
- target absent/same/conflict states;
- payload path confinement.

Disposable OCI registry не добавлен в backend unit CI этой задачи: текущий backend job не поднимает registry. End-to-end registry verification должна быть добавлена в scoped export/import integration workflow, где одновременно доступны Skopeo, registry и orchestration lifecycle.
