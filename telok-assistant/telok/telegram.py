import asyncio

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from dbos import DBOS
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from telok import editorial, integrations, providers, publisher
from telok.db import transaction
from telok.domain import DomainError, queue, set_context
from telok.models import Context, Project, Receipt, Version, Work
from telok.schemas import Intent


def ingest(update_id: int, payload: dict):
    with transaction() as session:
        previous = session.execute(select(Receipt).where(Receipt.update_id == update_id)).scalar_one_or_none()
        if previous:
            return previous.id
        from telok.db import uid

        receipt_id = uid()
        created = session.execute(
            insert(Receipt)
            .values(id=receipt_id, update_id=update_id, payload=payload)
            .on_conflict_do_nothing(index_elements=[Receipt.update_id])
            .returning(Receipt.id)
        ).scalar_one_or_none()
        if not created:
            return session.execute(select(Receipt.id).where(Receipt.update_id == update_id)).scalar_one()
        queue(session, "telok_telegram", f"telegram:{update_id}", receipt_id)
        return receipt_id


def message(chat_id: int, value: str, markup: dict | None = None, reply_to: int | None = None):
    token = integrations.token()
    if not token:
        return
    with httpx.Client(timeout=20, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": value.encode("utf-16-le")[:7000].decode("utf-16-le", errors="ignore"),
                **({"reply_markup": markup} if markup else {}),
                **(
                    {"reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True}}
                    if reply_to
                    else {}
                ),
            },
        )
        if not response.json().get("ok"):
            raise DomainError("Не удалось отправить сообщение помощника.")
        return response.json()["result"]["message_id"]


@DBOS.step(name="notify_preview", retries_allowed=False)
def notify_preview(chat_id: int, version_id: str):
    from telok import storage

    with transaction() as session:
        version = session.get(Version, version_id)
        body = version.body
        assets = version.asset_ids
        revision = version.revision
    token = integrations.token()
    if not token:
        return
    if assets:
        data, meta = storage.read(assets[0])
        method, field = ("sendVideo", "video") if meta["mime"] == "video/mp4" else ("sendPhoto", "photo")
        with httpx.Client(timeout=30, transport=httpx.HTTPTransport(retries=0)) as client:
            response = client.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data={"chat_id": chat_id, "caption": body},
                files={field: ("preview.mp4" if field == "video" else "preview.png", data, meta["mime"])},
            )
            if not response.json().get("ok"):
                raise DomainError("Не удалось показать media preview.")
    else:
        message(chat_id, body)
    with transaction() as session:
        version = session.get(Version, version_id)
        project = session.get(Project, version.project_id)
        actor = project.owner_id
        warnings = [f["message"] for f in version.qa.get("findings", []) if f["category"] == "editorial"]
    from telok.callbacks import create_actions

    actions = create_actions(version_id, actor)
    summary = f"Версия {revision}\nID: {version_id}\n" + "\n".join(warnings)
    buttons = [
        [
            {"text": "Правка текста", "callback_data": actions["edit_text"]},
            {"text": "Правка изображения", "callback_data": actions["edit_image"]},
        ]
    ]
    if len(summary.encode("utf-16-le")) // 2 <= 3400:
        buttons.insert(
            0,
            [
                {
                    "text": "Подтвердить с замечаниями" if warnings else "Подтвердить",
                    "callback_data": actions["approve"],
                }
            ],
        )
    else:
        summary = f"Версия {revision}: отчёт длинный. Подтвердите материал после полного просмотра в панели."
    message(chat_id, summary, {"inline_keyboard": buttons})


