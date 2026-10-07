from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from telok import budget, editorial, publisher, telegram
from telok.api import manual_release
from telok.db import transaction
from telok.domain import DomainError
from telok.models import Project, Publication, SendAttempt
from tests.test_invariants import outgoing


def test_image_edit_preserves_text_and_claims(monkeypatch):
    ctx = {
        "kind": "edit",
        "edit_scope": "image",
        "project_id": "p",
        "request_id": "r",
        "previous_text": "Текст владельца.",
        "previous_draft": {"claims": []},
        "evidence": [],
    }
    monkeypatch.setattr(
        editorial.providers,
        "text",
        lambda *args: {
            "text": "Model unexpectedly changed this",
            "claims": [],
            "visual": {"prompt": "p", "required_references": [], "exclusions": []},
        },
    )
    result = editorial.produce(ctx)
    assert result["text"] == "Текст владельца."


def test_mandatory_brand_and_previous_references(monkeypatch):
    called = []
    monkeypatch.setattr(editorial.providers, "image", lambda *args: called.append(args[-1]) or "result")
    ctx = {
        "kind": "edit",
        "edit_scope": "image",
        "project_id": "p",
        "request_id": "r",
        "format": "IMAGE_POST",
        "previous_assets": ["original"],
        "snapshot": {"brand": {"references": ["brand"]}},
    }
    assert editorial.make_image(ctx, {"visual": {"prompt": "p", "required_references": []}}) == ["result"]
    assert called[0]["required_references"] == ["original", "brand"]


def test_text_edit_reuses_image_without_provider(monkeypatch):
    monkeypatch.setattr(
        editorial.providers, "image", lambda *args: pytest.fail("Unnecessary paid image regeneration")
    )
    assert editorial.make_image(
        {"kind": "edit", "edit_scope": "text", "format": "IMAGE_POST", "previous_assets": ["original"]}, {}
    ) == ["original"]


def test_two_approvals_one_intent(project, monkeypatch):
    with transaction() as session:
        session.get(Project, project["id"]).channel_id = "-1001234567"
    v = manual_release(project["id"], {"text": "Вопрос?", "owner_reviewed_facts": True})
    enqueues = []
    monkeypatch.setattr(publisher, "queue", lambda *args: enqueues.append(args[2]))
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: publisher.approve(v["id"], 1, []), range(5)))
    assert len({r["id"] for r in results}) == 1
    assert len(enqueues) == 1


def test_unknown_retry_requires_explicit_risk_and_new_revision(project, monkeypatch):
    pub, _ = outgoing(project)
    publisher.send(pub, 1, lambda *args: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(DomainError):
        publisher.retry_unknown(pub, 1, True, False)
    monkeypatch.setattr(publisher, "queue", lambda *args: None)
    result = publisher.retry_unknown(pub, 1, True, True)
    assert result["revision"] == 2
    assert publisher.claim(pub, 1)["status"] == "STALE"
    with transaction() as session:
        attempts = (
            session.execute(select(SendAttempt).where(SendAttempt.publication_id == pub)).scalars().all()
        )
        assert len(attempts) == 1 and attempts[0].status == "UNKNOWN"


def test_duplicate_ingress_concurrent_has_one_receipt(monkeypatch):
    queued = []
    monkeypatch.setattr(telegram, "queue", lambda *args: queued.append(args[2]))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: telegram.ingest(123400000, {"message": {}}), range(8)))
    assert len(set(results)) == 1
    assert len(queued) == 1


def test_late_budget_settlement_releases_hold(project):
    attempt = budget.reserve(project["id"], "late", "test", 0.8)
    budget.uncertain(attempt)
    budget.settle(attempt, 0.3, "invoice:manual-verified")
    assert budget.reserve(project["id"], "new", "test", 0.6)


def test_late_old_receipt_cancels_not_started_manual_retry(project, monkeypatch):
    pub, _ = outgoing(project)
    publisher.send(pub, 1, lambda *args: (_ for _ in ()).throw(TimeoutError()))
    with transaction() as session:
        old_attempt = session.get(Publication, pub).attempt_id
    monkeypatch.setattr(publisher, "queue", lambda *args: None)
    publisher.retry_unknown(pub, 1, True, True)
    publisher.finish_send(pub, old_attempt, "SENT", {"message_id": 45, "chat_id": -1001234567})
    assert publisher.claim(pub, 2)["status"] == "STALE"
    with transaction() as session:
        assert session.get(Publication, pub).status == "SENT"


def test_provider_failure_hides_secret_and_holds_reservation(project, monkeypatch):
    from pydantic import SecretStr

    from telok import providers
    from telok.models import ProviderAttempt
    from telok.schemas import Draft
    from telok.settings import settings

    cfg = settings()
    monkeypatch.setattr(cfg, "openai_api_key", SecretStr("test-secret-never-display"))
    monkeypatch.setattr(cfg, "text_call_reserve_usd", 0.1)

    class BrokenClient:
        def __init__(self, **kwargs):
            self.responses = self

        def parse(self, **kwargs):
            raise RuntimeError("credential test-secret-never-display")

    monkeypatch.setattr(providers, "OpenAI", BrokenClient)
    with pytest.raises(DomainError) as failure:
        providers.text(project["id"], "private-test", "producer", {}, Draft)
    assert "test-secret" not in str(failure.value)
    with transaction() as session:
        attempt = session.query(ProviderAttempt).filter_by(request_id="private-test").one()
        assert attempt.status == "UNKNOWN"


def test_too_long_text_rejected_before_image_cost(monkeypatch):
    monkeypatch.setattr(
        editorial.providers,
        "text",
        lambda *args: {"text": "😀" * 600, "claims": [], "visual": {}, "rationale": "Fixture"},
    )
    with pytest.raises(DomainError):
        editorial.produce(
            {"kind": "generate", "format": "IMAGE_POST", "project_id": "p", "request_id": "r", "evidence": []}
        )
