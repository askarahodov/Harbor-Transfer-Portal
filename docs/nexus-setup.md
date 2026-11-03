# Настройка Sonatype Nexus для Harbor Transfer Portal

## Важно: Docker v2 не означает Helm OCI

В инфраструктуре с **Nexus 3 до 3.94** необходимо различать:

- **Docker hosted**: обычный Docker Registry HTTP API v2, доступный через отдельный connector (порт или отдельный виртуальный хост). Portal использует Skopeo для экспорта/импорта контейнерных образов. Этот сценарий проверен на настоящем **Nexus 3.70.1**, Docker schema2, в GitHub Actions.
- **Helm hosted**: классический Helm chart repository с HTTP-путём /repository/helm-hosted/index.yaml и пакетами .tgz. **Текущий Helm OCI transport Portal не реализует протокол legacy Helm hosted**. Не включайте передачу Helm через Nexus до разработки и проверки отдельного адаптера, если используется именно этот тип репозитория.
- **OCI repository**: отдельный формат Nexus, появившийся в **3.94.0**. Не предполагается и не требуется для существующих старых Nexus. Применять helm push oci://... к legacy Helm hosted нельзя.

Подтверждённая совместимость с Nexus **3.70.1** не доказывает поведение каждой версии Nexus, конкретных корпоративных сертификатов или reverse proxy.

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

Пример настроек Portal:

~~~dotenv
NEXUS_URL=https://docker-nexus.example.internal
NEXUS_USER=transfer-bot
NEXUS_PASSWORD_FILE=/app/data/secrets/nexus-password
NEXUS_VERIFY_TLS=true
NEXUS_CA_FILE=/app/data/secrets/nexus-ca.pem
~~~

Можно создать отдельный профиль с типом **nexus** через **Settings → Harbor Profiles**. У каждого профиля должны быть собственные URL, credentials и CA; глобальный Nexus-пароль не должен подменять пароль выбранного профиля.

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

Автоматический PR-тест запускает Nexus 3.70.1 с Docker hosted v2 и проверяет:

- реальный Skopeo export/import, digest и Docker schema2;
- неправильные credentials, недоступный connector, несовпадение digest, path traversal;
- отдельный именованный Nexus profile;
- HTTPS через тестовый nginx reverse proxy: отказ с недоверенным CA и успешное соединение с CA выбранного профиля.

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
