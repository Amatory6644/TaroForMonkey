from datetime import timedelta

from sqlalchemy import select

from telok import providers, storage
from telok.db import now, transaction, uid
from telok.domain import DomainError, canonical, factual_checks, owned, queue, record, row_dict
from telok.models import Evidence, Item, Plan, Project, Publication, Version, Work
from telok.schemas import Concepts, Direction, Draft, Review, Strategy


def request_work(project_id: str, actor: int, kind: str, payload: dict, operation_id: str | None = None):
    if kind not in {"generate", "edit", "ideas", "plan", "research", "video"}:
        raise DomainError("Неизвестный workflow.")
    with transaction() as session:
        project = owned(session, project_id, actor)
        if project.paused:
            raise DomainError("Проект на паузе.")
        request_id = operation_id or uid()
        existing = session.get(Work, request_id)
        if existing:
            if existing.project_id != project_id:
                raise DomainError("Operation ID принадлежит другому проекту.")
            return row_dict(existing)
        data = dict(payload)
        linked_plan = None
        if data.get("slot_id") and kind == "generate":
            linked_plan = session.execute(
                select(Plan)
                .where(Plan.id == data.get("plan_id"), Plan.project_id == project_id)
                .with_for_update()
            ).scalar_one()
            slots = [dict(slot) for slot in linked_plan.slots]
            target = next((slot for slot in slots if slot["id"] == data["slot_id"]), None)
            if not target:
                raise DomainError("Slot не найден.")
            if target.get("work_id"):
                existing_slot_work = session.get(Work, target["work_id"])
                if existing_slot_work:
                    return row_dict(existing_slot_work)
            data["format"] = target["format"]
        if kind in {"generate", "edit"}:
            if data.get("format", "IMAGE_POST") not in {"TEXT_POST", "IMAGE_POST"}:
                raise DomainError("Поддерживаются TEXT_POST/IMAGE_POST.")
            if kind == "edit":
                version = session.get(Version, data.get("version_id", ""))
                if not version or version.project_id != project_id:
                    raise DomainError("Версия не найдена.")
                item = session.execute(
                    select(Item).where(Item.id == version.item_id).with_for_update()
                ).scalar_one()
                if version.id != item.current_version_id or version.revision != item.revision:
                    raise DomainError("Правка основана на устаревшей версии.")
                if version.format == "VIDEO_SHORT":
                    raise DomainError("Используйте видеоредактор для правки VIDEO_SHORT.")
                data.update(item_id=item.id, previous=version.id, format=version.format)
                if version.brief.get("slot_id"):
                    data.update(plan_id=version.brief["plan_id"], slot_id=version.brief["slot_id"])
                    linked_plan = session.execute(
                        select(Plan).where(Plan.id == data["plan_id"]).with_for_update()
                    ).scalar_one()
                    slots = [dict(slot) for slot in linked_plan.slots]
                    target = next(slot for slot in slots if slot["id"] == data["slot_id"])
                # Reserve revision now: already approved outgoing version cannot be silently sent during an edit.
                pubs = session.execute(
                    select(Publication).where(Publication.version_id == version.id).with_for_update()
                ).scalars()
                for pub in pubs:
                    if pub.status not in {"SENDING", "SENT", "UNKNOWN"}:
                        pub.status, pub.revision = "CANCELLED", pub.revision + 1
            else:
                item = Item(project_id=project_id)
                session.add(item)
                session.flush()
                data["item_id"] = item.id
            item.revision += 1
            data["revision"] = item.revision
        if kind == "video":
            previous_id = data.get("previous_video_id")
            if previous_id:
                version = session.get(Version, previous_id)
                if not version or version.project_id != project_id or version.format != "VIDEO_SHORT":
                    raise DomainError("Исходный VIDEO_SHORT не найден.")
                item = session.execute(
                    select(Item).where(Item.id == version.item_id).with_for_update()
                ).scalar_one()
                if item.current_version_id != version.id or item.revision != version.revision:
                    raise DomainError("Исходная видеоверсия устарела.")
                for pub in session.execute(
                    select(Publication).where(Publication.version_id == version.id).with_for_update()
                ).scalars():
                    if pub.status not in {"SENDING", "SENT", "UNKNOWN"}:
                        pub.status, pub.revision = "CANCELLED", pub.revision + 1
                if data.get("audio_asset_id") and data["audio_asset_id"] == version.brief.get(
                    "audio_asset_id"
                ):
                    before = [scene["narration"] for scene in version.brief["storyboard"]["scenes"]]
                    after = [scene["narration"] for scene in data["storyboard"]["scenes"]]
                    if before != after:
                        raise DomainError(
                            "При изменении narration загрузите новую озвучку либо используйте TTS."
                        )
                data["previous"] = version.id
            else:
                item = Item(project_id=project_id)
                session.add(item)
                session.flush()
            item.revision += 1
            data.update(item_id=item.id, revision=item.revision)
        data["snapshot"] = {
            "brand": project.brand,
            "brand_revision": project.brand_revision,
            "description": project.description,
            "timezone": project.timezone,
        }
        work = Work(
            id=request_id, project_id=project_id, kind=kind, payload=data, workflow_id=f"work:{request_id}:1"
        )
        session.add(work)
        session.flush()
        if linked_plan:
            target["work_id"], target["state"] = request_id, "IN_PRODUCTION"
            linked_plan.slots = slots
        queue(session, "telok_generate", work.workflow_id, request_id)
        record(session, project_id, "work_requested", {"id": request_id, "kind": kind})
        return row_dict(work)


