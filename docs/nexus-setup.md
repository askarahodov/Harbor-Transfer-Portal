# Настройка Sonatype Nexus для Harbor Transfer Portal

## Обзор

Harbor Transfer Portal поддерживает **Sonatype Nexus Repository** (версия 3.40+) как альтернативный реестр для Docker/OCI образов и Helm чартов. Nexus работает как полноценная альтернатива Harbor в контексте передачи артефактов между изолированными контурами.

## Требования к Nexus

- **Nexus Repository Manager** версии **3.40.0+** (для поддержки Helm OCI)
- **Docker Registry API v2** (встроен в Nexus 3.x)
- **Helm Registry API** (доступен с Nexus 3.40+)
- Настроенный **Docker-hosted** репозиторий
- Настроенный **Helm-hosted** репозиторий (OCI формат)

## Настройка Nexus

### 1. Создание Docker-hosted репозитория

1. В Nexus UI: **Administration → Repositories → Create repository**
2. Тип: **Docker (hosted)**
3. Настройки:
   - **Name**: `docker-hosted` (или любое имя)
   - **HTTP порт**: `8082` (или другой свободный)
   - **HTTPS порт**: `8443` (рекомендуется для production)
   - **Force basic auth**: включить
   - **Deploy policy**: `Allow redeploy` (для перезаписи тегов)

### 2. Создание Helm-hosted репозитория (Nexus 3.40+)

1. **Administration → Repositories → Create repository**
2. Тип: **Helm (hosted)**
3. Настройки:
   - **Name**: `helm-hosted`
   - **Online**: включить
   - **Force basic auth**: включить

### 3. Настройка пользователей и прав

#### Создание роли для Transfer Portal:
1. **Administration → Security → Roles → Create role**
2. **Role ID**: `transfer-portal-role`
3. **Name**: `Transfer Portal Role`
4. **Privileges**:
   - `nx-repository-view-docker-docker-hosted-*` (для Docker)
   - `nx-repository-view-helm-helm-hosted-*` (для Helm)
   - `nx-repository-admin-docker-docker-hosted` (для управления)
   - `nx-repository-admin-helm-helm-hosted` (для управления)

#### Создание пользователя:
1. **Administration → Security → Users → Create user**
2. **User ID**: `transfer-bot`
3. **Name**: `Transfer Portal Bot`
4. **Email**: `transfer@company.com`
5. **Password**: сгенерированный сложный пароль
6. **Roles**: назначить `transfer-portal-role`

### 4. Настройка TLS/SSL (для production)

#### Вариант А: Nexus за reverse proxy (nginx/Traefik)
```nginx
# nginx.conf
server {
    listen 443 ssl;
    server_name nexus.company.com;
    
    ssl_certificate /etc/ssl/certs/nexus.crt;
    ssl_certificate_key /etc/ssl/private/nexus.key;
    
    location / {
        proxy_pass http://nexus:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

#### Вариант Б: Nexus с встроенным HTTPS
1. Импортируйте сертификат в Java keystore:
```bash
keytool -importkeystore \
  -srckeystore nexus.p12 \
  -srcstoretype PKCS12 \
  -destkeystore /opt/sonatype-work/nexus3/etc/ssl/keystore.jks \
  -deststoretype JKS
```
2. Настройте `nexus.properties`:
```properties
application-port-ssl=8443
ssl.etc=etc/ssl
ssl.keystore=etc/ssl/keystore.jks
ssl.keystore.password=changeit
ssl.key.password=changeit
```

### 5. Настройка в Harbor Transfer Portal

#### В .env файле:
```bash
# Nexus настройки
NEXUS_URL=https://nexus.company.com:8443
NEXUS_USER=transfer-bot
NEXUS_PASSWORD=your-secure-password
NEXUS_VERIFY_TLS=true
NEXUS_CA_FILE=/app/data/secrets/nexus-ca.pem
```

#### Создание профиля Nexus в UI:
1. Откройте **Settings → Harbor Profiles**
2. Нажмите **Добавить профиль**
3. Заполните:
   - **Имя**: `Nexus DC-1`
   - **URL**: `https://nexus.company.com:8443`
   - **Тип реестра**: `Sonatype Nexus`
   - **Service account**: `transfer-bot`
   - **Проверять TLS**: ✓
   - **Enabled**: ✓
