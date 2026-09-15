# Destination plan integrity и TOCTOU boundary

Этот документ фиксирует security/correctness contract для TARGET destination mapping (P8.2.4) и дополняет `import-orchestration.md`.

## Две разные идентичности plan

`plan_id` — стабильная identity конкретного bundle + resolved source→TARGET mapping. Она не меняется только из-за того, что между двумя Preview изменилось наблюдаемое состояние TARGET (`NEW`/`SAME`/`CONFLICT`). Это позволяет безопасно связывать повторный Preview с тем же destination intent.

`plan_hash` — canonical SHA-256 конкретного подтверждённого snapshot. Он дополнительно связывает operation id, delivery id, planner actor, bundle SHA-256, timestamp, normalized mapping, source/final TARGET identities и наблюдаемое TARGET состояние. Persisted policy v2 с изменённым hash/content отвергается fail-closed.

Execute принимает только уже сохранённый `destination_plan_id`; executor не принимает новый mapping payload и не пересчитывает destination самостоятельно.

## Local-Harbor-only invariant

User mapping задаёт только локальные project/path components. Registry authority берётся из настроенного local Harbor. SOURCE repository/path проходит normalization; scheme/authority injection, traversal, backslash и malformed components блокируются до mutation.

После mapping все artifacts сравниваются по фактическим OCI registry coordinates. Если два source artifacts, включая image и Helm chart разных типов, дают один repository+tag/version, весь неоднозначный plan становится invalid до Harbor mutation.

## Explicit создание отсутствующего Harbor project

Отсутствующий TARGET project не создаётся автоматически ни при Preview, ни в Import executor. Если destination plan возвращает `import_destination_project_missing`, Portal показывает отдельное административное действие:

- endpoint `POST /api/harbor/projects` доступен только роли `admin` и только в authoritative runtime mode `TARGET`;
- request принимает только нормализованное имя project, флаг `public` и необязательный `operation_id` для audit correlation; дополнительные поля запрещены, поэтому request не может подменить registry URL, credentials или local Harbor authority;
- UI требует ввести точное имя отсутствующего project перед отправкой create-запроса;
- штатный UI создаёт private project (`public=false`);
- существующий project возвращает idempotent `created=false`; race, когда другой actor создаёт project между exists-check и Harbor POST, также нормализуется в `created=false` после повторной exact проверки;
- Harbor authentication/permission/availability ошибки возвращаются через существующий safe error contract, без upstream body и credentials;
- операция Import после project creation остаётся в прежнем состоянии. Клиент обязан заново построить destination plan; только отдельный последующий Execute может начать Import.

Mutation удерживает runtime-mode guard на время local Harbor action, поэтому переключение SOURCE/TARGET не может пересечься с project creation. Audit event `harbor.project.create` хранит actor, local Harbor host, project, public flag, result и optional operation correlation; raw credential material и upstream error text не сохраняются.

Operator не получает create action: UI сообщает точное имя отсутствующего project и предлагает обратиться к администратору. Viewer также не получает mutation capability.

## Pre-mutation revalidation

Persisted Preview не считается актуальным доказательством TARGET state на момент import. Execution worker непосредственно перед mutation каждого mapped artifact повторно вызывает target inspection:

- target отсутствует → mutation разрешена;
- identity совпадает → `SKIPPED`, без push;
- другой digest/content identity → `CONFLICT`, default deny;
- inspection unavailable/unknown/error → fail closed, без push.

Bundle SHA-256, signature/schema/payload verification и delivery id также проверяются повторно до Harbor mutation.

## Остаточное TOCTOU-окно

OCI Registry/Harbor API не предоставляет Portal атомарный compare-and-set, который объединял бы `inspect` и последующий Skopeo/Helm push в одну транзакцию. Поэтому после последней успешной inspection и до фактического push остаётся минимальное внешнее race window: другой actor/process теоретически может изменить тот же TARGET reference.

Portal не заявляет устранение этого ограничения. Риск уменьшается тем, что inspection выполняется непосредственно перед mutation, silent overwrite по умолчанию запрещён, Skopeo/Helm выполняют собственные verification checks, а post-push TARGET identity сохраняется в operation/receipt. Полностью atomic cross-process protection потребовала бы registry-side conditional mutation primitive; при его отсутствии поведение остаётся fail-closed на всех состояниях, которые Portal успевает наблюдать до mutation.

## Audit/receipt

Audit и receipt сохраняют `plan_id`, `plan_hash`, bundle SHA-256, source identity и фактические final TARGET references. Credentials, auth material и private signing keys в destination plan/audit/receipt не записываются.
