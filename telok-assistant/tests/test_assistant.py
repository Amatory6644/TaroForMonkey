import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from telok import assistant, production, research
from telok.ai import chatgpt_auth, credentials, router
from telok.ai.contracts import AIResult, ProviderError
from telok.ai.providers import LocalQwenProvider, mapped_error
from telok.api import app
from telok.db import transaction
from telok.models import AIInvocation, TaskArtifact, Work
from telok.settings import settings


@pytest.fixture(autouse=True)
def protected_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "credentials_dir", tmp_path / "credentials")
    credentials.update(route="CHATGPT_ONLY")
    yield


def profile():
    return {
        "client_id": "oaiapp_test",
        "subject": "person",
        "access_token": "secret_access",
        "refresh_token": "secret_refresh",
        "expires_at": time.time() + 3600,
        "scope": "openid chatgpt.tokens.use.direct",
        "credits_block_confirmed": True,
    }


def test_dpapi_atomic_storage_and_safe_status():
    p = profile()
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])
    assert credentials.read()["profiles"][p["client_id"]]["access_token"] == "secret_access"
    if __import__("os").name == "nt":
        assert b"secret_access" not in (credentials.root() / "runtime.bin").read_bytes()
    assert "secret_access" not in json.dumps(chatgpt_auth.status())
    assert "secret_refresh" not in json.dumps(chatgpt_auth.status())


def test_stable_host_and_new_oauth_values():
    from urllib.parse import parse_qs, urlparse

    first = parse_qs(urlparse(chatgpt_auth.start()).query)
    second = parse_qs(urlparse(chatgpt_auth.start()).query)
    assert first["ext_agent_host_id"] == second["ext_agent_host_id"]
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]
    assert first["redirect_uri"] == ["http://127.0.0.1:8481/auth/callback"]


def test_callback_rejects_wrong_state_and_client():
    chatgpt_auth.start()
    with pytest.raises(ProviderError, match="не совпадает"):
        chatgpt_auth.callback({"state": "wrong", "code": "secret"})
    data = credentials.read()
    data["pending_oauth"]["client_id"] = "registered"
    credentials.update(**data)
    with pytest.raises(ProviderError, match="регистрация"):
        chatgpt_auth.callback(
            {"state": data["pending_oauth"]["state"], "client_id": "other", "code": "secret"}
        )


def test_permission_and_budget_are_independent_gates():
    p = profile()
    p["scope"] = "openid"
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])
    with pytest.raises(ProviderError) as error:
        chatgpt_auth.access_token()
    assert error.value.code == "PLAN_PERMISSION_MISSING"
    p["scope"] = "chatgpt.tokens.use.direct"
    p["credits_block_confirmed"] = False
    credentials.update(profiles={p["client_id"]: p})
    with pytest.raises(ProviderError) as error:
        chatgpt_auth.access_token()
    assert error.value.code == "USAGE_SETUP"


def test_refresh_is_serialized_and_rotated(monkeypatch):
    p = profile()
    p["expires_at"] = 0
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])
    calls = []

    def request(method, url, **kwargs):
        calls.append(kwargs["data"])
        time.sleep(0.1)
        return {"access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 3600}

    monkeypatch.setattr(chatgpt_auth, "safe_request", request)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: chatgpt_auth.access_token(), range(2)))
    assert results == ["new_access", "new_access"]
    assert len(calls) == 1
    assert calls[0]["client_id"] == "oaiapp_test"
    assert credentials.read()["profiles"][p["client_id"]]["refresh_token"] == "new_refresh"


def test_transient_refresh_keeps_credentials(monkeypatch):
    p = profile()
    p["expires_at"] = 0
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])

    def fail(*a, **k):
        raise ProviderError("NETWORK", "Нет сети")

    monkeypatch.setattr(chatgpt_auth, "safe_request", fail)
    with pytest.raises(ProviderError):
        chatgpt_auth.access_token()
    assert credentials.read()["profiles"][p["client_id"]]["refresh_token"] == "secret_refresh"


def test_terminal_refresh_clears_credentials(monkeypatch):
    p = profile()
    p["expires_at"] = 0
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])

    def fail(*a, **k):
        raise ProviderError("REAUTH_REQUIRED", "Повторите вход")

    monkeypatch.setattr(chatgpt_auth, "safe_request", fail)
    with pytest.raises(ProviderError):
        chatgpt_auth.access_token()
    assert "refresh_token" not in credentials.read()["profiles"][p["client_id"]]


def test_local_rejects_external_and_userinfo():
    for url in ("https://example.com/v1", "http://127.0.0.1@evil.test/v1", "http://user@127.0.0.1/v1"):
        credentials.update(local_url=url)
        with pytest.raises(ProviderError):
            LocalQwenProvider()._url()


