# AGENTS.md — правила AI-разработки Harbor Transfer Portal

Этот документ — точка входа для AI-агентов. При расхождении с текущим кодом, контрактными тестами, ADR или нормативной документацией не угадывайте: установите фактическое поведение и зафиксируйте противоречие в PR. Дополнительные требования: [CONTRIBUTING.md](CONTRIBUTING.md), [docs/README.md](docs/README.md), [docs/architecture.md](docs/architecture.md), [docs/security.md](docs/security.md), [docs/testing.md](docs/testing.md).

## Перед изменениями

1. Уточните цель, acceptance criteria и ограничения по GitHub Issue; определите затронутые файлы и зависимости.
2. Проверьте актуальные `main`, целевую ветку, открытые PR и последние изменения: другие агенты могут менять код параллельно.
3. Прочитайте реализацию, тесты, нормативные документы и связанные ADR. Не опирайтесь на устаревшее историческое ТЗ.
4. Выберите минимальное обратимое решение и scoped-тесты; не совмещайте исправление бага с несвязанным рефакторингом.
5. Работайте в отдельной ветке. Не меняйте `main` напрямую.

## Границы безопасности и автономности

- SOURCE и TARGET физически/сетево изолированы; подписанный Offline Bundle, SHA-256 sidecar и handoff — граница доверия. Не добавляйте прямую сетевую связь или общие signing private keys.
- Harbor Helm OCI и Nexus legacy Helm HTTP — разные протоколы. Не подменяйте Nexus Helm обычным OCI.
- Сервисные учётные записи, TLS/CA, repository ACL, signed provenance, anti-overwrite и импортируемые пути — security-sensitive. Не логируйте secrets и не обходите проверки ради прохождения тестов.
- Агент может создавать ветку, код, тесты, документацию и PR. **Не** выполняет автоматически merge в `main`, production deploy, production migrations, изменение credentials/IAM/network/security policies, удаление данных или изменение протокола с нарушением обратной совместимости.
- Открытый draft PR #451 — интеграционный release candidate, а не разрешение на релиз; regional acceptance и incident #422 не закрывать только по зелёному CI.

## Цикл задачи

`Issue → analyze/root cause → small implementation → selected tests → diff/security review → documentation → PR → CI → checkpoint`.

- Запускайте проверки согласно [docs/testing.md](docs/testing.md); для документации — `make docs-check`.
- Не маскируйте ошибки через `|| true`, `continue-on-error`, ослабление assertions или фиктивные тесты.
- После red CI устанавливайте первопричину и исправляйте её; повторяйте необходимые проверки.
- PR содержит Goal, Scope, Acceptance Criteria, Tests (команда и результат), Risk, Documentation Impact, Blockers, Next.
- Статус DONE разрешён только при выполненных критериях, успешных обязательных checks, review и отсутствии блокирующих дефектов. GitHub CI — контрольная точка, но не замена региональной production acceptance.
- Если задача завершена, переходите к следующей независимой READY Issue только при наличии разрешённого механизма запуска; никогда не утверждайте, что фоновый агент работает, если автоматизация не настроена.

## Координация

Один рабочий участок — один активный владелец изменений. Для независимых задач используйте отдельные ветки и PR; перед следующим commit перепроверяйте HEAD и diff. Прогресс фиксируйте кратко: Completed / Validation / Findings / Remaining / Blockers / Next. Порядок дальнейших работ: [ROADMAP.md](ROADMAP.md).
