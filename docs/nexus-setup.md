# Настройка Sonatype Nexus для Harbor Transfer Portal

## Важно: Docker v2 не означает Helm OCI

В инфраструктуре с **Nexus 3 до 3.94** необходимо различать:

- **Docker hosted**: обычный Docker Registry HTTP API v2, доступный через отдельный connector (порт или отдельный виртуальный хост). Portal использует Skopeo для экспорта/импорта контейнерных образов. Этот сценарий проверен на **Nexus 3.69.0-02 OSS** и **Nexus 3.70.1**, Docker schema2, в изолированном GitHub Actions.
- **Helm hosted**: классический Helm chart repository с HTTP-путём /repository/helm-hosted/index.yaml и пакетами .tgz. **Текущий Helm OCI transport Portal не реализует протокол legacy Helm hosted**. Не включайте передачу Helm через Nexus до разработки и проверки отдельного адаптера, если используется именно этот тип репозитория.
- **OCI repository**: отдельный формат Nexus, появившийся в **3.94.0**. Не предполагается и не требуется для существующих старых Nexus. Применять helm push oci://... к legacy Helm hosted нельзя.

Подтверждённая совместимость с **Nexus 3.69.0-02 OSS** (точная версия регионального сервера) и тестовой 3.70.1 не доказывает соответствие корпоративным настройкам, сетевым правилам, сертификатам или reverse proxy.