def test_fallback_visible_and_search_not_downgraded(project, monkeypatch):
    credentials.update(route="AUTO")

    def limit(*a, **k):
        raise ProviderError("PLAN_LIMIT_REACHED", "Лимит")

    monkeypatch.setattr(router.ChatGPTPlanProvider, "generate", limit)
    monkeypatch.setattr(
        router.LocalQwenProvider, "generate", lambda *a, **k: AIResult("Локальный ответ", "LocalQwen", "qwen")
    )
    result = router.invoke(project["id"], uuid4().hex, "test", [{"role": "user", "content": "test"}])
    assert result["fallback"] and result["provider"] == "LocalQwen"
    with pytest.raises(ProviderError):
        router.invoke(project["id"], uuid4().hex, "test", [], search=True)


def test_unknown_started_attempt_not_repeated(project, monkeypatch):
    request = uuid4().hex
    with transaction() as s:
        s.add(
            AIInvocation(
                project_id=project["id"], request_id=request, provider="ChatGPTPlan", status="STARTED"
            )
        )
    with pytest.raises(ProviderError) as e:
        router.invoke(project["id"], request, "test", [])
    assert e.value.code == "UNKNOWN_PREVIOUS_ATTEMPT"


def test_personal_workspace_is_idempotent_and_owned():
    actor = 900001
    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(assistant.inbox, [actor, actor]))
    assert values[0]["id"] == values[1]["id"]
    assert values[0]["brand"]["kind"] == "personal"


def test_ingress_deduplicated_and_parent_acl(project, monkeypatch):
    monkeypatch.setattr(assistant, "queue", lambda *a, **k: None)
    one = assistant.submit(1, "test", project["id"], "operation")
    two = assistant.submit(1, "test", project["id"], "operation")
    assert one["id"] == two["id"]
    other = assistant.inbox(222)
    with pytest.raises(ValueError):
        assistant.submit(222, "continue", other["id"], parent_id=one["id"])
    with pytest.raises(ValueError):
        assistant.details(one["id"], 222)


def test_cancellation_preserves_state(project, monkeypatch):
    monkeypatch.setattr(assistant, "queue", lambda *a, **k: None)
    task = assistant.submit(1, "test", project["id"])
    assistant.cancel(task["id"], 1)
    with pytest.raises(ValueError):
        assistant.context(task["id"])


def test_memory_project_filter_and_relevance(project):
    with transaction() as s:
        w = Work(project_id=project["id"], kind="assistant", payload={}, workflow_id=uuid4().hex)
        s.add(w)
        s.flush()
        s.add(
            TaskArtifact(
                project_id=project["id"], request_id=w.id, stage="answer", body={"text": "hotel quiet rooms"}
            )
        )
    with transaction() as s:
        assert research.relevant_memory(s, project["id"], "hotel")
        assert not research.relevant_memory(s, project["id"], "unrelatedword")
        assert not research.relevant_memory(s, "other-project", "hotel")


def test_export_scene_hash_depends_on_global_rules():
    pack = {
        "shared_visual_rules": ["warm"],
        "scenes": [{"scene_id": "s01", "prompt": "room", "duration_seconds": 5, "reference_asset_ids": []}],
    }
    first = production.export_manifest(pack)
    pack["shared_visual_rules"] = ["cool"]
    assert first["scenes"][0]["content_hash"] != production.export_manifest(pack)["scenes"][0]["content_hash"]


def test_documents_and_size():
    assert research.extract_document("Тест".encode(), "test.txt")["text"] == "Тест"
    with pytest.raises(ValueError):
        research.extract_document(b"x" * 10_000_001, "test.txt")
    with pytest.raises(ValueError):
        research.extract_document(b"test", "test.exe")


def test_setup_contains_no_secrets_and_csrf():
    p = profile()
    credentials.update(profiles={p["client_id"]: p}, active_profile=p["client_id"])
    client = TestClient(app)
    response = client.get("/api/assistant/setup")
    assert response.status_code == 200 and "secret_access" not in response.text
    assert client.post("/api/assistant/config", json={"route": "AUTO"}).status_code == 403
    assert (
        client.post(
            "/api/assistant/config",
            headers={"X-Telok-Client": "dashboard", "Origin": "https://evil.test"},
            json={},
        ).status_code
        == 403
    )


def test_precise_limit_code():
    assert mapped_error("subscription_sharing_usage_limit_exceeded", 429).code == "PLAN_LIMIT_REACHED"


