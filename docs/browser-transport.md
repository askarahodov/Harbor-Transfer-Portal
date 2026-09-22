# Browser ↔ Portal transport security

**Статус:** обязательная deployment boundary для authenticated browser use.

Harbor Transfer Portal использует локальную HTTP-связь между frontend Nginx и backend внутри Compose network. Это не означает, что пароль пользователя или bearer JWT должны передаваться по открытой сети.

## Поддерживаемая production topology

Штатная схема для SOURCE и TARGET:

```text
Browser
   │ HTTPS
   ▼
site-managed TLS terminator / reverse proxy
   │ HTTP на доверенном локальном сегменте хоста
   ▼
127.0.0.1:PORTAL_HTTP_PORT
   │
   ▼
frontend Nginx → backend:8000
```

Release kit не содержит TLS private key или site certificate. Сертификат, private key, DNS name и TLS policy принадлежат площадке и настраиваются на внешнем reverse proxy/Nginx/HAProxy/другом утверждённом TLS terminator.

## Safe defaults

`.env.example` задаёт:

```text
PORTAL_HTTP_BIND=127.0.0.1
PORTAL_HTTP_PORT=8080
PORTAL_BROWSER_SCHEME=http
```

`PORTAL_HTTP_BIND=127.0.0.1` означает, что raw HTTP listener по умолчанию недоступен с других хостов. Он предназначен для bootstrap/diagnostics и как upstream локального TLS terminator.

Backend также fail-closed проверяет сочетание настроек: при `PORTAL_BROWSER_SCHEME=http` разрешён только `PORTAL_HTTP_BIND=127.0.0.1`. Попытка запустить plain HTTP на non-loopback interface отклоняется при загрузке configuration. Non-loopback bind допустим только с явным `PORTAL_BROWSER_SCHEME=https`.

Перед authenticated remote browser use настройте site TLS terminator и установите:

```text
PORTAL_BROWSER_SCHEME=https
```

после чего перезапустите Compose. Эта настройка является trusted deployment statement: backend использует её для security-sensitive browser attributes, включая `Secure` у краткоживущего export download cookie.

`PORTAL_BROWSER_SCHEME=https` нельзя выставлять, если пользователь фактически открывает Portal по HTTP: Secure cookie тогда закономерно не будет отправляться браузером по незащищённому каналу.

## Forwarded headers не являются trust source

Portal намеренно не определяет внешний scheme из client-supplied `X-Forwarded-Proto`.

Frontend Nginx:

- удаляет `X-Forwarded-Proto` перед backend;
- перезаписывает `X-Forwarded-For` непосредственным peer address вместо добавления пользовательской цепочки;
- не требует расширять Uvicorn proxy-header trust.

Поэтому прямой клиент не может заставить backend считать HTTP-запрос HTTPS только через поддельный forwarding header.

## TLS terminator на том же хосте

Предпочтительный вариант: TLS terminator слушает site HTTPS port, а upstream направлен на:

```text
http://127.0.0.1:8080
```

В `.env`:

```text
PORTAL_HTTP_BIND=127.0.0.1
PORTAL_HTTP_PORT=8080
PORTAL_BROWSER_SCHEME=https
```

Backend `:8000` штатным Compose не публикуется на host; frontend обращается к нему только через внутреннюю Compose network по `backend:8000`.

## TLS terminator на отдельном доверенном узле

Если организационная схема требует отдельный reverse proxy, loopback binding использовать нельзя. В этом случае явно задайте `PORTAL_HTTP_BIND` адресом выделенного внутреннего интерфейса хоста Portal и ограничьте firewall так, чтобы к HTTP listener мог обращаться только утверждённый TLS terminator.

Не используйте `0.0.0.0` как удобный production default. Если bind на широкий интерфейс всё же необходим по архитектуре площадки, сетевой ACL/firewall становится обязательной частью trust boundary.

При этом обязательно задайте:

```text
PORTAL_BROWSER_SCHEME=https
```

Иначе backend отклонит configuration. Backend не принимает решение о HTTPS по forwarding headers.

## Local diagnostic mode

Для локальной проверки на самом хосте допустим:

```text
PORTAL_HTTP_BIND=127.0.0.1
PORTAL_BROWSER_SCHEME=http
```

и доступ к `http://127.0.0.1:8080`.

Это bootstrap/diagnostic режим, а не штатный remote authenticated transport. Не вводите production credentials через HTTP endpoint, доступный другим узлам сети.

## Проверка после настройки

Проверьте:

1. HTTP listener Portal не доступен с недоверенных узлов напрямую.
2. Browser открывает только site HTTPS URL и видит ожидаемый сертификат.
3. `GET /api/health` через HTTPS работает через тот же reverse proxy.
4. Login проходит без mixed-content/network errors.
5. Export download ticket через HTTPS получает cookie с `Secure; HttpOnly; SameSite=Strict`.
6. Изменение клиентом `X-Forwarded-Proto` не меняет security behavior Portal.
7. Non-loopback bind с `PORTAL_BROWSER_SCHEME=http` не проходит startup validation.

## Что не относится к этой настройке

`PORTAL_BROWSER_SCHEME` не управляет TLS соединением Portal ↔ Harbor. Для local Harbor продолжает использоваться отдельная policy `HARBOR_VERIFY_TLS` и managed/private CA. Не отключайте Harbor TLS verification для исправления browser HTTPS.
