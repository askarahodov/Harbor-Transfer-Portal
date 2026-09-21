# Runtime SOURCE/TARGET mode

**Статус:** актуальный runtime/admin/offline contract.

Harbor Transfer Portal использует **один deployment**, который в каждый момент работает в одной роли: `SOURCE` или `TARGET`. Переключение роли выполняется через UI/runtime API и не требует rebuild или restart контейнеров.

Runtime mode — это роль текущего Portal instance. Он **не выбирает другой Harbor**, не переносит credentials между контурами и не меняет Bundle v1. Portal по-прежнему знает только свой настроенный локальный Harbor.

## Bootstrap и persistent state

`PORTAL_CONTOUR` используется только как bootstrap default для новой базы данных, в которой ещё нет persistent runtime mode.

При первом startup backend сохраняет в SQLite:

- `runtime.portal_mode` — текущий `SOURCE`/`TARGET`;
- `runtime.portal_mode_version` — monotonic revision, начальное значение `1`.

После этого persistent state authoritative. Изменение `PORTAL_CONTOUR` в environment и restart **не переопределяют** уже сохранённый runtime mode.

Каждое реальное переключение увеличивает `runtime.portal_mode_version`. No-op switch в уже активный mode не увеличивает revision.

## Переключение в UI

Operator/Admin может переключать mode через runtime mode switcher. Viewer видит текущий mode, но не может его менять.

Перед switch UI явно предупреждает, что незавершённые EXPORT/IMPORT operations будут отменены. После успешного switch UI показывает IDs автоматически отменённых operations; их полный terminal state остаётся доступен в History.

После backend-confirmed switch frontend использует authoritative runtime state и сразу меняет доступную навигацию/actions:

- `SOURCE` — export workspace и SOURCE signing controls;
- `TARGET` — import workspace и TARGET trusted-key controls.

Stale frontend responses из предыдущего mode не должны возвращать скрытые actions.

## Operation safety

Переключение mode и создание mode-bound export/import operation входят в одну process-local serialization boundary для v1 single-backend-instance + SQLite deployment.

Подтверждённый switch автоматически завершает незавершённые mode-bound operations как `CANCELLED`:

- состояния без выполняющегося worker (`CREATED`, `UPLOADED`, `DISCOVERED`, `READY`) закрываются сразу;
- выполняющиеся workers (`VALIDATING`, `RUNNING`, `PACKAGING`, `VERIFYING`, `IMPORTING`, `VERIFYING_TARGET`) получают cancellation, а authoritative mode меняется только после фактической остановки worker;
- Skopeo/Helm subprocess при coroutine cancellation принудительно завершается и Portal дожидается process exit;
- во время двухфазного switch действует pending-switch barrier: новый export/import старт получает `runtime_mode_switch_in_progress` и не может создать новую блокирующую operation.

Portal не считает ещё работающий worker «отменённым» только ради смены UI mode. Если worker нельзя безопасно остановить, switch завершается ошибкой и старый authoritative mode остаётся активным.

Каждая новая mode-bound operation сохраняет snapshot:

- `runtime_mode`;
- `runtime_mode_version`.

Это используется для recovery/diagnostics и не позволяет молча продолжить operation в несовместимом mode.

## Restart и upgrade

При restart backend сначала читает persistent runtime state, затем запускает recovery/background workers. Поэтому выбранный mode и revision сохраняются между restart.

Upgrade существующей базы выполняется обычным Alembic path. Migration `0007_runtime_mode_operation_snapshot` добавляет nullable snapshot fields к существующим operations, не переписывая историю. Для installation, где runtime metadata ещё отсутствует, первый startup после upgrade безопасно bootstrap-ит mode из `PORTAL_CONTOUR` с revision `1`; далее persistent state становится authoritative.

Legacy operation rows без snapshot остаются читаемыми. Snapshot может быть backfill-ен только в совместимом mode в предусмотренном recovery/start path; несовместимое продолжение не должно выполняться молча.

## Backup и restore

Runtime mode является частью SQLite persistent state. Корректный backup deployment должен включать database file вместе с остальным persistent application state.

Безопасный порядок для file-level SQLite backup:

1. остановить/quiesce backend, чтобы не копировать активную транзакцию;
2. сохранить SQLite database и остальные требуемые persistent paths;
3. выполнить restore в persistent volume целиком;
4. запустить backend и проверить `/api/runtime`;
5. убедиться, что восстановлены **и mode, и `runtime.portal_mode_version`**.

После restore `PORTAL_CONTOUR` не должен переопределять восстановленное значение. Например, backup с `TARGET` revision `2`, восстановленный при environment `PORTAL_CONTOUR=SOURCE`, обязан стартовать как `TARGET` revision `2`.

Key material имеет отдельный sensitive backup contract, описанный в [universal-mode-key-isolation.md](universal-mode-key-isolation.md). Runtime restore не объединяет SOURCE signing identity и TARGET trust semantics.

## Offline install

Для clean-host installation predictable default задаётся `PORTAL_CONTOUR` в `.env` до первого запуска. После первого successful startup роль переключается через UI и сохраняется в SQLite.

Offline kit не требует отдельного SOURCE/TARGET image или rebuild: один и тот же versioned deployment используется последовательно в обеих ролях. При upgrade/restore необходимо сохранять persistent `data`/SQLite state согласно offline lifecycle procedure.

## Audit

Реальное переключение создаёт `runtime_mode_changed` audit event с:

- `previous`;
- `current`;
- `mode_version`;
- actor из authenticated request.

No-op switch не создаёт ложное изменение. Event не содержит Harbor credentials, PEM или другого secret material.

## Expected lifecycle

Один running Portal поддерживает следующий сценарий без Docker restart:

1. `SOURCE` — доступен export workspace, TARGET import endpoint fail-closed;
2. switch `SOURCE -> TARGET` — revision увеличивается;
3. `TARGET` — доступен import workspace, SOURCE export endpoint fail-closed;
4. switch `TARGET -> SOURCE` — revision снова увеличивается;
5. SOURCE workspace снова доступен на том же process/deployment.

Это переключение рабочей роли локального Portal. Оно не означает сетевой связи между физически изолированными Harbor-контурами.

## Verification

Lifecycle contract покрывается scoped tests:

- upgrade `0006 -> head` и runtime metadata bootstrap/backfill;
- bootstrap-only `PORTAL_CONTOUR` после restart;
- database backup/restore с сохранением mode + revision;
- concurrent switches без split-brain;
- automatic cancellation of READY/in-flight blockers before switch;
- pending-switch barrier against concurrent operation starts;
- active-operation guard и operation snapshot tests;
- SOURCE/TARGET API guards;
- SOURCE -> TARGET -> SOURCE cycle в одном Portal process;
- frontend runtime store/mode switcher/key-control regressions;
- existing security, key isolation и operation barrier suites.

Bundle v1 не изменяется этим lifecycle contract.
