# API просмотра локального Harbor

Этот документ фиксирует стабильный portal-owned контракт чтения локального Harbor для frontend и последующих SOURCE/TARGET workflow. Браузер никогда не обращается к Harbor напрямую и не получает `HARBOR_USER`, `HARBOR_PASSWORD`, токены или CA-файлы.

## Авторизация

Все endpoints ниже требуют действующий bearer token портала. Роли `viewer`, `operator` и `admin` могут читать каталог артефактов. Изменение Harbor-конфигурации в этот API не входит.

## Endpoints

### `GET /api/harbor/connection`

Возвращает безопасный статус подключения:

```json
{
  "connected": true,
  "version": "2.13.0",
  "auth_mode": "db_auth"
}
```

Пароль, имя сервисной учётной записи, URL с credentials и другие секреты не возвращаются.

### `GET /api/harbor/projects`

Query-параметры:

- `page` — номер страницы, начиная с 1;
- `page_size` — от 1 до 100, по умолчанию 50;
- `search` — регистронезависимый partial/token поиск по имени проекта.

Ответ содержит `pagination` и нормализованный список `items`.

### `GET /api/harbor/projects/{project}/repositories`

Поддерживает те же `page`, `page_size`, `search`.

Harbor может вернуть имя repository в виде `project/nested/repo`. Портал намеренно нормализует его до project-relative формы `nested/repo`. Именно это значение нужно передавать в запрос артефактов и позднее использовать как часть стабильного выбора для export/import workflow.

### `GET /api/harbor/projects/{project}/artifacts?repository=nested/repo`

`repository` передаётся query-параметром, поэтому вложенные имена с `/` не зависят от неоднозначного path routing. Дополнительно поддерживаются `page`, `page_size` и `search`; поиск сопоставляется с digest и tag/reference.

При обращении backend к Harbor API project-relative repository преобразуется в Harbor-совместимый path parameter с двойным percent-encoding. Например, `appt/appointment-api` передаётся upstream как `appt%252Fappointment-api`. Одинарное `appt%2Fappointment-api` декодируется routing-слоем Harbor слишком рано и для вложенного repository может дать `404`, который портал нормализует в `harbor_not_found`.

Нормализованный артефакт содержит:

- `kind`: `container-image`, `helm-chart` или `unknown-oci`;
- `project`;
- project-relative `repository`;
- `references` — все теги/версии, которые Harbor сообщил для artifact;
- `digest`;
- `size`, если известен;
- `pushed_at`, если известен;
- `media_type` и `artifact_type` для дальнейшей диагностики/группировки.

Поля, которые Harbor не сообщает надёжно, остаются `null`; портал не генерирует вымышленные значения.

## Поиск

Portal использует штатный Harbor query API, а не скачивает весь каталог для каждого запроса:

- projects/repositories передают fuzzy-фильтр `q=name=~...`;
- поиск tag/version передаёт `q=tags=~...`;
- digest prefix передаётся как `q=digest=~...`;
- параметры идут через HTTP query params, а не через ручную сборку URL.

Для запроса из одного фрагмента Harbor выполняет фильтрацию и пагинацию полностью на своей стороне. Для составного запроса портал рассматривает пробел и `/ - _ . :` как границы слов. Например, `report api` находит `softrust-report-api`. В этом режиме портал сначала сужает кандидатов upstream fuzzy-фильтром по наиболее информативному токену, затем выполняет bounded token-filter. Поисковый fallback ограничен числом Harbor pages и не превращается в неограниченный full-crawl.

Выбор для export после поиска остаётся строгим: frontend отправляет конкретный tag/version и digest, а backend повторно проверяет exact reference перед операцией.

## Классификация OCI

Классификация выполняется только по данным Harbor (`type`, `media_type`, `manifest_media_type`, `artifact_type`, annotations/extra attributes). Явный chart/Helm тип классифицируется как `helm-chart`, image/container — как `container-image`, всё остальное остаётся `unknown-oci`. Неизвестный OCI artifact не считается ошибкой и не должен ломать UI.

## Пагинация и производительность

Portal API возвращает единый объект:

```json
{
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 123
  },
  "items": []
}
```

Frontend не читает Harbor-specific `X-Total-Count`. Backend запрашивает у Harbor только нужную upstream page и преобразует `X-Total-Count` в portal-owned `pagination.total`. Поэтому открытие page 1 больше не требует чтения page 2..N даже при тысячах проектов, repositories или artifacts.

Для projects/repositories backend просит Harbor сортировать по имени; artifacts запрашиваются в стабильном upstream порядке по времени публикации. Portal продолжает нормализовывать DTO и project-relative repository names, но не материализует весь каталог перед выдачей обычной страницы.

## Ошибки

Harbor-ошибки не проксируются как raw upstream body. Portal error envelope содержит стабильный `error.code`:

- `harbor_not_configured` — локальный Harbor не настроен;
- `harbor_unavailable` — timeout, соединение или временная недоступность;
- `harbor_auth_failed` — учётные данные портала отвергнуты Harbor;
- `harbor_forbidden` — Harbor запретил доступ сервисной учётной записи;
- `harbor_not_found` — проект/repository/artifact отсутствует;
- `harbor_rate_limited` — Harbor временно ограничил запросы;
- `harbor_invalid_response` — upstream вернул некорректный ответ, включая отсутствие корректного `X-Total-Count` у paged browse;
- `harbor_error` — прочая нормализованная ошибка Harbor.

Raw Harbor response, пароль и другие секреты в сообщении ошибки не включаются.

## Граница ответственности

Этот слой только читает каталог и нормализует его для UI. Настройка credentials/CA относится к P3.3, а Skopeo/Helm export/import — к P4.1/P4.2. Frontend export wizard должен строить выбор только на этих portal-owned DTO, а не на произвольных Harbor JSON объектах.