def context(request_id: str) -> dict:
    with transaction() as session:
        work = session.get(Work, request_id)
        project = session.get(Project, work.project_id)
        if work.status == "CANCELLED" or project.paused:
            raise DomainError("Работа остановлена.")
        work.status = "RUNNING"
        evidence = [
            row_dict(entry)
            for entry in session.execute(select(Evidence).where(Evidence.project_id == project.id)).scalars()
        ]
        for index, fact in enumerate(work.payload["snapshot"]["brand"].get("facts", [])):
            evidence.append(
                {
                    "id": f"brand:{work.payload['snapshot']['brand_revision']}:{index}",
                    "claim": fact,
                    "excerpt": fact,
                    "kind": "owner_statement",
                    "assessment": "supports",
                    "valid_until": None,
                }
            )
        history = [
            entry.body[:350]
            for entry in session.execute(
                select(Version)
                .where(Version.project_id == project.id)
                .order_by(Version.created_at.desc())
                .limit(15)
            ).scalars()
        ]
        previous = session.get(Version, work.payload.get("previous", ""))
        return {
            "request_id": request_id,
            "project_id": project.id,
            "kind": work.kind,
            **work.payload,
            "evidence": evidence,
            "history": history,
            "previous_text": previous.body if previous else "",
            "previous_draft": previous.brief.get("draft", {}) if previous else {},
            "previous_assets": previous.asset_ids if previous else [],
        }


def produce(ctx: dict) -> dict:
    value = providers.text(ctx["project_id"], ctx["request_id"], "producer", ctx, Draft)
    if ctx["kind"] == "edit" and ctx.get("edit_scope") == "image":
        value["text"] = ctx["previous_text"]
        value["claims"] = ctx["previous_draft"].get("claims", [])
    limit = 4096 if ctx.get("format") == "TEXT_POST" else 1024
    if len(value["text"].encode("utf-16-le")) // 2 > limit:
        raise DomainError("Текст превышает platform limit; image call не запущен.")
    errors = factual_checks(value, ctx["evidence"])
    if errors:
        raise DomainError("; ".join(errors))
    return value


def make_image(ctx: dict, draft: dict) -> list[str]:
    if ctx.get("format") == "TEXT_POST":
        return []
    if ctx["kind"] == "edit" and ctx.get("edit_scope") == "text" and ctx["previous_assets"]:
        return ctx["previous_assets"]
    spec = dict(draft["visual"])
    refs = ctx["snapshot"]["brand"].get("references", [])
    if ctx["kind"] == "edit" and ctx.get("edit_scope") in {"image", "all"}:
        refs = [*ctx["previous_assets"], *refs]
    spec["required_references"] = list(dict.fromkeys([*refs, *spec.get("required_references", [])]))
    return [providers.image(ctx["project_id"], ctx["request_id"], spec)]


def review(ctx: dict, draft: dict, assets: list[str]) -> dict:
    value = providers.text(
        ctx["project_id"],
        ctx["request_id"],
        "reviewer",
        {"brand": ctx["snapshot"]["brand"], "draft": draft, "evidence": ctx["evidence"]},
        Review,
        assets,
    )
    hard = factual_checks(draft, ctx["evidence"])
    if len(draft["text"].encode("utf-16-le")) // 2 > (1024 if assets else 4096):
        hard.append("Текст превышает лимит выбранной одиночной Telegram-публикации.")
    for asset in assets:
        storage.read(asset, ctx["project_id"])
    return {"hard": hard, **value}


