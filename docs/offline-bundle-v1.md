# Протокол офлайн-пакета Harbor Transfer Portal v1

Статус: нормативный документ для семейства схем `1.x`.

## Назначение

Установки SOURCE и TARGET не имеют сетевого соединения друг с другом. Совместимость между ними определяется только этим протоколом пакета доставки. Пакет не содержит учётных данных Harbor, токенов или закрытых ключей подписи.

## Имя файла пакета

Рекомендуемый формат: `DELIVERY-YYYYMMDD-RANDOM.htp.tar.gz`, где `RANDOM` — 6–32 заглавных латинских букв или цифр, полученных из криптографически стойкого источника случайности. `delivery_id` использует то же значение `DELIVERY-YYYYMMDD-RANDOM` и после создания не изменяется.

## Обязательные элементы верхнего уровня

- `manifest.json`;
- `manifest.sig`;
- `checksums.sha256`;
- один или несколько payload-файлов/каталогов, указанных в `manifest.json`.

Все пути используют `/` как разделитель и задаются относительно корня архива.

## Подписываемые байты

`manifest.sig` содержит Ed25519-подпись точных канонических UTF-8 байтов `manifest.json`.

Канонический JSON определяется следующим образом:

- без BOM;
- кодировка UTF-8;
- ключи объектов отсортированы;
- отсутствуют незначащие пробелы;
- используются разделители JSON `,` и `:`;
- поля со значением `null` не сериализуются.

Перед проверкой подписи реализация не должна разбирать и повторно сериализовать манифест по другим правилам.

## Контрольные суммы

`checksums.sha256` содержит SHA-256 для каждого payload-файла. Он **не должен** содержать запись для самого себя или `manifest.sig`, чтобы не создавать циклических зависимостей.

Подлинность `manifest.json` подтверждается Ed25519-подписью. Контрольная сумма и размер каждого payload дополнительно записываются в соответствующем descriptor манифеста.

Для payload, представленного одним файлом, `payload_sha256` равен SHA-256 этого файла, а `payload_size` — размеру файла.

Для payload-каталога OCI image-layout descriptor использует детерминированный tree checksum. Все строки `checksums.sha256`, относящиеся к файлам под `payload_path`, сортируются лексикографически по полному archive path и сериализуются в точном формате `<sha256>  <archive/path>\n`. `payload_sha256` равен SHA-256 UTF-8 concatenation этих строк, а `payload_size` равен сумме размеров файлов. Tree checksum является transport-integrity metadata и **не является** OCI digest; authoritative source OCI digest хранится отдельно в `source_digest`.

Порядок проверки на TARGET:

1. безопасная структура архива;
2. поддерживаемая major-версия схемы;
3. канонический манифест и Ed25519-подпись;
4. синтаксис `checksums.sha256`;
5. SHA-256 и размеры payload;
6. семантическая валидация манифеста;
7. только после этого — импорт.

## Манифест

Обязательные поля: `schema_version`, `delivery_id`, UTC `created_at`, `created_by`, несекретные metadata `source` и непустой массив `artifacts`.

Descriptor контейнерного образа содержит как минимум:

- `type=container-image`;
- repository;
- tag/reference;
- исходный OCI digest;
- относительный путь payload;
- SHA-256 payload;
- размер payload.

Descriptor Helm-чарта содержит как минимум:

- `type=helm-chart`;
- repository/name;
- version;
- исходный OCI digest, если его можно получить с достаточной надёжностью;
- относительный путь `.tgz` payload;
- SHA-256;
- размер.

Неизвестные необязательные поля внутри major-версии 1 следует игнорировать, если это безопасно. Неподдерживаемая major-версия должна быть отклонена до импорта.

## Представление контейнерных образов

Контейнерные образы хранятся внутри пакета как каталог OCI image-layout, соответствующий подходу `skopeo copy --all ... oci:<path>:<tag>`, а не как Docker archive.

Такое представление лучше сохраняет multi-architecture index/manifest и OCI digests. Внешний `.htp.tar.gz` уже обеспечивает переносимость одним файлом, поэтому дополнительный Docker-style tar внутри пакета не требуется.

Архитектурное решение подробно зафиксировано в [ADR-009](adr/ADR-009-oci-layout-payload.md).

## Безопасность архива

Reader/verifier обязан отклонять:

- абсолютные пути;
- path traversal (`..`);
- `\` как разделитель пути;
- имена с NUL;
- дублирующиеся archive members;
- symlink;
- hardlink;
- device nodes;
- FIFO;
- иные неподдерживаемые типы файлов.

Критические элементы `manifest.json`, `manifest.sig` и `checksums.sha256` должны встречаться ровно по одному разу. Распаковка разрешена только в новый контролируемый каталог.

Реализация verifier должна применять конфигурируемые верхние пределы archive size, суммарного extracted size, количества members и длины path. Для gzip/tar также применяется defensible compression-ratio limit до extraction, чтобы malformed/high-expansion archive отклонялся до записи payload на диск.

В штатном layout container image payload располагается под `images/`, Helm package — обычный `.tgz` под `charts/`. Каждый payload-файл должен быть покрыт ровно одной checksum-строкой и ровно одним artifact descriptor root; undeclared и пересекающиеся payload roots запрещены.

## Модель состояний операций

Базовая цепочка экспорта:

`CREATED → VALIDATING → RUNNING → PACKAGING → VERIFYING → COMPLETED`.

`FAILED` и `CANCELLED` — терминальные выходы из активных стадий при ошибке или отмене.

Базовая цепочка импорта:

`UPLOADED|DISCOVERED → VERIFYING → READY → IMPORTING → VERIFYING_TARGET → COMPLETED`.

`FAILED`, `REJECTED` и `CANCELLED` — терминальные состояния. `REJECTED` означает, что пакет отклонён до импорта из-за ошибки доверия, совместимости или политики валидации.

Недопустимый переход состояния является доменной ошибкой и не должен автоматически приводиться к другому состоянию.

## Состояния отдельных артефактов

Используются состояния:

`PENDING`, `RUNNING`, `IMPORTED`, `SKIPPED`, `CONFLICT`, `FAILED`, `VERIFIED`.

`SKIPPED` означает отсутствие registry mutation. Для `SAME` это идемпотентный успех, потому что TARGET уже содержит ожидаемый артефакт. Для explicit conflict-skip policy `SKIPPED` обязан сопровождаться persisted причиной `import_conflict_skipped` и фактически наблюдаемым TARGET digest: это не утверждение, что содержимое совпадает, а доказательство намеренного сохранения существующего TARGET artifact.

`CONFLICT` означает, что в той же логической точке назначения уже существует другое проверенное содержимое; без explicit skip/overwrite policy такой artifact блокирует execution.
