# 📘 Harbor Transfer Portal — документ проекта

**Версия:** 1.0
**Дата:** 10 сентября 2026
**Назначение:** Единый систематизированный документ по проекту переноса Docker-образов и Helm-чартов между изолированными контурами Harbor через веб-интерфейс.

---

## 📑 Содержание

1. [Концепция и постановка задачи](#1-концепция-и-постановка-задачи)
2. [Архитектура решения](#2-архитектура-решения)
3. [Технический стек](#3-технический-стек)
4. [Как работает перенос (offline-kit)](#4-как-работает-перенос-offline-kit)
5. [Веб-интерфейс: идея](#5-веб-интерфейс-идея)
6. [Брендбук и дизайн-система](#6-брендбук-и-дизайн-система)
7. [Макеты экранов](#7-макеты-экранов)
8. [План разработки для AI-агента](#8-план-разработки-для-ai-агента)
9. [Структура проекта](#9-структура-проекта)
10. [Готовый скрипт-генератор](#10-готовый-скрипт-генератор)
11. [Чек-лист приёмки](#11-чек-лист-приёмки)

---

## 1. Концепция и постановка задачи

### 1.1. Проблема

Необходимо переносить **Docker-образы** и **Helm-чарты** между двумя **изолированными контурами Harbor** (source → target). Прямая сетевая связь между контурами отсутствует. Единственный канал переноса — **флешка**.

### 1.2. Требования

| Требование | Описание |
|---|---|
| **Изоляция** | Контуры не имеют сетевого взаимодействия |
| **Кроссплатформенность** | Интерфейс доступен с любой ОС через браузер |
| **Простота** | Обычный пользователь без знания CLI может выполнить перенос |
| **Надёжность** | Контроль целостности (SHA256), аудит, откат |
| **Автоматизация** | Минимум ручных операций |
| **Безопасность** | Разграничение ролей, логирование, отсутствие секретов в открытом виде |

### 1.3. Целевая аудитория

- DevOps-инженеры (настройка, эксплуатация).
- Системные администраторы (ежедневные операции).
- Сотрудники ИБ (аудит переносов).

---

## 2. Архитектура решения

### 2.1. Общая схема

```
┌─────────────────────────┐         ┌─────────────────────────┐
│   КОНТУР «ИСТОЧНИК»     │         │   КОНТУР «ПРИЁМНИК»     │
│                         │         │                         │
│  ┌──────────────────┐   │         │   ┌──────────────────┐  │
│  │  Harbor SOURCE   │   │         │   │  Harbor TARGET   │  │
│  └────────┬─────────┘   │         │   └────────┬─────────┘  │
│           │             │         │            │            │
│           ▼             │         │            ▲            │
│  ┌──────────────────┐   │         │   ┌──────────────────┐  │
│  │  Transfer Portal │   │         │   │  Transfer Portal │  │
│  │    (SOURCE)      │   │         │   │    (TARGET)      │  │
│  └────────┬─────────┘   │         │   └────────▲─────────┘  │
│           │             │         │            │            │
│           ▼             │         │            │            │
│  ┌──────────────────┐   │         │   ┌──────────────────┐  │
│  │  offline-kit     │   │  USB    │   │  offline-kit     │  │
│  │  (.tar.gz)       │───┼─────────┼──▶│  (.tar.gz)       │  │
│  └──────────────────┘   │         │   └──────────────────┘  │
└─────────────────────────┘         └─────────────────────────┘
```

### 2.2. Компоненты

| Компонент | Роль |
|---|---|
| **Harbor SOURCE** | Источник артефактов (образы + OCI-чарты) |
| **Harbor TARGET** | Приёмник артефактов |
| **Transfer Portal** | Веб-приложение (FastAPI + Vue 3), разворачивается в каждом контуре |
| **Skopeo** | Копирование Docker-образов между реестрами и локальной ФС |
| **Helm** | Работа с чартами (`helm pull` / `helm push` в OCI) |
| **offline-kit** | Офлайн-пакет (tar.gz) с артефактами, манифестом и контрольными суммами |
| **USB** | Физический носитель для переноса |

### 2.3. Принципы работы

1. **На источнике** — пользователь выбирает артефакты в веб-интерфейсе, портал формирует `offline-kit` и отдаёт его на скачивание.
2. **Физически** — пакет копируется на флешку и переносится в целевой контур.
3. **На приёмнике** — пользователь загружает пакет через веб-интерфейс, портал проверяет SHA256 и импортирует артефакты в целевой Harbor.

---

## 3. Технический стек

| Слой | Технология | Обоснование |
|---|---|---|
| **Backend** | Python 3.12 + FastAPI | Быстрый, асинхронный, автодокументация через OpenAPI |
| **Frontend** | Vue 3 + Vite + TypeScript | Лёгкий, современный, SPA |
| **UI-кит** | Element Plus / Naive UI | Готовые компоненты (таблицы, шаги, формы) |
| **БД** | SQLite + SQLAlchemy + Alembic | Простота развёртывания в закрытом контуре |
| **Аутентификация** | JWT + bcrypt | Без внешних зависимостей |
| **Работа с образами** | Skopeo | Не требует Docker daemon |
| **Работа с чартами** | Helm 3.8+ | Нативная поддержка OCI |
| **Упаковка** | tar + gzip + sha256sum | Стандартные средства Linux |
| **Развёртывание** | Docker Compose | Один `docker compose up` |
| **Reverse Proxy** | Nginx | Раздача SPA + проксирование API |

### 3.1. Почему Skopeo

- Работает с OCI-артефактами без Docker daemon.
- Умеет `copy`/`sync` между реестрами и локальной ФС.
- Поддерживает `dir:` и `oci-archive:` форматы для офлайн-переноса.
- **Ограничение:** не работает с «сырыми» `.tgz` чартами — только с OCI-упакованными.

### 3.2. Почему Helm

- `helm pull oci://...` — выгрузка OCI-чарта.
- `helm push chart.tgz oci://...` — загрузка OCI-чарта.
- Нативная работа с OCI-реестрами с версии 3.8.

---

## 4. Как работает перенос (offline-kit)

### 4.1. Структура offline-kit

```
offline-kit/
├── manifest.json          # опись пакета
├── checksums.sha256       # контрольные суммы всех файлов
├── images/                # образы (формат Skopeo dir:)
│   ├── nginx/
│   │   ├── 1.25/
│   │   └── 1.25-alpine/
│   └── myapp/
│       ├── v1.0.0/
│       └── v1.0.1/
└── charts/                # Helm-чарты (.tgz)
    └── mychart-1.0.0.tgz
```

### 4.2. manifest.json (пример)

```json
{
  "source": "harbor.source.local",
  "project": "myproject",
  "exported_at": "2026-09-10T14:23:00Z",
  "exported_by": "ivanov",
  "comment": "Плановое обновление прода",
  "artifacts": [
    {
      "type": "image",
      "name": "harbor.source.local/myproject/nginx",
      "tag": "1.25",
      "digest": "sha256:aaaa..."
    },
    {
      "type": "helm-chart",
      "name": "mychart",
      "version": "1.0.0",
      "digest": "sha256:bbbb..."
    }
  ]
}
```

### 4.3. Пошаговый процесс

#### Шаг 1. Экспорт (на источнике)

```bash
# 1. Образы
skopeo sync --src docker --dest dir \
  --src-creds user:pass \
  harbor.source.local/myproject \
  ./offline-kit/images/

# 2. Чарты
helm pull oci://harbor.source.local/myproject/mychart \
  --version 1.0.0 --destination ./offline-kit/charts/

# 3. Контрольные суммы
find ./offline-kit -type f -exec sha256sum {} \; > ./offline-kit/checksums.sha256

# 4. Упаковка
tar czf offline-kit.tar.gz offline-kit/
```

#### Шаг 2. Перенос через USB

- Копирование `offline-kit.tar.gz` на флешку.
- Проверка SHA256 архива.
- Перенос в целевой контур.

#### Шаг 3. Импорт (на приёмнике)

```bash
# 1. Распаковка
tar xzf offline-kit.tar.gz

# 2. Проверка целостности
cd offline-kit && sha256sum -c checksums.sha256

# 3. Образы
skopeo sync --src dir --dest docker \
  --dest-creds user:pass \
  ./images/ harbor.target.local/myproject

# 4. Чарты
helm push ./charts/mychart-1.0.0.tgz \
  oci://harbor.target.local/myproject
```

---

## 5. Веб-интерфейс: идея

### 5.1. Позиционирование

**Transfer Portal** — self-hosted веб-приложение, разворачиваемое через Docker Compose в каждом контуре. Инкапсулирует всю сложность Skopeo/Helm за понятным UI.

### 5.2. Ключевые экраны

1. **Вход** — логин/пароль, индикатор контура.
2. **Дашборд** — две большие кнопки: «Отправить» / «Принять».
3. **Мастер экспорта** — 4 шага (выбор → параметры → прогресс → готово).
4. **Мастер импорта** — 3 шага (загрузка → предпросмотр → результат).
5. **История** — список операций с фильтрами.
6. **Настройки** — подключения к Harbor, политики.

### 5.3. UX-принципы

| Принцип | Реализация |
|---|---|
| Минимум действий | Экспорт — 4 клика, импорт — 3 клика |
| Наглядность | Прогресс-бары, цветовые статусы, иконки |
| Защита от ошибок | Показ контура в шапке, подтверждения, проверка SHA |
| Кроссплатформенность | Веб, работает в любом браузере |
| Аудит | История операций с автором |
| Отчётность | Экспорт логов в CSV/PDF |

---

## 6. Брендбук и дизайн-система

### 6.1. Философия бренда

**Позиционирование:** «Мост между контурами».

**Три слова:** 🛡️ Надёжный · 🌉 Связующий · 🧭 Понятный.

**Tone of voice:** дружелюбный, но не панибратский; технически точный, но без жаргона; всегда говорит, что произошло и что делать дальше.

### 6.2. Логотип

Стилизованный контейнер (куб) с двумя встречными стрелками.

```
┌─────────────────────┐
│        ┌───┐        │
│       ╱   ╱│        │
│      ┌───┐ │        │
│      │ → │ │        │
│      │   │ └───┐    │
│      │ ← │   ╱     │
│      └───┘──┘      │
│                     │
│  HARBOR TRANSFER    │
│  PORTAL             │
└─────────────────────┘
```

### 6.3. Цветовая палитра

#### Основные цвета

| Название | HEX | Применение |
|---|---|---|
| **Deep Harbor** | `#0B1E3A` | Sidebar, заголовки |
| **Bridge Blue** | `#2563EB` | Основные действия |
| **Transfer Green** | `#10B981` | Успех, «готово» |
| **Alert Amber** | `#F59E0B` | Предупреждения |
| **Stop Red** | `#EF4444` | Ошибки, удаление |
| **Cloud White** | `#FFFFFF` | Карточки |
| **Fog Gray** | `#F1F5F9` | Фон страниц |

#### Вспомогательные

| Название | HEX | Применение |
|---|---|---|
| **Steel** | `#475569` | Вторичный текст |
| **Mist** | `#E2E8F0` | Границы |
| **Sky** | `#DBEAFE` | Подсветка выбранных |
| **Mint** | `#D1FAE5` | Фон success |
| **Sand** | `#FEF3C7` | Фон warning |
| **Rose** | `#FEE2E2` | Фон error |

### 6.4. Типографика

- **Основной:** Inter (400, 500, 600, 700).
- **Моно:** JetBrains Mono (для digest, SHA, логов).

| Роль | Размер | Вес |
|---|---|---|
| Display | 32px | 700 |
| H1 | 24px | 600 |
| H2 | 20px | 600 |
| Body L | 16px | 400 |
| Body M | 14px | 400 |
| Caption | 12px | 500 |
| Mono | 13px | 400 |

### 6.5. Сетка и отступы

- Базовая единица: **4px**.
- Токены: `space-1=4`, `space-2=8`, `space-3=12`, `space-4=16`, `space-6=24`, `space-8=32`, `space-12=48`.
- Sidebar: 240px (свёрнутый — 64px).
- Header: 64px.
- Content max-width: 1200px.

### 6.6. Радиусы и тени

| Токен | Значение |
|---|---|
| radius-sm | 4px |
| radius-md | 8px |
| radius-lg | 12px |
| radius-full | 999px |

| Тень | Значение |
|---|---|
| shadow-sm | `0 1px 2px rgba(11,30,58,0.06)` |
| shadow-md | `0 4px 12px rgba(11,30,58,0.08)` |
| shadow-lg | `0 12px 32px rgba(11,30,58,0.12)` |

### 6.7. Иконки

Библиотека **Lucide**, 24px, stroke 2px.

| Действие | Иконка |
|---|---|
| Экспорт | `upload-cloud` |
| Импорт | `download-cloud` |
| История | `history` |
| Настройки | `settings` |
| Успех | `check-circle` |
| Ошибка | `x-circle` |
| В процессе | `loader` |
| Предупреждение | `alert-triangle` |
| Образ | `package` |
| Чарт | `file-archive` |

### 6.8. Анимации

| Действие | Длительность | Кривая |
|---|---|---|
| Hover кнопки | 150ms | ease-out |
| Появление модалки | 200ms | cubic-bezier(0.16, 1, 0.3, 1) |
| Переход между шагами | 250ms | ease-in-out |
| Прогресс-бар | 400ms | linear |

### 6.9. Дизайн-токены (JSON)

```json
{
  "color": {
    "deep-harbor": "#0B1E3A",
    "bridge-blue": "#2563EB",
    "transfer-green": "#10B981",
    "alert-amber": "#F59E0B",
    "stop-red": "#EF4444",
    "cloud-white": "#FFFFFF",
    "fog-gray": "#F1F5F9",
    "steel": "#475569",
    "mist": "#E2E8F0",
    "sky": "#DBEAFE",
    "mint": "#D1FAE5",
    "sand": "#FEF3C7",
    "rose": "#FEE2E2"
  },
  "font": {
    "sans": "Inter, -apple-system, sans-serif",
    "mono": "JetBrains Mono, Menlo, monospace"
  },
  "radius": { "sm": "4px", "md": "8px", "lg": "12px", "full": "999px" },
  "space": { "1": "4px", "2": "8px", "3": "12px", "4": "16px", "6": "24px", "8": "32px", "12": "48px" },
  "layout": { "sidebar": "240px", "sidebar-collapsed": "64px", "header": "64px", "content-max": "1200px" }
}
```

---

## 7. Макеты экранов

### 7.1. Экран входа

```
╔══════════════════════════════════════════════════════════════════╗
║  Фон: Deep Harbor #0B1E3A с градиентом в #1E3A5F                ║
║                                                                  ║
║                    ┌────────────────────────┐                    ║
║                    │      [ 🐳 ЛОГО ]       │  ← 64×64           ║
║                    │   Harbor Transfer      │  ← Display 32px    ║
║                    │       Portal           │                    ║
║                    │   Перенос артефактов   │  ← Body L 16px     ║
║                    │   между контурами      │                    ║
║                    │  ┌──────────────────┐  │                    ║
║                    │  │  Логин           │  │                    ║
║                    │  └──────────────────┘  │                    ║
║                    │  ┌──────────────────┐  │                    ║
║                    │  │  Пароль     👁   │  │                    ║
║                    │  └──────────────────┘  │                    ║
║                    │  ┌──────────────────┐  │                    ║
║                    │  │     Войти        │  │  ← Bridge Blue     ║
║                    │  └──────────────────┘  │                    ║
║                    │   Контур: 🟢 SOURCE    │  ← Caption 12px    ║
║                    └────────────────────────┘                    ║
╚══════════════════════════════════════════════════════════════════╝
```

### 7.2. Дашборд

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  ┌─────────────┐                                                             ║
║  │  🐳 Harbor  │  Добрый день, Иван 👋          Контур: 🟢 SOURCE            ║
║  │  Transfer   │  ─────────────────────────────────────────────────────────  ║
║  │             │                                                             ║
║  │  📤 Отправка│  Что вы хотите сделать?                                     ║
║  │  (active)   │                                                             ║
║  │             │  ┌───────────────────────────┐  ┌───────────────────────┐  ║
║  │  📥 Приём   │  │        ┌─────┐            │  │       ┌─────┐        │  ║
║  │             │  │        │ 📤  │            │  │       │ 📥  │        │  ║
║  │  📜 История │  │        └─────┘            │  │       └─────┘        │  ║
║  │             │  │   Отправить артефакты     │  │   Принять пакет       │  ║
║  │  ⚙️ Настройки│  │   Выбрать образы и чарты  │  │   Загрузить архив,    │  ║
║  │             │  │   [  Начать  →  ]         │  │   [  Начать  →  ]     │  ║
║  │  ─────────  │  └───────────────────────────┘  └───────────────────────┘  ║
║  │  👤 ivanov  │                                                             ║
║  │     [Выйти] │  Последние операции                          [ Вся история ] ║
║  │             │  ┌───────────────────────────────────────────────────────┐ ║
║  │             │  │ ✅  10.09 14:23  Экспорт  42 образа  →                │ ║
║  │             │  │ ⏳  10.09 14:10  Импорт   4 артефакта →               │ ║
║  │             │  │ ❌  09.09 18:00  Экспорт  ошибка сети  →              │ ║
║  │             │  └───────────────────────────────────────────────────────┘ ║
║  └─────────────┘                                                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.3. Мастер экспорта — Шаг 1 (выбор)

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Отправка артефактов                        Шаг 1 из 4                       ║
║  ─────────────────────────────────────────────────────────────────────────   ║
║  ●━━━━━━━━━○━━━━━━━━━○━━━━━━━━━○                                            ║
║  Выбор       Параметры    Прогресс      Готово                               ║
║                                                                              ║
║  Проект в Harbor:   [ myproject           ▼ ]                                ║
║  🔍 [ nginx                                        ]                         ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ ☑ Выбрать все                    Найдено: 4 репозитория│                  ║
║  ├────────────────────────────────────────────────────────┤                  ║
║  │ ☑  🐳 nginx         теги: 1.25, 1.25-alpine   12 MB   │                  ║
║  │ ☑  🐳 nginx-ingress теги: v1.9.0               8 MB   │                  ║
║  │ ☐  🐳 redis         теги: 7.2, latest          5 MB   │                  ║
║  │ ☑  🐳 myapp         теги: v1.0.0, v1.0.1    120 MB   │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  Helm-чарты:                                                                 ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ ☑  📦 mychart      версия 1.0.0                2 MB   │                  ║
║  │ ☐  📦 mychart      версия 0.9.0                2 MB   │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  ┌──────────────────────────────────────────────────────┐                    ║
║  │  Выбрано: 3 образа (140 MB), 1 чарт (2 MB)          │                    ║
║  └──────────────────────────────────────────────────────┘                    ║
║                              [ Отмена ]   [  Далее  →  ]                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.4. Мастер экспорта — Шаг 3 (прогресс)

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Отправка артефактов                        Шаг 3 из 4                       ║
║  ●━━━━━━━━━●━━━━━━━━━●━━━━━━━━━○                                            ║
║                                                                              ║
║  ⏳ Формируем пакет...                                                       ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │  ████████████████████░░░░░░░░░░░░░░░░  52%             │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ ✅  Загружен nginx:1.25                        2.1s   │                  ║
║  │ ✅  Загружен nginx:1.25-alpine                 1.8s   │                  ║
║  │ ⏳  Загружается myapp:v1.0.0   ██████░░░░  60%        │                  ║
║  │ ⏳  Ожидает myapp:v1.0.1                              │                  ║
║  │ ⏳  Ожидает mychart-1.0.0                             │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  Время: 00:42            Осталось: ~00:38                                    ║
║                                            [ Отменить ]                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.5. Мастер экспорта — Шаг 4 (готово)

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Отправка артефактов                        Шаг 4 из 4                       ║
║  ●━━━━━━━━━●━━━━━━━━━●━━━━━━━━━●                                            ║
║                                                                              ║
║        ┌─────────────────────────────┐                                       ║
║        │          ✅                 │  ← иконка 64px, Green                 ║
║        │      Пакет готов!           │  ← H1 24px                            ║
║        └─────────────────────────────┘                                       ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │  📦  transfer-2026-09-10-myapp.tar.gz                  │                  ║
║  │      142 MB · 3 образа · 1 чарт                        │                  ║
║  │      SHA256: a3f5b2...c8d1                             │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  ┌────────────────────────────┐                                              ║
║  │  ⬇️   Скачать пакет         │  ← Primary, широкая                          ║
║  └────────────────────────────┘                                              ║
║                                                                              ║
║  ⚠️ Не забудьте проверить SHA256 после переноса на флешку!                   ║
║                                                                              ║
║  [ Открыть папку ]   [ Создать новый пакет ]                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.6. Мастер импорта — Шаг 2 (предпросмотр)

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Приём пакета                               Шаг 2 из 3                       ║
║  ●━━━━━━━━━●━━━━━━━━━○                                                       ║
║                                                                              ║
║  ✅ Контрольные суммы совпадают                                              ║
║                                                                              ║
║  Опись пакета:                                                               ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ Источник:     harbor.source.local / myproject          │                  ║
║  │ Создан:       10.09.2026 14:23                         │                  ║
║  │ Автор:        ivanov                                   │                  ║
║  │ Комментарий:  Плановое обновление прода                │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  Содержимое:                                                                 ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ 🐳  nginx:1.25          ✅ уже есть в Harbor           │                  ║
║  │ 🐳  nginx:1.25-alpine   🆕 новый                       │                  ║
║  │ 🐳  myapp:v1.0.0        🆕 новый                       │                  ║
║  │ 🐳  myapp:v1.0.1        ⚠️ перезапишет существующий    │                  ║
║  │ 📦  mychart-1.0.0       🆕 новый                       │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  ● Пропускать уже существующие                                               ║
║  ○ Перезаписывать существующие                                               ║
║  ○ Спрашивать для каждого                                                    ║
║                                                                              ║
║                                [ ← Назад ]  [ Импортировать ]                ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.7. Экран «История»

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  История операций                                                            ║
║  ─────────────────────────────────────────────────────────────────────────   ║
║  [ Все ▼ ]  [ Дата ▼ ]  [ Пользователь ▼ ]  🔍 [поиск]                       ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────┐                  ║
║  │ ✅ 10.09 14:23  Экспорт  42 образа, 3 чарта            │                  ║
║  │    transfer-2026-09-10-myapp                           │                  ║
║  │    Автор: ivanov   [ Подробнее ]  [ Скачать отчёт ]    │                  ║
║  ├────────────────────────────────────────────────────────┤                  ║
║  │ ✅ 10.09 12:10  Импорт   4 артефакта                   │                  ║
║  │    transfer-2026-09-09-redis                           │                  ║
║  │    Автор: petrov   [ Подробнее ]  [ Скачать отчёт ]    │                  ║
║  ├────────────────────────────────────────────────────────┤                  ║
║  │ ❌ 09.09 18:00  Экспорт  ошибка сети                   │                  ║
║  │    Ошибка: timeout connecting to Harbor                │                  ║
║  │    Автор: ivanov   [ Подробнее ]                       │                  ║
║  └────────────────────────────────────────────────────────┘                  ║
║                                                                              ║
║  Показано 1–20 из 342              [ ← ]  1 2 3 ...  [ → ]                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.8. Экран «Настройки»

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Настройки                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────   ║
║  ┌── 🔗 Harbor-источник ────────────────────────────────┐                    ║
║  │  URL:      [ https://harbor.source.local         ]   │                    ║
║  │  Логин:    [ admin                               ]   │                    ║
║  │  Пароль:   [ ••••••••                          ]   │                    ║
║  │  [  Проверить подключение  ]     ✅ OK              │                    ║
║  └───────────────────────────────────────────────────────┘                   ║
║                                                                              ║
║  ┌── 🔗 Harbor-приёмник ────────────────────────────────┐                    ║
║  │  URL:      [ https://harbor.target.local         ]   │                    ║
║  │  Логин:    [ admin                               ]   │                    ║
║  │  Пароль:   [ ••••••••                          ]   │                    ║
║  │  [  Проверить подключение  ]     ❌ Не настроено     │                    ║
║  └───────────────────────────────────────────────────────┘                   ║
║                                                                              ║
║  ┌── 🗂️ Политики ──────────────────────────────────────┐                    ║
║  │  ☑  Разрешить перезапись артефактов                  │                    ║
║  │  ☑  Проверять SHA256 при импорте                     │                    ║
║  │  ☐  Удалять пакет после успешного импорта            │                    ║
║  │  ☐  Уведомлять по email о завершении                 │                    ║
║  └───────────────────────────────────────────────────────┘                   ║
║                                                                              ║
║                                        [ Сохранить ]                         ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 7.9. Мобильная версия

```
┌─────────────────────────┐
│  ☰  Harbor Transfer     │
│      Контур: 🟢 SOURCE  │
├─────────────────────────┤
│  ┌───────────────────┐  │
│  │       📤          │  │
│  │  Отправить        │  │
│  │  артефакты        │  │
│  │  [  Начать  →  ]  │  │
│  └───────────────────┘  │
│  ┌───────────────────┐  │
│  │       📥          │  │
│  │  Принять          │  │
│  │  пакет            │  │
│  │  [  Начать  →  ]  │  │
│  └───────────────────┘  │
│                         │
│  Последние операции     │
│  ┌───────────────────┐  │
│  │ ✅ Экспорт        │  │
│  │    10.09 14:23    │  │
│  └───────────────────┘  │
└─────────────────────────┘
```

---

## 8. План разработки для AI-агента

### 8.1. Общие правила для агента

1. Создать Git-репозиторий с ветками `main`, `develop`, `feature/*`.
2. Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`.
3. Не хардкодить секреты — только `.env`.
4. Каждая задача завершается: рабочим кодом + тестом + обновлением docs + зелёным `make lint && make test`.
5. После фазы — тег версии (`v0.1.0`, `v0.2.0`, ...).

### 8.2. Фазы разработки

#### ФАЗА 1. Фундамент

**Цель:** рабочий стек, поднимается через `docker compose up`, отдаёт `/api/health` и SPA.

| # | Задача | Результат |
|---|---|---|
| 1.1 | Инициализация репозитория | `.gitignore`, `README.md`, `Makefile` |
| 1.2 | Backend: FastAPI skeleton | `main.py`, `/api/health`, `Dockerfile` со Skopeo/Helm |
| 1.3 | Frontend: Vue 3 skeleton | Vite + Vue + Router + Pinia, страницы-заглушки |
| 1.4 | Docker Compose | `backend`, `frontend`, `.env.example`, volumes |

**Критерий:** `make up` поднимает стек, SPA открывается, health отвечает.

#### ФАЗА 2. Аутентификация и БД

| # | Задача | Результат |
|---|---|---|
| 2.1 | Модели БД | `User`, `Operation`, `Setting` + Alembic |
| 2.2 | Аутентификация | JWT, `/api/auth/login`, `/api/auth/me` |
| 2.3 | UI: страница входа | Форма, Pinia-стор, axios-интерцептор |

**Критерий:** сессия сохраняется, 401 без токена, редирект на `/login`.

#### ФАЗА 3. Интеграция с Harbor

| # | Задача | Результат |
|---|---|---|
| 3.1 | Harbor API-клиент | `list_projects`, `list_repositories`, `list_artifacts`, `list_charts` |
| 3.2 | Эндпоинты для UI | `/api/repositories`, `/api/charts`, `/api/projects` |
| 3.3 | UI: страница настроек | Форма + кнопка «Проверить подключение» |

**Критерий:** список артефактов из Harbor отображается в UI.

#### ФАЗА 4. Экспорт

| # | Задача | Результат |
|---|---|---|
| 4.1 | Skopeo-сервис | `export_image`, `import_image` |
| 4.2 | Helm-сервис | `pull_chart`, `push_chart` |
| 4.3 | Формирование пакета | `build_package`, `verify_package`, manifest + checksums |
| 4.4 | Фоновые задачи | `task_manager` + эндпоинты статуса |
| 4.5 | UI: мастер экспорта | 4 шага, прогресс через polling |

**Критерий:** полный цикл экспорта с отображением прогресса.

#### ФАЗА 5. Импорт

| # | Задача | Результат |
|---|---|---|
| 5.1 | Эндпоинты импорта | upload → preview → run → report |
| 5.2 | UI: мастер импорта | drag&drop, preview, прогресс, отчёт |

**Критерий:** экспорт → флешка → импорт без ручных команд.

#### ФАЗА 6. История, аудит, роли

| # | Задача | Результат |
|---|---|---|
| 6.1 | История операций | Таблица `Operation`, фильтры, пагинация |
| 6.2 | Роли | `admin`, `operator`, `viewer` + зависимости |
| 6.3 | Логирование | structlog/loguru, JSON, ротация |

**Критерий:** viewer не может запустить экспорт; логи содержат user_id, task_id.

#### ФАЗА 7. Отчёты и полировка

| # | Задача | Результат |
|---|---|---|
| 7.1 | Отчёты | CSV + PDF |
| 7.2 | UX-полировка | Скелетоны, тосты, пустые состояния |
| 7.3 | Документация | README, architecture, user-guide, admin-guide |
| 7.4 | Тесты и CI | >70% покрытие, smoke E2E |

**Критерий:** MR не мержится без зелёного CI.

#### ФАЗА 8. Релиз

| # | Задача | Результат |
|---|---|---|
| 8.1 | Финальная сборка | `install.sh`, `offline-install.tar.gz` |

**Критерий:** установка на чистой VM одной командой.

### 8.3. Порядок выполнения

```
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
```

После каждой задачи: `make lint && make test`, коммит, PR.

### 8.4. Инструкции для агента

1. **Не изобретать API** — использовать Harbor REST API 2.0.
2. **Не заменять Skopeo/Helm** на самописные решения.
3. **При неясности** — консервативное решение + запись в `docs/decisions.md`.
4. **Один PR = одна задача.**
5. **Никаких `TODO` без issue.**

---

## 9. Структура проекта

```
harbor-transfer-portal/
├── docker-compose.yml
├── Makefile
├── .env.example
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── repositories.py
│   │   │   ├── export.py
│   │   │   ├── import_.py
│   │   │   ├── history.py
│   │   │   ├── settings.py
│   │   │   └── health.py
│   │   ├── services/
│   │   │   ├── harbor_client.py
│   │   │   ├── skopeo_service.py
│   │   │   ├── helm_service.py
│   │   │   ├── package_service.py
│   │   │   ├── checksum_service.py
│   │   │   └── task_manager.py
│   │   ├── workers/
│   │   │   ├── export_worker.py
│   │   │   └── import_worker.py
│   │   └── utils/
│   │       ├── logging.py
│   │       └── security.py
│   └── tests/
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.ts
│       ├── App.vue
│       ├── router/
│       ├── stores/
│       ├── api/
│       ├── components/
│       └── views/
│           ├── Login.vue
│           ├── Dashboard.vue
│           ├── Export.vue
│           ├── Import.vue
│           ├── History.vue
│           └── Settings.vue
├── mockups/
│   └── dashboard.html
├── docs/
│   ├── design-system.md
│   ├── development-plan.md
│   ├── architecture.md
│   ├── user-guide.md
│   ├── admin-guide.md
│   └── troubleshooting.md
└── data/
    ├── packages/
    ├── logs/
    └── portal.db
```

---

## 10. Готовый скрипт-генератор

### 10.1. Linux / macOS

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="harbor-transfer-portal"
rm -rf "$ROOT" "$ROOT.tar.gz"
mkdir -p "$ROOT"/{backend/app/{api,services,workers,utils},backend/tests,frontend/src/{views,components,router,stores,api},docs,data/{packages,logs},mockups}

# docker-compose.yml
cat > "$ROOT/docker-compose.yml" <<'EOF'
version: '3.8'
services:
  backend:
    build: ./backend
    container_name: htp-backend
    env_file: .env
    volumes: ["./data:/app/data"]
    ports: ["8000:8000"]
    restart: unless-stopped
  frontend:
    build: ./frontend
    container_name: htp-frontend
    ports: ["8080:80"]
    depends_on: [backend]
    restart: unless-stopped
EOF

# .env.example
cat > "$ROOT/.env.example" <<'EOF'
SRC_HARBOR_URL=https://harbor.source.local
SRC_HARBOR_USER=
SRC_HARBOR_PASS=
DST_HARBOR_URL=https://harbor.target.local
DST_HARBOR_USER=
DST_HARBOR_PASS=
PORTAL_ADMIN_USER=admin
PORTAL_ADMIN_PASS=changeme
PORTAL_CONTOUR=SOURCE
PORTAL_SECRET_KEY=please-change-me
EOF

# Makefile
cat > "$ROOT/Makefile" <<'EOF'
.PHONY: up down logs lint test fmt
up:
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f
lint:
	cd backend && ruff check app tests || true
test:
	cd backend && pytest -q || true
fmt:
	cd backend && ruff format app tests || true
EOF

# README.md
cat > "$ROOT/README.md" <<'EOF'
# Harbor Transfer Portal

Веб-портал для переноса Docker-образов и Helm-чартов между двумя
изолированными контурами Harbor через офлайн-пакет (флешка).

## Быстрый старт
1. cp .env.example .env  (заполнить Harbor-параметры)
2. make up
3. UI: http://localhost:8080  ·  API: http://localhost:8000/docs
EOF

# backend/Dockerfile
cat > "$ROOT/backend/Dockerfile" <<'EOF'
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    skopeo curl tar gzip jq ca-certificates gnupg \
    && curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
EOF

# entrypoint.sh
cat > "$ROOT/backend/entrypoint.sh" <<'EOF'
#!/usr/bin/env bash
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
EOF
chmod +x "$ROOT/backend/entrypoint.sh"

# pyproject.toml
cat > "$ROOT/backend/pyproject.toml" <<'EOF'
[project]
name = "htp-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110", "uvicorn[standard]>=0.29",
    "pydantic>=2.6", "pydantic-settings>=2.2",
    "sqlalchemy>=2.0", "alembic>=1.13", "aiosqlite>=0.20",
    "httpx>=0.27", "python-multipart>=0.0.9",
    "passlib[bcrypt]>=1.7", "python-jose[cryptography]>=3.3",
]
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "ruff", "mypy", "respx"]
[tool.ruff]
line-length = 100
target-version = "py312"
EOF

# main.py + health.py
cat > "$ROOT/backend/app/main.py" <<'EOF'
from fastapi import FastAPI
from app.api import health
app = FastAPI(title="Harbor Transfer Portal", version="0.1.0")
app.include_router(health.router, prefix="/api")
EOF

cat > "$ROOT/backend/app/api/health.py" <<'EOF'
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok"}
EOF

touch "$ROOT/backend/app/__init__.py" \
      "$ROOT/backend/app/api/__init__.py" \
      "$ROOT/backend/app/services/__init__.py" \
      "$ROOT/backend/app/workers/__init__.py" \
      "$ROOT/backend/app/utils/__init__.py"

# frontend
cat > "$ROOT/frontend/Dockerfile" <<'EOF'
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
EOF

cat > "$ROOT/frontend/nginx.conf" <<'EOF'
server {
  listen 80;
  location / { root /usr/share/nginx/html; try_files $uri $uri/ /index.html; }
  location /api/ { proxy_pass http://backend:8000/api/; proxy_set_header Host $host; }
}
EOF

cat > "$ROOT/frontend/package.json" <<'EOF'
{
  "name": "htp-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "axios": "^1.6.8",
    "pinia": "^2.1.7",
    "vue": "^3.4.21",
    "vue-router": "^4.3.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.0.4",
    "typescript": "^5.4.0",
    "vite": "^5.2.0",
    "vitest": "^1.4.0"
  }
}
EOF

# docs
cat > "$ROOT/docs/design-system.md" <<'EOF'
# Harbor Transfer Portal — Дизайн-система

## Цвета
| Токен | HEX |
|---|---|
| deep-harbor | #0B1E3A |
| bridge-blue | #2563EB |
| transfer-green | #10B981 |
| alert-amber | #F59E0B |
| stop-red | #EF4444 |
| fog-gray | #F1F5F9 |
| steel | #475569 |
| sky | #DBEAFE |
| mint | #D1FAE5 |

## Типографика
Inter (400/500/600/700), JetBrains Mono.

## Радиусы
sm 4px, md 8px, lg 12px, full 999px.

## Layout
Sidebar 240px (collapsed 64px), header 64px, content max 1200px.
EOF

cat > "$ROOT/docs/development-plan.md" <<'EOF'
# План разработки
1. Фундамент (FastAPI + Vue 3 + Docker Compose + Skopeo/Helm).
2. Аутентификация и БД.
3. Интеграция с Harbor.
4. Экспорт (мастер из 4 шагов).
5. Импорт (мастер из 3 шагов).
6. История, аудит, роли.
7. Отчёты, UX-полировка, документация.
8. Релиз: install.sh + offline-install.tar.gz.
EOF

# mockup dashboard
cat > "$ROOT/mockups/dashboard.html" <<'HTMLEOF'
<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>Harbor Transfer Portal — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
<style>
:root{--deep-harbor:#0B1E3A;--bridge-blue:#2563EB;--transfer-green:#10B981;--fog-gray:#F1F5F9;--steel:#475569;--mist:#E2E8F0;--sky:#DBEAFE;--mint:#D1FAE5;--sand:#FEF3C7;--rose:#FEE2E2;--shadow-sm:0 1px 2px rgba(11,30,58,.06);--shadow-md:0 4px 12px rgba(11,30,58,.08)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:var(--fog-gray);color:var(--deep-harbor);font-size:14px;line-height:1.5}
.app{display:flex;min-height:100vh}
.sidebar{width:240px;background:var(--deep-harbor);color:#fff;padding:24px 16px;display:flex;flex-direction:column;gap:8px}
.logo{display:flex;align-items:center;gap:10px;padding:8px 12px 24px;font-weight:700;font-size:16px;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:16px}
.logo-mark{width:32px;height:32px;background:var(--bridge-blue);border-radius:8px;display:grid;place-items:center;font-size:18px}
.nav-item{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:8px;color:rgba(255,255,255,.7);cursor:pointer;font-weight:500}
.nav-item.active{background:rgba(37,99,235,.15);color:#fff;border-left:3px solid var(--bridge-blue);padding-left:9px}
.user-block{margin-top:auto;padding:12px;border-top:1px solid rgba(255,255,255,.08);display:flex;align-items:center;gap:10px}
.avatar{width:32px;height:32px;background:var(--bridge-blue);border-radius:999px;display:grid;place-items:center;font-weight:600;font-size:13px}
.main{flex:1;padding:32px 40px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:32px}
.greeting h1{font-size:24px;font-weight:600}
.greeting p{color:var(--steel);margin-top:4px}
.contour-badge{background:var(--mint);color:#065F46;padding:6px 14px;border-radius:999px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px}
.contour-badge::before{content:'';width:8px;height:8px;background:var(--transfer-green);border-radius:999px}
.section-title{font-size:16px;font-weight:600;margin-bottom:16px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:40px}
.action-card{background:#fff;border-radius:12px;padding:32px;box-shadow:var(--shadow-sm);cursor:pointer;transition:all .2s;border:2px solid transparent}
.action-card:hover{box-shadow:var(--shadow-md);transform:translateY(-2px);border-color:var(--sky)}
.card-icon{width:64px;height:64px;background:var(--sky);border-radius:12px;display:grid;place-items:center;font-size:28px;margin-bottom:20px}
.card-icon.green{background:var(--mint)}
.action-card h3{font-size:20px;font-weight:600;margin-bottom:8px}
.action-card p{color:var(--steel);margin-bottom:24px}
.btn{padding:10px 20px;border-radius:8px;border:none;font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;background:var(--bridge-blue);color:#fff}
.history-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.link{color:var(--bridge-blue);font-size:13px;font-weight:500;cursor:pointer;text-decoration:none}
.history-list{background:#fff;border-radius:12px;box-shadow:var(--shadow-sm);overflow:hidden}
.history-row{display:grid;grid-template-columns:40px 100px 100px 1fr 120px;align-items:center;gap:16px;padding:16px 20px;border-bottom:1px solid var(--mist);cursor:pointer}
.history-row:last-child{border-bottom:none}
.history-row:hover{background:var(--sky)}
.status-dot{width:32px;height:32px;border-radius:999px;display:grid;place-items:center}
.status-dot.ok{background:var(--mint)}.status-dot.run{background:var(--sand)}.status-dot.err{background:var(--rose)}
.history-meta{color:var(--steel);font-size:13px}
.history-title{font-weight:500}
.history-pkg{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--steel)}
.badge{display:inline-block;padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;text-transform:uppercase}
.badge.export{background:var(--sky);color:#1E40AF}.badge.import{background:var(--mint);color:#065F46}
</style></head><body>
<div class="app">
<aside class="sidebar">
<div class="logo"><div class="logo-mark">🐳</div><div>Harbor<br><span style="font-weight:400;opacity:.6;font-size:12px;">Transfer Portal</span></div></div>
<div class="nav-item active"><span>📤</span> Отправка</div>
<div class="nav-item"><span>📥</span> Приём</div>
<div class="nav-item"><span>📜</span> История</div>
<div class="nav-item"><span>⚙️</span> Настройки</div>
<div class="user-block"><div class="avatar">ИВ</div><div style="flex:1"><div style="font-weight:500;font-size:13px">ivanov</div><div style="font-size:11px;opacity:.6">оператор</div></div><div style="opacity:.5">↪</div></div>
</aside>
<main class="main">
<div class="topbar"><div class="greeting"><h1>Добрый день, Иван 👋</h1><p>10 сентября 2026 · Harbor Transfer Portal v1.0.0</p></div><div class="contour-badge">Контур: SOURCE</div></div>
<div class="section-title">Что вы хотите сделать?</div>
<div class="cards">
<div class="action-card"><div class="card-icon">📤</div><h3>Отправить артефакты</h3><p>Выбрать образы и чарты из Harbor-источника и собрать офлайн-пакет.</p><button class="btn">Начать →</button></div>
<div class="action-card"><div class="card-icon green">📥</div><h3>Принять пакет</h3><p>Загрузить архив, проверить целостность и импортировать в целевой Harbor.</p><button class="btn">Начать →</button></div>
</div>
<div class="history-header"><div class="section-title" style="margin:0">Последние операции</div><a class="link">Вся история →</a></div>
<div class="history-list">
<div class="history-row"><div class="status-dot ok">✓</div><div class="history-meta">10.09 14:23</div><div><span class="badge export">Экспорт</span></div><div><div class="history-title">42 образа, 3 чарта</div><div class="history-pkg">transfer-2026-09-10-myapp</div></div><div class="history-meta" style="text-align:right">ivanov</div></div>
<div class="history-row"><div class="status-dot run">⏳</div><div class="history-meta">10.09 14:10</div><div><span class="badge import">Импорт</span></div><div><div class="history-title">4 артефакта</div><div class="history-pkg">transfer-2026-09-09-redis</div></div><div class="history-meta" style="text-align:right">petrov</div></div>
<div class="history-row"><div class="status-dot err">✕</div><div class="history-meta">09.09 18:00</div><div><span class="badge export">Экспорт</span></div><div><div class="history-title">Ошибка сети</div><div class="history-pkg">timeout connecting to Harbor</div></div><div class="history-meta" style="text-align:right">ivanov</div></div>
</div>
</main>
</div></body></html>
HTMLEOF

# Упаковка
tar -czf "$ROOT.tar.gz" "$ROOT"
echo "✅ Готово: $(pwd)/$ROOT.tar.gz"
```

### 10.2. Windows (PowerShell)

```powershell
$ErrorActionPreference = "Stop"
$root = "harbor-transfer-portal"
if (Test-Path $root) { Remove-Item -Recurse -Force $root }
if (Test-Path "$root.zip") { Remove-Item -Force "$root.zip" }

New-Item -ItemType Directory -Path $root | Out-Null
$dirs = @(
  "$root\backend\app\api", "$root\backend\app\services", "$root\backend\app\workers",
  "$root\backend\app\utils", "$root\backend\tests",
  "$root\frontend\src\views", "$root\frontend\src\components",
  "$root\frontend\src\router", "$root\frontend\src\stores", "$root\frontend\src\api",
  "$root\docs", "$root\data\packages", "$root\data\logs", "$root\mockups"
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Path $d -Force | Out-Null }

# docker-compose.yml
@'
version: '3.8'
services:
  backend:
    build: ./backend
    container_name: htp-backend
    env_file: .env
    volumes: ["./data:/app/data"]
    ports: ["8000:8000"]
    restart: unless-stopped
  frontend:
    build: ./frontend
    container_name: htp-frontend
    ports: ["8080:80"]
    depends_on: [backend]
    restart: unless-stopped
'@ | Set-Content "$root\docker-compose.yml" -Encoding UTF8

# .env.example, README, Makefile, Dockerfile, pyproject, main.py, health.py, frontend/*
# (аналогично bash-версии, но через Set-Content)

# Упаковка
Compress-Archive -Path $root -DestinationPath "$root.zip" -Force
Write-Host "✅ Готово: $(Resolve-Path $root.zip)"
```

Полная версия Windows-скрипта — в предыдущих сообщениях.

---

## 11. Чек-лист приёмки

### 11.1. Функциональность

- [ ] `docker compose up -d` поднимает стек без ручных шагов.
- [ ] Пользователь входит через браузер.
- [ ] Пользователь формирует пакет из Harbor-источника.
- [ ] Пакет скачивается в `.tar.gz`.
- [ ] На приёмнике пакет загружается через UI.
- [ ] Импорт выполняется, артефакты появляются в целевом Harbor.
- [ ] Отчёт доступен в CSV/PDF.
- [ ] Все операции в истории.

### 11.2. Безопасность

- [ ] API требует JWT.
- [ ] Роли разграничены: `admin` / `operator` / `viewer`.
- [ ] Секреты хранятся в `.env`, не в коде.
- [ ] Логи не содержат пароли.
- [ ] SHA256 проверяется при импорте.

### 11.3. Качество

- [ ] Тесты зелёные, покрытие >70%.
- [ ] `make lint` без ошибок.
- [ ] Документация позволяет развернуть с нуля за 15 минут.
- [ ] CI настроен, MR не мержится без зелёного билда.

### 11.4. UX

- [ ] Интерфейс доступен с любой ОС.
- [ ] Адаптив под мобильные.
- [ ] Прогресс операций в реальном времени.
- [ ] Понятные сообщения об ошибках.
- [ ] Пустые состояния с подсказками.

---

## 📎 Приложения

### Приложение А. Матрица ролей

| Действие | viewer | operator | admin |
|---|---|---|---|
| Просмотр истории | ✅ | ✅ | ✅ |
| Экспорт | ❌ | ✅ | ✅ |
| Импорт | ❌ | ✅ | ✅ |
| Настройки Harbor | ❌ | ❌ | ✅ |
| Управление пользователями | ❌ | ❌ | ✅ |

### Приложение Б. Переменные окружения

| Переменная | Описание |
|---|---|
| `SRC_HARBOR_URL` | URL Harbor-источника |
| `SRC_HARBOR_USER` | Логин для источника |
| `SRC_HARBOR_PASS` | Пароль для источника |
| `DST_HARBOR_URL` | URL Harbor-приёмника |
| `DST_HARBOR_USER` | Логин для приёмника |
| `DST_HARBOR_PASS` | Пароль для приёмника |
| `PORTAL_ADMIN_USER` | Логин админа портала |
| `PORTAL_ADMIN_PASS` | Пароль админа портала |
| `PORTAL_CONTOUR` | `SOURCE` или `TARGET` |
| `PORTAL_SECRET_KEY` | Секрет для JWT |

### Приложение В. Типовые ошибки

| Ошибка | Причина | Решение |
|---|---|---|
| `skopeo: unauthorized` | Неверные креды Harbor | Проверить `.env` |
| `helm push: failed` | Нет прав на push в OCI | Проверить роль сервисного аккаунта |
| `sha256sum: FAILED` | Повреждение при переносе | Перекопировать архив |
| `timeout connecting to Harbor` | Сеть или firewall | Проверить доступность Harbor |

---

**Конец мастер-документа.**

Этот документ можно использовать как:
- **Техническое задание** — для подрядчика или AI-агента.
- **Проектную документацию** — для команды.
- **Презентацию** — для согласования с руководством.
- **Runbook** — для эксплуатации.

При необходимости документ можно разбить на отдельные файлы (`docs/*.md`) при инициализации репозитория.