@DBOS.step(name="handle_telegram_command", retries_allowed=False)
def handle_receipt(receipt_id: str):
    with transaction() as session:
        receipt = session.get(Receipt, receipt_id)
        payload = receipt.payload
    if payload.get("callback_query"):
        callback = payload["callback_query"]
        actor = callback.get("from", {}).get("id", 0)
        chat_id = callback.get("message", {}).get("chat", {}).get("id", 0)
        if actor not in integrations.allowed():
            return {"status": "IGNORED"}
        from telok.callbacks import apply

        token = integrations.token()
        if token:
            try:
                with httpx.Client(timeout=5, transport=httpx.HTTPTransport(retries=0)) as client:
                    client.post(
                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                        json={"callback_query_id": callback["id"]},
                    )
            except Exception:
                pass  # Best-effort UI acknowledgement has no business side effects.
        try:
            result = apply(callback.get("data", ""), actor)
            message(
                chat_id,
                "Пришлите описание правки следующим сообщением."
                if result["status"] == "AWAITING_EDIT"
                else "Действие: " + result["status"],
            )
            return result
        except DomainError as exc:
            message(chat_id, str(exc))
            return {"status": "FAILED"}
    msg = payload.get("message", {})
    actor = msg.get("from", {}).get("id", 0)
    chat_id = msg.get("chat", {}).get("id", 0)
    if actor not in integrations.allowed():
        return {"status": "IGNORED"}
    command = (msg.get("text") or msg.get("caption") or "").strip()
    if msg.get("chat", {}).get("type") != "private":
        return {"status": "IGNORED"}
    try:
        with transaction() as session:
            editing_context = session.execute(
                select(Context).where(Context.actor_id == actor)
            ).scalar_one_or_none()
            pending_legacy_edit = bool(editing_context and editing_context.pending_edit)
        if (not command.startswith("/") and not pending_legacy_edit) or command.startswith(
            ("/ask ", "/research ", "/content ", "/cancel ")
        ):
            from telok import assistant
            from telok.models import TaskMessage

            if command.startswith("/cancel "):
                result = assistant.cancel(command.split(maxsplit=1)[1], actor)
                message(chat_id, "Задача отменена.")
                return result
            with transaction() as session:
                context = session.execute(
                    select(Context).where(Context.actor_id == actor)
                ).scalar_one_or_none()
                project_id = context.project_id if context else None
                reply = msg.get("reply_to_message", {}).get("message_id")
                linked = (
                    session.execute(
                        select(TaskMessage).where(
                            TaskMessage.chat_id == chat_id, TaskMessage.message_id == reply
                        )
                    ).scalar_one_or_none()
                    if reply
                    else None
                )
                parent_id = linked.request_id if linked else None
                if linked:
                    project_id = linked.project_id
            docs, references = [], []
            if msg.get("photo"):
                project_id = project_id or assistant.inbox(actor)["id"]
                references.append(integrations.download_reference(msg["photo"][-1]["file_id"], project_id))
            if msg.get("document"):
                doc = msg["document"]
                docs.append(
                    integrations.download_document(doc["file_id"], doc.get("file_name", "document.txt"))
                )
            if msg.get("voice"):
                raise DomainError("Для голосового сообщения ещё не подключена транскрипция. Пришлите текст.")
            mode = "AUTO"
            for prefix, selected in (
                ("/ask ", "ASK"),
                ("/research ", "RESEARCH"),
                ("/content ", "CONTENT_CAMPAIGN"),
            ):
                if command.startswith(prefix):
                    command, mode = command[len(prefix) :], selected
                    break
            task = assistant.submit(
                actor,
                command or "Проанализируй приложенный документ",
                project_id,
                f"tg-assistant-{receipt.update_id}",
                parent_id,
                chat_id,
                msg.get("message_id"),
                docs,
                mode,
                reference_asset_ids=references,
            )
            mid = message(
                chat_id,
                "Принято. Задача: " + task["id"] + "\nДля продолжения ответьте на это сообщение.",
                reply_to=msg.get("message_id"),
            )
            if mid:
                with transaction() as session:
                    session.add(
                        TaskMessage(
                            project_id=task["project_id"],
                            request_id=task["id"],
                            chat_id=chat_id,
                            message_id=mid,
                        )
                    )
            return {"status": "QUEUED", "request_id": task["id"]}
        if command in {"/start", "/help"}:
            message(
                chat_id,
                "Telok • AI-команда\nПришлите задачу обычным сообщением.\n/content задача — контент-пакет\n/research тема — исследование\n/cancel ID — отмена\n/projects — проекты\n/use ID — выбрать\n/ideas 5 тема\n/plan 2026-10-05\n/produce задача\n/status\n/approve VERSION_ID\n/edit VERSION_ID text правка\n/pause • /resume",
            )
            return {"status": "OK"}
        with transaction() as session:
            projects = session.execute(select(Project).where(Project.owner_id == actor)).scalars().all()
            context = session.execute(select(Context).where(Context.actor_id == actor)).scalar_one_or_none()
            project_id = context.project_id if context else ""
        if command == "/projects":
            message(
                chat_id,
                "\n".join(f"{p.name} — /use {p.id}" for p in projects)
                or "Создайте проект в панели администратора.",
            )
            return {"status": "OK"}
        if command.startswith("/use "):
            set_context(actor, command.split(maxsplit=1)[1])
            message(chat_id, "Проект выбран.")
            return {"status": "OK"}
        if not project_id:
            if len(projects) == 1:
                project_id = projects[0].id
                set_context(actor, project_id)
            else:
                raise DomainError("Выберите проект через /projects.")
        if context and context.pending_edit and not command.startswith("/"):
            pending = dict(context.pending_edit)
            editorial.request_work(
                project_id,
                actor,
                "edit",
                {
                    "version_id": pending["version_id"],
                    "edit_scope": pending["scope"],
                    "brief": command,
                    "chat_id": chat_id,
                },
                f"tg-edit-{receipt.update_id}",
            )
            with transaction() as session:
                current = session.get(Context, context.id)
                if current.pending_edit == pending:
                    current.pending_edit = {}
            message(chat_id, "Правка принята.")
        elif command.startswith("/approve "):
            version_id = command.split(maxsplit=1)[1]
            result = publisher.approve(version_id, actor, [])
            message(chat_id, f"Публикация: {result['status']}.")
        elif command.startswith("/edit "):
            parts = command.split(maxsplit=3)
            if len(parts) != 4 or parts[2] not in {"text", "image", "all"}:
                raise DomainError("/edit VERSION_ID text|image|all описание")
            editorial.request_work(
                project_id,
                actor,
                "edit",
                {"version_id": parts[1], "edit_scope": parts[2], "brief": parts[3], "chat_id": chat_id},
                f"tg-edit-{receipt.update_id}",
            )
            message(chat_id, "Правка принята.")
        elif command == "/status":
            with transaction() as session:
                works = (
                    session.execute(
                        select(Work)
                        .where(Work.project_id == project_id)
                        .order_by(Work.created_at.desc())
                        .limit(5)
                    )
                    .scalars()
                    .all()
                )
            message(
                chat_id, "\n".join(f"{w.kind}: {w.status}\n{w.error}" for w in works) or "Нет активных задач."
            )
        elif command in {"/pause", "/resume"}:
            with transaction() as session:
                project = session.get(Project, project_id)
                project.paused = command == "/pause"
            message(
                chat_id,
                "Пауза включена."
                if command == "/pause"
                else "Пауза снята. При необходимости повторите остановленный запрос из панели.",
            )
        else:
            if command.startswith("/ideas"):
                parts = command.split(maxsplit=2)
                payload = {
                    "count": int(parts[1]) if len(parts) > 1 else 3,
                    "brief": parts[2] if len(parts) > 2 else "",
                }
                kind = "ideas"
            elif command.startswith("/plan"):
                parts = command.split(maxsplit=1)
                if len(parts) < 2:
                    raise DomainError("Укажите точную дату: /plan YYYY-MM-DD.")
                kind, payload = "plan", {"start": parts[1], "count": 7}
            elif command.startswith("/produce "):
                kind, payload = "generate", {"brief": command.split(maxsplit=1)[1], "format": "IMAGE_POST"}
            else:
                request_id = f"tg-intent-{receipt.update_id}"
                with transaction() as session:
                    project = session.get(Project, project_id)
                    intent_context = {
                        "message": command,
                        "brand": project.brand,
                        "timezone": project.timezone,
                    }
                intent = providers.text(project_id, request_id, "assistant", intent_context, Intent)
                if intent["missing_fields"]:
                    raise DomainError("Уточните: " + ", ".join(intent["missing_fields"]))
                kind = {"produce": "generate", "ideas": "ideas", "plan": "plan", "status": "status"}[
                    intent["intent"]
                ]
                if kind == "status":
                    message(chat_id, "Используйте /status для текущих задач.")
                    return {"status": "OK"}
                payload = {"brief": intent["brief"], "count": intent["count"], "format": "IMAGE_POST"}
                if kind == "plan":
                    if not intent["target_date"]:
                        raise DomainError("Укажите начало недели в формате YYYY-MM-DD.")
                    payload["start"] = intent["target_date"]
            payload["chat_id"] = chat_id
            work = editorial.request_work(project_id, actor, kind, payload, f"tg-work-{receipt.update_id}")
            message(chat_id, f"Принято. Задача {work['id']}.")
        return {"status": "OK"}
    except Exception as exc:
        message(
            chat_id,
            str(exc)
            if isinstance(exc, (DomainError, ValueError))
            else "Не удалось выполнить команду. Проверьте настройки в панели.",
        )
        return {"status": "FAILED"}


