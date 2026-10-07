from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from telok import storage
from telok.db import now, transaction, uid
from telok.domain import DomainError, canonical, owned, queue, record, row_dict
from telok.models import Approval, Item, Project, Publication, SendAttempt, Version
from telok.settings import settings


def approve(version_id: str, actor: int, accepted: list[str], at: datetime | None = None):
    with transaction() as session:
        version = session.get(Version, version_id)
        if not version:
            raise DomainError("Версия не найдена.")
        project = owned(session, version.project_id, actor)
        item = session.execute(select(Item).where(Item.id == version.item_id).with_for_update()).scalar_one()
        if item.current_version_id != version.id or item.revision != version.revision:
            raise DomainError("Версия устарела.")
        if version.demo:
            raise DomainError("Демонстрационный материал нельзя публиковать. Создайте собственный release.")
        if not project.channel_id or project.channel_id != version.manifest["channel_id"]:
            raise DomainError("Канал должен быть настроен до выпуска версии. Нужен новый manifest.")
        if version.qa.get("hard") or any(
            f["category"] in {"factual", "brand"} for f in version.qa.get("findings", [])
        ):
            raise DomainError("Есть блокирующие ошибки QA.")
        warnings = [f["message"] for f in version.qa.get("findings", []) if f["category"] == "editorial"]
        if any(w not in accepted for w in warnings):
            raise DomainError("Редакционные замечания нужно принять явно.")
        previous = session.execute(
            select(Publication).where(Publication.version_id == version.id)
        ).scalar_one_or_none()
        if previous:
            return row_dict(previous)
        when = at or now()
        if when.tzinfo is None or when < now() - timedelta(seconds=10):
            raise DomainError("Нужна будущая дата с timezone.")
        approval = Approval(
            version_id=version.id,
            actor_id=actor,
            manifest_hash=version.manifest_hash,
            channel_id=project.channel_id,
            expires_at=when + timedelta(hours=2),
            accepted_findings=accepted,
        )
        publication = Publication(
            project_id=project.id, version_id=version.id, not_before=when, latest_at=when + timedelta(hours=1)
        )
        session.add_all([approval, publication])
        session.flush()
        version.status = "APPROVED"
        queue(
            session,
            "telok_publish",
            f"publish:{publication.id}:1",
            {"id": publication.id, "revision": 1},
            when,
        )
        record(session, project.id, "approved", {"version": version.id, "hash": version.manifest_hash})
        return row_dict(publication)


def reschedule(publication_id: str, actor: int, at: datetime | None, cancel: bool = False):
    with transaction() as session:
        pub = session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        ).scalar_one()
        owned(session, pub.project_id, actor)
        if pub.status in {"SENDING", "SENT", "UNKNOWN"}:
            raise DomainError("Отправка начата или неоднозначна; перенос/отмена не могут заменить её.")
        pub.revision += 1
        if cancel:
            pub.status = "CANCELLED"
        else:
            if not at or at.tzinfo is None or at <= now():
                raise DomainError("Нужна будущая дата с timezone.")
            pub.not_before, pub.latest_at, pub.status = at, at + timedelta(hours=1), "SCHEDULED"
            approval = session.execute(
                select(Approval).where(Approval.version_id == pub.version_id)
            ).scalar_one()
            approval.expires_at = at + timedelta(hours=2)
            queue(
                session,
                "telok_publish",
                f"publish:{pub.id}:{pub.revision}",
                {"id": pub.id, "revision": pub.revision},
                at,
            )
        return row_dict(pub)


