import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt

from telok.ai import credentials
from telok.ai.contracts import ProviderError

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK = "http://127.0.0.1:8481/auth/callback"


def safe_request(method, url, **kwargs):
    try:
        with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, url, **kwargs)
        if response.status_code != 200:
            code = ""
            try:
                payload = response.json()
                error = payload.get("error")
                code = error.get("code", "") if isinstance(error, dict) else error
            except Exception:
                pass
            terminal = {
                "invalid_grant",
                "invalid_refresh_token",
                "refresh_token_expired",
                "refresh_token_invalidated",
                "refresh_token_reused",
            }
            raise ProviderError(
                "REAUTH_REQUIRED" if code in terminal else "AUTH_UNAVAILABLE",
                "Требуется повторный вход."
                if code in terminal
                else "Сервис авторизации временно недоступен.",
            )
        return response.json()
    except httpx.HTTPError:
        raise ProviderError("NETWORK", "Нет соединения с сервисом авторизации.") from None


def start(new_profile=False):
    with credentials.locked():
        data = credentials.read()
        data.setdefault("host_id", "urn:uuid:" + str(uuid4()))
        profile = data.get("profiles", {}).get(data.get("active_profile"), {}) if not new_profile else {}
        verifier, state, nonce = (secrets.token_urlsafe(48) for _ in range(3))
        client_id = profile.get("client_id", "dynamic_agent_client")
        data["pending_oauth"] = {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "client_id": client_id,
            "subject": profile.get("subject"),
            "expires": time.time() + 600,
        }
        credentials.write(data)
    params = dict(
        client_id=client_id,
        ext_agent_host_id=data["host_id"],
        response_type="code",
        redirect_uri=CALLBACK,
        scope=SCOPE,
        resource=RESOURCE,
        state=state,
        nonce=nonce,
        code_challenge_method="S256",
        code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode(),
    )
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "Telok"
    # No id_token_hint in returned URL: avoids retaining an identity token in browser history.
    return AUTH + "/api/accounts/authorize?" + urlencode(params)


def verify_identity(token, client_id, nonce=None):
    discovery = safe_request("GET", AUTH + "/.well-known/openid-configuration")
    if discovery.get("issuer") != AUTH or not discovery.get("jwks_uri", "").startswith(AUTH + "/"):
        raise ProviderError("AUTH_INVALID", "Недопустимый издатель авторизации.")
    jwks = safe_request("GET", discovery["jwks_uri"])
    try:
        header = jwt.get_unverified_header(token)
        key_data = next(k for k in jwks["keys"] if k.get("kid") == header.get("kid"))
        key = jwt.PyJWK.from_dict(key_data).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=AUTH,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        if nonce and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError()
        return claims
    except Exception:
        raise ProviderError("AUTH_INVALID", "Не удалось проверить личность аккаунта.") from None


def callback(params: dict):
    with credentials.locked():
        data = credentials.read()
        pending = data.get("pending_oauth", {})
        if (
            not pending
            or pending["expires"] < time.time()
            or not hmac.compare_digest(pending["state"], params.get("state", ""))
        ):
            raise ProviderError("AUTH_STATE", "Попытка входа истекла или не совпадает. Начните вход заново.")
        data.pop("pending_oauth", None)
        credentials.write(data)
        if params.get("error"):
            raise ProviderError("CONSENT_DECLINED", "Вход отменён.")
        client_id = params.get("client_id", pending["client_id"])
        if (
            client_id == "dynamic_agent_client"
            or (pending["client_id"] != "dynamic_agent_client" and client_id != pending["client_id"])
            or not params.get("code")
        ):
            raise ProviderError("AUTH_CLIENT", "Некорректная регистрация приложения.")
        tokens = safe_request(
            "POST",
            AUTH + "/api/accounts/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": params["code"],
                "code_verifier": pending["verifier"],
                "redirect_uri": CALLBACK,
                "resource": RESOURCE,
            },
        )
        if (
            not tokens.get("access_token")
            or tokens.get("token_type", "").lower() != "bearer"
            or int(tokens.get("expires_in", 0)) <= 0
        ):
            raise ProviderError("AUTH_INVALID", "Авторизация не вернула допустимые credentials.")
        claims = verify_identity(tokens.get("id_token", ""), client_id, pending["nonce"])
        if pending.get("subject") and claims["sub"] != pending["subject"]:
            raise ProviderError("AUTH_IDENTITY", "Аккаунт не совпадает с выбранным подключением.")
        profile = {
            **tokens,
            "client_id": client_id,
            "subject": claims["sub"],
            "email": claims.get("email", ""),
            "expires_at": time.time() + int(tokens["expires_in"]),
            "verified": False,
            "credits_block_confirmed": False,
        }
        data.setdefault("profiles", {})[client_id] = profile
        data["active_profile"] = client_id
        credentials.write(data)
    return status()


