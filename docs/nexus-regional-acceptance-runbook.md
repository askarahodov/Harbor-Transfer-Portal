# Региональная квалификация Nexus 3.69 OSS — единый протокол SOURCE → TARGET

> **Статус: НЕ ВЫПОЛНЕНО / NO-GO.** Это инструкция и форма фиксации фактов,
> а не отчёт об успешном тестировании регионального TARGET. Технический
> кандидат находится в draft PR #451; **не** в основной ветке.

Связанные задачи: [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434),
[#435](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/435),
[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).
История CI и известные ограничения: [журнал Nexus RC](nexus-release-readiness-2026-11-03.md).

## 1. Границы и разрешения

- Точно согласовать с владельцем контуров **временное окно, тестовые registry
  endpoints и namespaced test-репозитории**. Никаких запросов на изменение
  существующих production tags, прав, realm или сертификатов.
- Отдельные SOURCE и TARGET Portal, БД, файловое хранилище, private key и
  сервисные аккаунты. SOURCE private key **не переносить на TARGET**:
  переносится только **проверенный публичный ключ** доверенного подписанта.
- Использовать только подписанный физический bundle/sidecar/handoff. Не
  подключать SOURCE и TARGET через прямые сетевые копирования, общую БД или
  общий registry credentials.
- Ниже команды диагностики **только на чтение**, кроме отдельного
  согласованного тестового push в выделенный namespace. Не отключать TLS,
  не передавать пароль аргументом командной строки, не включать trace с
  Authorization header, не прикладывать токены или .env в отчёт.
- Условия остановки: неожиданный HTTP redirect, неверная hostname/SAN,
  недоверенный CA, запрос чужой учётной записи, неподписанный архив,
  отсутствие test namespace либо mismatch digest. Отметить FAIL, не
  обходить проверку безопасности.

## 2. Паспорт двух реестров (оператор заполняет фактическими данными)

| Параметр | SOURCE | TARGET |
| --- | --- | --- |
| Дата, оператор и change/approval ID | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| Nexus version, edition | ожидается 3.69.0-02 OSS; проверить | ожидается 3.69.0-02 OSS; проверить |
| Portal commit SHA + версия | `4881993` — только CI proof; регионально не установлен | `4881993` — только CI proof; регионально не установлен |
| Docker v2 endpoint root (без `/repository`) | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| UI/API endpoint Nexus (отдельно от Docker) | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| Схема HTTPS, сертификат, chain/SAN | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Docker realm, Basic/Bearer challenge, proxy redirect | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Выделенный test repository/namespace, очищаемый после теста | НЕ СОГЛАСОВАНО | НЕ СОГЛАСОВАНО |
| Учётная запись и минимальный scope (без самих секретов) | read + browse/inspect | read + add/edit тестового namespace |
| Путь для evidence/receipt и владелец хранения | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |

Для Docker Registry v2 требуется **корень Docker connector**, например
`https://registry.example.internal`. Nexus UI `https://nexus:8081` и
`/repository/helm-hosted` не являются взаимозаменяемыми адресами.

## 3. Безопасная, невносящая изменений проверка каждого endpoint

На хосте соответствующего контура из **разрешённой сети**, с доверенным
корневым CA и БЕЗ credentials, проверить:

~~~bash
# Переменные задаются локально администратором; URL должен быть HTTPS.
REGISTRY_URL="https://your-approved-docker-v2-endpoint"
CA_FILE="/path/to/approved-ca.pem"

curl --silent --show-error --max-time 15 --max-redirs 0 \
  --cacert "$CA_FILE" \
  --output /dev/null --dump-header /dev/stderr \
  "$REGISTRY_URL/v2/"
~~~

Ожидается `200 OK` для публичного connector или `401 Unauthorized` с
`WWW-Authenticate: Basic/Bearer` для закрытого. **Не считать 401
успешной проверкой credentials.** Запретить `-k`, `--insecure`,
`-L` и вывод Authorization. Не сохранять заголовки `Set-Cookie` или
иные чувствительные данные в общий лог; записать только status,
схему challenge, наличие `Docker-Distribution-API-Version: registry/2.0`,
отсутствие редиректа, доверие CA и проверенную SAN.

Затем через Portal выбрать **Nexus · Docker v2** и запустить
`Проверить Docker Registry v2` для фактического профиля. Записать
санитизированный `code` ответа. Для SOURCE account дополнительно
сделать inspect **одного разрешённого тестового** `repository:tag` и
сверить `sha256`. Не использовать произвольный `library/busybox:latest`
как обязательный probe. Для TARGET account **не утверждать write scope**
по одному успешному GET `/v2/`: право push доказывает только
согласованный тестовый импорт и post-inspection.

## 4. Единый сквозной сценарий — только на тестовом образе

1. Подтвердить физическую изоляцию сетей и невозможность прямого
   SOURCE→TARGET API, отдельные credentials, независимые paths и
   доверенный SOURCE **public** key на TARGET. Записать, кто и как
   транспортирует физический носитель.
2. SOURCE: exact `repository:tag`, preview digest, operation ID,
   export `COMPLETED`. Скачать через browser download-ticket ровно три
   файла: `*.htp.tar.gz`, `*.sha256`, `*.htp-handoff.json`.
   Зафиксировать размер/sha256, signer fingerprint, schema 1.1,
   `source.registry_type=nexus`, **не публикуя приватных ключей**.
3. Передать файлы согласно утверждённой процедуре, без сетевого канала
   Portal↔Portal. TARGET: browser upload/discovery, проверка доверенного
   подписанта, handoff, checksum и preview `READY`.
4. TARGET: составить destination plan в выделенный тестовый namespace.
   До `execute` убедиться, что `NEW` и `target_repository` точен;
   импортировать с `overwrite_conflicts=false`. Записать operation ID,
   receipt и фактический **target digest**; он обязан совпадать с
   SOURCE digest. Сравнить физический receipt и API.
5. Повторить тот же bundle: `SAME`, `SKIPPED`, digest не меняется.
   Проверить `CONFLICT` на тестовом теге с другим digest: без approval
   mutation блокируется, явный `skip_conflicts` не меняет target digest.
   Явный overwrite проверять **только** по дополнительному разрешению
   владельца тестового namespace; в production не запускать.
6. На **отдельных тестовых** bundle проверить отказ на повреждённом
   checksum, handoff и архиве; не допускаются импортные операции/
   частичная публикация. Проверить отказ при недоступном TARGET,
   запрещённом write scope и неверном CA без вывода пароля. После этого
   повторить удачный импорт и проверить отсутствие побочных изменений.
7. Воспроизвести отмену/сбой на выделенной тестовой операции, повторный
   запуск backend по утверждённой процедуре и проверку очистки
   незавершённых публикаций без удаления завершённых bundles.
8. В UI проверить, что профиль Nexus **не** вызывает Harbor browse API,
   отображается как `Nexus · Docker v2`, Helm OCI не предлагается.
   Отдельно выполнить Harbor регрессию, включая Helm OCI на Harbor, если
   таковой используется.

## 5. Матрица подтверждения (не считать CI региональным evidence)

| Gate | CI на одном SHA | Региональная среда |
| --- | --- | --- |
| SOURCE exact tag / signed bundle / download и контроль целостности | [PASS #41978495693](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41978495693) | SOURCE ранее проходил на другой ревизии; текущий RC **НЕ ПРОВЕРЕН** |
| Nexus → Nexus TARGET NEW/SAME/CONFLICT, digest/receipt | [PASS #41978495693](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41978495693) | **НЕ ПРОВЕРЕН** |
| Отказные SOURCE/ TARGET paths, signed trust | CI на disposable Docker connectors; обновление #451 требует отдельного зелёного SHA | **НЕ ПРОВЕРЕНО** |
| TLS/CA, proxy, DNS, реальные read/write RBAC | CI использует test CA и временные аккаунты | **НЕ ПРОВЕРЕНО** |
| Harbor↔Nexus смешанные направления | **НЕ ДОКАЗАНО** отдельным реальным E2E | **НЕ ПРОВЕРЕНО** |
| Отсутствие несанкционированной сетевой связи и secret leakage | Сценарий имитирует offline handoff; не доказывает corporate isolation | **НЕ ПРОВЕРЕНО** |
| Браузерная приёмка, operators, audit/receipt retention | Vue/API тесты, не физический браузер | **НЕ ПРОВЕРЕНО** |

## 6. Решение по legacy Helm для Nexus 3.69 OSS (#435)

**Текущая техническая граница — строго Docker-only, без Helm OCI и без
legacy Helm hosted адаптера. Это НЕ является подтверждением владельца
контура, что Helm не требуется.** Пока решение `needed/not needed` не
получено, status = **UNKNOWN**.

| Репозиторий/формат | SOURCE экспорт | TARGET импорт | Статус |
| --- | --- | --- | --- |
| Nexus Docker hosted / Docker v2 | Docker image / exact tag | Docker push + digest | поддерживается в CI |
| Nexus Helm hosted / `index.yaml` + `.tgz` | нет | нет | unsupported |
| Nexus raw с `.tgz` | нет | нет | unsupported |
| Nexus OCI repository на 3.69 OSS | не предполагать | не предполагать | unsupported |
| Harbor OCI Helm chart | через Harbor | через Harbor | отдельно поддерживается; regression CI |

Требуется решение владельца: `needed` / `not needed`, реальный тип
хранилища (Helm hosted/proxy/group, raw, внешний chartmuseum, отсутствует),
SOURCE и TARGET потребности отдельно, approver + дата. При `needed`
следует **сначала** согласовать HTTP каталог/download/права на
`index.yaml` и `.tgz`, подписи/checksum, overwrite policy и модель
публикации, затем создавать задачи реализации. Нельзя подменять
legacy Helm через `helm push oci://`.

## 7. Финальный протокол и критерий GO/NO-GO

| Поле | Значение |
| --- | --- |
| Время и дата испытания | НЕ ЗАПОЛНЕНО |
| Оператор / reviewer / владелец контуров | НЕ ЗАПОЛНЕНО |
| Точное имя и SHA релизного кандидата | `integration/nexus-oss-release-candidate-451`; CI SHA `4881993`, регионально не установлен |
| SOURCE и TARGET endpoints (без секретов) | НЕ ЗАПОЛНЕНО |
| Внешние ссылки на change approval и evidence | НЕ ПРЕДОСТАВЛЕНО |
| SHA-256 bundle, signer fingerprint, operation IDs, receipt | НЕ ПРЕДОСТАВЛЕНО |
| Итоги каждого gate и список известных ограничений | НЕ ПРОВЕРЕНО |
| Отдельное решение владельца по Helm | UNKNOWN |
| Открытый Harbor incident #422 | Независимое расследование; не выдавать за решённый |
| Вердикт | **NO-GO** |
| Подпись уполномоченного approver | НЕ ПРЕДОСТАВЛЕНА |

**GO** допустим только при успешном текущем CI на одном
релизном SHA, зелёной региональной сквозной приёмке SOURCE→TARGET,
подтверждённых TLS/proxy/RBAC, отсутствии открытых P0 и формальной
авторизации релиза. Ошибка, отсутствие доказательств или неутверждённый
scope означает **NO-GO**; устные заверения не заменяют evidence.
