import copy
import io

import pytest
from PIL import Image

from telok import assistant, local_studio
from telok.db import transaction
from telok.models import Work
from tests.test_assistant_media import task


def photo():
    out = io.BytesIO()
    Image.new("RGB", (576, 1024), "#294d3b").save(out, format="PNG")
    return out.getvalue()


def test_import_frame_owner_and_approval(project, monkeypatch):
    t = task(project, monkeypatch)
    with pytest.raises(ValueError):
        local_studio.import_frame(t["id"], 2, "s01", photo())
    frame = local_studio.import_frame(t["id"], 1, "s01", photo())
    assert not frame["approved"]
    with pytest.raises(ValueError):
        local_studio.approve(t["id"], 1, "s01", "wrong-version")
    assert local_studio.approve(t["id"], 1, "s01", frame["asset_id"])["approved"]
    with transaction() as s:
        w = s.get(Work, t["id"])
        result = copy.deepcopy(w.result)
        result["production"]["scenes"][0]["prompt"] = "changed"
        w.result = result
    with pytest.raises(ValueError):
        local_studio.approve(t["id"], 1, "s01", frame["asset_id"])


def test_generation_cache_and_original_reference(project, monkeypatch):
    t = task(project, monkeypatch)
    original = local_studio.import_frame(t["id"], 1, "s01", photo())
    calls = []
    monkeypatch.setattr(local_studio, "status", lambda: {"ready": True})

    def generate(prompt, seed, reference):
        calls.append((seed, reference))
        return photo()

    monkeypatch.setattr(local_studio, "comfy_image", generate)
    a = local_studio.generate(t["id"], 1, "s01", 0)
    b = local_studio.generate(t["id"], 1, "s01", 0)
    assert a == b and len(calls) == 1
    assert a["source_asset_id"] == original["asset_id"] and calls[0][1]
    assert local_studio.generate(t["id"], 1, "s01", 1)["asset_id"] != a["asset_id"]
    assert len(calls) == 2


def test_storyboard_requires_current_frames_and_is_separate(project, monkeypatch):
    t = task(project, monkeypatch)
    with pytest.raises(ValueError):
        local_studio.storyboard(t["id"], 1)
    for scene in ["s01", "s02"]:
        local_studio.import_frame(t["id"], 1, scene, photo())
    result = local_studio.storyboard(t["id"], 1)
    assert abs(result["technical"]["duration"] - 4) < 0.1
    stored = assistant.details(t["id"], 1)["result"]
    assert stored["storyboard_asset_id"] and not stored.get("video_asset_id")
    local_studio.import_frame(t["id"], 1, "s01", photo())
    assert not assistant.details(t["id"], 1)["result"]["storyboard_asset_id"]


def test_prompt_compiler_caches_pack_and_rejects_wrong_scenes(project, monkeypatch):
    from telok.ai import router
    from telok.media import pack_scene

    t = task(project, monkeypatch)
    with transaction() as session:
        w = session.get(Work, t["id"])
        result = copy.deepcopy(w.result)
        result["production"]["scenes"][0]["prompt"] = "Фотография одежды"
        w.result = result
    calls = []
    monkeypatch.setattr(
        router,
        "structured",
        lambda *a, **k: (
            calls.append(1)
            or {
                "prompts": {
                    "s01": "A neutral clothing studio photograph.",
                    "s02": "A close up clothing studio photograph.",
                }
            }
        ),
    )
    value, scene, manifest = pack_scene(t["id"], 1, "s01")
    assert "clothing" in local_studio.english_prompt(value, scene, manifest)
    value, scene, manifest = pack_scene(t["id"], 1, "s01")
    local_studio.english_prompt(value, scene, manifest)
    assert calls == [1]


def test_seedance_uses_only_exact_approved_first_frame(project, monkeypatch):
    import json

    import httpx

    from telok import media
    from telok.ai import credentials
    from telok.settings import settings
    from tests.test_assistant_media import pack

    value = pack()
    value["scenes"][0]["duration_seconds"] = 5
    t = task(project, monkeypatch, value)
    frame = local_studio.import_frame(t["id"], 1, "s01", photo())
    local_studio.approve(t["id"], 1, "s01", frame["asset_id"])
    monkeypatch.setattr(
        credentials,
        "read",
        lambda: {
            "seedance": {"enabled": True, "api_key": "fixture", "model": "dreamina-seedance-2-5-fixture"}
        },
    )
    monkeypatch.setattr(settings(), "seedance_call_reserve_usd", 0.1)
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "fixture_job"})

    original = httpx.Client
    monkeypatch.setattr(media.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(provider)))
    media.submit(t["id"], 1, "s01")
    assert calls[0]["ratio"] == "adaptive"
    images = [v for v in calls[0]["content"] if v["type"] == "image_url"]
    assert len(images) == 1 and images[0]["role"] == "first_frame"
