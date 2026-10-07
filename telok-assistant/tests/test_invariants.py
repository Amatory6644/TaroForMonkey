import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from telok import budget, publisher, storage
from telok.api import app, manual_release
from telok.db import now, transaction
from telok.domain import DomainError, calendar_slots, factual_checks, owned
from telok.models import Approval, Item, Project, ProviderAttempt, Publication, SendAttempt, Version


def png():
    data = io.BytesIO()
    Image.new("RGB", (64, 64), "#ccd9c0").save(data, format="PNG")
    return data.getvalue()


def outgoing(project):
    with transaction() as session:
        p = session.get(Project, project["id"])
        p.channel_id = "-1001234567"
    version = manual_release(
        project["id"], {"text": "Редакторский вопрос.", "asset_ids": [], "owner_reviewed_facts": True}
    )
    with transaction() as session:
        session.add(
            Approval(
                version_id=version["id"],
                actor_id=1,
                manifest_hash=version["manifest_hash"],
                channel_id="-1001234567",
                expires_at=now() + timedelta(hours=1),
            )
        )
        pub = Publication(
            project_id=project["id"],
            version_id=version["id"],
            not_before=now() - timedelta(seconds=1),
            latest_at=now() + timedelta(hours=1),
        )
        session.add(pub)
        session.flush()
        return pub.id, version


def test_project_acl(project):
    with transaction() as session:
        with pytest.raises(DomainError):
            owned(session, project["id"], 2)


def test_storage_hash_and_project_isolation(project):
    asset = storage.put(project["id"], png(), "image/png")
    assert storage.read(asset, project["id"])[0] == png()
    with pytest.raises(ValueError):
        storage.read(asset, "another-project")


def test_unknown_mime_and_invalid_png_rejected(project):
    with pytest.raises(ValueError):
        storage.put(project["id"], b"bad", "application/javascript")
    with pytest.raises(UnidentifiedImageError):
        storage.put(project["id"], b"bad", "image/png")


def test_budget_parallel_reservations(project):
    def attempt(_):
        try:
            return budget.reserve(project["id"], "one-request", "test", 0.1)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        result = list(pool.map(attempt, range(15)))
    assert sum(x is not None for x in result) == 10


def test_unknown_budget_is_not_released(project):
    attempt = budget.reserve(project["id"], "request-a", "test", 0.6)
    budget.uncertain(attempt)
    with pytest.raises(ValueError):
        budget.reserve(project["id"], "request-b", "test", 0.5)


def test_physical_attempts_have_separate_reservations(project):
    a = budget.reserve(project["id"], "request", "test", 0.1)
    b = budget.reserve(project["id"], "request", "test", 0.1)
    assert a != b
    budget.finish(a, {"output_tokens": 10})
    with transaction() as session:
        assert session.get(ProviderAttempt, a).status == "ESTIMATED"
        assert session.get(ProviderAttempt, b).status == "RESERVED"


def test_missing_support_and_expired_evidence():
    draft = {"claims": [{"text": "Launch is tomorrow.", "evidence_ids": ["x"]}]}
    assert factual_checks(
        draft, [{"id": "x", "claim": "Launch is tomorrow.", "assessment": "insufficient", "excerpt": "URL"}]
    )
    assert factual_checks(
        draft,
        [
            {
                "id": "x",
                "claim": "Launch is tomorrow.",
                "assessment": "supports",
                "excerpt": "tomorrow",
                "valid_until": (now() - timedelta(days=1)).isoformat(),
            }
        ],
    )


def test_unrelated_claim_is_not_supported():
    draft = {"claims": [{"text": "Launch is tomorrow.", "evidence_ids": ["x"]}]}
    assert factual_checks(
        draft, [{"id": "x", "claim": "Brand name is Telok.", "assessment": "supports", "excerpt": "Telok"}]
    )


def test_calendar_cross_month():
    slots = calendar_slots([{"title": "A"}, {"title": "B"}], "2026-10-31", "Europe/Moscow")
    assert [s["date"] for s in slots] == ["2026-10-31", "2026-11-01"]
    assert slots[0]["id"] != slots[1]["id"]


