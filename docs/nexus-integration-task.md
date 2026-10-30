# Задача: Интеграция Nexus Repository в Harbor Transfer Portal

## Контекст
Текущий проект поддерживает только Harbor как источник/приёмник артефактов (container images + Helm OCI charts). Требуется добавить поддержку **Sonatype Nexus Repository** как альтернативного источника/приёмника.

## Требования

### 1. Поддерживаемые типы артефактов в Nexus
- **Docker/OCI images** (Docker registry API v2)
- **Helm charts** (Helm registry API / OCI registry)
- **Maven artifacts** (опционально, для полноты)
- **npm, PyPI, NuGet, и др.** — только если требуется по ТЗ

### 2. Режимы работы
- **SOURCE** — экспорт из Nexus (pull образов/чартов, формирование bundle)
- **TARGET** — импорт в Nexus (push образов/чартов из bundle)

### 3. Аутентификация и авторизация
- Basic Auth (username/password)
- Token-based auth (Nexus tokens)
- Anonymous access (если включено в Nexus)
- Поддержка custom CA / TLS verification (как для Harbor)

### 4. API интеграция
Nexus предоставляет:
- **Docker Registry API v2** (`/v2/...`) — для Docker/OCI images
- **Helm Registry API** — для Helm charts (Nexus 3.40+)
- **REST API** (`/service/rest/v1/...`) — для управления репозиториями, компонентами, поиска

## Архитектурные решения

### 1. Абстракция Registry Client
Создать общий интерфейс `RegistryClient` с реализациями:
- `HarborRegistryClient` (существующий)
- `NexusRegistryClient` (новый)

Общий интерфейс:
```python
class RegistryClient(Protocol):
    def list_repositories(self, project: str) -> list[Repository]
    def list_artifacts(self, project: str, repository: str) -> list[Artifact]
    def get_artifact_digest(self, project: str, repository: str, reference: str) -> str | None
    # ... остальные методы
```

### 2. Factory Pattern для создания клиентов
```python
class RegistryClientFactory:
    @staticmethod
    def create(registry_type: str, config: RegistryConfig) -> RegistryClient:
        if registry_type == "harbor":
            return HarborRegistryClient(...)
        elif registry_type == "nexus":
            return NexusRegistryClient(...)
        else:
            raise ValueError(...)
```

### 3. Конфигурация Nexus профиля
Добавить в `HarborProfile` (переименовать в `RegistryProfile` или добавить поле `registry_type`):
```python
class RegistryProfile:
    id: str
    name: str
    registry_type: Literal["harbor", "nexus"]
    url: str
    # общие поля
    # nexus-специфичные поля
    docker_repo_path: str  # e.g. "docker-hosted"
    helm_repo_path: str    # e.g. "helm-hosted"
    maven_repo_path: str   # optional
```

### 4. Сохранение обратной совместимости
- Существующие `HarborProfile` продолжают работать как `registry_type="harbor"`
- Миграция БД через Alembic
- UI: выбор типа реестра при создании профиля

## План реализации

### Фаза 1: Core Infrastructure (Backend)
1. **Миграция БД** — добавить `registry_type` в `harbor_profiles` таблицу
2. **RegistryClient Protocol** — общий интерфейс
3. **NexusRegistryClient** — реализация для Nexus
   - `list_projects` → `list_repositories` в Nexus терминах
   - `list_repositories` → список компонентов в репозитории
   - `list_artifacts` → детали компонентов
   - Docker/Helm API поддержка
4. **RegistryClientFactory** — фабрика создания клиентов
4. **Интеграция в Export/Import Orchestrators** — использовать фабрику вместо прямого HarborClient

### Фаза 2: Export/Import Support
1. **Export Orchestrator** — поддержка Nexus как SOURCE
   - `skopeo copy` для Docker images (Nexus поддерживает Docker Registry API v2)
   - `helm pull` для Helm charts (Nexus 3.40+ поддерживает OCI)
2. **Import Orchestrator** — поддержка Nexus как TARGET
   - `skopeo copy` для push в Nexus
   - `helm push` для Helm charts

### Фаза 3: Frontend & Settings
1. **Settings UI** — выбор типа реестра при создании профиля
2. **Nexus-specific поля** в форме настроек
3. **Connection test** для Nexus

### Фаза 4: Tests & Documentation
1. **Unit/Integration tests** для NexusClient
2. **Integration tests** с локальным Nexus (Testcontainers)
3. **Документация** — настройка Nexus, отличия от Harbor

## Оценка трудозатрат

| Фаза | Оценка |
|------|--------|
| 1. Core Infrastructure | 3-4 дня |
| 2. Export/Import Support | 2-3 дня |
| 3. Frontend & Settings | 1-2 дня |
| 4. Tests & Docs | 1-2 дня |
| **Итого** | **7-11 дней** |

## Риски и ограничения

1. **Nexus версии** — Docker Registry API v2 поддерживается с Nexus 3.x, Helm OCI — с 3.40+
2. **Maven/другие форматы** — требуют отдельной реализации, не в MVP
3. **Nexus API отличия** — пути API отличаются от Harbor (`/service/rest/v1/` vs `/api/v2.0/`)
3. **Realms/Privileges** — настройка прав в Nexus отличается от Harbor
4. **Testing** — нужен Nexus в CI (Testcontainers или отдельный контейнер)

## Следующие шаги

1. Создать ветку `feat/nexus-support`
2. Начать с Фазы 1: миграция БД + Protocol + NexusRegistryClient
3. Написать unit-тесты для NexusRegistryClient
4. Интегрировать в Export/Import orchestrators
4. Frontend изменения
5. Tests + Docs

## Зависимости

- `requests` / `httpx` — уже есть
- `skopeo` — уже используется для Docker, совместим с Nexus Docker API
- `helm` CLI — уже используется, совместим с Nexus Helm OCI
- `httpx` — для Nexus REST API calls

---

**Статус:** Готово к старту реализации. Начать с создания ветки и миграции БД.