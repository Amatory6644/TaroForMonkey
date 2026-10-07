import hmac
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm.exc import NoResultFound

from telok import editorial, publisher, storage
from telok.db import engine, now, transaction, uid
from telok.domain import (
    DomainError,
    add_evidence,
    create_project,
    owned,
    queue,
    record,
    row_dict,
    update_brand,
)
from telok.models import (
    Asset,
    Audit,
    Evidence,
    Feedback,
    Plan,
    Project,
    ProviderAttempt,
    Publication,
    Version,
    Work,
)
from telok.settings import settings

app = FastAPI(title="Telok Editorial API", version="0.1.0", docs_url=None, redoc_url=None)
WEB = Path(__file__).parent / "web"


@app.middleware("http")
async def access(request: Request, call_next):
    cfg = settings()
    if cfg.env == "development" and request.headers.get("host", "").split(":")[0] not in {
        "127.0.0.1",
        "localhost",
        "testserver",
    }:
        return JSONResponse(
            {"detail": "Development UI РґРѕСЃС‚СѓРїРµРЅ С‚РѕР»СЊРєРѕ РЅР° localhost."}, status_code=403
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin mutation Р·Р°РїСЂРµС‰РµРЅР°."}, status_code=403)
        if request.headers.get("x-telok-client") != "dashboard":
            return JSONResponse({"detail": "РќСѓР¶РµРЅ X-Telok-Client: dashboard."}, status_code=403)
    if (
        cfg.env == "production"
        and request.url.path.startswith("/api/")
        and request.url.path not in {"/api/login", "/api/health"}
    ):
        token = request.headers.get("authorization", "").removeprefix("Bearer ") or request.cookies.get(
            "telok_session", ""
        )
        if not hmac.compare_digest(token, cfg.admin_token.get_secret_value()):
            return JSONResponse(
                {"detail": "РўСЂРµР±СѓРµС‚СЃСЏ РІС…РѕРґ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°."}, status_code=401
            )
    return await call_next(request)


@app.exception_handler(DomainError)
async def domain_error(request: Request, error: DomainError):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(ValueError)
async def validation_error(request: Request, error: ValueError):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.get("/")
def home():
    return FileResponse(WEB / "index.html")


@app.get("/app.js")
def javascript():
    return FileResponse(WEB / "app.js", media_type="application/javascript")


@app.get("/style.css")
def stylesheet():
    return FileResponse(WEB / "style.css", media_type="text/css")


@app.post("/api/login")
def login(body: dict):
    cfg = settings()
    if not hmac.compare_digest(str(body.get("token", "")), cfg.admin_token.get_secret_value()):
        raise HTTPException(401, "РќРµРІРµСЂРЅС‹Р№ РєР»СЋС‡ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°.")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "telok_session",
        cfg.admin_token.get_secret_value(),
        httponly=True,
        secure=cfg.env == "production",
        samesite="strict",
        max_age=3600,
    )
    return response


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"database": "ready"}


@app.get("/api/configuration")
def configuration():
    cfg = settings()
    return {
        "mode": cfg.env,
        "text_ready": cfg.provider_ready(),
        "image_ready": cfg.provider_ready(True),
        "telegram_ready": bool(cfg.telegram_token.get_secret_value()),
        "storage": cfg.storage,
        "text_model": cfg.text_model,
        "image_model": cfg.image_model,
        "task_budget": cfg.task_budget_usd,
        "daily_budget": cfg.daily_budget_usd,
        "auto_enabled": cfg.enable_auto,
        "publishing_enabled": cfg.publishing_enabled,
    }


@app.get("/api/projects")
def projects():
    with transaction() as session:
        return [
            row_dict(p)
            for p in session.execute(select(Project).where(Project.owner_id == settings().owner_id)).scalars()
        ]


@app.post("/api/projects")
def project_create(body: dict):
    return create_project(settings().owner_id, str(body.get("name", "")), str(body.get("description", "")))


