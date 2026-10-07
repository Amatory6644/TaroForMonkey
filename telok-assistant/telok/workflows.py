from datetime import datetime, timedelta

from dbos import DBOS
from sqlalchemy import select

from telok import editorial, publisher
from telok.db import now, transaction
from telok.domain import DomainError
from telok.models import Project, Version, Work

prepare = DBOS.step(name="prepare_context")(editorial.context)
produce = DBOS.step(name="produce_text")(editorial.produce)
image = DBOS.step(name="produce_image")(editorial.make_image)
review = DBOS.step(name="review_final")(editorial.review)
save = DBOS.step(name="save_release")(editorial.save_release)
ideas = DBOS.step(name="ideas_plan")(editorial.ideas_or_plan)
strategy = DBOS.step(name="strategy")(editorial.strategy)


@DBOS.step(name="mark_error")
def mark_error(request_id: str, message: str):
    with transaction() as session:
        work = session.get(Work, request_id)
        if work and work.status != "CANCELLED":
            work.status, work.error = (
                ("PAUSED" if session.get(Project, work.project_id).paused else "FAILED"),
                message[:500],
            )


@DBOS.workflow(name="telok_generate", serialization_type="portable_json")
def generate(request_id: str) -> dict:
    try:
        ctx = prepare(request_id)
        if ctx["kind"] == "video":
            return render_video(ctx)
        if ctx["kind"] in {"ideas", "plan", "research"}:
            result = strategy(ctx) if ctx["kind"] == "research" else ideas(ctx)
            if ctx.get("chat_id"):
                from telok.telegram import notify_result

                try:
                    notify_result(ctx["chat_id"], request_id, result)
                except Exception:
                    notification_error(ctx["project_id"], request_id)
            return result
        draft = produce(ctx)
        assets = image(ctx, draft)
        qa = review(ctx, draft, assets)
        result = save(ctx, draft, assets, qa)
        if ctx.get("chat_id") and result.get("id"):
            from telok.telegram import notify_preview

            try:
                notify_preview(ctx["chat_id"], result["id"])
            except Exception:
                notification_error(ctx["project_id"], request_id)
        return {"version_id": result.get("id"), "status": result.get("status")}
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, DomainError)
            else f"Шаг не завершён ({type(exc).__name__}). Проверьте настройки provider и trace."
        )
        mark_error(request_id, message)
        return {"status": "FAILED", "error": message}


@DBOS.step(name="send_publication", retries_allowed=False)
def send_publication(payload: dict) -> dict:
    return publisher.send(payload["id"], payload["revision"])


@DBOS.workflow(name="telok_publish", serialization_type="portable_json")
def publish_job(payload: dict) -> dict:
    return send_publication(payload)


@DBOS.step(name="campaign_tick")
def campaign_tick():
    from telok.editorial import request_work

    with transaction() as session:
        projects = [
            project.id
            for project in session.execute(select(Project)).scalars()
            if project.policy.get("enabled") and not project.paused
        ]
    for project_id in projects:
        with transaction() as session:
            project = session.execute(
                select(Project).where(Project.id == project_id).with_for_update()
            ).scalar_one()
            policy = dict(project.policy)
            maximum = min(int(policy.get("max_pending", 3)), 10)
            pending = (
                session.execute(
                    select(Version).where(
                        Version.project_id == project_id,
                        Version.status.in_(["READY_FOR_REVIEW", "CHANGES_REQUIRED"]),
                    )
                )
                .scalars()
                .all()
            )
            running = (
                session.execute(
                    select(Work).where(Work.project_id == project_id, Work.status.in_(["QUEUED", "RUNNING"]))
                )
                .scalars()
                .all()
            )
            next_at = datetime.fromisoformat(policy.get("next_at", now().isoformat()))
            if len(pending) + len(running) >= maximum or next_at > now():
                continue
            operation_id = policy.get("next_operation_id")
            if not operation_id:
                from telok.db import uid

                operation_id = uid()
            # Keep the same operation key across a crash before enqueue.
            policy["next_operation_id"] = operation_id
            project.policy = policy
            actor = project.owner_id
            brief = policy.get(
                "brief", "Создай нейтральный вопрос аудитории без неизвестных фактов о бренде."
            )
        request_work(project_id, actor, "generate", {"brief": brief, "format": "IMAGE_POST"}, operation_id)
        with transaction() as session:
            project = session.execute(
                select(Project).where(Project.id == project_id).with_for_update()
            ).scalar_one()
            policy = dict(project.policy)
            policy["next_at"] = (
                now() + timedelta(hours=max(1, int(policy.get("every_hours", 24))))
            ).isoformat()
            policy.pop("next_operation_id", None)
            project.policy = policy


@DBOS.workflow(name="telok_campaign", serialization_type="portable_json")
def campaign_job(scheduled_at: datetime, actual_at: datetime):
    campaign_tick()


@DBOS.workflow(name="telok_telegram", serialization_type="portable_json")
def telegram_job(receipt_id: str):
    from telok.telegram import handle_receipt

    return handle_receipt(receipt_id)


@DBOS.workflow(name="telok_probe", serialization_type="portable_json")
def probe(payload: dict):
    first = probe_checkpoint(payload)
    if payload.get("sleep"):
        DBOS.sleep(payload["sleep"])
    return first


@DBOS.step(name="probe_checkpoint")
def probe_checkpoint(payload: dict):
    from telok.domain import record

    with transaction() as session:
        record(session, "", "probe_step", {"probe_id": payload["id"]})
    return {"checkpoint": payload["id"]}


@DBOS.step(name="notification_error")
def notification_error(project_id: str, request_id: str):
    from telok.domain import record

    with transaction() as session:
        record(session, project_id, "notification_failed", {"request_id": request_id})


@DBOS.step(name="render_video", retries_allowed=False)
def render_video(ctx: dict):
    from telok.video import render_release

    with transaction() as session:
        project = session.get(Project, ctx["project_id"])
        actor = project.owner_id
    return render_release(ctx["project_id"], actor, {**ctx, "request_id": ctx["request_id"]})
