from datetime import timedelta

from sqlalchemy import select

from telok.db import now, transaction
from telok.domain import DomainError, owned, record, set_context
from telok.models import Callback, Context, Item, Version


def create_actions(version_id: str, actor: int) -> dict[str, str]:
    with transaction() as session:
        version = session.get(Version, version_id)
        if not version:
            raise DomainError("Версия не найдена.")
        owned(session, version.project_id, actor)
        warnings = [f["message"] for f in version.qa.get("findings", []) if f["category"] == "editorial"]
        tokens = {}
        for action in ["approve", "edit_text", "edit_image"]:
            token = Callback(
                project_id=version.project_id,
                version_id=version_id,
                actor_id=actor,
                action=action,
                expires_at=now() + timedelta(hours=24),
                payload={"accepted_findings": warnings},
            )
            session.add(token)
            session.flush()
            tokens[action] = "t:" + token.id
        return tokens


def apply(token_value: str, actor: int) -> dict:
    if not token_value.startswith("t:"):
        raise DomainError("Неизвестное действие.")
    with transaction() as session:
        token = session.execute(
            select(Callback).where(Callback.id == token_value[2:]).with_for_update()
        ).scalar_one_or_none()
        if not token or token.actor_id != actor or token.expires_at < now():
            raise DomainError("Кнопка недоступна или истекла.")
        owned(session, token.project_id, actor)
        if token.consumed:
            return {"status": "ALREADY_USED"}
        version = session.get(Version, token.version_id)
        item = session.get(Item, version.item_id)
        if item.current_version_id != version.id or item.revision != version.revision:
            raise DomainError("Кнопка относится к старой версии.")
        action, project_id, version_id = token.action, token.project_id, token.version_id
        accepted = token.payload.get("accepted_findings", [])
    if action == "approve":
        from telok.publisher import approve

        result = approve(version_id, actor, accepted)
    elif action in {"edit_text", "edit_image"}:
        set_context(actor, project_id)
        with transaction() as session:
            context = session.execute(
                select(Context).where(Context.actor_id == actor).with_for_update()
            ).scalar_one()
            context.pending_edit = {
                "version_id": version_id,
                "scope": "text" if action == "edit_text" else "image",
            }
        result = {"status": "AWAITING_EDIT", "scope": action}
    else:
        raise DomainError("Неизвестное действие.")
    with transaction() as session:
        token = session.get(Callback, token_value[2:])
        token.consumed = True
        record(
            session,
            project_id,
            "callback_applied",
            {"action": action, "version_id": version_id, "actor": actor},
        )
    return result