@app.get("/api/projects/{project_id}/overview")
def overview(project_id: str):
    with transaction() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        project = owned(session, project_id, settings().owner_id)

        def rows(cls, limit=30):
            return [
                row_dict(r)
                for r in session.execute(
                    select(cls)
                    .where(cls.project_id == project_id)
                    .order_by(cls.created_at.desc())
                    .limit(limit)
                ).scalars()
            ]

        return {
            "project": row_dict(project),
            "metrics": {
                "charged_usd": float(
                    session.execute(
                        select(func.coalesce(func.sum(ProviderAttempt.charged), 0)).where(
                            ProviderAttempt.project_id == project_id
                        )
                    ).scalar()
                ),
                "reserved_usd": float(
                    session.execute(
                        select(func.coalesce(func.sum(ProviderAttempt.reserved), 0)).where(
                            ProviderAttempt.project_id == project_id,
                            ProviderAttempt.status.in_(["RESERVED", "UNKNOWN"]),
                        )
                    ).scalar()
                ),
                "feedback_count": session.execute(
                    select(func.count()).select_from(Feedback).where(Feedback.project_id == project_id)
                ).scalar(),
                "views": None,
                "clicks": None,
                "ctr": None,
                "telegram_metrics_source": "unavailable_without_authorized_external_import",
            },
            "versions": rows(Version),
            "works": rows(Work),
            "plans": rows(Plan, 5),
            "publications": rows(Publication),
            "evidence": rows(Evidence),
            "feedback": rows(Feedback),
            "attempts": rows(ProviderAttempt, 100),
            "audit": rows(Audit, 40),
            "assets": rows(Asset, 50),
        }


@app.put("/api/projects/{project_id}/brand")
def brand_edit(project_id: str, body: dict):
    return update_brand(
        project_id,
        settings().owner_id,
        body["brand"],
        int(body["expected_revision"]),
        body.get("description"),
    )


@app.post("/api/projects/{project_id}/channel")
def channel_edit(project_id: str, body: dict):
    channel = str(body.get("channel_id", "")).strip()
    if channel and not (channel.startswith("@") or (channel.startswith("-") and channel[1:].isdigit())):
        raise DomainError("РЈРєР°Р¶РёС‚Рµ @username Р»РёР±Рѕ РѕС‚СЂРёС†Р°С‚РµР»СЊРЅС‹Р№ channel ID.")
    with transaction() as session:
        project = owned(session, project_id, settings().owner_id)
        project.channel_id = channel
        record(session, project_id, "channel_changed", {"channel": channel})
        return row_dict(project)


@app.post("/api/projects/{project_id}/evidence")
def evidence_create(project_id: str, body: dict):
    return add_evidence(
        project_id,
        settings().owner_id,
        body["claim"],
        body["excerpt"],
        body.get("url", ""),
        body.get("kind", "owner_statement"),
        body.get("assessment", "insufficient"),
    )


@app.post("/api/projects/{project_id}/assets")
async def asset_upload(project_id: str, file: UploadFile):
    with transaction() as session:
        owned(session, project_id, settings().owner_id)
    data = await file.read(50_000_001)
    asset_id = storage.put(project_id, data, file.content_type or "")
    return {"id": asset_id}


