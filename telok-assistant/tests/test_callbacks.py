from datetime import timedelta

import pytest

from telok.api import manual_release
from telok.callbacks import apply, create_actions
from telok.db import now, transaction
from telok.domain import DomainError
from telok.models import Callback, Context, Item
from tests.test_invariants import outgoing


def test_opaque_callback_actor_and_expiry(project):
    v = manual_release(project["id"], {"text": "Вопрос?", "owner_reviewed_facts": True})
    tokens = create_actions(v["id"], 1)
    assert len(tokens["edit_text"].encode()) <= 64
    with pytest.raises(DomainError):
        apply(tokens["edit_text"], 2)
    with transaction() as session:
        session.get(Callback, tokens["edit_text"][2:]).expires_at = now() - timedelta(seconds=1)
    with pytest.raises(DomainError):
        apply(tokens["edit_text"], 1)


def test_callback_rejects_stale_version(project):
    v = manual_release(project["id"], {"text": "Вопрос?", "owner_reviewed_facts": True})
    token = create_actions(v["id"], 1)["approve"]
    with transaction() as session:
        session.get(Item, v["item_id"]).revision += 1
    with pytest.raises(DomainError):
        apply(token, 1)


def test_callback_edit_context_survives_new_session(project):
    v = manual_release(project["id"], {"text": "Вопрос?", "owner_reviewed_facts": True})
    token = create_actions(v["id"], 1)["edit_text"]
    assert apply(token, 1)["status"] == "AWAITING_EDIT"
    assert apply(token, 1)["status"] == "ALREADY_USED"
    with transaction() as session:
        context = session.query(Context).filter_by(actor_id=1).one()
        assert context.pending_edit == {"version_id": v["id"], "scope": "text"}


def test_callback_approve_idempotent_and_version_bound(project, monkeypatch):
    from telok import publisher

    pub, v = outgoing(project)
    monkeypatch.setattr(publisher, "queue", lambda *args: pytest.fail("Existing intent must be reused"))
    token = create_actions(v["id"], 1)["approve"]
    assert apply(token, 1)["id"] == pub
    assert apply(token, 1)["status"] == "ALREADY_USED"