def claim(publication_id: str, expected_revision: int) -> dict:
    # Called inside EVERY physical HTTP step, never as a replay-cached claim step.
    with transaction() as session:
        pub = session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        ).scalar_one()
        if pub.revision != expected_revision or pub.status in {"CANCELLED", "EXPIRED"}:
            return {"send": False, "status": "STALE"}
        if pub.status == "SENT":
            return {"send": False, "status": "SENT", "receipt": pub.receipt}
        if pub.status in {"SENDING", "UNKNOWN"}:
            pub.status = "UNKNOWN"
            return {"send": False, "status": "UNKNOWN"}
        version = session.get(Version, pub.version_id)
        item = session.get(Item, version.item_id)
        project = session.get(Project, pub.project_id)
        approval = session.execute(
            select(Approval).where(Approval.version_id == version.id)
        ).scalar_one_or_none()
        if now() > pub.latest_at or (approval and now() > approval.expires_at):
            pub.status = "EXPIRED"
            return {"send": False, "status": "EXPIRED"}
        if project.paused or now() < pub.not_before:
            return {"send": False, "status": "PAUSED_OR_EARLY"}
        if (
            not approval
            or version.demo
            or version.id != item.current_version_id
            or version.revision != item.revision
        ):
            pub.status = "CANCELLED"
            return {"send": False, "status": "INVALID_RELEASE"}
        manifest = version.manifest
        if (
            canonical(manifest) != version.manifest_hash
            or approval.manifest_hash != version.manifest_hash
            or approval.channel_id != project.channel_id
            or manifest["channel_id"] != project.channel_id
            or project.owner_id != approval.actor_id
            or version.brief.get("snapshot", {}).get("brand_revision") != project.brand_revision
            or version.qa.get("hard")
            or any(f["category"] in {"factual", "brand"} for f in version.qa.get("findings", []))
        ):
            pub.status = "CANCELLED"
            return {"send": False, "status": "GATE_FAILED"}
        attempt = SendAttempt(id=uid(), publication_id=pub.id, revision=pub.revision)
        session.add(attempt)
        pub.status, pub.attempt_id = "SENDING", attempt.id
        return {"send": True, "attempt_id": attempt.id, "manifest": manifest, "project_id": project.id}


def finish_send(publication_id: str, attempt_id: str, status: str, receipt: dict, error: str = ""):
    with transaction() as session:
        pub = session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        ).scalar_one()
        attempt = session.get(SendAttempt, attempt_id)
        attempt.status, attempt.receipt, attempt.error = status, receipt, error
        # A late successful receipt must remain recorded, even after a domain operation.
        if pub.attempt_id == attempt_id:
            pub.status, pub.receipt, pub.error = status, receipt, error
        elif status == "SENT" and pub.status == "SCHEDULED" and not pub.attempt_id:
            # A verified old receipt arrived before a manually authorized new send.
            pub.status, pub.receipt, pub.error, pub.attempt_id = "SENT", receipt, "", attempt_id
            pub.revision += 1
        record(
            session,
            pub.project_id,
            "publication_" + status.lower(),
            {"attempt": attempt_id, "receipt": receipt},
        )


def send(publication_id: str, revision: int, transport=None) -> dict:
    if transport is None and not settings().publishing_enabled:
        return {"status": "PUBLISHING_DISABLED"}
    with transaction() as session:
        pub = session.get(Publication, publication_id)
        version = session.get(Version, pub.version_id)
        manifest = version.manifest
        project_id = version.project_id
    # Read/verify immutable bytes before claim; send exactly these bytes.
    files = []
    for asset in manifest["assets"]:
        data, meta = storage.read(asset["id"], project_id)
        if meta["sha256"] != asset["sha256"]:
            raise DomainError("Asset hash изменён.")
        files.append((data, meta))
    if transport is None and not settings().telegram_token.get_secret_value():
        raise DomainError("Telegram token не настроен; внешняя отправка не началась.")
    gate = claim(publication_id, revision)
    if not gate["send"]:
        return gate
    try:
        outcome = (transport or telegram_transport)(gate["manifest"], files)
        finish_send(publication_id, gate["attempt_id"], "SENT", outcome)
        return {"status": "SENT", "receipt": outcome}
    except KnownRejection as exc:
        finish_send(publication_id, gate["attempt_id"], "FAILED", {}, exc.safe_message)
        return {"status": "FAILED", "retry_after": exc.retry_after}
    except Exception:
        finish_send(
            publication_id, gate["attempt_id"], "UNKNOWN", {}, "Неоднозначный результат внешней отправки."
        )
        return {"status": "UNKNOWN"}