def release(
    session,
    project: Project,
    item: Item,
    request_id: str,
    body: str,
    assets: list[str],
    brief: dict,
    qa: dict,
    demo: bool = False,
    format_name: str | None = None,
):
    if not body.strip():
        raise DomainError("Пустой текст нельзя выпускать.")
    if len(body.encode("utf-16-le")) // 2 > (1024 if assets else 4096):
        raise DomainError("Текст превышает ограничения выбранного одиночного сообщения.")
    if len(assets) > 1:
        raise DomainError("Первый релиз отправляет один media asset.")
    refs = []
    for asset_id in assets:
        data, meta = storage.read(asset_id, project.id)
        expected_mime = {"image/png", "image/jpeg"} if format_name != "VIDEO_SHORT" else {"video/mp4"}
        if meta["mime"] not in expected_mime:
            raise DomainError("Asset MIME не соответствует выбранному формату.")
        refs.append(
            {
                "id": asset_id,
                "sha256": meta["sha256"],
                "mime": meta["mime"],
                "object_version": meta["info"].get("object_version", ""),
            }
        )
    format_name = format_name or ("IMAGE_POST" if refs else "TEXT_POST")
    method = {"TEXT_POST": "sendMessage", "IMAGE_POST": "sendPhoto", "VIDEO_SHORT": "sendVideo"}[format_name]
    manifest = {
        "schema_version": 1,
        "renderer_version": "plain-text-1",
        "project_id": project.id,
        "channel_id": project.channel_id,
        "method": method,
        "text": body,
        "entities": [],
        "assets": refs,
        "link_preview_options": {"is_disabled": True},
        "show_caption_above_media": False,
        "has_spoiler": False,
        "protect_content": False,
    }
    version = Version(
        project_id=project.id,
        item_id=item.id,
        revision=item.revision,
        request_id=request_id,
        body=body,
        format=format_name,
        asset_ids=assets,
        brief=brief,
        qa=qa,
        manifest=manifest,
        manifest_hash=canonical(manifest),
        demo=demo,
        status="CHANGES_REQUIRED"
        if qa.get("hard") or any(f["category"] in {"factual", "brand"} for f in qa.get("findings", []))
        else "READY_FOR_REVIEW",
    )
    session.add(version)
    session.flush()
    if item.current_version_id:
        previous = session.get(Version, item.current_version_id)
        previous.status = "SUPERSEDED"
    item.current_version_id = version.id
    return version


def save_release(ctx: dict, draft: dict, assets: list[str], qa: dict) -> dict:
    with transaction() as session:
        work = session.get(Work, ctx["request_id"])
        existing = session.execute(select(Version).where(Version.request_id == work.id)).scalar_one_or_none()
        if existing:
            return row_dict(existing)
        project = session.get(Project, ctx["project_id"])
        item = session.execute(select(Item).where(Item.id == ctx["item_id"]).with_for_update()).scalar_one()
        if work.status == "CANCELLED" or project.paused or item.revision != ctx["revision"]:
            work.status = "CANCELLED"
            return {"status": "CANCELLED", "reason": "stale_or_cancelled"}
        version = release(
            session,
            project,
            item,
            work.id,
            draft["text"],
            assets,
            {
                "snapshot": ctx["snapshot"],
                "brief": ctx.get("brief", ""),
                "draft": draft,
                **({"plan_id": ctx["plan_id"], "slot_id": ctx["slot_id"]} if ctx.get("slot_id") else {}),
            },
            qa,
        )
        work.status = version.status
        work.result = {"version_id": version.id}
        if ctx.get("slot_id"):
            plan = session.execute(
                select(Plan).where(Plan.id == ctx["plan_id"]).with_for_update()
            ).scalar_one()
            slots = [dict(slot) for slot in plan.slots]
            target = next(slot for slot in slots if slot["id"] == ctx["slot_id"])
            if target.get("work_id") == work.id:
                target["version_id"], target["state"] = version.id, version.status
                plan.slots = slots
        record(session, project.id, "release_created", {"version": version.id, "hash": version.manifest_hash})
        return row_dict(version)


def ideas_or_plan(ctx: dict) -> dict:
    count = max(1, min(int(ctx.get("count", 3)), 20))
    output = providers.text(ctx["project_id"], ctx["request_id"], "ideas", {**ctx, "count": count}, Concepts)
    ideas = output["ideas"]
    if len(ideas) != count or len({idea["title"].casefold() for idea in ideas}) != count:
        raise DomainError("Provider не соблюдал количество/разнообразие идей.")
    ranking = providers.text(
        ctx["project_id"],
        ctx["request_id"],
        "director",
        {"ideas": ideas, "brand": ctx["snapshot"]["brand"]},
        Direction,
    )
    if sorted(ranking["ranked_indexes"]) != list(range(count)):
        raise DomainError("Director вернул некорректное ранжирование.")
    ranked = [{"id": uid(), **ideas[index]} for index in ranking["ranked_indexes"]]
    with transaction() as session:
        work = session.get(Work, ctx["request_id"])
        if work.status == "CANCELLED":
            return {"status": "CANCELLED"}
        if ctx["kind"] == "plan":
            from telok.domain import calendar_slots

            slots = calendar_slots(
                ranked,
                ctx.get("start", (now() + timedelta(days=1)).date().isoformat()),
                ctx["snapshot"]["timezone"],
            )
            plan = Plan(project_id=ctx["project_id"], slots=slots)
            session.add(plan)
            session.flush()
            work.result = {"plan_id": plan.id, "slots": slots}
        else:
            work.result = {"ideas": ranked, "rationale": ranking["rationale"]}
        work.status = "SUCCEEDED"
        return work.result


def strategy(ctx: dict):
    output = providers.text(ctx["project_id"], ctx["request_id"], "strategist", ctx, Strategy)
    with transaction() as session:
        work = session.get(Work, ctx["request_id"])
        if work.status != "CANCELLED":
            work.result, work.status = output, "SUCCEEDED"
    return output
