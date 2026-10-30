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
    async def inspect_image(self, image: ImageReference) -> ImageInspection: ...
    async def export_image(self, image: ImageReference, destination: Path) -> ExportResult: ...
    async def import_image(self, payload: Path, target: ImageReference, *, expected_digest: str) -> ImportResult: ...
    async def inspect_target(self, image: ImageReference, *, expected_digest: str | None = None) -> TargetInspection: ...
```

### 2. Factory Pattern для создания клиентов
```python
RegistryClientFactory = Callable[[Session, str | None], "RegistryClient"]

def create_registry_client(
    registry_type: Literal["harbor", "nexus"],
    session: Session,
    settings: Settings,
    *,
    profile_id: str | None = None,
    progress: Callable | None = None,
) -> RegistryClient:
    ...
```

### 3. Конфигурация Nexus профиля
Добавить в `HarborProfile` поле `registry_type`:
```python
@dataclass(frozen=True, slots=True)
class HarborProfile:
    id: str
    name: str
    url: str
    username: str | None
    verify_tls: bool
    enabled: bool
    is_default: bool = False
    registry_type: Literal["harbor", "nexus"] = "harbor"
```

### 4. Сохранение обратной совместимости
- Существующие `HarborProfile` продолжают работать как `registry_type="harbor"`
- Миграция БД через Alembic (0011_registry_type_for_profiles)
- UI: выбор типа реестра при создании профиля (Фаза 3)

## План реализации

### Фаза 1: Core Infrastructure (Backend) ✅ ЗАВЕРШЕНА
1. ✅ **Миграция БД** — добавить `registry_type` в `harbor_profiles` таблицу (0011_registry_type_for_profiles)
2. ✅ **RegistryClient Protocol** — общий интерфейс в `registry_client.py`
3. ✅ **NexusRegistryClient** — реализация для Nexus в `nexus_service.py`
   - ✅ Docker/OCI operations через skopeo
   - ⏳ Helm OCI API — в процессе (Фаза 2)
4. ✅ **RegistryClientFactory** — фабрика создания клиентов в `registry_client.py`
   - `RegistryClientFactory = Callable[[Session, str | None], "RegistryClient"]`
   - `create_registry_client(registry_type, session, settings, profile_id, progress)`
5. ✅ **Интеграция в Export/Import Orchestrators** — используют фабрику вместо прямого HarborClient

### Фаза 2: Export/Import Support 🔄 В ПРОЦЕССЕ
1. ✅ **Export Orchestrator** — поддержка Nexus как SOURCE
   - ✅ `skopeo copy` для Docker images (Nexus поддерживает Docker Registry API v2)
   - ⏳ `helm pull` для Helm charts — в процессе
2. ✅ **Import Orchestrator** — поддержка Nexus как TARGET
   - ✅ `skopeo copy` для push в Nexus (через RegistryClient)
   - ⏳ `helm push` для Helm charts — в процессе
3. ✅ **PublicationSafeExportOrchestrator** — обновлён для использования RegistryClientFactory

### Фаза 3: Frontend & Settings ⏳ ПЛАНИРУЕТСЯ
1. **Settings UI** — выбор типа реестра при создании профиля
2. **Nexus-specific поля** в форме настроек (docker_repo_path, helm_repo_path)
3. **Connection test** для Nexus

### Фаза 4: Tests & Documentation ⏳ ПЛАНИРУЕТСЯ
1. **Unit/Integration tests** для NexusService
2. **Integration tests** с локальным Nexus (Testcontainers)
3. **Документация** — настройка Nexus, отличия от Harbor

## Текущий статус (Фаза 2 завершена частично)

### ✅ Завершено в Фазе 1-2:
- Миграция 0011_registry_type_for_profiles применена
- HarborProfile с полем `registry_type: Literal["harbor", "nexus"]`
- Схемы: HarborProfileResponse, HarborProfileCreate, HarborProfilePatch с registry_type
- API: _profile_response возвращает registry_type
- Config: добавлены настройки Nexus (nexus_url, nexus_user, nexus_password, nexus_verify_tls, nexus_ca_file)
- nexus_service.py — NexusService с поддержкой skopeo для Docker/OCI
- registry_client.py — RegistryClient protocol, RegistryClientFactory, create_registry_client()
- ExportArtifactMaterializer — использует RegistryClient для container image export
- ExportOrchestrator — передаёт RegistryClientFactory в materializer
- ImportOrchestrator — использует RegistryClientFactory, create_registry_client()
- PublicationSafeExportOrchestrator — обновлён для RegistryClientFactory
- ExportPublicationGuard — обновлён для RegistryClientFactory
- ImportOrchestrator — использует RegistryClientFactory, create_registry_client()

### 🔄 В процессе (Фаза 2):
- [ ] Helm OCI support в NexusService (pull_chart, push_chart для Nexus)
- [ ] ImportArtifactClassifier — обновление для работы с Nexus при инспекции TARGET
- [ ] ExportArtifactMaterializer — поддержка Helm через RegistryClient

### ⏳ Планируется (Фаза 3-4):
- Frontend: выбор типа реестра при создании профиля
- Nexus-specific поля в UI (docker_repo_path, helm_repo_path)
- Connection test для Nexus
- Unit/Integration tests для NexusService
- Integration tests с локальным Nexus (Testcontainers)
- Документация: настройка Nexus, отличия от Harbor

## Риски и ограничения

1. **Nexus версии** — Docker Registry API v2 поддерживается с Nexus 3.x, Helm OCI — с 3.40+
2. **Maven/другие форматы** — требуют отдельной реализации, не в MVP
3. **Nexus API отличия** — пути API отличаются от Harbor (`/service/rest/v1/` vs `/api/v2.0/`)
3. **Realms/Privileges** — настройка прав в Nexus отличается от Harbor
4. **Testing** — нужен Nexus в CI (Testcontainers или отдельный контейнер)

## Следующие шаги

1. ⏳ Добавить Helm OCI support в NexusService (pull_chart, push_chart)
2. ⏳ Обновить ImportArtifactClassifier для работы с Nexus
3. 🎨 Frontend: UI для выбора типа реестра при создании профиля
4. 🧪 Tests: Unit/Integration тесты для NexusService
5. 📝 Documentation: docs/nexus-setup.md, docs/registry-comparison.md

## Зависимости

- `requests` / `httpx` — уже есть
- `skopeo` — уже используется для Docker, совместим с Nexus Docker API
- `helm` CLI — уже используется, совместим с Nexus Helm OCI
- `httpx` — для Nexus REST API calls (если понадобится для advanced features)

---

**Текущий статус:** Фаза 1 ✅, Фаза 2 🔄 (Docker/OCI работает, Helm в процессе), Фаза 3-4 ⏳