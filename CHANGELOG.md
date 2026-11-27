# Changelog

Все заметные пользователю изменения Harbor Transfer Portal документируются здесь.
Проект следует Semantic Versioning для продуктовых релизов.

## 1.1.0 - 2026-11-27

### Добавлено

- Работа с **Nexus Docker Registry v2** как SOURCE/TARGET: просмотр, инспекция и
  экспорт по точному repository+tag, импорт на TARGET через Docker v2 connector.
- **Classic Helm HTTP** (не OCI) для Nexus: hosted-репозитории `index.yaml`/`.tgz`,
  запись только в точный hosted repository с fail-closed проверкой.
- Type-aware профили реестров и подписи в интерфейсе: Harbor / Nexus Docker / Nexus Helm.
- Bundle v1.1 хранит registry origin с обратной совместимостью с Harbor-бандлами v1.0.

### Исправлено

- Транспортные флаги Helm OCI для Nexus: `--plain-http` для `http://`,
  `--insecure-skip-tls-verify` только при отключённой проверке TLS, `--ca-file`
  для корпоративного CA.
- Компактный `skopeo inspect` устойчив к репозиториям с тысячами RepoTags;
  усечённый вывод возвращает типизированную ошибку `skopeo_output_truncated`.
- Публикация immutable import receipt атомарна; успешный receipt доступен только
  после перехода операции в `COMPLETED`; `skip_conflicts` фиксируется во всех
  путях формирования receipt.
- Проверка подключения Harbor выполняется вне event loop и не блокирует сервер.

### Изменено

- pytest 9.2 / pytest-asyncio 1.5 и minimatch 11 закрывают открытые
  Dependabot-алерты.
- Nexus legacy Docker v2 E2E выполняется scoped-шагами в job `Integration`;
  контрактный тест CI не даёт scope-областям разойтись с `quality-gate`.
- Offline kit smoke и тест `/api/health` берут версию из единственного источника
  истины (`backend/app/__init__.py`), поэтому bump версии не рассинхронизирует
  релизную идентичность.

### Приёмка

- Региональная приёмка подтверждена владельцем на Windows (owner-attested,
  Gate A/B/C). Независимые артефакты и подписи operator/security/owner
  отслеживаются в [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/
  [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) и
  [#575](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/575).

## 1.0.0 - 2026-09-14

### Добавлено

- Режимы портала SOURCE и TARGET для физически изолированных контуров Harbor.
- Signed Offline Bundle Protocol v1 с подписями Ed25519 и контрольными суммами payload.
- Экспорт и импорт container images через Skopeo с проверкой digest.
- Экспорт и импорт Helm OCI charts с подписанной целостностью пакета, безопасной
  семантикой повторной передачи между разными реестрами и default-deny обработкой
  конфликтов.
- Ролевой доступ для ролей admin, operator и viewer.
- Постоянная история операций, immutable import receipts и отчёты CSV/PDF.
- Offline installation kit с заранее собранными образами, контрольными суммами и
  установкой одной командой для SOURCE или TARGET.
- Хелперы жизненного цикла: backup, restore, upgrade и uninstall.
- Scoped CI-гейты для backend, frontend, protocol/security, Compose и OCI
  интеграции; clean-host offline install и изолированный SOURCE→TARGET acceptance
  в job `Integration`, а также контракты Windows offline lifecycle на
  `windows-2022`. Реальный Windows Docker Desktop и региональная приёмка
  оставались внешними гейтами (issue #507).

### Безопасность

- Распаковка архивов, работа с signing keys, credentials реестров и пути передачи
  проверяются fail-closed.
- Harbor credentials, JWT secrets и приватные signing keys SOURCE не попадают в
  transfer и release архивы.
- Конфликтующие TARGET-теги или версии чартов не перезаписываются без явной
  авторизованной политики.

### Известные ограничения

- SOURCE и TARGET намеренно не связаны сетью; обращение с физическим носителем —
  ответственность оператора.
- Откат через несовместимые миграции базы данных может потребовать восстановления
  backup, сделанного до обновления.
- Digest Helm OCI manifest может измениться после push в другой реестр; для
  семантики повторной передачи используются подписанная целостность пакета чарта и
  проверенная SOURCE→TARGET provenance.
