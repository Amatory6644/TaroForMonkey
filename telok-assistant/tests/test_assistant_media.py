import copy
import json
import subprocess
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from telok import assistant, clip_montage, media, production
from telok.ai import chatgpt_auth, credentials, providers, router
from telok.ai.contracts import ProviderError
from telok.db import transaction
from telok.models import MediaJob, Work
from telok.settings import settings
from tests.test_assistant import protected_runtime  # noqa: F401


def pack():
    return {
        "title": "Fixture",
        "concept": "test",
        "shared_visual_rules": ["portrait"],
        "verified_facts": [],
        "assumptions": ["synthetic"],
        "scenes": [
            {
                "scene_id": "s01",
                "duration_seconds": 2,
                "purpose": "intro",
                "subject": "test",
                "action": "still",
                "camera": "static",
                "light": "day",
                "narration": "",
                "screen_text": "",
                "sound": "silence",
                "reference_asset_ids": [],
                "prompt": "synthetic clip",
            },
            {
                "scene_id": "s02",
                "duration_seconds": 2,
                "purpose": "close",
                "subject": "test",
                "action": "still",
                "camera": "static",
                "light": "day",
                "narration": "",
                "screen_text": "",
                "sound": "silence",
                "reference_asset_ids": [],
                "prompt": "synthetic clip",
            },
        ],
        "post_text": "test",
        "cta": "write",
        "missing_inputs": [],
    }


def task(project, monkeypatch, value=None):
    monkeypatch.setattr(assistant, "queue", lambda *a, **k: None)
    t = assistant.submit(1, "synthetic fixture", project["id"])
    with transaction() as s:
        w = s.get(Work, t["id"])
        w.result = {"production": value or pack()}
        w.status = "READY_FOR_REVIEW"
    return t


def test_operation_identity_is_scoped_to_owner_and_project(project, monkeypatch):
    monkeypatch.setattr(assistant, "queue", lambda *a, **k: None)
    a = assistant.submit(1, "private", project["id"], "same-operation")
    other = assistant.inbox(800001)
    b = assistant.submit(800001, "other", other["id"], "same-operation")
    assert a["id"] != b["id"]
    assert b["payload"]["prompt"] == "other"


def test_input_bounds_and_selected_idea(project, monkeypatch):
    monkeypatch.setattr(assistant, "queue", lambda *a, **k: None)
    for extra in (
        {"selected_idea": 3},
        {"selected_idea": True},
        {"documents": [{"text": "x" * 60001}]},
        {"documents": "bad"},
        {"reference_asset_ids": ["foreign"]},
    ):
        with pytest.raises(ValueError):
            assistant.submit(1, "test", project["id"], **extra)


def test_single_scene_edit_rejects_unrequested_changes(project):
    original = pack()
    ctx = {
        "project_id": project["id"],
        "history": {"result": {"production": original}},
        "edit_scene_ids": ["s02"],
    }
    edited = copy.deepcopy(original)
    edited["scenes"][1]["prompt"] = "calmer"
    production.validate_pack(edited, ctx)
    assert (
        production.export_manifest(edited)["scenes"][0]["content_hash"]
        == production.export_manifest(original)["scenes"][0]["content_hash"]
    )
    edited["scenes"][0]["prompt"] = "unrequested"
    with pytest.raises(ValueError):
        production.validate_pack(edited, ctx)


def test_qwen_enforces_schema_at_server(monkeypatch):
    credentials.update(local_model="qwen")
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"value": 1}'}}]}
        )

    transport = httpx.MockTransport(respond)
    original = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: original(transport=transport))
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    providers.LocalQwenProvider().generate("test", [{"role": "user", "content": "test"}], schema=schema)
    assert captured[0]["response_format"] == {"type": "json_object", "schema": schema}


