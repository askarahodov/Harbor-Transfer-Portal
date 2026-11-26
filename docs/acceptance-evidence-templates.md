# Шаблоны evidence внешней приёмки

**Статус:** формы сбора доказательств, **не доказательство приёмки**. Ни одна таблица ниже не является подтверждением реального теста, пока не заполнена фактическими значениями на разрешённом стенде и не подписана ответственными.

Этот документ задаёт единый формат operator/security evidence для трёх внешних gate:

- **Gate A — [#507](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/507)**: Windows 10/11 clean-host offline lifecycle.
- **Gate B — [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)**: физически изолированный Harbor Core ↔ Nexus SOURCE→TARGET handoff.
- **Gate C — [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436)**: региональная приёмка Nexus (TLS/CA, ACL, версии/редакции, внешний writer).

Это шаблоны для операторов и security. Они **не** заменяют [Testing/CI](testing.md), [Signed physical handoff](physical-handoff.md), [Security](security.md) и не отменяют региональный **NO-GO** из [Draft #503](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/503).

## 0. Общие правила

### 0.1. Допустимые статусы

| Статус | Значение |
| --- | --- |
| `NOT RUN` | Проверка не выполнялась или evidence не приложен. Значение по умолчанию для каждой строки. |
| `PASS` | Проверка выполнена на реальном стенде, результат совпал с ожидаемым, evidence приложен и подписан. |
| `FAIL` | Проверка выполнена, результат не совпал с ожидаемым. Исправление требует нового прогона. |
| `BLOCKED` | Проверка невозможна без внешнего ресурса (доступ, окно, credentials, изменение). Указать blocker и владельца. |
| `N/A` | Проверка неприменима к данному контуру. Требует обоснования, а не пустой ячейки. |

Если хотя бы один обязательный пункт gate находится в `NOT RUN`, `FAIL` или `BLOCKED`, итоговый вердикт gate — **не PASS**. Пустая ячейка эквивалентна `NOT RUN`.

### 0.2. Что запрещено прикладывать

- `.env` и любые полные environment dumps;
- пароли, service passwords, `JWT_SECRET`, bearer/`Set-Cookie` токены;
- SOURCE private signing key и любые private keys;
- backup ZIP/архивы с секретами;
- Harbor/Nexus credentials и credential-bearing URL;
- сырые `stderr`/тела ответов с URL, токенами или внутренними именами.

Разрешено прикладывать хеши, digest, operation ID, HTTP status, sanitized JSON отчётов, redacted command transcript и ссылки на защищённое хранилище evidence.

### 0.3. Идентификаторы и хранение

Поля `environment`, `operator`, `change_id` и аналогичные метки — нейтральные ASCII-идентификаторы: латинские буквы, цифры, `.`, `_`, `-`, не более 128 символов, первый символ — буква или цифра. URL, пути, пробелы и секреты в идентификаторы не попадают.

Evidence хранится в защищённом месте с ограниченным доступом; срок хранения и владелец фиксируются в паспорте gate. Отчёт о приёмке не публикуется как CI artifact, если он содержит данные корпоративного контура.

### 0.4. Запрет объявлять GO

`PASS` отдельной строки, зелёный CI или disposable E2E **не** являются релизным GO. GO для Gate B/C возможен только после заполнения всех обязательных пунктов и **подписей** operator, security и владельца контура. До этого вердикт — **NO-GO**, а Issues #434/#436 остаются открытыми.

## 1. Gate A — #507: Windows 10/11 clean-host offline lifecycle

Исполняемая процедура прогона — `docs/windows-offline-acceptance.md` из [Draft PR #548](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/feat/507-windows-offline-lifecycle/docs/windows-offline-acceptance.md) (не в `main`). Ниже — форма фиксации результатов этой процедуры.

### 1.1. Статус gate

| Поле | Значение |
| --- | --- |
| Gate | A — #507 Windows offline lifecycle |
| Статус | `NOT RUN` |
| Регион/площадка | `НЕ ЗАПОЛНЕНО` |
| Дата и окно теста | `НЕ ЗАПОЛНЕНО` |
| Change/approval ID | `НЕ ЗАПОЛНЕНО` |
| Ответственный owner | `НЕ НАЗНАЧЕН` |
| Расположение evidence | `НЕ ЗАПОЛНЕНО` |

### 1.2. Паспорт хоста и kit

| Поле | Значение | Evidence |
| --- | --- | --- |
| Host/VM ID (disposable, не production) | `НЕ ЗАПОЛНЕНО` | инвентарная запись |
| Windows edition и build (`winver`) | `НЕ ЗАПОЛНЕНО` | скриншот/команда |
| Architecture (host) | `НЕ ЗАПОЛНЕНО` | `docker info` |
| PowerShell version | `НЕ ЗАПОЛНЕНО` | `$PSVersionTable` |
| Docker Desktop version | `НЕ ЗАПОЛНЕНО` | About/`docker version` |
| Docker Engine version | `НЕ ЗАПОЛНЕНО` | `docker version` |
| Compose version (v2) | `НЕ ЗАПОЛНЕНО` | `docker compose version` |
| Linux containers mode подтверждён | `NOT RUN` | `docker info --format '{{.OSType}}'` = `linux` |
| Clean-host preflight: нет существующего Compose project/volume | `NOT RUN` | `docker compose ls --all`, `docker volume ls` |
| Kit version | `НЕ ЗАПОЛНЕНО` | `release-version.txt` |
| Kit archive filename | `НЕ ЗАПОЛНЕНО` | список файлов |
| Kit archive SHA-256 (внешний) | `НЕ ЗАПОЛНЕНО` | `Get-FileHash` + sidecar |
| Sidecar SHA-256 совпал | `NOT RUN` | сравнение |
| Architecture совпала с `release-arch.txt` | `NOT RUN` | сравнение |
| Portal commit SHA кандидата (CI-tested) | `НЕ ЗАПОЛНЕНО` | git/gate запись |

### 1.3. Матрица lifecycle-команд

| Шаг | Точная команда (без секретов) | Ожидаемый результат | Статус | Evidence |
| --- | --- | --- | --- | --- |
| Install (offline, no build/pull) | `.\install.cmd` | Установка без build/pull, healthy Portal | `NOT RUN` | transcript + `docker compose ps` |
| Повторный install (idempotency) | `.\install.cmd` | `.env` hash не меняется, состояние сохранено | `NOT RUN` | hash before/after |
| Start | `.\start.ps1` | Compose healthy, `/api/health` успешен | `NOT RUN` | health output |
| Stop | `.\stop.ps1` | Контейнеры остановлены, volume/config сохранены | `NOT RUN` | `docker compose ps` |
| Health readiness | `Invoke-WebRequest http://127.0.0.1:8080/api/health` | HTTP успех после start/restore/upgrade | `NOT RUN` | response status |
| Backup | `.\backup.ps1 -BackupDir <secure>` | ZIP + `.sha256`, ACL каталога защищён | `NOT RUN` | пути (без содержимого) |
| Restore (matching version) | `.\restore.ps1 -BackupArchive <zip> -ConfirmRestore` | Volume/.env восстановлены, health успешен | `NOT RUN` | transcript |
| Upgrade (старший kit) | `.\upgrade.ps1 -NewKitDir <new> -InstallDir <old>` | `UPGRADE_OK`, health новой версии | `NOT RUN` | transcript + версии |
| Failed-upgrade rollback | `.\upgrade.ps1 ...` с unhealthy candidate | Non-zero, `ROLLBACK_OK`, старая версия healthy | `NOT RUN` | transcript |
| Uninstall (preserve) | `.\uninstall.ps1` | Volume и `.env` сохранены, kit снова стартует | `NOT RUN` | `docker volume inspect` |
| Uninstall purge (disposable) | `.\uninstall.ps1 -PurgeData -ConfirmPurge DELETE_PORTAL_DATA` | Volume удалён только после проверенного backup | `NOT RUN` | transcript |

### 1.4. Preservation и idempotency

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| `.env` SHA-256 до/после повторного install | Идентичны | `NOT RUN` | хеши (не содержимое) |
| `.env` ACL защищён от inheritance | `AreAccessRulesProtected = true` | `NOT RUN` | `Get-Acl` |
| Persistent volume после stop/start | Не удалён, данные доступны | `NOT RUN` | `docker volume inspect` |
| Runtime mode после restore | Соответствует восстановленной SQLite, не переопределён `.env` | `NOT RUN` | `/api/runtime` |
| Настройки `.env` кроме `PORTAL_VERSION` после upgrade | Сохранены | `NOT RUN` | diff ключей (не значений) |
| Backup/`restore` требуют matching-версию | Иная версия отклоняется | `NOT RUN` | код отказа |

### 1.5. Negative cases

| Сценарий | Ожидаемый fail-closed результат | Статус | Evidence |
| --- | --- | --- | --- |
| Docker/Compose отсутствуют или не запущены | Скрипт возвращает non-zero с понятной ошибкой | `NOT RUN` | exit code |
| Повреждённый payload (SHA-256 mismatch) | Распаковка/установка отклонена до mutation | `NOT RUN` | exit code + сообщение |
| Неверная architecture kit | Установка отклонена | `NOT RUN` | exit code |
| Неисправные/unhealthy containers | Health/readiness не PASS, оператор уведомлён | `NOT RUN` | health output |
| Попытка build/pull при offline install | Отсутствует; install не требует сети к registry | `NOT RUN` | transcript |
| Backup ZIP/`.env` не приложены к evidence | Подтверждено redaction | `NOT RUN` | чек-лист |

### 1.6. Обязательные артефакты Gate A

- паспорт хоста (§1.2) с версиями Windows, Docker Desktop/Engine и Compose;
- commit SHA кандидата, kit version, architecture и внешний SHA-256;
- redacted command transcript для всех шагов §1.3;
- `docker compose ps` и health output до/после restart, restore и upgrade;
- hash `.env` до/после rerun и подтверждение защищённого ACL;
- подтверждение сохранения volume при uninstall и удаления только при purge;
- результаты negative cases §1.5;
- заполненная таблица подписей §1.8.

### 1.7. Критерии PASS Gate A

1. Все строки §1.2–§1.5 — `PASS` (или обоснованный `N/A` там, где это допустимо).
2. Clean-host install прошёл offline, без image build/pull.
3. Re-run идемпотентен, `.env`/state/volume сохранены.
4. Negative cases возвращают non-zero, а не «тихий» успех.
5. Failed-upgrade завершается `ROLLBACK_OK` с healthy старой версией.
6. Evidence не содержит секретов.

### 1.8. Подписи Gate A

| Роль | Имя/ID | Дата | Подпись/verdict |
| --- | --- | --- | --- |
| Operator | `НЕ ПОДПИСАНО` | `—` | `—` |
| Independent reviewer | `НЕ ПОДПИСАНО` | `—` | `—` |
| Owner | `НЕ ПОДПИСАНО` | `—` | `—` |
| Gate verdict | — | — | **NO-GO** до подписей |

## 2. Gate B — #434: изолированный Harbor Core ↔ Nexus SOURCE→TARGET handoff

Gate проверяет **физическую** передачу signed delivery triplet между разными изолированными контурами. Disposable CI/E2E на настоящем Harbor Core и Nexus — необходимое, но недостаточное условие; региональный контур требует собственного evidence.

### 2.1. Статус gate

| Поле | Значение |
| --- | --- |
| Gate | B — #434 interop handoff |
| Статус | `NOT RUN` |
| Регион/площадка | `НЕ ЗАПОЛНЕНО` |
| Дата и окно | `НЕ ЗАПОЛНЕНО` |
| Change/approval ID | `НЕ ЗАПОЛНЕНО` |
| Owner / operator / security reviewer | `НЕ НАЗНАЧЕНЫ` |
| Расположение evidence | `НЕ ЗАПОЛНЕНО` |

### 2.2. Паспорт контуров SOURCE и TARGET

| Поле | SOURCE | TARGET |
| --- | --- | --- |
| Тип registry | Harbor Core / Nexus | Harbor Core / Nexus |
| Версия и edition (подтверждено HTTP header/паспортом) | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Portal commit SHA развёрнутой ревизии | `НЕ УСТАНОВЛЕН` | `НЕ УСТАНОВЛЕН` |
| Project/repository и test namespace | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Service identity ID (без секрета) | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| TLS trust: CA, SAN, chain | `НЕ ПРОВЕРЕНО` | `НЕ ПРОВЕРЕНО` |
| SOURCE public signing key fingerprint, доверенный TARGET | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Наличие SOURCE private key на TARGET | — | `NOT RUN` (ожидается отсутствие) |

### 2.3. Delivery triplet и chain-of-custody

| Поле | Значение | Evidence |
| --- | --- | --- |
| Delivery/operation ID | `НЕ ЗАПОЛНЕНО` | export record |
| Bundle filename | `НЕ ЗАПОЛНЕНО` | download record |
| Bundle SHA-256 | `НЕ ЗАПОЛНЕНО` | `.sha256` |
| Sidecar SHA-256 совпал с bundle | `NOT RUN` | сравнение |
| Signed handoff `.htp-handoff.json` присутствует | `NOT RUN` | файл |
| Handoff Ed25519 signature доверена TARGET | `NOT RUN` | verification result |
| SOURCE fingerprint в handoff совпал с доверенным | `NOT RUN` | сравнение |
| Носитель/канал передачи | `НЕ ЗАПОЛНЕНО` | custody record |
| Chain-of-custody: кто/когда/кому передал | `НЕ ЗАПОЛНЕНО` | подписанная ведомость |
| Verify-before-import (handoff `VERIFIED`) | `NOT RUN` | UI/API status |
| TARGET receipt: immutable, привязан к operation | `NOT RUN` | receipt path/hash |
| Source digest = target digest | `NOT RUN` | digest comparison |

### 2.4. Матрица верификации и импорта

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| Handoff `VERIFIED` до Bundle preview | Обязательно до preview/import | `NOT RUN` | статус |
| Handoff `MISMATCH`/`UNTRUSTED` блокирует discovery | Отказ fail-closed | `NOT RUN` | код ошибки |
| Bundle checksum/schema/signature verification | Только после успеха — preview/import | `NOT RUN` | transcript |
| Destination plan `NEW` для отсутствующего artifact | Разрешён импорт | `NOT RUN` | plan |
| `overwrite_conflicts=false` в TARGET | Без default overwrite | `NOT RUN` | operation policy |
| Idempotent replay того же bundle | `SAME`/`SKIPPED`, без перепубликации | `NOT RUN` | replay record |
| Conflict с другим digest | Default deny, без mutation | `NOT RUN` | conflict record |
| Receipt/history/audit сохранены и неизменяемы | Присутствуют, retention соблюдён | `NOT RUN` | receipt |
| SOURCE private key не попал в TARGET/bundle | Подтверждено отсутствие | `NOT RUN` | inventory check |

### 2.5. Negative cases Gate B

| Сценарий | Ожидаемый fail-closed результат | Статус | Evidence |
| --- | --- | --- | --- |
| Tampered bundle (изменён payload) | Отклонение до registry mutation | `NOT RUN` | код отказа |
| Tampered/отсутствующий `.sha256` sidecar | Отклонение | `NOT RUN` | код отказа |
| Подделанный handoff / неизвестный signer | `UNTRUSTED`/`MISMATCH`, импорт заблокирован | `NOT RUN` | статус handoff |
| Неверный CA/TLS trust | Отказ без bypass | `NOT RUN` | код отказа |
| Replay с изменённым digest | `CONFLICT`, без overwrite | `NOT RUN` | conflict record |
| Partial failure / потеря соединения | Без ложного успешного receipt, recovery по процедуре | `NOT RUN` | operation status |
| Попытка импорта без физического handoff | Заблокирована | `NOT RUN` | код отказа |

### 2.6. Обязательные артефакты Gate B

- паспорт SOURCE/TARGET §2.2 с реальными версиями/edition и fingerprint;
- delivery triplet: bundle, `.sha256`, signed handoff (публичные файлы) + их SHA-256;
- подписанная chain-of-custody ведомость носителя;
- handoff verification result и TARGET receipt с operation ID;
- digest comparison SOURCE/TARGET;
- redacted transcript replay/conflict и negative cases;
- подтверждение отсутствия SOURCE private key и credentials противоположного контура;
- подписи §2.8.

### 2.7. Критерии PASS Gate B

1. SOURCE и TARGET физически изолированы; перенос только через triplet и разрешённый носитель.
2. Handoff проверен до Bundle preview и импорта.
3. Source digest совпал с target digest; receipt неизменяем и привязан к operation.
4. Replay безопасен (`SAME`/`SKIPPED`), conflict default-deny, tamper отклонён.
5. Нет утечки private key/секретов; evidence sanitized.
6. Все подписи §2.8 получены.

### 2.8. Подписи Gate B

| Роль | Имя/ID | Дата | Подпись/verdict |
| --- | --- | --- | --- |
| SOURCE operator | `НЕ ПОДПИСАНО` | `—` | `—` |
| TARGET operator | `НЕ ПОДПИСАНО` | `—` | `—` |
| Security reviewer | `НЕ ПОДПИСАНО` | `—` | `—` |
| Owner | `НЕ ПОДПИСАНО` | `—` | `—` |
| Gate verdict | — | — | **NO-GO** до подписей |

## 3. Gate C — #436: региональная приёмка Nexus

Gate проверяет корпоративный Nexus разных версий/редакций: TLS/CA/SAN/chain, proxy/realm/redirect без bypass, per-repository ACL, multi-repo read/write, отсутствие утечки скрытых имён и гарантию no-overwrite при внешнем writer. Детальный runbook и read-only preflight — в [Draft #503](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/fix/nexus-direct-image-lookup-20261117/docs/nexus-regional-acceptance-runbook.md).

### 3.1. Статус gate

| Поле | Значение |
| --- | --- |
| Gate | C — #436 regional Nexus |
| Статус | `NOT RUN` |
| Регион/площадка | `НЕ ЗАПОЛНЕНО` |
| Дата и окно | `НЕ ЗАПОЛНЕНО` |
| Change/approval ID | `НЕ ЗАПОЛНЕНО` |
| Owner / operator / security reviewer | `НЕ НАЗНАЧЕНЫ` |
| Расположение evidence | `НЕ ЗАПОЛНЕНО` |

### 3.2. Версии и редакции Nexus

| Поле | SOURCE | TARGET |
| --- | --- | --- |
| Nexus version / build / edition (OSS/Pro) | `НЕ ПРОВЕРЕНО` | `НЕ ПРОВЕРЕНО` |
| Способ подтверждения (HTTP header/паспорт сервера) | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Docker v2 connector URL (по repository) | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Nexus UI/API HTTPS origin (отдельно от connector) | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Helm HTTP repository path / `index.yaml` | `НЕ ЗАПОЛНЕНО` | `НЕ ЗАПОЛНЕНО` |
| Portal commit SHA развёрнутой ревизии | `НЕ УСТАНОВЛЕН` | `НЕ УСТАНОВЛЕН` |
| Непрозрачный proxy header и версия | `NOT RUN` | `NOT RUN` |

### 3.3. TLS/CA/SAN/chain

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| Corporate CA доверен Portal | Без `--insecure`/`curl -k` | `NOT RUN` | конфиг CA (без ключа) |
| SAN совпадает с endpoint | Совпадение | `NOT RUN` | certificate summary |
| Full chain валиден | Полная цепочка | `NOT RUN` | chain summary |
| Неверный/чужой CA отклоняется | Fail-closed | `NOT RUN` | код отказа |
| Redirect на чужой host отклоняется | Без follow с credentials | `NOT RUN` | код отказа |
| Proxy/ambient `HTTP_PROXY` не используется неявно | Явная политика | `NOT RUN` | конфиг |

### 3.4. Proxy / realm / redirect без bypass

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| Docker Token Realm соответствует approved origin | Без подмены | `NOT RUN` | realm URL (sanitized) |
| Bearer/Basic auth challenge классифицируется корректно | 401/403 различимы | `NOT RUN` | HTTP status |
| Reverse proxy сохраняет Host/X-Forwarded-Proto по политике | Без TLS bypass | `NOT RUN` | конфиг/ответ |
| Небезопасный Bearer realm отклоняется до передачи credentials | Fail-closed | `NOT RUN` | код отказа |

### 3.5. Per-repository ACL-матрица

Заполняется **отдельной строкой на каждый** Docker/Helm repository. Пустой список или отказ inventory не означает отсутствие репозиториев.

| Поле | Значение |
| --- | --- |
| Contour (`SOURCE`/`TARGET`) | `НЕ ЗАПОЛНЕНО` |
| Repository name | `НЕ ЗАПОЛНЕНО` |
| Format (`docker`/`helm`) | `НЕ ЗАПОЛНЕНО` |
| Type (`hosted`/`proxy`/`group`) | `НЕ ЗАПОЛНЕНО` |
| Connector/UI HTTPS origin | `НЕ ЗАПОЛНЕНО` |
| Service identity ID (без секрета) | `НЕ ЗАПОЛНЕНО` |
| Browse / read / pull | `NOT RUN` |
| Write / upload (hosted, отдельное разрешение) | `NOT RUN` |
| Отказ 401 по неразрешённому repo | `NOT RUN` |
| Отказ 403 по неразрешённому repo | `NOT RUN` |
| Отсутствие утечки имён скрытых репозиториев | `NOT RUN` |
| Image digest или Helm chart/version/SHA-256 | `НЕ ЗАПОЛНЕНО` |
| Test policy / namespace | `НЕ СОГЛАСОВАНО` |

### 3.6. Multi-repo и изоляция

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| Несколько Docker repositories с разными ACL | Доступны независимо | `NOT RUN` | матрица §3.5 |
| Несколько Helm HTTP repositories | Доступны независимо | `NOT RUN` | матрица §3.5 |
| Отказ одного repo не ломает другой | Изоляция результата | `NOT RUN` | transcript |
| Nexus REST inventory не обходит ACL | Скрытые имена не раскрываются | `NOT RUN` | sanitized report |
| Portal inspect Docker digest через service identity | Digest получен | `NOT RUN` | digest record |
| Обычный Helm index/chart/version/`.tgz` SHA-256 | Проверено | `NOT RUN` | hash record |
| Proxy/group не предлагаются как writable | Только read | `NOT RUN` | plan |
| Helm OCI на Nexus не используется | Отсутствует | `NOT RUN` | конфиг/plan |

### 3.7. External-writer no-overwrite guarantee

`overwrite_conflicts=false` **не** является атомарным compare-and-swap против внешнего writer: push выполняется после inspect. Для GO нужна подтверждённая серверная защита (exclusive publisher/immutable tags/conditional publish или формально одобренная архитектура) и негативный тест под параллельной записью.

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| Механизм защиты от внешнего writer определён и одобрен | Immutable/exclusive publisher или эквивалент | `NOT RUN` | approval record |
| Параллельная внешняя запись не перезаписывает artifact | Отказ/сохранение исходного digest | `NOT RUN` | concurrency test |
| Conflict обнаруживается и не маскируется как `NEW` | `CONFLICT`, без mutation | `NOT RUN` | operation record |
| Локальная блокировка одного Portal не выдаётся за гарантию | Явно указано в evidence | `NOT RUN` | запись оператора |

### 3.8. Handoff и replay на Nexus-контуре

| Проверка | Ожидание | Статус | Evidence |
| --- | --- | --- | --- |
| SOURCE Preview → export `COMPLETED` | Signed triplet | `NOT RUN` | operation ID |
| Физический browser handoff `READY` | Verify до import | `NOT RUN` | статус |
| TARGET destination plan `NEW` | Только hosted import | `NOT RUN` | plan |
| Target digest + immutable receipt | Совпали с source | `NOT RUN` | receipt |
| Replay `SAME`/`SKIPPED` | Без перепубликации | `NOT RUN` | replay record |
| Tampered archive/signature/CA | Отклонено | `NOT RUN` | код отказа |
| Audit/cancel/recovery на test namespace | Проверено | `NOT RUN` | журнал |

### 3.9. Обязательные артефакты Gate C

- паспорт §3.2 с фактическими version/build/edition и способом подтверждения;
- sanitized read-only preflight report (Docker v2 + Helm) из разрешённой сети;
- заполненная per-repository ACL-матрица §3.5 (несколько Docker и Helm repos);
- TLS/CA/SAN/chain и proxy/realm/redirect evidence без bypass;
- доказательство отсутствия утечки скрытых имён при 401/403;
- evidence no-overwrite под параллельной внешней записью;
- operation IDs, bundle hash, receipt и replay/conflict records;
- подписи §3.11.

### 3.10. Критерии PASS Gate C

1. Реальные версии/editions Nexus и Portal commit SHA зафиксированы и подтверждены.
2. Corporate TLS/CA/SAN/chain валидны, proxy/realm/redirect без bypass.
3. Per-repository ACL изолированы: отказ одного repo не ломает другой, скрытые имена не утекают.
4. Multi-repo Docker и Helm HTTP работают; Helm OCI не используется; proxy/group read-only.
5. No-overwrite гарантия против внешнего writer подтверждена негативным тестом.
6. Signed handoff, receipt и digest проверены; replay/conflict/tamper fail-closed.
7. Подписи operator, security и owner получены.

### 3.11. Подписи Gate C

| Роль | Имя/ID | Дата | Подпись/verdict |
| --- | --- | --- | --- |
| Operator | `НЕ ПОДПИСАНО` | `—` | `—` |
| Security reviewer | `НЕ ПОДПИСАНО` | `—` | `—` |
| Registry owner | `НЕ ПОДПИСАНО` | `—` | `—` |
| Gate verdict | — | — | **NO-GO** до подписей |

## 4. Сводный вердикт

Заполняется только после подписей соответствующих gate. Пустое поле эквивалентно `NOT RUN`, а итоговый вердикт — **NO-GO**.

| Gate | Issue | Статус | Verdict | Approver | Evidence location |
| --- | --- | --- | --- | --- | --- |
| A — Windows offline lifecycle | #507 | `NOT RUN` | `NO-GO` | `НЕ ПОДПИСАНО` | `НЕ ЗАПОЛНЕНО` |
| B — Harbor↔Nexus handoff | #434 | `NOT RUN` | `NO-GO` | `НЕ ПОДПИСАНО` | `НЕ ЗАПОЛНЕНО` |
| C — Regional Nexus | #436 | `NOT RUN` | `NO-GO` | `НЕ ПОДПИСАНО` | `НЕ ЗАПОЛНЕНО` |

Зелёный CI, disposable E2E на настоящем Harbor Core/Nexus и заполненные шаблоны **не** являются доказательством региональной приёмки. GO требует реальных стендов, полного набора evidence и подписанного разрешения владельца.

## 5. Связанные документы

- [Testing/CI](testing.md) — какие проверки автоматизированы и что остаётся внешним.
- [Signed physical handoff](physical-handoff.md) — triplet, custody и verify-before-import.
- [Security](security.md) — trust boundaries и граница гарантии no-overwrite.
- [Offline lifecycle](offline-lifecycle.md) — backup/restore/upgrade контракт.
- [Deferred tasks](deferred-tasks.md) и [ROADMAP](../ROADMAP.md) — границы релиза и внешние blockers.
- [Draft #503 runbook](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/fix/nexus-direct-image-lookup-20261117/docs/nexus-regional-acceptance-runbook.md) и [Draft #548 procedure](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/feat/507-windows-offline-lifecycle/docs/windows-offline-acceptance.md) — процедуры прогона из draft-веток, не из `main`.