async def poll():
    if not integrations.token() or not integrations.allowed():
        raise DomainError("Для бота нужны TELOK_TELEGRAM_TOKEN и allowed Telegram IDs.")
    bot = Bot(integrations.token())
    with transaction() as session:
        maximum = session.execute(
            select(Receipt.update_id).order_by(Receipt.update_id.desc()).limit(1)
        ).scalar_one_or_none()
    offset = (maximum + 1) if maximum is not None else None
    try:
        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset, timeout=20, allowed_updates=["message", "callback_query"]
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(min(max(exc.retry_after, 1), 60))
                continue
            except TelegramNetworkError:
                await asyncio.sleep(3)
                continue
            for update in updates:
                # Commit command and queue before confirming it via next long-poll offset.
                payload = update.model_dump(mode="json", by_alias=True)
                actor = (
                    (payload.get("message") or payload.get("callback_query") or {})
                    .get("from", {})
                    .get("id", 0)
                )
                if actor in integrations.allowed():
                    ingest(update.update_id, payload)
                offset = update.update_id + 1
    finally:
        await bot.session.close()


def main():
    asyncio.run(poll())


@DBOS.step(name="notify_result", retries_allowed=False)
def notify_result(chat_id: int, request_id: str, result: dict):
    if result.get("ideas"):
        body = "\n\n".join(
            f"{index + 1}. {idea['title']}\n{idea['hook']}" for index, idea in enumerate(result["ideas"])
        )
    elif result.get("slots"):
        body = "\n".join(f"{slot['date']}: {slot['title']}" for slot in result["slots"])
    else:
        body = "Исследование готово. Гипотезы и пробелы evidence доступны в панели."
    message(chat_id, body[:3100] + "\n\nЗадача: " + request_id)


if __name__ == "__main__":
    main()