def test_oidc_signed_identity_rejects_nonce_and_audience(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "fixture"

    def request(method, url, **kw):
        return (
            {"issuer": chatgpt_auth.AUTH, "jwks_uri": chatgpt_auth.AUTH + "/jwks"}
            if "well-known" in url
            else {"keys": [jwk]}
        )

    monkeypatch.setattr(chatgpt_auth, "safe_request", request)
    import time

    token = jwt.encode(
        {
            "iss": chatgpt_auth.AUTH,
            "aud": "client",
            "sub": "owner",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": "nonce",
        },
        private,
        algorithm="RS256",
        headers={"kid": "fixture"},
    )
    assert chatgpt_auth.verify_identity(token, "client", "nonce")["sub"] == "owner"
    with pytest.raises(ProviderError):
        chatgpt_auth.verify_identity(token, "client", "other")
    with pytest.raises(ProviderError):
        chatgpt_auth.verify_identity(token, "other", "nonce")


@pytest.mark.parametrize("terminal", [False, True])
def test_plan_stream_requires_terminal_completion(monkeypatch, terminal):
    monkeypatch.setattr(chatgpt_auth, "access_token", lambda: "fake")
    events = [SimpleNamespace(type="response.output_text.delta", delta="answer")]
    if terminal:
        events.append(
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="completed", id="r", usage=None, output=[]),
            )
        )

    class Stream:
        def __enter__(self):
            return iter(events)

        def __exit__(self, *args):
            pass

    class Client:
        def __init__(self, **kw):
            self.responses = SimpleNamespace(create=lambda **kw: Stream())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(providers, "OpenAI", Client)
    if terminal:
        assert providers.ChatGPTPlanProvider().generate("test", [], model="test").text == "answer"
    else:
        with pytest.raises(ProviderError) as e:
            providers.ChatGPTPlanProvider().generate("test", [], model="test")
        assert e.value.code == "INCOMPLETE"


def test_unknown_seedance_submission_is_not_repeated(project, monkeypatch):
    value = pack()
    value["scenes"][0]["duration_seconds"] = 5
    t = task(project, monkeypatch, value)
    credentials.update(
        seedance={"enabled": True, "api_key": "fake", "model": "dreamina-seedance-2-5-fixture"}
    )
    monkeypatch.setattr(settings(), "seedance_call_reserve_usd", 0.1)
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture uncertainty")

    original = httpx.Client
    monkeypatch.setattr(media.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(fail)))
    with pytest.raises(ValueError):
        media.submit(t["id"], 1, "s01")
    repeated = media.submit(t["id"], 1, "s01")
    assert repeated["status"] == "UNKNOWN"
    assert len(calls) == 1


def test_actual_import_montage_and_scene_cache(project, monkeypatch, tmp_path):
    t = task(project, monkeypatch)
    clip = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            settings().ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=180x320:r=24:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    data = clip.read_bytes()
    a = media.import_clip(t["id"], 1, "s01", data)
    assert media.import_clip(t["id"], 1, "s01", data)["id"] == a["id"]
    media.import_clip(t["id"], 1, "s02", data)
    result = clip_montage.assemble(t["id"], 1)
    assert result["technical"]["width"] == 720 and result["technical"]["height"] == 1280
    assert result["technical"]["audio"] and abs(result["technical"]["duration"] - 4) < 1
    revised = copy.deepcopy(pack())
    revised["scenes"][1]["prompt"] = "changed"
    new = task(project, monkeypatch, revised)
    media.import_clip(new["id"], 1, "s02", data)
    assert clip_montage.assemble(new["id"], 1)["technical"]["audio"]
    with transaction() as s:
        assert s.query(MediaJob).filter_by(project_id=project["id"], scene_id="s01").count() == 1
    with pytest.raises(ValueError):
        media.import_clip(t["id"], 200, "s01", data)


def test_targeted_stage_merges_only_requested_scenes(project, monkeypatch):
    t = task(project, monkeypatch)
    original = pack()
    changed = copy.deepcopy(original["scenes"][1])
    changed["prompt"] = "calmer movement"
    monkeypatch.setattr(router, "structured", lambda *a, **kw: {"scenes": [changed]})
    ctx = {
        "project_id": project["id"],
        "request_id": t["id"],
        "history": {"result": {"production": original}},
        "edit_scene_ids": ["s02"],
    }
    value = assistant.stage(ctx, "production", "edit", "ProductionPack")
    assert value["scenes"][0] == original["scenes"][0]
    assert value["scenes"][1] == changed
    assert value["shared_visual_rules"] == original["shared_visual_rules"]


def test_media_mode_without_provider_exports_prompts(project, monkeypatch):
    t = task(project, monkeypatch)
    assistant.enqueue_production(t["id"])
    result = assistant.details(t["id"], 1)["result"]
    assert result["production"] and "media_note" in result
    assert not result.get("video_asset_id")
