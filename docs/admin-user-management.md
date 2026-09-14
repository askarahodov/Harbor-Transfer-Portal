# Управление локальными пользователями

**Статус:** актуальная инструкция для admin console Harbor Transfer Portal v1.

Этот экран управляет только локальными учётными записями конкретного экземпляра Portal. Он не создаёт пользователей Harbor и не синхронизирует идентичности между SOURCE и TARGET.

## Доступ

Раздел **«Пользователи»** доступен только роли `admin` по маршруту `/users`.

Backend остаётся authoritative boundary:

```text
GET   /api/users
POST  /api/users
PATCH /api/users/{user_id}
```

`operator` и `viewer` получают `403` на mutation/read admin API независимо от скрытия пункта меню во frontend.

## Что показывает список

Для каждой локальной учётной записи отображаются:

- username;
- роль `admin`, `operator` или `viewer`;
- active/inactive status;
- время создания;
- время последнего успешного входа либо `Никогда`.

Password и password hash не входят в response contract и не отображаются.

## Создание пользователя

Admin задаёт:

1. username;
2. начальный пароль длиной не менее 12 символов;
3. роль.

Username нормализуется backend в lowercase/trim. После успешного создания пароль очищается из формы и не может быть прочитан через API.

## Роли

- `viewer` — read-only product access согласно backend RBAC;
- `operator` — transfer actions плюс viewer permissions;
- `admin` — operator permissions плюс settings/user administration.

Frontend role controls служат UX-ограничением. Серверная проверка роли выполняется для каждого admin endpoint.

## Enable/disable и смена роли

Изменение роли и active status требует явного подтверждения в UI.

Portal не поддерживает hard-delete локальных пользователей, потому что persisted operation/audit records могут ссылаться на их identity.

### Защита последнего активного admin

Backend отклоняет с `409` попытку:

- деактивировать единственного активного пользователя с ролью `admin`;
- изменить его роль на `operator` или `viewer`.

Сначала создайте/активируйте второй admin и только затем изменяйте первого. Это server-side invariant и не зависит от browser UI.

## Смена пароля

Admin вводит новый пароль длиной не менее 12 символов и отдельно подтверждает действие. Старое и новое значения не возвращаются API и не записываются в audit metadata.

Изменение password hash вступает в силу для следующей аутентификации. Действующий bearer token пользователя продолжает подчиняться текущей token-expiry модели; отключение пользователя блокирует его существующий token при следующей backend authorization check.

## Audit

Создание пользователя фиксируется как `user.created`, изменение роли/status/password — как `user.updated`.

Audit metadata содержит только:

- target user id;
- target username;
- имена изменённых полей.

Password/password hash и другие secret values в persisted audit не записываются.

## Операционная рекомендация

Для каждого рабочего пользователя используйте отдельную локальную учётную запись. Не раздавайте общий admin credential операторам. Перед деактивацией сотрудника убедитесь, что нужные operation/history records сохранены; hard-delete для этого не требуется.
