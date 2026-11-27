# Harbor Transfer Portal v1.1.0 — release notes

Harbor Transfer Portal v1.1.0 расширяет v1.0.0 **Nexus Docker Registry v2** и
**classic Helm HTTP** как SOURCE/TARGET реестрами, сохраняя существующий
Harbor workflow и формат signed Offline Bundle.

## Что нового по сравнению с v1.0.0

- портал работает с **Harbor** и **Nexus** registry-профилями в одном контуре;
- **Nexus Docker Registry v2**: browse/inspect точного repository+tag, экспорт
  через Skopeo, импорт в TARGET через Docker v2 connector;
- **classic Helm HTTP** (не OCI): `index.yaml`/`.tgz` hosted-репозитории,
  запись только в точный hosted repository с fail-closed проверкой;
- отдельные URL Docker connector и Nexus REST/Search origin; профиль задаёт
  repository, credential, TLS verification и custom CA;
- signed bundle хранит registry origin (Bundle v1.1) с сохранением обратной
  совместимости с Harbor v1.0 metadata;
- SOURCE → подписанный bundle → физически изолированный TARGET handoff,
  receipt с digest и проверкой соответствия до мутации TARGET;
- type-aware UI: подписи и действия профилей зависят от типа реестра.

## Улучшения, общие для реестров

- атомарная публикация immutable import receipt; успешный receipt доступен только
  после перехода operation в `COMPLETED`; сбой публикации — typed
  `import_receipt_publish_failed`; `skip_conflicts` фиксируется во всех путях;
- компактный `skopeo inspect` (`--no-tags` + ограниченный `--format`) — устойчив
  к репозиториям с тысячами RepoTags; явная ошибка `skopeo_output_truncated`;
- корректные Helm transport-флаги для Nexus OCI: `--plain-http` для `http://`,
  `--insecure-skip-tls-verify` только при отключённой проверке, `--ca-file` для
  корпоративного CA;
- Harbor connection probe выполняется вне event loop;
- pytest 9.2 / minimatch 11: закрыты Dependabot-алерты;
- CI: Nexus legacy Docker v2 E2E выполняется scoped-шагами в `Integration`,
  clean-host offline qualification и isolated SOURCE→TARGET acceptance входят в
  обязательные проверки; контрактный тест `tools/test_ci_workflow_contract.py`
  не даёт scope-областям разойтись с `quality-gate`.

## Идентичность release

Running product version доступна через `/api/health`, отображается в
authenticated UI и записывается в SOURCE bundle metadata. Offline kit хранит ту же
версию в `release-version.txt` и `release-manifest.json`.

## Приёмка

Региональная приёмка (реальные версии/редакции Nexus, корпоративные TLS/CA/proxy,
per-repo ACL, физическая изоляция и media handoff) **подтверждена владельцем**
(owner-attested, Windows). Независимые артефакты, отказные сценарии и подписи
operator/security/owner фиксируются по
[шаблонам приёмки](acceptance-evidence-templates.md) и
[региональному runbook](nexus-regional-acceptance-runbook.md). Owner-attested
подтверждение не подменяет независимую верификацию: до приложения артефактов
соответствующие gate остаются `NOT RUN` / без подписей.

## Совместимость

- Harbor workflow, Signed Bundle Protocol v1 и таблицы операций совместимы с
  v1.0.0; существующие Harbor-профили продолжают работать без изменений.
- Миграции базы данных применяются автоматически при старте; перед обновлением
  сделайте backup (см. `deploy/offline/README.md`).