Официальные источники: [Sonatype — OCI Repositories](https://help.sonatype.com/en/oci-repositories.html), [Sonatype — Helm Repositories](https://help.sonatype.com/en/helm-repositories.html).

## Docker hosted: подготовка Nexus

1. В Nexus UI откройте **Administration → Repositories → Create repository → docker (hosted)**.
2. Создайте отдельный репозиторий и назначьте **HTTP/HTTPS connector** (например, внутренний HTTP 8082). Для внешних клиентов используйте HTTPS на самом Nexus либо через reverse proxy.
3. Включите Docker Bearer Token Realm (**Administration → Security → Realms**) и настройте авторизацию. При необходимости включите Force Basic Authentication в параметрах Docker hosted.
4. Создайте минимально привилегированного сервисного пользователя. Для выбранного Docker repository нужны права просмотра и чтения на SOURCE, а также добавления/редактирования на TARGET. Административные права repository-wide не требуются для обычной передачи.
5. Проверьте путь **https://registry-host/v2/**: ответ **401** с challenge без credentials и успешная авторизация с credentials допустимы. URL профиля Portal должен быть **корнем Docker Registry v2**, а не URL Nexus UI и не /repository/docker-hosted.

### TLS через reverse proxy

Внешний virtual host реестра должен передавать Docker Registry v2 запросы **в Docker connector**, например порт 8082, а не в Nexus UI на 8081:

~~~nginx
server {
    listen 443 ssl;
    server_name docker-nexus.example.internal;

    ssl_certificate /etc/nginx/tls/server.crt;
    ssl_certificate_key /etc/nginx/tls/server.key;

    location / {
        proxy_pass http://nexus:8082;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $http_host;
    }
}
~~~

Проверьте, что Docker token/auth challenge совместим с внешним HTTPS-адресом и сертификат доверен стороне Portal. Для внутреннего CA используйте NEXUS_CA_FILE либо установите CA в профиль Nexus. **Не отключайте** TLS verification в production.

**Безопасность credentials:** проверки подключения, SOURCE Docker v2 browse и TARGET preflight
не отправляют Basic Auth по HTTP на удалённые адреса. Для изолированного
тестового TARGET connector допускается буквальный loopback IP `127.0.0.1`/`::1`;
это **не разрешение** на HTTP между серверами. Для сервисных учётных записей используйте HTTPS
на внешнем адресе Docker connector (при необходимости с TLS termination на reverse proxy),
а Nexus REST Search API также настройте через HTTPS. Внутренний HTTP connector,
закрытый reverse proxy, не означает, что Portal должен подключаться к нему по HTTP.

Пример настроек Portal:

~~~dotenv
NEXUS_URL=https://docker-nexus.example.internal
NEXUS_USER=transfer-bot
NEXUS_PASSWORD_FILE=/app/data/secrets/nexus-password
NEXUS_VERIFY_TLS=true
NEXUS_CA_FILE=/app/data/secrets/nexus-ca.pem
~~~

Можно создать отдельный профиль с типом **nexus** через **Settings → Harbor Profiles**. У каждого профиля должны быть собственные URL, credentials и CA; глобальный Nexus-пароль не должен подменять пароль выбранного профиля.

## Интерактивный поиск Docker образов и тегов

Для **Nexus OSS 3.69** Harbor Browse API не используется. В экспортном
мастере SOURCE доступны два механизма:

1. **Опциональные Docker v2-подсказки** через только-чтение
   `GET /v2/_catalog?n=100` и
   `GET /v2/<repository>/tags/list?n=100`. Nexus может запрещать
   `_catalog` или возвращать 401/404; отсутствие каталога **не** означает,
   что образ отсутствует. Список может быть неполным при пагинации.
2. **Рекомендуемый поиск на Nexus 3.69:** в настройках профиля с типом
   Nexus укажите **Nexus UI HTTPS URL** — корень UI/API
   (`https://nexus-ui.example.internal`, не Docker connector) и **имя
   Docker repository в Nexus UI**, например `docker-hosted`.
   Portal использует read-only
   `GET /service/rest/v1/search?repository=docker-hosted&format=docker`,
   читает ограниченное число страниц и выводит имена образов и теги.
   Учётная запись должна иметь разрешённое право search/browse на
   соответствующий Nexus repository. Никаких URL от пользователя в этом
   запросе, редиректов или переменных прокси из окружения; данные
   авторизации остаются в серверном профиле, HTTPS обязателен.

В обоих случаях можно отфильтровать подсказки вводом названия, выбрать
репозиторий и тег и нажать **«Проверить Docker image»**. Digest всегда
перепроверяется через Docker v2 перед экспортом. Если browse недоступен,
мастер явно показывает предупреждение и позволяет ввести точный
`repository:tag` вручную. Поиск не предоставляет гарантии полного
инвентаря и не заменяет проверку разрешений.

### Зависший или длительный экспорт

Для больших/multi-arch образов `skopeo copy --all` может долго скачивать
слои. Счётчик `0/1` — **число завершённых артефактов**, не объём
переданных байтов. Если операция зависла, сохраните operation ID, время,
фазу и санитизированные логи backend/Skopeo, проверьте диск, DNS,
соединение с Nexus Docker connector и доступность blob endpoint.

В интеграционном RC исправлено завершение **всей process group**
Skopeo/Nexus при тайм-ауте и отмене, включая потомков, удерживающих
stdout/stderr. `SKOPEO_TIMEOUT_SECONDS` по умолчанию **300 секунд**
(допускается до 3600); изменение лимита не устраняет сетевые и
реестровые ошибки. Не запускать повторные export той же версии
параллельно, не считайте `RUNNING` завершённым transfer и не передавайте
частичный архив. После обновления ветки повторите проверку **в
тестовом контуре**. При повторном зависании приложите к issue
operation ID, таймштампы и редактированные логи без секретов.

## Форматы артефактов

| Операция | Nexus Docker hosted v2 | Nexus Helm hosted (legacy) |
| --- | --- | --- |
| Container image inspect | Поддерживается и тестируется | Не применяется |
| Container image export/import через Skopeo | Поддерживается и тестируется | Не применяется |
| Helm pull через index.yaml | Не применяется | **Пока не реализован в Portal** |
| Helm upload .tgz через Components API | Не применяется | **Пока не реализован в Portal** |
| Helm pull/push через OCI | Не считать поддерживаемым на старом Nexus | **Неприменимо** |

Классический Helm hosted обычно предоставляет index.yaml и tarball через HTTP. Даже если Helm-чарты физически хранятся в Nexus, это не делает его OCI registry. Если Helm в ваших контурах не используется, подключать его к Nexus в Portal пока не нужно.

## Проверка совместимости в GitHub Actions

Workflow: **Actions → Nexus legacy Docker v2 E2E**, файл [.github/workflows/nexus-legacy-e2e.yml](../.github/workflows/nexus-legacy-e2e.yml).

Автоматический PR-тест запускает официальный образ **sonatype/nexus3:3.69.0** и дополнительно сверяет HTTP Server header с точной сборкой **Nexus/3.69.0-02 (OSS)**. Затем выполняет тесты Docker hosted v2:

- реальный Skopeo export/import, digest и Docker schema2;
- неправильные credentials, недоступный connector, несовпадение digest, path traversal;
- отдельный именованный Nexus profile;
- HTTPS через тестовый nginx reverse proxy: отказ с недоверенным CA и успешное соединение с CA выбранного профиля.

Результат проверки Nexus **3.69.0-02 OSS**: [GitHub Actions #41617019630](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41617019630) — **10/10 passed** (Docker Registry v2, профиль Portal, TLS/CA, ошибки). Ранее Nexus 3.70.1 также прошёл этот набор проверок.

Для проверки **конкретной версии** используйте **Run workflow** и параметр **nexus_version** — опубликованный тег Nexus **3.x до 3.94**. Если тег образа недоступен или эта версия использует другой REST API/настройки, workflow должен завершиться с ошибкой, а не пропускать тесты. Запуск не подключается к корпоративному Nexus: используется одноразовый контейнер на GitHub-hosted runner.

### Что НЕ доказывает CI

- Версию, конфигурацию и права доступа реальных корпоративных Nexus, которые не предоставлены.
- Доверие к корпоративному CA, особенности внутреннего reverse proxy, DNS/Firewall.
- Совместимость с **legacy Helm hosted**, пока не разработан отдельный сервис и сквозные тесты.
- Готовность к production release без проверки на выделенном стенде соответствующего контура.

## Диагностика

- **nexus_not_configured / nexus_url_invalid**: проверьте URL корня Docker Registry v2 без пути /repository/....
- **nexus_auth_failed**: проверьте сервисную учётную запись, Docker Token Realm и repository privileges.
- **nexus_tls_failed**: проверьте сертификат virtual host, hostname/SAN и цепочку CA, доступную Portal.
- **nexus_registry_connection_failed**: проверьте порт Docker connector, DNS, маршрутизацию и Firewall.
- **skopeo_digest_mismatch**: проверьте source и target registry, сохранность схемы Docker manifest и ограничения на перепаковку.
- Ошибки Helm OCI при работе с legacy Helm hosted — **несовместимость протоколов**, а не повод отключать проверку TLS.
