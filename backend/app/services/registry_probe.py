"""Docker Registry v2 connectivity probe, independent of repository contents."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.services.harbor_settings import EffectiveHarborSettings


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    code: str
    message: str


def _result(code: str, message: str, ok: bool = False) -> ProbeResult:
    return ProbeResult(ok=ok, code=code, message=message)


def _bearer_parameters(challenge: str) -> dict[str, str] | None:
    if not challenge.lower().startswith("bearer "):
        return None
    import re

    params = dict(re.findall(r'([a-zA-Z]+)="([^"]*)"', challenge[7:]))
    return params if "realm" in params else None


async def probe_docker_v2(
    profile: EffectiveHarborSettings,
    *,
    connect_timeout: float,
    read_timeout: float,
) -> ProbeResult:
    """Probe /v2/ and validate credentials when challenged, without image lookup.

    Bearer token realms are limited to the *same HTTPS origin* as the registry:
    never forward profile credentials to an untrusted challenge URL.
    """
    if not profile.url:
        return _result("registry_invalid_endpoint", "Не настроен Docker Registry v2 endpoint")
    parsed = urlsplit(profile.url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return _result("registry_invalid_endpoint", "Укажите корневой адрес Docker Registry v2, не Nexus UI")
    verify: bool | str = profile.verify_tls
    if profile.verify_tls and profile.ca_file:
        verify = str(profile.ca_file)
    auth = (
        httpx.BasicAuth(profile.username, profile.password)
        if profile.username is not None and profile.password is not None
        else None
    )
    try:
        async with httpx.AsyncClient(
            verify=verify,
            timeout=httpx.Timeout(\n                connect=connect_timeout, read=read_timeout,\n                write=read_timeout, pool=connect_timeout,\n            ),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(profile.url.rstrip("/") + "/v2/", auth=auth)
            if response.status_code == 200:
                return _result("registry_connection_ok", "Docker Registry v2 доступен", True)
            if response.status_code == 403:
                return _result("registry_forbidden", "Реестр запретил доступ")
            if response.status_code != 401:
                return _result("registry_invalid_endpoint", "Endpoint не отвечает как Docker Registry v2")
            challenge = response.headers.get("www-authenticate", "")
            if not challenge:
                return _result("registry_invalid_endpoint", "Нет Docker Registry v2 auth challenge")
            if not auth:
                return _result(
                    "registry_auth_required", "Docker Registry v2 доступен, учётные данные не настроены"
                )
            if challenge.lower().startswith("basic "):
                return _result("registry_auth_failed", "Реестр отклонил учётные данные")
            parameters = _bearer_parameters(challenge)
            if parameters is None:
                return _result(
                    "registry_auth_unsupported", "Неизвестный способ авторизации Docker Registry v2"
                )
            realm = urlsplit(parameters["realm"])
            if (
                realm.scheme != parsed.scheme
                or realm.netloc != parsed.netloc
                or realm.username
                or realm.password
                or not realm.path.startswith("/")
                or realm.fragment
            ):
                return _result(
                    "registry_auth_unsupported", "Auth realm требует отдельной проверки безопасности"
                )
            token_response = await client.get(
                parameters["realm"],
                params={k: v for k, v in parameters.items() if k in {"service", "scope"}},
                auth=auth,
            )
            if token_response.status_code in {401, 403}:
                return _result("registry_auth_failed", "Реестр отклонил учётные данные")
            if token_response.status_code != 200:
                return _result(
                    "registry_auth_unsupported", "Не удалось проверить авторизацию Docker Registry v2"
                )
            try:
                payload = token_response.json()
                token = payload.get("token") or payload.get("access_token")
            except (ValueError, AttributeError):
                token = None
            if not isinstance(token, str) or not token:
                return _result(
                    "registry_auth_unsupported", "Auth service не вернул Docker Registry token"
                )
            verified = await client.get(
                profile.url.rstrip("/") + "/v2/",
                headers={"Authorization": "Bearer " + token},
            )
            if verified.status_code == 200:
                return _result(
                    "registry_connection_ok", "Docker Registry v2 доступен, авторизация подтверждена", True
                )
            if verified.status_code == 403:
                return _result("registry_forbidden", "Реестр запретил доступ")
            return _result("registry_auth_failed", "Авторизация Docker Registry v2 не подтверждена")
    except httpx.TimeoutException:
        return _result("registry_unavailable", "Реестр не ответил вовремя")
    except (httpx.ConnectError, httpx.NetworkError):
        return _result("registry_unavailable", "Не удалось подключиться к реестру")
    except (httpx.InvalidURL, ValueError, OSError, ssl.SSLError):
        return _result("registry_tls_failed", "Ошибка TLS/CA или конфигурации подключения")
    except httpx.HTTPError:
        return _result("registry_unavailable", "Ошибка обмена с Docker Registry v2")