def test_simultaneous_send_claim_only_once(project):
    pub, _ = outgoing(project)
    calls = []

    def transport(manifest, files):
        calls.append(manifest["text"])
        time.sleep(0.05)
        return {"message_id": 123, "chat_id": -1001234567}

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: publisher.send(pub, 1, transport), range(5)))
    assert len(calls) == 1
    with transaction() as session:
        assert session.get(Publication, pub).status == "SENT"
        assert (
            len(session.execute(select(SendAttempt).where(SendAttempt.publication_id == pub)).scalars().all())
            == 1
        )


def test_lost_response_unknown_no_auto_repeat(project):
    pub, _ = outgoing(project)
    count = []

    def transport(*_):
        count.append(1)
        raise TimeoutError("accepted, reply lost")

    assert publisher.send(pub, 1, transport)["status"] == "UNKNOWN"
    assert publisher.send(pub, 1, transport)["status"] == "UNKNOWN"
    assert len(count) == 1


def test_late_receipt_updates_domain(project):
    pub, _ = outgoing(project)
    gate = publisher.claim(pub, 1)
    assert publisher.claim(pub, 1)["status"] == "UNKNOWN"
    publisher.finish_send(pub, gate["attempt_id"], "SENT", {"message_id": 987, "chat_id": -1001234567})
    with transaction() as session:
        assert session.get(Publication, pub).receipt["message_id"] == 987


def test_revision_cancel_and_expiry(project):
    pub, _ = outgoing(project)
    with transaction() as session:
        entry = session.get(Publication, pub)
        entry.revision = 2
    assert publisher.claim(pub, 1)["status"] == "STALE"
    with transaction() as session:
        entry = session.get(Publication, pub)
        entry.latest_at = now() - timedelta(seconds=1)
    assert publisher.claim(pub, 2)["status"] == "EXPIRED"


def test_pause_blocks_send(project):
    pub, _ = outgoing(project)
    with transaction() as session:
        session.get(Project, project["id"]).paused = True
    assert publisher.claim(pub, 1)["send"] is False


def test_brand_change_blocks_previous_approval(project):
    pub, _ = outgoing(project)
    with transaction() as session:
        session.get(Project, project["id"]).brand_revision += 1
    assert publisher.claim(pub, 1)["status"] == "GATE_FAILED"


def test_manifest_tamper_blocked(project):
    pub, version = outgoing(project)
    with transaction() as session:
        v = session.get(Version, version["id"])
        v.manifest = {**v.manifest, "text": "tampered"}
    assert publisher.claim(pub, 1)["status"] == "GATE_FAILED"


def test_stale_callback_cannot_approve(project, monkeypatch):
    _, version = outgoing(project)
    with transaction() as session:
        session.get(Item, version["item_id"]).revision += 1
    with pytest.raises(DomainError):
        publisher.approve(version["id"], 1, [])


def test_demo_release_cannot_publish(project):
    _, version = outgoing(project)
    with transaction() as session:
        session.get(Version, version["id"]).demo = True
    with pytest.raises(DomainError):
        publisher.approve(version["id"], 1, [])


def test_manual_requires_owner_attestation(project):
    with pytest.raises(DomainError):
        manual_release(project["id"], {"text": "Hello", "asset_ids": []})


def test_manual_utf16_limit(project):
    with pytest.raises(DomainError):
        manual_release(project["id"], {"text": "🌱" * 2050, "owner_reviewed_facts": True})


def test_api_csrf_and_localhost(project):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/projects", json={"name": "bad"}).status_code == 403
        assert (
            client.post(
                "/api/projects",
                json={"name": "bad"},
                headers={"X-Telok-Client": "dashboard", "Origin": "https://attacker.invalid"},
            ).status_code
            == 403
        )
        response = client.get("/api/projects")
        assert response.status_code == 200


def test_api_manual_round_trip(project):
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Telok-Client": "dashboard"}) as client:
        response = client.post(
            "/api/projects/" + project["id"] + "/manual",
            json={"text": "Один вопрос.", "owner_reviewed_facts": True},
        )
        assert response.status_code == 200, response.text
        version = response.json()
        assert version["manifest_hash"] and version["status"] == "READY_FOR_REVIEW"
        assert (
            client.get("/api/projects/" + project["id"] + "/overview").json()["versions"][0]["id"]
            == version["id"]
        )


def test_channel_change_blocks_send(project):
    pub, _ = outgoing(project)
    with transaction() as session:
        session.get(Project, project["id"]).channel_id = "-1009999"
    assert publisher.claim(pub, 1)["status"] == "GATE_FAILED"
