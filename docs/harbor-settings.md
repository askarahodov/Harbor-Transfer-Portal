# Настройка локального Harbor

## Назначение

Каждая установка Harbor Transfer Portal знает только один Harbor — локальный Harbor своего контура `SOURCE` или `TARGET`. В API и UI отсутствуют поля для учётных данных противоположного контура.

Настройки Harbor разделены на две категории:

- **обычная конфигурация** — URL, username/service account и политика TLS; runtime-значения сохраняются в SQLite в `setting_metadata`;
- **секреты и доверенный CA** — хранятся в server-owned файлах внутри persistent `/app/data`, а не в SQLite и не возвращаются в браузер.

## Приоритет конфигурации

При каждом Harbor-запросе backend собирает эффективную конфигурацию в следующем порядке.

### URL, username и TLS

1. runtime override, сохранённый администратором через `/api/settings/harbor`;
2. bootstrap-значение из `HARBOR_URL`, `HARBOR_USER`, `HARBOR_VERIFY_TLS`.

### Credential

1. runtime credential `/app/data/secrets/harbor-password`, созданный через admin API/UI;
2. файл, указанный в `HARBOR_PASSWORD_FILE`;
3. bootstrap `HARBOR_PASSWORD` из process environment.

`HARBOR_PASSWORD` оставлен только для совместимости bootstrap. Для новой установки предпочтителен file-backed secret или последующая ротация через UI.

### Custom CA

1. runtime CA `/app/data/secrets/harbor-ca.crt`, если администратор загрузил его через UI;
2. bootstrap `HARBOR_CA_FILE`, если runtime override не задан;
3. системное trust store, если дополнительный CA не настроен.

Явное удаление runtime CA через UI сохраняет режим `none`, поэтому bootstrap CA не включается обратно неожиданно.

## API администратора

Все endpoints ниже требуют роль `admin` на backend. Router guard во frontend является только дополнительным UX-ограничением и не заменяет server-side RBAC.

```text
GET    /api/settings/harbor
PATCH  /api/settings/harbor
PUT    /api/settings/harbor/credential
PUT    /api/settings/harbor/ca
DELETE /api/settings/harbor/ca
POST   /api/settings/harbor/test
```

### Safe read

`GET /api/settings/harbor` возвращает только:

- текущий contour;
- URL;
- username/service account;
- `verify_tls`;
- `credential_configured: true|false`;
- `custom_ca_configured: true|false`;
- безопасный источник CA: `runtime`, `bootstrap` или `null`.

Пароль/token, содержимое CA и filesystem path credential не возвращаются.

### PATCH semantics

Обычное сохранение URL/user/TLS не затрагивает существующий credential. Ротация секрета вынесена в отдельный `PUT /credential`, поэтому изменение URL не может случайно очистить пароль/token.

### TLS

TLS verification включена по умолчанию. Отключение возможно только явным admin-действием и пишет warning без секретных значений. Для Harbor с частным PKI предпочтительно установить доверенный CA, а не отключать проверку сертификата.

### Custom CA

UI принимает PEM/CRT файл. Backend проверяет, что содержимое может быть загружено как CA в Python SSL context, после чего атомарно публикует его в фиксированный server-owned path. Пользователь не передаёт произвольный путь файловой системы.

### Проверка подключения

`POST /api/settings/harbor/test` использует ту же эффективную конфигурацию, что и обычный Harbor browse API. Ответ содержит только безопасные version/auth mode. Upstream body и credential не проксируются; ошибки нормализуются в portal `error.code`, например `harbor_auth_failed` или `harbor_unavailable`.

## Файловые права

Backend image заранее создаёт `/app/data/secrets` с mode `0700`. Runtime credential и runtime CA публикуются через temporary file + `fsync` + atomic replace и получают mode `0600`.

Файлы находятся в persistent volume `portal-data`, поэтому обычный restart контейнера не стирает их. `docker compose down -v` удаляет volume и вместе с ним runtime secrets.

## Audit

Harbor admin mutations создают persistent audit events:

- `harbor.settings.updated`;
- `harbor.credential.rotated`;
- `harbor.ca.updated`;
- `harbor.ca.cleared`.

В metadata фиксируются только имена изменённых полей. Старые и новые password/token/CA contents не сохраняются. Полный audit/history UX развивается отдельно в задаче #21; текущая модель нужна уже сейчас, чтобы изменения security-sensitive настроек не оставались без следа.

## Ограничения v1

- runtime credential ротация через UI создаёт managed file внутри `portal-data`; отдельное удаление credential пока не является пользовательской операцией;
- CA upload предназначен для доверенного CA bundle, а не для клиентского приватного ключа;
- portal не управляет Harbor service account permissions — права учётной записи настраиваются в локальном Harbor;
- настройки этого экрана не создают и не предполагают сетевой путь к Harbor противоположного контура.