def status():
    with credentials.locked():
        data = credentials.read()
        p = data.get("profiles", {}).get(data.get("active_profile"), {})
    scopes = p.get("scope", "").split()
    return {
        "connected": bool(p.get("access_token")),
        "sharing": "chatgpt.tokens.use.direct" in scopes,
        "verified": bool(p.get("verified")),
        "email": p.get("email", ""),
        "credits_block_confirmed": bool(p.get("credits_block_confirmed")),
        "model": data.get("plan_model", ""),
        "route": data.get("route", "CHATGPT_ONLY"),
        "profiles": [{"id": k, "email": v.get("email", "")} for k, v in data.get("profiles", {}).items()],
    }


def access_token(require_budget=True):
    with credentials.locked():
        data = credentials.read()
        p = data.get("profiles", {}).get(data.get("active_profile"), {})
        if not p.get("access_token"):
            raise ProviderError("REAUTH_REQUIRED", "Подключите ChatGPT в локальной панели.")
        if "chatgpt.tokens.use.direct" not in p.get("scope", "").split():
            raise ProviderError("PLAN_PERMISSION_MISSING", "Разрешите использование ChatGPT plan.")
        if require_budget and not p.get("credits_block_confirmed"):
            raise ProviderError(
                "USAGE_SETUP", "Подтвердите ограничение purchased credits в настройках подключения."
            )
        if p["expires_at"] < time.time() + 90:
            try:
                tokens = safe_request(
                    "POST",
                    AUTH + "/api/accounts/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": p["client_id"],
                        "refresh_token": p.get("refresh_token", ""),
                        "resource": RESOURCE,
                    },
                )
            except ProviderError as exc:
                if exc.code == "REAUTH_REQUIRED":
                    for name in ("access_token", "refresh_token", "id_token"):
                        p.pop(name, None)
                    p["verified"] = False
                    credentials.write(data)
                raise
            if not tokens.get("access_token") or not tokens.get("refresh_token"):
                raise ProviderError("AUTH_INVALID", "Обновление credentials не завершено.")
            p.update(tokens)
            p["expires_at"] = time.time() + int(tokens["expires_in"])
            credentials.write(data)
        return p["access_token"]


def disconnect():
    confirmed = False
    with credentials.locked():
        data = credentials.read()
        p = data.get("profiles", {}).get(data.get("active_profile"), {})
        if p.get("refresh_token"):
            try:
                discovery = safe_request("GET", AUTH + "/.well-known/openid-configuration")
                endpoint = discovery.get("revocation_endpoint", "")
                if endpoint.startswith(AUTH + "/"):
                    with httpx.Client(timeout=15, trust_env=False) as client:
                        response = client.post(
                            endpoint,
                            data={
                                "token": p["refresh_token"],
                                "token_type_hint": "refresh_token",
                                "client_id": p["client_id"],
                            },
                        )
                    confirmed = response.status_code == 200
            except Exception:
                pass
        for key in ("access_token", "refresh_token", "id_token"):
            p.pop(key, None)
        p["verified"] = False
        data.pop("pending_oauth", None)
        credentials.write(data)
    return {"remote_revocation_confirmed": confirmed}
