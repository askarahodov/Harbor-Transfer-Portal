# Nexus Repository — актуальные требования и состояние интеграции

> **Статус на 2026-11-06: реализация в draft PR #451, региональная приёмка НЕ выполнена (NO-GO).**
> Этот документ описывает согласованный scope и проверяемые критерии. Он не даёт
> разрешения на merge в `main`, production deployment или работу в региональных
> контурах без согласования.
>
> Источник актуальных продуктовых требований: [#435](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/435),
> [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).
> Реализация и CI evidence: [draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451).
> Порядок операторской приёмки: [nexus-regional-acceptance-runbook.md](nexus-regional-acceptance-runbook.md).

## 1. Продукт и границы

Harbor Transfer Portal должен передавать контейнерные Docker-образы и **обычные
Helm HTTP charts** между физически изолированными SOURCE и TARGET, в том числе
Harbor↔Nexus и Nexus↔Nexus. Передача исключительно через **подписанный Offline
Bundle + SHA-256 sidecar + signed handoff**; в TARGET проверяются подпись
публичным ключом SOURCE, digest и receipt. Прямая сеть между контурами,
общая база данных и общий приватный ключ подписи недопустимы.

- В реальных Nexus используются разные версии и редакции OSS/Pro; нельзя
  предполагать одну версию. 3.69.0-02 OSS — только один disposable baseline,
  а 3.70.1 — дополнительная CI-проверка, не подтверждение версий регионов.
- Поддерживаемые форматы в scope: **Docker v2 и Helm HTTP**, включая несколько
  repositories типа hosted/proxy/group с раздельными service-account ACL.
- **Helm OCI в Nexus не используется и не должен вызываться.** Запрещены
  `helm push oci://` и автоматическое преобразование обычных Helm
  репозиториев в OCI. Harbor Helm OCI остаётся отдельным существующим сценарием.
- Maven, npm, PyPI, NuGet и другие форматы не входят в согласованный scope.
- Любая недоступность (401/403) одного repository не должна раскрывать
  скрытые имена или блокировать разрешённые независимые репозитории.

## 2. Транспорт и профили

| Область | SOURCE / чтение | TARGET / запись |
| --- | --- | --- |
| Nexus Docker | Отдельный HTTPS Docker v2 connector, inspect точного repository:tag и manifest SHA-256 | Skopeo через Docker v2; план NEW/SAME/CONFLICT, без неявного overwrite |
| Nexus Helm HTTP | HTTPS Nexus UI origin, `/repository/<repo>/index.yaml`, `.tgz` и SHA-256 | Только подтверждённый Helm **hosted** через Nexus Components API, без overwrite |
| Harbor | Существующие Harbor REST, проекты/robot identities, Registry v2 и Helm OCI там, где разрешено | Существующая Harbor TARGET-модель и проверяемые destination plans |

Docker connector и Nexus UI/REST origin — **разные адреса**. Каждый Docker
repository/connector может требовать отдельного Portal-профиля, CA и service
identity. Nexus inventory/REST Search не являются доказательством фактических
Docker pull и push прав. Legacy Harbor profiles по умолчанию остаются типом
`harbor`; тип Nexus явно задаётся в сохранённом профиле, а не выводится
из имени, URL или названия артефакта.

Система определяет доступные capabilities по реальным endpoint и ACL,
а не по жёсткому номеру версии. Для закрытого Docker browse поддерживается
точная проверка `repository:tag` с digest; отсутствующие или неразрешённые
repositories не допускают обхода прав через глобальный административный API.

## 3. Правила безопасности и корректности

1. Все сетевые операции используют валидный HTTPS origin, проверку CA/SAN;
   редиректы, TLS bypass, утечку credentials в report/log и неявные proxy
   переменные запрещать.
2. SOURCE повторно проверяет digest перед материализацией. Изменение tag или
   checksum во время экспорта приводит к явной ошибке, а не к подписанию
   устаревшего выбора.
3. Helm index должен иметь однозначные chart/version/digest и безопасные
   package paths; `.tgz` валидируется по SHA-256 и Chart.yaml. Отказ
   отдельного repository обрабатывается независимо.
4. TARGET объявляет Helm version отсутствующей по 404 `index.yaml`
   **только после подтверждения, что metadata относится к запрошенному
   Helm hosted repository**. Другая `name`, `format` или `type` не
   является доказательством `ABSENT` и запрещает операцию.
5. Запись разрешена только в явный approved hosted repository с
   подтверждённой TARGET identity. Proxy/group — read-only.
   Конфликт digest/version не перезаписывается.
6. При сбое SOURCE не публикует частичный bundle: первичный типизированный
   error code сохраняется, незавершённые артефакты получают abort status.
7. TARGET отклоняет tampered archive, sidecar, handoff, неверную подпись,
   неопознанный digest и запрещённый overwrite до передачи в Nexus.

## 4. Готовность и доказательства

**Подготовлено в draft #451 (не в `main`):** SOURCE/TARGET Docker и
обычный Helm HTTP, signed bundle/handoff/receipt, Nexus↔Nexus и Harbor↔Nexus
E2E, per-repository protection, UI, read-only regional preflight и runbook.
Запуски CI на disposable Nexus 3.69.0/3.70.1 и disposable реальном Harbor
Core 2.14.6 доказывают тестовые контуры, но не фактическую региональную
эксплуатацию. Точные commit SHA и соответствующие runs фиксируются в
описании PR #451; результаты после новых изменений перепроверяются.

**Для завершения #434/#436 обязательны:**
- фактические версии/редакции Nexus и Harbor, deployed Portal SHA, DNS/TLS/CA,
  proxy/realm, service identities и per-repository ACL SOURCE/TARGET;
- approved namespace и операторский read-only preflight из разрешённой сети;
- авторизованный **физический** перенос signed bundle/sidecar/handoff с
  TARGET digest, receipt, replay/conflict, tamper и отказными сценариями;
- reviewer/security и owner approvals, подтверждение безопасного rollback;
- успешные финальные CI/E2E на одном интеграционном SHA.

**Вердикт: NO-GO до региональной приёмки.** Отдельная ошибка Harbor
экспорта [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422)
не считается закрытой без traceback развёрнутого backend и RCA.
