# Региональная приёмка Nexus Docker + обычного Helm HTTP — SOURCE → TARGET

> **Состояние: NO-GO / региональная приёмка не выполнена.**
> Этот документ — исполняемый протокол и форма сбора доказательств, но **не**
> свидетельство доступа к корпоративным системам. Изменения находятся в
> [draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451),
> не в production или \`main\`. Без подписанного разрешения владельца контуров
> и фактических тестов в региональной сети статус не может стать GO.

Область: [#429](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/429),
[#435](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/435),
[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).
Инцидент Harbor [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422)
остаётся независимым.

## 1. Что действительно поддерживается и что требуется подтвердить

- **Nexus разной версии/редакции** на каждом SOURCE и TARGET. Версия
  \`3.69.0-02 OSS\` — один disposable CI baseline; **не** обязательная
  версия корпоративного Nexus. Зафиксировать версию, build и edition из
  фактического HTTP Server header / утверждённого паспорта сервера, а не
  из внешнего вида UI. Непрозрачный proxy header = версия не доказана.
- Только Nexus repositories с format **docker** и **helm**;
  \`hosted\`/\`proxy\`/\`group\` и ACL проверяются индивидуально.
  Список, доступный сервисной учётной записи, может не включать скрытые
  репозитории. \`403\` одного не должен останавливать другие.
- Для **Docker** подключение — корень HTTPS Docker v2 connector
  (\`https://docker-connector.example.internal/v2/\`).
  **Каждый Docker repository / connector имеет отдельный Portal-профиль**
  с назначенными service credentials/CA. Nexus UI API и Docker connector
  — не одно и то же; имена из inventory **не** создают URL автоматически.
  Просмотр Docker \`proxy/group\` не гарантирует право записи.
- **Helm — обычный HTTP Helm repository**, \`index.yaml\`, \`.tgz\`,
  hosted upload через Nexus Components API только при разрешённых
  правах. **Не используем OCI для Helm на Nexus.** Legacy Helm
  \`proxy/group\` не объявлять writable.
- CI на disposable Nexus уже подтверждал Docker/Helm signed Offline
  Bundle от SOURCE до TARGET receipt, включая replay/tamper; но
  **не** подтверждает корпоративные TLS/proxy/ACL и версионную матрицу
  региональных Nexus.

## 2. Безопасность, согласование и остановка

До любого обращения к региональным Nexus оператор документирует
change/approval ID, разрешённую сеть/хост, перечень SRC/TGT, **точные**
Docker/Helm endpoints, test namespace, допустимые read/pull и отдельно
допустимые write/cleanup, контакт владельца, дата/время и срок хранения
sanitized evidence. Не использовать production tags для проверки push.

**Обязательные запреты:** передавать пароль в CLI args, environment
dump, выводить Authorization / \`Set-Cookie\` / bearer token, включать
\`curl -k\`, \`--insecure\`, \`-L\` или редиректы с credentials; отключать
TLS, использовать административный список как подтверждение прав
сервисной учётной записи; запускать POST/PUT/DELETE без явного
разрешения test namespace; копировать приватный SOURCE signing key
на TARGET.

**Стоп:** неверный TLS trust/SAN/chain, неожиданный redirect,
несовпадающий \`source_digest\`/\`target_digest\`, непроверенный chart
checksum, неверная подпись, отсутствие разрешённого test namespace,
чужой репозиторий или неразрешённая операция записи. Отметить FAIL
и не обходить проверку безопасности.

## 3. Паспорт стендов (заполнить фактическими значениями)

| Поле | SOURCE | TARGET |
| --- | --- | --- |
| Регион, дата, оператор, change ID | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| Nexus version/build/edition, способ подтверждения | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Portal commit SHA и deployed build | НЕ УСТАНОВЛЕН | НЕ УСТАНОВЛЕН |
| URL Nexus UI/API (HTTPS, отдельно от connectors) | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| Docker v2 connector URLs по repository | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| Helm HTTP repository paths / index.yaml | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |
| DNS, reverse proxy, Host/X-Forwarded-Proto, CA, SAN/chain | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Docker Token Realm / Force Basic Auth / bearer challenge | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Уникальные service identity / отдельные права | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Разрешённый test namespace и writability | НЕ СОГЛАСОВАНО | НЕ СОГЛАСОВАНО |
| Bundle, sidecar, handoff, receipt paths и retention | НЕ ЗАПОЛНЕНО | НЕ ЗАПОЛНЕНО |

По каждому Nexus репозиторию заполнить **отдельную** строку
с \`source|target, name, format, hosted|proxy|group, connector/UI HTTPS origin,
service identity ID (без секрета), browse, read, pull, write, status 401/403,
image digest или Helm chart/version/SHA-256, test policy\`. Несколько
репозиториев и разные ACL обязательны в матрице; не считать пустой
список/отказ инвентаря отсутствием репозиториев.

## 4. Автоматизированный READ-ONLY preflight из разрешённой сети

В ветке PR есть:

- \`backend/integration/nexus_regional_preflight.py\` —
  **исключительно GET/HEAD**, HTTPS+trust CA, без redirect, без
  переменных HTTP_PROXY окружения, с ограничением ответа и
  изолированным результатом для каждого Docker/Helm repository.
- \`docs/nexus-regional-preflight.example.json\` — пример заполнения.
- \`backend/tests/test_nexus_regional_preflight.py\` — регрессии
  denied/allowed, malicious redirect, некорректных URL и запрета
  передачи credentials чужому host.

Оператор **из разрешённой сети**, после утверждения окна:

~~~bash
# В активированном Python 3.12 venv с зависимостями backend:
# pip install -r backend/requirements-dev.lock
# Не записывайте реальные пароли в JSON: password_file — отдельный файл mode 0600.
cp docs/nexus-regional-preflight.example.json /secure/region-preflight.json
# Отредактируйте внутри защищённого контура: approved URLs, CA file,
# identity, region, change ID, реальный 40-char Git commit SHA,
# разрешённые repository/image:tag и chart:version.
cd backend
python -m integration.nexus_regional_preflight \
  --config /secure/region-preflight.json \
  --output /secure/nexus-preflight-evidence.json
~~~

\`--output\` создаётся однократно с правами \`0600\` и **не**
перезаписывается. Конфигурация может содержать несколько независимых
проверок \`probes\` — SOURCE/TARGET и Docker/Helm; URL допускается
**только корневой HTTPS origin**. Для Docker выполняется \`GET /v2/\`,
проверяется \`200/401\` и схема Basic/Bearer, затем read-only
\`HEAD /v2/<image>/manifests/<tag>\` с \`Docker-Content-Digest\`.
Для Helm \`GET /repository/<repository>/index.yaml\`, чтение
конкретного chart/version и \`.tgz\` **только с настроенного Nexus
origin**, проверка SHA-256. Никакого Nexus Helm OCI и никакой записи.

**Важно:** статусы \`UNVERIFIED\`/ \`FAIL\` сохраняются раздельно.
Если Docker Token Realm требует Bearer exchange, прямой HTTP Basic
HEAD может дать \`401\` — это \`UNVERIFIED\`, **не** proof неверного
пароля и не PASS; выполните отдельный \`Проверить Docker Registry v2\`
и **inspect** с тем же Portal-профилем, запишите код и digest.
\`200\` для \`/v2/\` **не доказывает** права push.
\`available=false\` у глобального Nexus repositories API **не**
доказывает отсутствия доступных Docker/Helm repos.

В отчёте есть только утверждённые ID, репозиторий, тип,
контур, код исхода и HTTP status без токенов, паролей и сырых ошибок
транспорта. Хранить отчёт в защищённом месте. Даже если
\`preflight_status=PASS\`, \`release_verdict=NO-GO\` по определению:
инструмент **не выполняет** write, физический handoff или утверждение
релиза. Результаты самостоятельного запуска вне разрешённой сети
не являются evidence регионального контура.

## 5. Отдельные пер-репозиторные матрицы ACL

| Проверка для каждого repository | SOURCE | TARGET |
| --- | --- | --- |
| Docker v2 GET challenge и TLS/CA | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Docker read-only manifest HEAD/inspect digest | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Nexus REST Search/inventory с тем же service account | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Отказ 401/403 по неразрешённому repository (без утечки names) | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Helm index.yaml + конкретный chart/version/tgz sha256 | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |
| Docker hosted write test под отдельным разрешением | НЕ ПРИМЕНЯЕТСЯ | НЕ ПРОВЕРЕНО |
| Helm hosted Components upload test под отдельным разрешением | НЕ ПРИМЕНЯЕТСЯ | НЕ ПРОВЕРЕНО |
| proxy/group попытки записи не предлагаются | НЕ ПРОВЕРЕНО | НЕ ПРОВЕРЕНО |

Записывать точный HTTP status, profile ID, repository, approval,
hash/digest, время, URL origin по разрешённой политике redaction.
Для несовпадающих ACL: разрешённый repository продолжает работать
даже если другой возвращает 403. Не проверять denied запись POST —
достаточно зафиксировать отсутствие привилегии через согласованное
проверенное правило/RBAC или безопасную целевую валидацию.

## 6. Контролируемый полный SOURCE → физическая передача → TARGET

**Только после согласования выделенных тестовых namespaces и write-политик.**

1. SOURCE Portal: выбрать нужный Nexus профиль/connector по имени
   repository, проверить доступный Docker \`repository:tag\` и digest.
   Для обычного Helm выбрать \`helm repository\`, chart/version и SHA-256
   \`.tgz\`, без OCI. Зафиксировать действительный profile ID для каждой
   операции. **Не смешивать разные Docker connectors в одном профиле**.
2. Preview → подписанный Export \`COMPLETED\`; получить через browser
   download ticket \`*.htp.tar.gz\`, \`*.sha256\`,
   \`*.htp-handoff.json\`; записать operation ID, SHA-256, fingerprint,
   schema и \`source.registry_type=nexus\`.
3. Передать физически/утверждённым носителем без связи
   SOURCE↔TARGET. TARGET хранит только проверенный SOURCE **public**
   signing key, не private key. Проверить SHA-256/подпись/handoff и
   статус \`READY\`.
4. TARGET: destination plan только на заранее согласованный Docker
   hosted connector или Helm hosted repository. Проверить \`NEW\`,
   \`target_repository\` и source digest; выполнить
   \`overwrite_conflicts=false\`, сверить target digest и immutable
   receipt с исходным SHA-256. Не записывать в proxy/group.
5. Повторный handoff того же bundle: \`SAME\`/\`SKIPPED\`, без
   перепубликации. \`CONFLICT\` с другим digest не переписывать без
   явного дополнительного разрешения; default fail/skip.
6. Негативные сценарии: нарушенный bundle checksum, недоверенный
   signer, неверный/отсутствующий CA, запрещённая TARGET запись,
   смена source tag во время export, потеря соединения. Никаких
   side effects на чужих репозиториях; проверьте повторную удачную
   операцию в разрешённом тестовом пространстве.
7. Проверить журнал audit, срок хранения receipt, отмену и recovery,
   остановку/рестарт по утверждённой процедуре. Проверить Harbor
   регрессии отдельно и не выдавать тесты Nexus за решение #422.

### Все Helm download URL должны быть безопасными

Helm `index.yaml` может содержать несколько ссылок `urls` на один
chart/version. Безопасность нельзя определять только по первой ссылке:
SOURCE, TARGET и региональный preflight проверяют **каждую** запись
`urls` на допустимый путь ровно того же chart в том же Helm repository,
отсутствие query/fragment, обхода каталогов и credentials. Даже если
первая ссылка корректна, опасная последующая ссылка приводит к
`helm_index_invalid` в Portal или `index_invalid` в preflight
**до запроса .tgz** или Components upload. Сам Portal скачивает файл
только с доверенного HTTPS origin из профиля, игнорируя адрес из индекса.

### Атомарное создание evidence и ограничения входной конфигурации

Региональный CLI `backend/integration/nexus_regional_preflight.py`
принимает только **обычный JSON-файл не более 1 MiB**, открывая
его с запретом следования symlink (`O_NOFOLLOW`), проверяя тип
файла через `fstat` и ограничивая фактическое чтение. Слишком
большой, повреждённый, недоступный или не-UTF-8 файл отклоняется
**до сетевых запросов и до создания отчёта**; текст исключений,
содержимое конфига и секреты не выводятся в stderr.

Evidence записывается в приватный временный файл (`0600`)
**в той же директории**, синхронизируется и публикуется
атомарной операцией создания ссылки без перезаписи существующего
пути. Если назначение уже существует (в том числе symlink) или
публикация не удалась, исходный файл остаётся неизменным, а
временный файл очищается. Оператор использует защищённую
локальную директорию с достаточным свободным местом;
существующий evidence не перезаписывает и хранит отдельно.
Эта механика не означает, что корпоративная приёмка выполнена:
релизный вердикт остаётся `NO-GO`.

### Валидные поля заголовка evidence

Поля `environment`, `operator` и `change_id` входят в
долговременный JSON-отчёт без усечения, поэтому до любых probe
проверяются как непустые нейтральные идентификаторы длиной
не более 128 символов: латинские буквы, цифры, `.`, `_`, `-`;
первый символ — буква или цифра. URL, пути, пробелы,
переводы строк и произвольные секретные значения блокируются
с `evidence_identifier_invalid` до сетевых запросов и
без записи отчёта. Идентификаторы отражают утверждённые
оператором метки, а не полномочия или доказательство GO.

### Санитизация evidence при ошибочной конфигурации preflight

Региональный preflight не публикует в JSON-отчёте произвольные
значения из некорректных полей `id`, `repository`, `format`,
`contour` и `kind`. Неверные идентификаторы заменяются пустым
значением, недопустимые enum-поля — `null`; проверка помечается
`FAIL` с фиксированным кодом. Некорректные объекты probe
отклоняются с `probe_invalid` до любых сетевых запросов.
Оператор должен использовать нейтральные ID и не добавлять
пароли, токены или секретные URL в идентификаторы/метаданные
`environment`, `operator`, `change_id`.

### Региональный read-only preflight и полнота Helm index

`backend/integration/nexus_regional_preflight.py` проверяет целостность
всего доступного `index.yaml` до выбора и скачивания `.tgz`:
повторяющиеся YAML mapping keys на любой глубине, повторяющиеся
`chart/version`, некорректные digest/version/urls и превышение
лимитов приводят к `index_invalid` (`FAIL`).
Даже если повреждена запись другого chart, отчёт не объявляет
выбранный chart безопасным. При этом один недоступный repository
не блокирует проверку остальных явно разрешённых repositories.
Сканирование остаётся **только GET/HEAD**, не проверяет write ACL
и всегда выдаёт релизный `NO-GO` без ручной приёмки.

### Целостность TARGET upload: подписанный digest и snapshot файла

Перед любым Nexus Components POST TARGET читает `.tgz` из
локального обычного файла с запретом следования symlink
(`O_NOFOLLOW`), ограничивает чтение **100 MiB + 1 byte** и сверяет
SHA-256 **тех же байтов**, которые станут телом запроса, с
`source_digest` подписанного bundle. При несоответствии возвращается
`helm_payload_digest_mismatch`, при превышении размера —
`helm_chart_too_large`, при небезопасном файле —
`helm_package_invalid`; запись и обращения к Nexus не выполняются.
Отдельное предварительное чтение пути больше не считается
доказательством целостности последующей загрузки. Остаются
обязательными проверка пакета, hosted-only ACL, отсутствие
перезаписи и read-only postcheck после POST.

### Лимиты обработки обычного Helm `.tgz`

На SOURCE и перед TARGET Components upload каждую tar-запись Helm
package проверяют **последовательно**, не загружая весь список членов
архива через `getmembers()`. Допускается максимум **1000** записей
и суммарно не более **100 MiB** заявленного распакованного содержимого.
Небезопасные пути, символьные/жёсткие ссылки, повторяющиеся имена и
некорректный `Chart.yaml` отклоняются с `helm_package_invalid`;
метаданные chart/version могут находиться не первыми в архиве.
Это ограничивает обработку злонамеренных заголовков и не заменяет
проверку SHA-256 `.tgz`, подписанного bundle и региональный handoff.

### Однозначность YAML метаданных Helm

Нельзя считать `index.yaml` или `Chart.yaml` доверенными только потому,
что YAML успешно разобран. Если одноимённый ключ повторён в любой
вложенной YAML mapping (включая `entries`, `digest`, `name`, `version`),
операция отклоняется без выбора «последнего» значения.
Для index возвращается `helm_index_invalid` ещё на этапе просмотра,
SOURCE и TARGET; пакет с неоднозначным `Chart.yaml` отклоняется
с `helm_package_invalid` перед публикацией. Оператор исправляет
источник YAML и повторяет проверку, а не разрешает overwrite.

### Безопасность Helm SOURCE перед подписанием bundle

При загрузке Helm chart для SOURCE используется строгая проверка всего
`index.yaml`. Дублирующиеся `chart/version`, повреждённые digest,
неверные записи или опасные URL вызывают `helm_index_invalid` **до
запроса .tgz** и до создания подписанного bundle. Это исключает
произвольный выбор одной из конфликтующих версий и импорт
неоднозначного содержимого в изолированный TARGET. Каталог для
ознакомительного просмотра может фильтровать некорректные entries;
он не является разрешением на export. Оператор должен исправить
`index.yaml` в источнике, а не обходить проверку.

### Безопасность Helm index перед TARGET записью

Если `index.yaml` содержит некорректные chart keys, записи version,
checksum, ссылки на пакет **или повторяющиеся chart/version**, TARGET
планирование и upload возвращают
`helm_index_invalid` вместо вывода «версия отсутствует». Это
защита от ложного `NEW` и случайной перезаписи при неполной
видимости существующих версий. Чтение каталога может фильтровать
плохие записи, но **проверка перед записью должна быть строгой**.
При таком отказе оператор исправляет источник index/ACL и повторяет
read-only диагностику; не удаляет данные и не обходит проверку.
Обычный Helm `.tgz` также отклоняется как `helm_package_invalid`, если
архив содержит повторяющиеся пути (включая повторный `Chart.yaml`),
даже если первый файл metadata выглядит корректным.

## 7. Evidence и точные release gates

| Gate | CI / disposable Nexus | Региональный SOURCE/TARGET |
| --- | --- | --- |
| Docker multi-connector profile isolation, digest | PASS — [#42005739752](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42005739752) | **НЕ ПРОВЕРЕНО** |
| Helm index/tgz, signed bundle, TARGET receipt | PASS — [#42005739752](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42005739752) | **НЕ ПРОВЕРЕНО** |
| Backend/frontend/integration/security/protocol/docs | PASS — [CI #42005739662](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42005739662) | **НЕ УСТАНОВЛЕН В РЕГИОНЕ** |
| Per-repository ACL + TLS/CA + reverse proxy | Самоподписанный тестовый CA; **не** корпоративный | **НЕ ПРОВЕРЕНО** |
| Версии/редакции Nexus и HTTP Helm совместимость | CI baseline 3.69 OSS, не матрица регионов | **НЕ ПРОВЕРЕНО** |
| Физический handoff, approval, browser и operator receipt | Изолированные Portal API в тестах | **НЕ ПРОВЕРЕНО** |
| TARGET write policy, conflict/replay/recovery | Disposable E2E | **НЕ ПРОВЕРЕНО** |

Ссылки CI относятся к предыдущему уже зелёному SHA
\`c55fc355823306ada9b5f9536fca4508ff675f2d\` и **не**
подменяют повторную проверку HEAD PR после изменения этого runbook.

## 8. Финальное заключение (оператор/approver заполняют)

| Поле | Факт |
| --- | --- |
| Регион, дата, change approval | **НЕ ПРЕДОСТАВЛЕНО** |
| Оператор, reviewer, security/owner | **НЕ ПРЕДОСТАВЛЕНО** |
| SHA развернутой ревизии Portal + версии Nexus | **НЕ УСТАНОВЛЕНО** |
| Sanitized preflight report / подпись оператора | **НЕ ПРЕДОСТАВЛЕНО** |
| Пер-репозиторные ACL/CA/proxy evidence | **НЕ ПРЕДОСТАВЛЕНО** |
| Реальные signed archive / sidecar / handoff / receipt / SHA | **НЕ ПРЕДОСТАВЛЕНО** |
| Browser download + physical handoff + replay/conflict | **НЕ ПРОВЕРЕНО** |
| Открытый независимый Harbor incident #422 | **Не входит в решение по Nexus** |
| Релизный вердикт | **NO-GO** |
| Подпись ответственного approver | **НЕ ПРЕДОСТАВЛЕНА** |

**GO** возможен только после успешных текущих CI/E2E на **одном SHA**,
закрытия критических дефектов, проверки разных реально используемых
Nexus formats/types/versions и полномочий сервисного пользователя,
завершённой приёмки физического SOURCE→TARGET на разрешённом стенде
и **явного подписанного разрешения владельца**. Во всех остальных
случаях **NO-GO**.


## 9. Harbor ↔ Nexus — real Core/robot test and external limits

В draft PR #451 обязательный E2E включает два разных тестовых режима:

- **Независимый Docker Distribution 2.8.3** с Harbor-профилем,
  стабилизированными REST metadata и project/RBAC — проверка транспорта,
  подписанных бандлов, `NEW/SAME/CONFLICT/SKIPPED`, отказа overwrite.
  Это не фактический Harbor Core.
- **Настоящий одноразовый Harbor Core 2.14.6** со своими registry,
  token service и двумя приватными проектами, HTTPS с тестовым CA,
  отдельно настроенными project-scoped robots для SOURCE-read и
  TARGET-write. Используются **настоящие** Harbor REST metadata, проверка
  отрицательных ACL (каждый robot не видит чужой private project),
  Docker v2 через Skopeo и подписанный SOURCE→TARGET handoff в обоих
  направлениях, вместе с Nexus 3.69.0 OSS. В test fixture нет
  административного обхода scoped credentials; `GET /users/current`
  подтверждает admin только при первоначальном создании тестовых
  проектов и роботов. Для API-bootstrap Basic Auth запрещено
  смешивать с cookie-based session/CSRF без специального токена.
  Выполнены **2 пройденных E2E** на реальном Harbor в
  [Actions #42022465235](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/42022465235)
  при коммите `a3dcb80b2e3966c0afb4cd7f71a7d8933885ca05`.

**Это всё ещё disposable GitHub Actions, а не региональный Harbor.**
Операционная приёмка #434/#436 требует реальную корпоративную версию
Harbor, настройку project/robot ACL сервисной учётной записи, DNS,
TLS/certificate SAN, reverse proxy и firewall; утверждённые namespaces
и физический air-gap handoff с операторским receipt, digest и подписями.
Подтверждение CI нельзя использовать как разрешение production push,
как доказательство ролей реальной сервисной учётной записи или как
полномочие merge. При отсутствии таких доказательств релиз остаётся
**NO-GO**, а PR — Draft.