def test_multiple_signin_attempts_are_independent(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    first = parse_qs(urlparse(chatgpt_auth.start()).query)
    second = parse_qs(urlparse(chatgpt_auth.start()).query)
    assert first["state"][0] in credentials.read()["oauth_attempts"]
    assert second["state"][0] in credentials.read()["oauth_attempts"]
    monkeypatch.setattr(
        chatgpt_auth,
        "safe_request",
        lambda *a, **k: {
            "access_token": "fake",
            "refresh_token": "fake",
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": "fake",
            "scope": "chatgpt.tokens.use.direct",
        },
    )
    monkeypatch.setattr(chatgpt_auth, "verify_identity", lambda *a, **k: {"sub": "fixture"})
    chatgpt_auth.callback({"state": first["state"][0], "code": "fixture", "client_id": "oaiapp_fixture"})
    assert chatgpt_auth.status()["connected"]
    assert second["state"][0] in credentials.read()["oauth_attempts"]
    with pytest.raises(ProviderError):
        chatgpt_auth.callback({"state": first["state"][0], "code": "fixture", "client_id": "oaiapp_fixture"})


def test_oauth_attempt_storage_is_bounded():
    for _ in range(8):
        chatgpt_auth.start()
    assert len(credentials.read()["oauth_attempts"]) == 5


def test_openai_auth_uses_system_proxy(monkeypatch):
    captured = []
    original = __import__("httpx").Client
    transport = __import__("httpx").MockTransport(
        lambda request: __import__("httpx").Response(200, json={"ok": True})
    )

    def client(**kwargs):
        captured.append(kwargs["trust_env"])
        return original(transport=transport)

    monkeypatch.setattr(chatgpt_auth.httpx, "Client", client)
    assert chatgpt_auth.safe_request("GET", chatgpt_auth.AUTH + "/.well-known/openid-configuration")["ok"]
    assert captured == [True]


def test_callback_error_is_safe_and_actionable():
    client = TestClient(app)
    response = client.get("/auth/callback?state=wrong&code=secret_code")
    assert response.status_code == 200
    assert "AUTH_STATE" in response.text and "secret_code" not in response.text
    assert chatgpt_auth.status()["last_error"]["code"] == "AUTH_STATE"


def test_telegram_config_uses_proxy_and_verified_poller_state(monkeypatch):
    import httpx

    from telok import integrations

    credentials.update(legacy_poller_disabled=True)
    captured = []
    original = httpx.Client

    def response(request):
        result = {"username": "fixture_bot"} if request.url.path.endswith("getMe") else {"url": ""}
        return httpx.Response(200, json={"ok": True, "result": result})

    def client(**kwargs):
        captured.append(kwargs.get("trust_env"))
        return original(transport=httpx.MockTransport(response))

    monkeypatch.setattr(integrations.httpx, "Client", client)
    token = "1234567:" + "a" * 35
    result = integrations.configure(token, 550658811, False)
    assert result["configured"] and result["username"] == "fixture_bot"
    assert captured == [True]
    assert token not in json.dumps(result)
    assert integrations.allowed() == [550658811]


def test_telegram_webhook_failure_does_not_save_token(monkeypatch):
    import httpx

    from telok import integrations

    original = httpx.Client

    def response(request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"username": "fixture"}}
            if request.url.path.endswith("getMe")
            else {"ok": False},
        )

    monkeypatch.setattr(
        integrations.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(response))
    )
    with pytest.raises(ValueError, match="webhook"):
        integrations.configure("1234567:" + "a" * 35, 123, True)
    assert not integrations.token()


def test_telegram_api_starts_after_validated_save(monkeypatch):
    from telok import integrations

    calls = []
    monkeypatch.setattr(integrations, "configure", lambda *args: {"configured": True, "username": "fixture"})
    monkeypatch.setattr(integrations, "start_bot", lambda: calls.append("start") or {"running": True})
    r = TestClient(app).post(
        "/api/assistant/telegram",
        json={"token": "fixture", "owner_id": 123},
        headers={"X-Telok-Client": "dashboard"},
    )
    assert r.status_code == 200 and r.json()["running"] and calls == ["start"]


def test_telegram_poll_persists_allowed_update_before_offset(monkeypatch):
    import asyncio

    from telok import integrations, telegram
    from telok.domain import DomainError

    monkeypatch.setattr(integrations, "token", lambda: "fixture")
    monkeypatch.setattr(integrations, "allowed", lambda: [123])
    committed = []
    monkeypatch.setattr(telegram, "ingest", lambda update_id, payload: committed.append(update_id))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: Client())
    calls = []

    async def fetch(client, offset):
        calls.append(offset)
        if len(calls) > 1:
            assert committed == [11] and offset == 12
            raise DomainError("fixture stop")
        return {
            "ok": True,
            "result": [
                {"update_id": 10, "message": {"from": {"id": 999}}},
                {"update_id": 11, "message": {"from": {"id": 123}}},
            ],
        }

    monkeypatch.setattr(telegram, "fetch_updates", fetch)
    with pytest.raises(DomainError, match="fixture stop"):
        asyncio.run(telegram.poll())