@app.get("/api/assets/{asset_id}")
def asset_get(asset_id: str):
    with transaction() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise HTTPException(404)
        owned(session, asset.project_id, settings().owner_id)
        project_id = asset.project_id
    data, meta = storage.read(asset_id, project_id)
    return Response(
        data,
        media_type=meta["mime"],
        headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/projects/{project_id}/work")
def work_create(project_id: str, body: dict):
    kind = body.get("kind", "generate")
    payload = dict(body.get("payload", {}))
    if not payload.get("brief") and kind in {"generate", "edit"}:
        raise DomainError("РћРїРёС€РёС‚Рµ Р·Р°РґР°С‡Сѓ РёР»Рё РїСЂР°РІРєСѓ.")
    if kind == "plan":
        payload["count"] = min(7, max(1, int(payload.get("count", 7))))
    return editorial.request_work(project_id, settings().owner_id, kind, payload, body.get("operation_id"))


@app.post("/api/works/{request_id}/control")
def work_control(request_id: str, body: dict):
    action = body.get("action")
    with transaction() as session:
        work = session.execute(select(Work).where(Work.id == request_id).with_for_update()).scalar_one()
        project = owned(session, work.project_id, settings().owner_id)
        if action == "cancel":
            work.status = "CANCELLED"
        elif action == "retry" and work.status in {"FAILED", "PAUSED"}:
            if project.paused:
                raise DomainError("РЎРЅР°С‡Р°Р»Р° СЃРЅРёРјРёС‚Рµ РїСЂРѕРµРєС‚ СЃ РїР°СѓР·С‹.")
            from telok.models import Item

            if work.payload.get("item_id"):
                item = session.get(Item, work.payload["item_id"])
                if item.revision != work.payload["revision"]:
                    raise DomainError(
                        "Р—Р°РїСЂРѕСЃ СѓСЃС‚Р°СЂРµР». РЎРѕР·РґР°Р№С‚Рµ РЅРѕРІСѓСЋ РїСЂР°РІРєСѓ."
                    )
            work.status, work.error, work.workflow_id = "QUEUED", "", f"work:{work.id}:{uid()}"
            queue(session, "telok_generate", work.workflow_id, work.id)
        else:
            raise DomainError("РќРµРґРѕРїСѓСЃС‚РёРјРѕРµ РґРµР№СЃС‚РІРёРµ.")
        return row_dict(work)


@app.post("/api/projects/{project_id}/control")
def project_control(project_id: str, body: dict):
    with transaction() as session:
        project = owned(session, project_id, settings().owner_id)
        action = body.get("action")
        if action not in {"pause", "resume"}:
            raise DomainError("РќСѓР¶РЅС‹ pause РёР»Рё resume.")
        project.paused = action == "pause"
        if action == "resume":
            for pub in session.execute(
                select(Publication).where(
                    Publication.project_id == project_id, Publication.status == "SCHEDULED"
                )
            ).scalars():
                pub.revision += 1
                queue(
                    session,
                    "telok_publish",
                    f"publish:{pub.id}:{pub.revision}",
                    {"id": pub.id, "revision": pub.revision},
                    max(now(), pub.not_before),
                )
        record(session, project_id, action, {})
        return row_dict(project)


@app.post("/api/versions/{version_id}/approve")
def version_approve(version_id: str, body: dict):
    at = datetime.fromisoformat(body["at"]) if body.get("at") else None
    return publisher.approve(version_id, settings().owner_id, body.get("accepted_findings", []), at)


@app.post("/api/publications/{publication_id}/schedule")
def publication_schedule(publication_id: str, body: dict):
    at = datetime.fromisoformat(body["at"]) if body.get("at") else None
    return publisher.reschedule(publication_id, settings().owner_id, at, body.get("cancel", False))


@app.post("/api/publications/{publication_id}/reconcile")
def publication_reconcile(publication_id: str, body: dict):
    return publisher.reconcile(publication_id, settings().owner_id, body)


@app.post("/api/versions/{version_id}/feedback")
def feedback(version_id: str, body: dict):
    with transaction() as session:
        version = session.get(Version, version_id)
        if not version:
            raise HTTPException(404, "Р’РµСЂСЃРёСЏ РЅРµ РЅР°Р№РґРµРЅР°.")
        owned(session, version.project_id, settings().owner_id)
        decision = body.get("decision", "revise")
        if decision not in {"accept", "reject", "revise"}:
            raise DomainError("РќРµРёР·РІРµСЃС‚РЅР°СЏ РѕС†РµРЅРєР°.")
        session.add(
            Feedback(
                project_id=version.project_id,
                version_id=version_id,
                decision=decision,
                notes=str(body.get("notes", "")),
                editor_minutes=max(0, float(body.get("editor_minutes", 0))),
            )
        )
        return {"ok": True}


@app.post("/api/projects/{project_id}/campaign")
def campaign(project_id: str, body: dict):
    if body.get("auto"):
        raise DomainError("AUTO activation РїРѕРєР° РЅРµ РїСЂРѕР№РґРµРЅР°. Р”РѕСЃС‚СѓРїРµРЅ SEMI_AUTO.")
    policy = {
        "enabled": bool(body.get("enabled")),
        "max_pending": min(10, max(1, int(body.get("max_pending", 3)))),
        "every_hours": max(1, int(body.get("every_hours", 24))),
        "brief": str(body.get("brief", "")),
        "next_at": now().isoformat(),
        "mode": "SEMI_AUTO",
    }
    if policy["enabled"] and not settings().provider_ready(True):
        raise DomainError("РЎРЅР°С‡Р°Р»Р° РЅР°СЃС‚СЂРѕР№С‚Рµ СЂРµР°Р»СЊРЅС‹Рµ providers Рё Р±СЋРґР¶РµС‚.")
    with transaction() as session:
        project = owned(session, project_id, settings().owner_id)
        project.policy = policy
        return row_dict(project)


@app.post("/api/projects/{project_id}/video")
def video(project_id: str, body: dict):
    from telok.schemas import Storyboard

    Storyboard.model_validate(body["storyboard"])
    if body.get("owner_reviewed_facts") is not True:
        raise DomainError(
            "РџРѕРґС‚РІРµСЂРґРёС‚Рµ РїСЂРѕРІРµСЂРєСѓ С‚РµРєСЃС‚Р° Рё narration РІР»Р°РґРµР»СЊС†РµРј."
        )
    return editorial.request_work(project_id, settings().owner_id, "video", body, body.get("operation_id"))


@app.post("/api/login/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("telok_session")
    return response


@app.post("/api/projects/{project_id}/manual")
def manual_release(project_id: str, body: dict):
    from telok.domain import factual_checks
    from telok.editorial import release
    from telok.models import Item

    if not body.get("owner_reviewed_facts"):
        raise DomainError(
            "Р”Р»СЏ СЂСѓС‡РЅРѕРіРѕ С‚РµРєСЃС‚Р° С‚СЂРµР±СѓРµС‚СЃСЏ СЏРІРЅР°СЏ РїСЂРѕРІРµСЂРєР° С„Р°РєС‚РѕРІ РІР»Р°РґРµР»СЊС†РµРј."
        )
    text_body = str(body.get("text", ""))
    assets = body.get("asset_ids", [])
    if not isinstance(assets, list) or len(assets) > 1:
        raise DomainError("РќСѓР¶РµРЅ СЃРїРёСЃРѕРє РјР°РєСЃРёРјСѓРј РёР· РѕРґРЅРѕРіРѕ media asset.")
    with transaction() as session:
        project = owned(session, project_id, settings().owner_id)
        evidence = [
            row_dict(e)
            for e in session.execute(select(Evidence).where(Evidence.project_id == project_id)).scalars()
        ]
        errors = factual_checks({"claims": body.get("claims", [])}, evidence)
        if errors:
            raise DomainError("; ".join(errors))
        for asset_id in assets:
            data, meta = storage.read(asset_id, project_id)
            if meta["info"].get("demo"):
                raise DomainError(
                    "Demo asset РЅРµР»СЊР·СЏ РїСЂРµРІСЂР°С‚РёС‚СЊ РІ СЂРµР°Р»СЊРЅСѓСЋ РїСѓР±Р»РёРєР°С†РёСЋ."
                )
        item = Item(project_id=project_id, revision=1)
        session.add(item)
        session.flush()
        version = release(
            session,
            project,
            item,
            uid(),
            text_body,
            assets,
            {
                "snapshot": {"brand_revision": project.brand_revision},
                "source": "owner",
                "claims": body.get("claims", []),
            },
            {
                "hard": [],
                "findings": [],
                "summary": "РўРµС…РЅРёС‡РµСЃРєРёР№ РїР°РєРµС‚ РїСЂРѕРІРµСЂРµРЅ. Р¤Р°РєС‚С‹ РїСЂРѕРІРµСЂРµРЅС‹ РІР»Р°РґРµР»СЊС†РµРј; AI-review РЅРµ РІС‹РїРѕР»РЅСЏР»СЃСЏ.",
            },
        )
        record(
            session, project_id, "manual_release", {"version_id": version.id, "actor": settings().owner_id}
        )
        return row_dict(version)


@app.exception_handler(NoResultFound)
async def not_found(request: Request, error):
    return JSONResponse({"detail": "Р—Р°РїРёСЃСЊ РЅРµ РЅР°Р№РґРµРЅР°."}, status_code=404)


@app.post("/api/publications/{publication_id}/retry-unknown")
def publication_retry_unknown(publication_id: str, body: dict):
    return publisher.retry_unknown(
        publication_id,
        settings().owner_id,
        body.get("previous_executor_stopped") is True,
        body.get("accept_duplicate_risk") is True,
    )


@app.post("/api/projects/{project_id}/sources/import")
def source_import(project_id: str, body: dict):
    from telok.domain import fetch_source

    with transaction() as session:
        owned(session, project_id, settings().owner_id)
    url, excerpt = str(body.get("url", "")), str(body.get("excerpt", "")).strip()
    text_body = fetch_source(url)
    if not excerpt or excerpt not in text_body:
        raise DomainError("Р¦РёС‚Р°С‚Р° РЅРµ РЅР°Р№РґРµРЅР° РІ РїРѕР»СѓС‡РµРЅРЅРѕРј РёСЃС‚РѕС‡РЅРёРєРµ.")
    return add_evidence(
        project_id, settings().owner_id, str(body.get("claim", "")), excerpt, url, "external", "insufficient"
    )


@app.post("/api/attempts/{attempt_id}/settle")
def settle_attempt(attempt_id: str, body: dict):
    from telok import budget

    with transaction() as session:
        attempt = session.get(ProviderAttempt, attempt_id)
        if not attempt:
            raise HTTPException(404)
        owned(session, attempt.project_id, settings().owner_id)
    budget.settle(attempt_id, float(body["charged_usd"]), str(body["provenance"]))
    return {"ok": True}


@app.get("/api/versions/{version_id}")
def version_get(version_id: str):
    with transaction() as session:
        version = session.get(Version, version_id)
        if not version:
            raise HTTPException(404)
        owned(session, version.project_id, settings().owner_id)
        return row_dict(version)


from telok.assistant_api import routes as assistant_routes  # noqa: E402

app.include_router(assistant_routes)