class KnownRejection(Exception):
    def __init__(self, safe_message: str, retry_after: int = 0):
        self.safe_message, self.retry_after = safe_message, retry_after


def telegram_transport(manifest: dict, files: list[tuple]) -> dict:
    token = settings().telegram_token.get_secret_value()
    method = manifest["method"]
    data = {"chat_id": manifest["channel_id"], "protect_content": str(manifest["protect_content"]).lower()}
    multipart = None
    if method == "sendMessage":
        data["text"] = manifest["text"]
        data["link_preview_options"] = '{"is_disabled":true}'
    else:
        field = "video" if method == "sendVideo" else "photo"
        data.update(caption=manifest["text"], show_caption_above_media="false", has_spoiler="false")
        multipart = {
            field: ("release.mp4" if field == "video" else "release.png", files[0][0], files[0][1]["mime"])
        }
    with httpx.Client(timeout=45, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=multipart)
    payload = response.json()
    if not payload.get("ok"):
        # Only explicit Bot API errors prove rejection. HTTP/network/parse failures are UNKNOWN.
        if response.status_code in {400, 401, 403, 429} and payload.get("error_code") == response.status_code:
            raise KnownRejection(
                f"Telegram отказал: код {response.status_code}.",
                payload.get("parameters", {}).get("retry_after", 0),
            )
        raise RuntimeError("Неоднозначный ответ.")
    result = payload["result"]
    return {"message_id": result["message_id"], "chat_id": result["chat"]["id"]}


def reconcile(publication_id: str, actor: int, receipt: dict):
    if not isinstance(receipt.get("message_id"), int) or not receipt.get("chat_id"):
        raise DomainError("Нужны достоверные message_id и chat_id.")
    with transaction() as session:
        pub = session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        ).scalar_one()
        project = owned(session, pub.project_id, actor)
        if str(receipt["chat_id"]) != project.channel_id:
            raise DomainError("Receipt относится к другому каналу.")
        if pub.status != "UNKNOWN" or not pub.attempt_id:
            raise DomainError("Reconciliation допустима только для UNKNOWN.")
        pub.status, pub.receipt = "SENT", receipt
        attempt = session.get(SendAttempt, pub.attempt_id)
        attempt.status, attempt.receipt = "SENT", receipt
        record(session, project.id, "manual_reconciliation", receipt)
        return row_dict(pub)


def retry_unknown(publication_id: str, actor: int, confirmed_stopped: bool, accepted_risk: bool):
    if not confirmed_stopped or not accepted_risk:
        raise DomainError("Подтвердите остановку предыдущего исполнителя и риск дубликата.")
    with transaction() as session:
        pub = session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        ).scalar_one()
        project = owned(session, pub.project_id, actor)
        if pub.status != "UNKNOWN":
            raise DomainError("Новая неопределённая попытка допустима только после UNKNOWN.")
        if project.paused:
            raise DomainError("Проект на паузе.")
        previous_attempt = pub.attempt_id
        pub.revision += 1
        pub.status, pub.attempt_id, pub.receipt, pub.error = "SCHEDULED", "", {}, ""
        pub.not_before, pub.latest_at = now(), now() + timedelta(hours=1)
        approval = session.execute(select(Approval).where(Approval.version_id == pub.version_id)).scalar_one()
        approval.expires_at = now() + timedelta(hours=2)
        queue(
            session,
            "telok_publish",
            f"publish:{pub.id}:{pub.revision}",
            {"id": pub.id, "revision": pub.revision},
        )
        record(
            session,
            project.id,
            "manual_retry_risk_accepted",
            {"previous_attempt": previous_attempt, "revision": pub.revision, "actor": actor},
        )
        return row_dict(pub)
