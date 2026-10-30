# Сравнение Harbor и Nexus в Harbor Transfer Portal

## Обзор

Harbor Transfer Portal поддерживает два типа реестров как SOURCE/TARGET:
- **Harbor** — основной, изначально поддерживаемый
- **Sonatype Nexus** — добавлен в v1.0.0 как альтернатива

Оба реестра работают через единый интерфейс `RegistryClient`, что обеспечивает одинаковый UX в UI и API.

## Сравнение возможностей

| Характеристика | Harbor | Nexus (3.40+) |
|---|---|---|
| **Docker/OCI Images** | ✅ Полная поддержка | ✅ Полная поддержка |
| **Helm Charts (OCI)** | ✅ Полная поддержка | ✅ С 3.40+ |
| **Maven/NPM/PyPI** | ❌ | ✅ (но не в Portal) |
| **Docker Registry API v2** | ✅ | ✅ |
| **Helm OCI Registry API** | ✅ | ✅ (3.40+) |
| **REST API** | `/api/v2.0` | `/service/rest/v1` |
| **RBAC** | Проекты/Роли | Realms/Privileges |
| **TLS/CA** | ✅ | ✅ |
| **Anonymous access** | Настраиваемо | Настраиваемо |

## Архитектурные различия

### Harbor
- **API**: REST `/api/v2.0`
- **Аутентификация**: Basic Auth / Bearer Token
- **Проекты**: Логическое разделение образов
- **Роботы**: Robot Accounts для CI/CD

### Nexus
- **API**: Docker Registry API v2 + REST `/service/rest/v1`
- **Аутентификация**: Basic Auth / Token / Anonymous
- **Репозитории**: Hosted/Proxy/Group
- **Права**: Realms + Privileges + Roles

## Настройка в Portal

### Harbor Profile (по умолчанию)
```json
{
  "registry_type": "harbor",
  "url": "https://harbor.company.com",
  "username": "transfer-bot",
  "verify_tls": true,
  "enabled": true
}
```

### Nexus Profile
```json
{
  "registry_type": "nexus",
  "url": "https://nexus.company.com:8443",
  "username": "transfer-bot",
  "verify_tls": true,
  "enabled": true
}
```

## Использование в Export/Import Workflow

### Export (SOURCE)
1. Пользователь выбирает профиль (Harbor или Nexus)
2. Portal подключается к выбранному реестру
3. Выбирает артефакты (образы/чарты)
4. Формирует bundle через `skopeo` (Docker) или `helm` (Helm)

### Import (TARGET)
1. Пользователь выбирает профиль TARGET (Harbor или Nexus)
2. Portal проверяет состояние TARGET через `inspect_target`
3. Выполняет `import` через `skopeo` / `helm push`

## Технические нюансы

### Docker/OCI Images
| Аспект | Harbor | Nexus |
|---|---|---|
| API для list images | `/api/v2.0/projects/{name}/repositories` | Docker Registry API `/v2/_catalog` |
| Pull | `skopeo copy docker://harbor/...` | `skopeo copy docker://nexus/...` |
| Push | `skopeo copy ... docker://harbor/...` | `skopeo copy ... docker://nexus/...` |
| Digest | sha256 | sha256 |

### Helm Charts
| Аспект | Harbor | Nexus (3.40+) |
|---|---|---|
| API | Helm Registry API | Helm Registry API (OCI) |
| Pull | `helm pull oci://harbor/...` | `helm pull oci://nexus/...` |
| Push | `helm push oci://harbor/...` | `helm push oci://nexus/...` |
| Репозиторий | `harbor-project/chart` | `helm-hosted/chart` |

## Миграция и совместимость

### Обратная совместимость
- ✅ Существующие Harbor профили работают без изменений
- ✅ `registry_type` по умолчанию = `"harbor"`
- ✅ API не изменен, добавлено только поле `registry_type`

### Миграция с Harbor на Nexus
1. Создать Nexus профиль в Portal
2. Проверить подключение
3. В Export workflow выбрать Nexus как SOURCE
4. В Import workflow выбрать Nexus как TARGET
5. Проверить end-to-end передачу

## Производительность и масштабируемость

| Метрика | Harbor | Nexus |
|---|---|---|
| Работа с большими образами | Хорошо | Хорошо |
| Конкурентные операции | Хорошо | Хорошо |
| Память (Java) | Go (мало) | Java (требует 4GB+) |
| Время запуска | Быстро | Медленнее |
| Ресурсы | Низкие | Высокие |

## Рекомендации по выбору

### Выбирайте Harbor, если:
- Уже используется в организации
- Нужен простой UI для управления
- Ограниченные ресурсы серверов
- Не нужны Maven/NPM репозитории

### Выбирайте Nexus, если:
- Уже используется в организации
- Нужен универсальный репозиторий (Maven + Docker + Helm + npm)
- Нужен Proxy к внешним репозиториям (Docker Hub, Maven Central)
- Есть опыт администрирования Java-приложений

## Troubleshooting

| Проблема | Harbor | Nexus |
|---|---|---|
| 401 Unauthorized | Проверить robot account | Проверить user + privileges |
| 404 Not Found | Проверить project/repo name | Проверить repository name |
| TLS Error | Проверить CA в Harbor | Проверить CA в Nexus + keystore |
| Helm 404 | Проверить OCI registry enabled | Проверить Nexus 3.40+ + helm-hosted repo |

## Ссылки

- [Harbor API Documentation](https://goharbor.io/docs/2.10/working-with-projects/working-with-images/)
- [Nexus Docker Registry](https://help.sonatype.com/repomanager3/docker-registry)
- [Nexus Helm Registry](https://help.sonatype.com/repomanager3/helm-repositories)
- [Harbor Transfer Portal Docs](/docs/)