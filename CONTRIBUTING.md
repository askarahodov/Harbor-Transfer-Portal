# Правила участия в разработке

Этот репозиторий разрабатывается людьми и ИИ-агентами. Для всех участников действуют одинаковые инженерные требования.

## Язык документации

Человекоориентированная документация проекта ведётся **на русском языке**: README, руководства, ADR, эксплуатационные инструкции, troubleshooting, security-документы и поясняющие разделы для разработчиков.

Технические идентификаторы не переводятся, если это нарушит контракт или затруднит работу с кодом: имена API endpoints, переменных окружения, JSON-полей, enum, статусов, файлов, CLI-команд, библиотек и стандартов сохраняются в исходном виде. Код и машинные схемы могут использовать английские идентификаторы.

При изменении поведения обновляйте соответствующую русскоязычную документацию в той же итерации.

Карта документации, её статусы и приоритет источников находятся в [docs/README.md](docs/README.md). Исторический design document не должен использоваться вместо актуального architecture/protocol/ADR source.

## Ветки и pull request

- Создавайте сфокусированные ветки от актуальной базовой ветки.
- Рекомендуемые имена: `feat/<scope>`, `fix/<scope>`, `docs/<scope>`, `chore/<scope>`.
- Один pull request должен решать одну связную задачу или представлять одно независимо проверяемое изменение.
- Заголовок PR должен следовать Conventional Commits, например `feat: add bundle manifest model`.
- В описании PR указывайте связанную GitHub issue, scope, выполненные проверки, известные ограничения и решения, влияющие на безопасность.
- Не добавляйте несвязанный рефакторинг в feature/bugfix PR.

## Коммиты

Используйте Conventional Commits:

```text
feat: add export task model
fix: reject unsafe bundle path
refactor: isolate harbor client
test: cover digest conflict handling
docs: document bundle verification
chore: update developer tooling
```

Коммиты должны оставаться понятными и не содержать runtime-данные или сгенерированные артефакты, которые не являются частью исходного кода.

## Политика тестирования

Во время разработки запускайте минимально достаточный набор проверок для затронутого поведения. Примеры:

- только backend → соответствующие unit/API tests и lint/type checks;
- только frontend → unit/component tests, lint, typecheck и build;
- протокол или общий контракт → затронутые backend/frontend проверки плюс contract/security tests;
- transfer engine → scoped unit tests и соответствующая локальная integration fixture;
- deployment/packaging → config/build/smoke checks;
- документация → `make docs-check`; Markdown-файлы под `deploy/` остаются docs-only scope, а Compose scope относится к runtime/non-Markdown изменениям deployment.

`make docs-check` проверяет локальные Markdown-ссылки без сетевых запросов. Missing repository target или ссылка за пределы repository root являются ошибкой documentation gate.

На merge checkpoint все обязательные CI-проверки репозитория должны быть зелёными.

Нельзя превращать красную проверку в зелёную ослаблением или пропуском контроля. В частности, запрещены `|| true`, `; true`, игнорирование кодов завершения подпроцессов и `continue-on-error: true` для обязательных lint/tests.

## Правила для ИИ-агентов

1. Перед изменением прочитайте issue, связанные ADR и актуальную реализацию.
2. Держите diff в пределах scope задачи и учитывайте параллельную работу других агентов/разработчиков.
3. Исправляйте первопричину, а не подгоняйте тесты под ошибочное поведение.
4. Считайте учётные данные Harbor, содержимое пакетов и импортируемые пути недоверенными или чувствительными данными.
5. Не размещайте секреты в исходниках, тестах, логах команд, тексте PR или fixtures.
6. Для subprocess используйте структурированные аргументы и явную валидацию вместо shell-конкатенации.
7. При изменении поведения добавляйте или обновляйте scoped-тесты.
8. Перед завершением проверяйте итоговый diff на утечки секретов, случайные файлы, generated data, path traversal, shell injection и несвязанные правки.
9. Обновляйте документацию в той же итерации, если изменился контракт, эксплуатация или архитектура.
10. Останавливайтесь только при реальном blocker; не придумывайте реализацию для нерешённого security/protocol решения.

## Локальная конфигурация

Скопируйте `.env.example` в `.env`. Файл `.env` намеренно игнорируется Git. Значения в `.env.example` являются только безопасными placeholders и не должны использоваться как production credentials.

## Запуск локального Compose после обновления исходников

Canonical developer launcher — `tools/dev.py`; он одинаков для Linux и Windows и не использует shell-конкатенацию. Подробности и platform boundary: [docs/development.md](docs/development.md).

Linux:

```bash
git pull
python3 tools/dev.py up
```

Windows PowerShell:

```powershell
git pull
.\dev.ps1 up
```

Linux `make up` остаётся convenience alias и делегирует ту же Python orchestration. Windows не требует GNU Make, WSL или Git Bash.

`up` передаёт текущий Git revision в Docker build, пересобирает images и принудительно recreate-ит контейнеры. В верхней панели рядом с product version отображается короткий `UI <revision>`; после обновления source он должен соответствовать первым 12 символам `git rev-parse HEAD`.

## Проверка распространения ошибок

Make-цели намеренно строгие. Пример ручной проверки:

```bash
make test-backend
printf 'exit code: %s\n' "$?"
```

Если pytest завершается ошибкой, `make test-backend` обязан вернуть ненулевой код. Аналогичное правило действует для остальных обязательных lint/test/build/docs целей.