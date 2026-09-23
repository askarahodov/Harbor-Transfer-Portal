# Настройки Portal

**Статус:** актуальная документация экрана `/settings` для роли `admin`.

Экран **Настройки** управляет server-side конфигурацией текущей installation. Он не является
локальным browser preference screen: изменения выполняются через backend API, проходят
server-side validation и там, где это предусмотрено, отражаются в audit.

Связанные подробные документы:

- [Harbor profiles](harbor-profiles.md);
- [Signing и trusted-key management](key-management.md);
- [Политики переноса](transfer-policies.md);
- [Storage retention](storage-retention.md);
- [Руководство администратора](admin-guide.md).

## Доступ :id=access

Route `/settings` доступен только роли `admin`. Ограничение дублируется backend RBAC:
скрытие экрана во frontend не является security boundary.

В заголовке Settings отображается текущий runtime contour. SOURCE/TARGET влияет прежде
всего на readiness и key-management controls, но не превращает Settings в отдельную
конфигурацию другого физического Harbor.

## Harbor profiles :id=harbor-profiles

Верхняя широкая карточка **Harbor profiles** показывает именованные Harbor profiles,
legacy fallback profile и безопасные metadata:

- display name;
- URL;
- username, если он задан;
- TLS verification state;
- наличие credential;
- наличие custom CA;
- enabled/disabled state;
- legacy-fallback/default state.

Admin может создать дополнительный profile, изменить его metadata, проверить соединение,
обновить credential, установить/заменить custom CA, включить/отключить profile и удалить
не-default profile, если backend разрешает mutation.

Credential и существующий CA content не подставляются обратно в форму и не возвращаются
API как plaintext/PEM. Поле Custom CA используется только для новой установки/замены;
удаление existing CA выполняется отдельным действием в строке profile.

Disabled profile остаётся в management list, но не возвращается safe selectable API и не
предлагается в новых Export/Import workflows. Legacy fallback нельзя отключить через UI:
сначала выберите другой fallback. Backend повторно проверяет эти ограничения.

### Legacy fallback Harbor

Selector в Settings задаёт **legacy fallback** для клиентов, которые не передают explicit
`profile_id`. Он не определяет Harbor для нового browser transfer workflow.

В актуальном UI operator/admin выбирает Harbor непосредственно:

- в SOURCE **Отправка** — до browse/preview/export;
- в TARGET **Приём** — до upload/discovery/preview/import.

Backend фиксирует `profile_id`, display name и URL snapshot в operation. После создания
operation выбор блокируется и worker использует operation-bound profile. Последующее
изменение legacy fallback не перенаправляет такую operation на другой Harbor.

Legacy fallback сохраняется для backward compatibility и административной диагностики.
Подробный contract — [harbor-profiles.md](harbor-profiles.md).

## First-run readiness :id=readiness

Карточка **First-run readiness** проверяет минимальные условия текущего runtime contour:

- доступность Harbor;
- в SOURCE — наличие signing identity;
- в TARGET — наличие хотя бы одного enabled trusted SOURCE key.

Кнопка **Обновить readiness** повторно запрашивает backend state. Readiness — это
операционная подсказка; конкретная export/import mutation всё равно выполняет собственные
server-side preflight checks.

## Default Harbor profile :id=default-harbor

Карточка **Default Harbor profile** сохраняет backward-compatible bootstrap profile.

Доступны:

- URL локального Harbor;
- service account / username;
- TLS verification flag;
- connection test.

Этот блок не является editor любого выбранного дополнительного profile. Дополнительные
profiles изменяются в карточке **Harbor profiles** выше.

Отключение TLS verification показывается как warning. Для штатной эксплуатации
предпочтителен корректный trusted CA.

## Credential и custom CA :id=harbor-secrets

Отдельный блок **Credential** позволяет установить или ротировать secret default Harbor
profile. Существующее значение не выдаётся обратно frontend.

Блок **Пользовательский CA** принимает PEM/CRT CA bundle и позволяет удалить managed CA.
После удаления backend может использовать deployment fallback, если он настроен.

Secrets и CA paths не должны попадать в screenshots, issue, audit metadata или
repository files.

## Signing и trust keys :id=keys

`KeyManagementPanel` меняет доступные действия в зависимости от contour.

В SOURCE admin управляет signing identity и staged rotation. В TARGET admin управляет
trusted SOURCE public keys и trust-package enrollment.

Private SOURCE signing key через normal API/UI не возвращается. Полный lifecycle,
confirmation requirements и overlap rotation описаны в
[key-management.md](key-management.md).

## Политики переноса :id=transfer-policies

Широкая карточка **Политики переноса** управляет runtime policy values:

- разрешением explicit overwrite конфликтов на TARGET;
- browser upload limit;
- максимальным размером Bundle archive;
- максимальным extracted size;
- максимальным количеством archive members;
- обязательным disk reserve;
- configured operation concurrency;
- retention/cleanup;
- TARGET destination mapping defaults.

Backend валидирует значения. UI показывает effective concurrency и
`restart_required_fields`, если конкретное изменение начинает действовать только после
restart backend.

Подробные semantics и precedence: [transfer-policies.md](transfer-policies.md).

## Очистка transfer storage :id=retention

Подраздел **Очистка transfer storage** управляет:

- сроком хранения готовых SOURCE bundles;
- сроком хранения failed/partial TARGET bundles;
- периодом cleanup check.

UI показывает значения в днях/минутах, backend contract хранит их в секундах.

Текущие default values:

```text
SOURCE готовые пакеты:       7 суток
TARGET failed/partial:       7 суток
Проверка cleanup:            60 минут
```

Изменение этих policy values применяется без restart backend. Очистка payload storage не
удаляет persisted History, receipts и audit evidence.

Подробности: [storage-retention.md](storage-retention.md).

## TARGET mapping defaults :id=target-mapping

Подраздел **TARGET mapping defaults** задаёт default destination projects для Container
Images и Helm Charts, а также явные пары:

```text
source-project=target-project
```

UI показывает текущую mapping policy revision. Новые destination plans фиксируют revision;
последующее изменение global defaults не должно переписывать уже подтверждённый plan.

Пустые defaults не создают скрытый mapping: unmapped artifact остаётся fail-closed.

## Сообщения и ошибки :id=feedback

Успешные mutations отображают status message. Backend validation/conflict возвращается как
error message и не должен подменяться frontend optimistic state.

Типичные примеры:

- active Harbor нельзя переключить, пока есть blocking operations;
- referenced/default profile защищён от недопустимого удаления;
- malformed mapping line отклоняется;
- credential должен быть непустым;
- CA должен пройти backend validation;
- key/trust mutation может требовать explicit confirmation.

При ошибке сначала исправляйте указанное server-side condition, а не изменяйте persistent
SQLite/files вручную.

## См. также :id=related

- [Руководство администратора](admin-guide.md)
- [Harbor profiles](harbor-profiles.md)
- [Signing и trusted-key management](key-management.md)
- [Политики переноса](transfer-policies.md)
- [Storage retention](storage-retention.md)
- [Runtime mode](runtime-mode.md)
- [Troubleshooting](troubleshooting.md)
