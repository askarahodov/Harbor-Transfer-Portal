# Nexus Docker v2 — журнал доработок и статус подготовки релиза

**Срез на 03.11.2026 · Harbor Transfer Portal · кандидат на следующий релиз (версия/тег не назначены)**

> **Статус: НЕ ВЫПУЩЕНО / NO-GO для Nexus production.** Этот документ описывает
> изменения, реализованные в **открытых draft PR** и проверенные на их ветках,
> **не функциональность текущей `main` / опубликованного v1.0.0**.
> Слияние, выпуск offline kit, выкладка на SOURCE/TARGET и приёмка
> корпоративной инфраструктуры ещё не подтверждены.

**Базовый выпущенный продукт:** [v1.0.0](release-notes-v1.0.0.md)
([CHANGELOG](../CHANGELOG.md)). **Кандидат:** поддержка Nexus
**3.69.0-02 OSS**, классического **Docker Registry HTTP API v2**
(Docker schema2) без OCI Repository / Helm OCI. Для Harbor сохраняется
существующий экспорт container images и Helm OCI.

## 1. Что доработано

| Область | Реализовано в draft PR | Свидетельство / оговорка |
| --- | --- | --- |
| Проверка подключения Nexus | [#438](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/438), [issue #428](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/428): Docker v2 connection probe без зависимости от наличия `library/busybox:latest`; типизированная диагностика ошибок сети/TLS/auth | CI **success**; корпоративный URL, CA и пользователь ещё не подтверждены |
| Выбор SOURCE-образа | [#439](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/439), [issue #429](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/429): исключение Harbor REST API при Nexus, ввод точного `namespace/repository:tag`, inspect/digest и добавление в export selection | CI + disposable Nexus E2E **success**; не считать реализованным общим каталогом/пагинированным browse |
| SOURCE preview и подготовка экспорта | [#440](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/440), [issue #430](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/430): type-aware inspection, повторная проверка ожидаемого digest, запрет legacy Helm через `nexus_legacy_helm_unsupported` | CI **success**; размер Nexus image при preview может оставаться неизвестным |
| Подписанное происхождение SOURCE | [#441](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/441), [issue #432](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/432): Nexus bundle **schema 1.1** с `source.registry_type=nexus` и `source.registry`, без ложного `source.harbor` | CI, protocol/backend/integration gates **success**; TARGET должен поддерживать 1.1 до перехода SOURCE |
| Полный SOURCE API → файл | [#442](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/442), [issue #433](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/433): интегрированная ветка поверх #441 с изменениями #439/#440, login → inspect/preview → start → completed → signed bundle/sidecar/handoff → cookie download-ticket → SHA-256 и проверка подписи | [Общий CI #41633770476](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770476) **success**; [Nexus OSS E2E #41633770502](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770502) **success** (11 тестов в dedicated Nexus suite) |

### Что изменилось для оператора

После включения подготовленной ветки в будущий release оператор сможет выбрать
профиль Nexus на SOURCE, ввести точный Docker repository/tag, проверить digest,
добавить образ в список, сделать preview, запустить создание подписанного
`.htp.tar.gz` и скачать комплект с короткоживущим download-ticket.

**Пока этого нет в опубликованной v1.0.0 / `main`:** пять PR не слиты.
Нельзя считать изменения доступными на корпоративном стенде, пока не подтверждён
его точный commit/build.

### Совместимость и безопасность

- **Harbor:** схемы `1.0` и прежнее `source.harbor` сохраняются;
  имеющиеся Harbor image/Helm OCI сценарии остаются независимыми.
- **Nexus:** schema `1.1`, подписанные `registry_type` и `registry`;
  адрес и тип источника входят в каноническую подпись Ed25519, содержимое
  проверяется по SHA-256. Логины/пароли/JWT/private signing keys в bundle
  не включаются.
- **TARGET-first:** сначала обновить проверяющий TARGET для принятия bundle
  1.0 и 1.1, затем SOURCE. Старый TARGET может отклонить пакет Nexus 1.1.
- **Fail-closed:** изменение digest после выбора должно блокировать экспорт;
  неподдерживаемый Nexus Helm OCI не маскируется под Docker.
- Объединённый PR #442 является **интеграционной проверочной веткой**, а не
  дополнительным независимым релизом и не основанием автоматически
  сливать/релизить #439–#441.

## 2. Реестр проверки и обязательные оставшиеся работы

| Контроль | Текущее состояние (03.11.2026) | Необходимое условие закрытия |
| --- | --- | --- |
| Ruff / Mypy / backend pytest / frontend lint, typecheck, tests, build / security | **PASS** на интеграционной ветке #442 | После финального объединения заново запустить CI на окончательном SHA |
| Disposable Nexus 3.69.0-02 OSS, Docker schema2, SOURCE signed export, checksum, download-ticket | **PASS**: [workflow](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770502) | Проверить на финальном интегрированном SHA |
| TARGET upload/discovery → trust → classification → import → digest → receipt | **НЕ ПРОВЕРЕНО сквозным Nexus E2E**: [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) открыта | Отдельный успешный Nexus TARGET E2E на 3.69 OSS |
| UI/a11y и корректные Harbor/Nexus подписи в Settings/Export | **ЧАСТИЧНО**: типизированный SOURCE selection есть; [#431](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/431) открыта | Проверка UI/сетевых вызовов и отсутствие неверных Harbor-подписей |
| Негативные сценарии: missing tag/digest drift, отсутствие частичного bundle, cancel, недостаток диска, сбой worker, expiry ticket | **ЧАСТИЧНО**: digest drift проверен; остальные acceptance cases #433 не закрыты | Дополнительные регрессионные и интеграционные тесты |
| Региональные Docker connector / auth realm / TLS / CA / reverse proxy / RBAC / DNS | **НЕ ПРОВЕРЕНО в корпоративной сети**: [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) открыта | Заполненный протокол приёмки ниже, без записи секретов |
| Legacy Helm на Nexus 3.69 OSS | **НЕ ПОДДЕРЖИВАЕТСЯ**: [#435](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/435) открыта | Решение владельца контура: `не нужен` или отдельный scope реализации |
| Исходный инцидент Harbor export `operation_worker_failed` | **ОТКРЫТ**: [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422), причина не подтверждена traceback | Отдельная диагностика/исправление, не подменять зелёным Nexus E2E |
| Merge, release tag, offline-kit qualification и deploy | **НЕ ВЫПОЛНЕНЫ** | Code review, сквозной TARGET E2E, региональная приёмка и release approval |

**Релизный вердикт на дату документа: NO-GO** для Nexus production.
Причины: несмерженные PR, отсутствие полного TARGET E2E и региональной
приёмки. Текущий Harbor v1.0.0 и его прежняя квалификация **не переименовываются**
в «Nexus release». Решение о включении Nexus Docker-only и отдельном
инциденте Harbor #422 оформляется на release review явно.

## 3. Протокол локальной/региональной приёмки (заполняет владелец стенда)

**Дата/время:** _не заполнено_  
**Исполнитель и стенд:** _не заполнено_  
**SOURCE URL и build SHA:** _не заполнено_  
**TARGET URL и build SHA:** _не заполнено_  
**Nexus build/version:** _ожидается Nexus/3.69.0-02 (OSS), проверить_  
**Docker connector URL:** _указать без credentials_  
**Тип тестового репозитория и namespace:** _не заполнено_  
**TLS/CA/RBAC:** _не заполнено_  
**Используемые тестовые теги и digest:** _не заполнено_

Отмечайте каждый шаг как `PASS`, `FAIL`, `BLOCKED` или `NOT RUN` и
прикладывайте безопасную ссылку на evidence (без password/token/private key).

| Шаг | Ожидаемый результат | Факт / evidence / комментарий |
| --- | --- | --- |
| 1. Portal SOURCE, профиль Nexus | Выбран тип `nexus`, адрес **Docker v2 connector** (не Nexus UI), connectivity probe успешен без существующего busybox | NOT RUN |
| 2. Реальная корпоративная TLS/CA/auth | Корректное доверие CA, без `verify_tls=false` в production, профиль имеет только нужные права | NOT RUN |
| 3. Exact repository/tag → inspect | Существующий **тестовый** Docker image получает точный digest; нет Harbor API запросов для Nexus | NOT RUN |
| 4. Preview → start export | SOURCE operation идёт до `COMPLETED`; неверный digest/tag отвергается | NOT RUN |
| 5. Browser download | Доступны bundle `.htp.tar.gz`, `.sha256` и signed `.htp-handoff.json`; SHA-256 совпадает, нет неполной публикации | NOT RUN |
| 6. TARGET trust/verify/preview | Обновлённый TARGET доверяет утверждённому SOURCE public key, принимает 1.1 и не ослабляет проверки старых 1.0 bundle | NOT RUN |
| 7. TARGET import и контроль digest | Импорт в **выделенный тестовый namespace**; digest подтверждён, receipt сохранён, конфликты не перезаписываются неявно | NOT RUN |
| 8. Негативные сценарии и recovery | Неверные credentials/CA, invalid tag/digest, отказ/отмена, нехватка диска, истечение ticket дают безопасный результат | NOT RUN |
| 9. Регрессия Harbor | Доступность Harbor profile, image и Helm OCI workflow не нарушена | NOT RUN |

**Никаких destructive операций с production-образами, паролями и закрытыми
ключами.** Используйте тестовый namespace с согласованными правами.
Для интерфейса оцените фактический browser flow: тест через FastAPI
`TestClient` **не является** доказательством корректной работы браузера
или пользовательских подсказок.

### Результат региональной приёмки

- **Общий статус:** `NOT RUN`
- **Открытые дефекты с severity:** _не заполнено_
- **Отклонения/ограничения, принятые владельцем:** _не заполнено_
- **Ссылки на логи/скриншоты/архивы evidence:** _не заполнено_
- **Решение:** `NO-GO` (до выполнения обязательных условий)
- **Кто согласовал / дата:** _не заполнено_

## 3a. Региональная приёмка SOURCE (факт 03.11.2026)

**Основание:** [отчёт оператора в PR #442](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/442#issuecomment-6604837504).
Это отдельный проверенный оператором стенд, **не** disposable GitHub Actions.

- **Сборка исходного PR #442:** `478b64b`. Подключение Nexus
  `registry_connection_ok`, inspect/digest PASS. Экспорт **FAIL**
  (`nexus_not_found`): сборка полного Docker path продублировала часть
  `0702/centos7`, получив `0702/centos7/centos7`.
- **Исправление:** `d909275`, ветка
  `fix/442-nexus-multi-segment-repository`: сохранять полный Docker path в
  `ResolvedExportArtifact` при `project=None`. Исправление интегрировано
  в head PR #442, добавлен регрессионный тест для namespace и явная
  E2E-проверка пути в подписанном манифесте. **CI нового head проверяется
  отдельно; результат старого `478b64b` нельзя переносить на новый head.**
- **Повторная проверка оператором с исправлением:** экспорт
  `COMPLETED`, artifact `VERIFIED`; скачан архив размером
  **181 151 577 байт**; SHA-256 совпал (представлен оператором как
  `1a615d10…e26c`); sidecar и подписанный handoff присутствуют;
  Ed25519 signature **VALID**; manifest schema **1.1**,
  `registry_type: nexus`.
- **TARGET:** **NOT RUN**, целевой контур отсутствует на стенде.
- **Возврат к исходной конфигурации:** оператор восстановил локальный стек
  на `main` (`7600ad9`) и сообщил об успешном
  `registry_connection_ok`. Резервная копия сохранена локально оператором;
  её существование и целостность удалённо не проверялись.
- **Отдельные тестовые/окруженческие замечания:** изолированные тесты
  `test_unconfigured_harbor_has_stable_error` могут подхватывать
  `HARBOR_URL` из корневого `.env`; `test_export_cancellation` зависит
  от наличия локального `backend/data/keys/source-private.pem`.
  Эти находки **не исправлены** PR #442 и требуют изоляции тестового
  окружения, чтобы поведение на чистом CI не маскировало дефекты.

**Обновлённый вердикт:** региональный **SOURCE сценарий — PASS с фиксом**,
но Nexus SOURCE+TARGET release по-прежнему **NO-GO** до полного TARGET E2E,
проверки итогового commit SHA, review/merge и release qualification.

## 4. Перед выпуском и после выпуска

1. Завершить/согласовать зависимые PR #438–#442, затем обязательные #431,
   #434, #436; зафиксировать решение по #435 и статус #422.
2. Проверить итоговую **объединённую ветку**: один immutable commit SHA,
   green CI, Nexus SOURCE/TARGET E2E, backwards-compatible Harbor bundle
   1.0, отсутствие секретов и ошибок signing/provenance.
3. Провести review миграции и резервных копий. Если выпускаются SOURCE и TARGET,
   **обновить TARGET раньше SOURCE**, проверить архивный Harbor 1.0 и новый
   Nexus 1.1 bundle до разрешения импорта.
4. Назначить фактическую продуктовую версию/tag и собрать offline kit,
   проверить его подписи/хэши, installation и rollback. Обновить этот документ,
   [CHANGELOG](../CHANGELOG.md), текущий release notes и эксплуатационные
   инструкции **после реального утверждения релиза**.
5. После выпуска записать: tag, SHA, offline-kit digest, дату, кто
   утвердил, целевые версии SOURCE/TARGET, дату/результат smoke test,
   известные ограничения и ссылку на release evidence.

## 5. Ссылки и владельцы источников истины

- [PR #438](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/438),
  [#439](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/439),
  [#440](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/440),
  [#441](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/441),
  [#442](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/442)
- [Nexus deployment/diagnostics](nexus-setup.md),
  [protocol 1.0 normative contract](offline-bundle-v1.md),
  [протокол Nexus 1.1 в PR #441](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/feat/nexus-bundle-provenance-432/docs/nexus-bundle-provenance.md)
- [Общий CI #41633770476](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770476),
  [Nexus E2E #41633770502](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770502)
- [Региональная приёмка #436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436),
  [TARGET E2E #434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434),
  [Harbor export incident #422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422)

**Правило актуализации:** при каждом merge/change request обновлять статусы PR,
тесты и blockers; после локальной проверки заполнить раздел 3 и только на
основе evidence менять `NO-GO` на `GO`.