4. Нажмите **Добавить профиль**
5. Нажмите **Проверить** для теста подключения

## Особенности работы с Nexus

### Docker/OCI Images
- Nexus использует стандартный **Docker Registry API v2**
- Поддерживаются все стандартные операции: `pull`, `push`, `inspect`
- Путь к образу: `nexus.company.com:8443/project/image:tag`

### Helm Charts (Nexus 3.40+)
- Nexus 3.40+ поддерживает **Helm OCI Registry API**
- Формат пуша: `helm push chart.tgz oci://nexus.company.com:8443/helm-hosted`
- Формат пула: `helm pull oci://nexus.company.com:8443/helm-hosted/chart-name --version 1.0.0`
- Путь к чарту: `nexus.company.com:8443/helm-hosted/chart-name:version`

### Поддерживаемые операции

| Операция | Harbor | Nexus |
|----------|--------|-------|
| `skopeo copy` (export) | ✅ | ✅ |
| `skopeo copy` (import) | ✅ | ✅ |
| `helm pull` (export) | ✅ | ✅ (3.40+) |
| `helm push` (import) | ✅ | ✅ (3.40+) |
| `inspect` | ✅ | ✅ |

## Ограничения и нюансы

1. **Nexus версии**: Helm OCI требует **Nexus 3.40.0+**
2. **Репозитории**: В Nexus пути к репозиториям включают имя репозитория в пути (например, `docker-hosted/project/image`)
3. **Права доступа**: Nexus использует granular privileges, нужно правильно настроить роли
4. **TLS**: Для production всегда используйте HTTPS с валидным сертификатом
5. **Anonymous access**: По умолчанию отключен, требуется аутентификация

## Пример docker-compose для локального тестирования Nexus

```yaml
version: '3.8'
services:
  nexus:
    image: sonatype/nexus3:3.68
    container_name: nexus
    ports:
      - "8081:8081"   # UI
      - "8082:8082"   # Docker registry
      - "8443:8443"   # HTTPS
    volumes:
      - nexus-data:/nexus-data
    environment:
      - INSTALL4J_ADD_VM_PARAMS=-Xms2g -Xms2g -XX:MaxDirectMemorySize=3g
    ulimits:
      nofile:
        soft: 65536
        hard: 65536

volumes:
  nexus-data:
```

## Troubleshooting

### Ошибка "nexus_not_configured"
- Проверьте, что `NEXUS_URL` задан в `.env`
- Убедитесь, что URL доступен из контейнера Portal

### Ошибка "nexus_auth_failed"
- Проверьте логин/пароль в профиле
- Убедитесь, что пользователь имеет правильные права в Nexus

### Ошибка "nexus_tls_failed"
- Проверьте `NEXUS_VERIFY_TLS` и `NEXUS_CA_FILE`
- Убедитесь, что CA сертификат доступен в контейнере

### Ошибка "helm_pull_failed"
- Убедитесь, что Nexus версии 3.40+
- Проверьте, что Helm-hosted репозиторий создан и доступен
- Проверьте формат URL: `oci://nexus-host/helm-repo/chart-name`

## Миграция с Harbor на Nexus

Для миграции существующих профилей:
1. Создайте новый профиль с `registry_type: "nexus"`
2. Укажите URL Nexus, credentials
3. Проверьте подключение через UI
4. В Export/Import workflow выбирайте Nexus профиль

Существующие Harbor профили продолжают работать без изменений (`registry_type="harbor"` по умолчанию).