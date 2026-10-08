import html
import json
import time

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select

from telok import assistant, integrations
from telok.ai import chatgpt_auth, credentials
from telok.ai.providers import ChatGPTPlanProvider, LocalQwenProvider
from telok.db import transaction
from telok.domain import DomainError, owned, row_dict
from telok.models import Project, Work
from telok.settings import settings

routes = APIRouter()


def local_only(request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise DomainError("Настройки подключения доступны только на этом компьютере.")


@routes.get("/assistant")
def page():
    from pathlib import Path

    return FileResponse(Path(__file__).parent / "web" / "assistant.html")


@routes.get("/assistant.js")
def script():
    from pathlib import Path

    return FileResponse(Path(__file__).parent / "web" / "assistant.js", media_type="application/javascript")


@routes.get("/api/assistant/setup")
def setup(request: Request):
    local_only(request)
    tg = integrations.config()
    return {
        "chatgpt": chatgpt_auth.status(),
        "telegram": {
            "configured": bool(integrations.token()),
            "username": tg.get("username", ""),
            "owner_id": tg.get("owner_id"),
        },
        "local": {
            "url": credentials.read().get("local_url", "http://127.0.0.1:8080/v1"),
            "model": credentials.read().get("local_model", ""),
        },
    }


@routes.post("/api/assistant/telegram")
def configure_telegram(request: Request, body: dict):
    local_only(request)
    return integrations.configure(
        body.get("token", ""), body.get("owner_id", 0), body.get("old_poller_stopped") is True
    )


@routes.post("/api/assistant/chatgpt/connect")
def connect(request: Request, body: dict):
    local_only(request)
    return {"url": chatgpt_auth.start(bool(body.get("new_profile")))}


@routes.get("/auth/callback")
def callback(request: Request):
    local_only(request)
    try:
        chatgpt_auth.callback(dict(request.query_params))
        message = "Подключение сохранено. Вернитесь в Telok и проверьте модель."
    except DomainError as exc:
        code = getattr(exc, "code", "AUTH_FAILED")
        credentials.update(last_auth_error={"code": code, "time": int(time.time())})
        message = "Вход не завершён: " + str(exc) + " Код: " + code
    except Exception:
        credentials.update(last_auth_error={"code": "AUTH_FAILED", "time": int(time.time())})
        message = "Вход не завершён из-за внутренней ошибки. Код: AUTH_FAILED."
    message = html.escape(message)
    return HTMLResponse(
        '<meta charset="utf-8"><meta name="referrer" content="no-referrer"><script>history.replaceState(null,"","/auth/callback")</script><h2>'
        + message
        + '</h2><a href="/assistant">Вернуться в Telok</a>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@routes.post("/api/assistant/chatgpt/disconnect")
def disconnect(request: Request):
    local_only(request)
    return chatgpt_auth.disconnect()


@routes.get("/api/assistant/models/{provider}")
def models(provider: str, request: Request):
    local_only(request)
    return (ChatGPTPlanProvider() if provider == "plan" else LocalQwenProvider()).models()


@routes.post("/api/assistant/config")
def configure(request: Request, body: dict):
    local_only(request)
    if body.get("route", "CHATGPT_ONLY") not in {"CHATGPT_ONLY", "LOCAL_ONLY", "AUTO"}:
        raise DomainError("Неизвестный маршрут.")
    with credentials.locked():
        data = credentials.read()
        for key in ("route", "local_model", "local_url", "plan_model"):
            if key in body:
                data[key] = str(body[key])[:300]
        if "credits_block_confirmed" in body:
            p = data.get("profiles", {}).get(data.get("active_profile"), {})
            if not p:
                raise DomainError("Сначала подключите ChatGPT.")
            p["credits_block_confirmed"] = body["credits_block_confirmed"] is True
        credentials.write(data)
    return {"saved": True}


@routes.post("/api/assistant/chatgpt/check")
def check(request: Request):
    local_only(request)
    result = ChatGPTPlanProvider().generate(
        "Ответь только: подключение работает", [{"role": "user", "content": "Проверка подключения"}]
    )
    with credentials.locked():
        data = credentials.read()
        data["profiles"][data["active_profile"]]["verified"] = True
        credentials.write(data)
    return {"text": result.text, "provider": result.provider, "model": result.model}


@routes.get("/api/assistant/projects")
def projects():
    assistant.inbox(integrations.owner())
    with transaction() as session:
        return [
            row_dict(p)
            for p in session.execute(
                select(Project).where(Project.owner_id == integrations.owner())
            ).scalars()
        ]


@routes.get("/api/assistant/tasks")
def tasks():
    with transaction() as session:
        return [
            row_dict(w)
            for w in session.execute(
                select(Work)
                .join(Project, Work.project_id == Project.id)
                .where(Project.owner_id == integrations.owner(), Work.kind == "assistant")
                .order_by(Work.created_at.desc())
                .limit(40)
            ).scalars()
        ]


@routes.post("/api/assistant/tasks")
def create(body: dict):
    return assistant.submit(
        integrations.owner(),
        body.get("prompt", ""),
        body.get("project_id"),
        body.get("operation_id"),
        body.get("parent_id"),
        documents=body.get("documents"),
        mode=body.get("mode", "AUTO"),
        selected_idea=body.get("selected_idea"),
        reference_asset_ids=body.get("reference_asset_ids"),
        edit_scene_ids=body.get("edit_scene_ids"),
    )


@routes.get("/api/assistant/tasks/{request_id}")
def detail(request_id: str):
    return assistant.details(request_id, integrations.owner())


@routes.post("/api/assistant/tasks/{request_id}/cancel")
def cancel(request_id: str):
    return assistant.cancel(request_id, integrations.owner())


@routes.get("/api/assistant/tasks/{request_id}/export")
def export(request_id: str):
    task = assistant.details(request_id, integrations.owner())
    return Response(
        json.dumps(task["result"], ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="telok-result.json"'},
    )


@routes.post("/api/assistant/document")
async def document(file: UploadFile):
    from telok.research import extract_document

    return extract_document(await file.read(10_000_001), file.filename or "document.txt")


@routes.post("/api/assistant/tasks/{request_id}/scenes/{scene_id}/import")
async def import_clip(request_id: str, scene_id: str, file: UploadFile):
    from telok.media import import_clip

    return import_clip(request_id, integrations.owner(), scene_id, await file.read(50_000_001))


@routes.post("/api/assistant/tasks/{request_id}/scenes/{scene_id}/generate")
def generate_clip(request_id: str, scene_id: str):
    return enqueue_media(request_id, "generate", scene_id)


@routes.post("/api/assistant/media/{job_id}/sync")
def media_status(job_id: str):
    from telok.media import poll

    return poll(job_id, integrations.owner())


@routes.post("/api/assistant/tasks/{request_id}/montage")
def montage(request_id: str):
    return enqueue_media(request_id, "montage")


@routes.post("/api/assistant/tasks/{request_id}/video-qa")
def video_qa(request_id: str):
    return enqueue_media(request_id, "qa")


@routes.post("/api/assistant/seedance/config")
def seedance_config(request: Request, body: dict):
    local_only(request)
    model = str(body.get("model", ""))
    if not model.startswith("dreamina-seedance-2-5-"):
        raise DomainError("Выберите подтверждённый model ID Seedance 2.5.")
    api_key = str(body.get("api_key", ""))
    if not api_key:
        raise DomainError("Нужен ключ BytePlus ModelArk.")
    credentials.update(
        seedance={
            "api_key": api_key,
            "model": model,
            "enabled": body.get("enabled") is True,
            "download_domains": body.get("download_domains", []),
        }
    )
    return {"saved": True, "enabled": body.get("enabled") is True}


def enqueue_media(request_id, action, scene_id=""):
    from telok.domain import canonical, queue

    task = assistant.details(request_id, integrations.owner())
    if not task["result"].get("production"):
        raise DomainError("Нет производственного пакета.")
    if action == "generate":
        cfg = credentials.read().get("seedance", {})
        if not cfg.get("enabled") or not cfg.get("api_key") or settings().seedance_call_reserve_usd <= 0:
            raise DomainError("Сначала подключите Seedance и денежные лимиты.")
    workflow_id = "media:" + canonical(
        {"request_id": request_id, "action": action, "scene": scene_id, "pack": task["result"]["production"]}
    )
    with transaction() as session:
        project = owned(session, task["project_id"], integrations.owner())
        if project.paused or task["status"] == "CANCELLED":
            raise DomainError("Проект или задача остановлены.")
        queue(
            session,
            "telok_media",
            workflow_id,
            {"request_id": request_id, "actor": integrations.owner(), "action": action, "scene_id": scene_id},
        )
    return {"status": "QUEUED", "workflow_id": workflow_id}


@routes.post("/api/assistant/projects")
def create_project(body: dict):
    from telok.domain import create_project

    name = str(body.get("name", "")).strip()
    if not name or len(name) > 150:
        raise DomainError("Название проекта: 1–150 символов.")
    return create_project(integrations.owner(), name)


@routes.post("/api/assistant/projects/{project_id}/reference")
async def reference(project_id: str, file: UploadFile):
    from telok import storage

    with transaction() as session:
        owned(session, project_id, integrations.owner())
    if file.content_type not in {"image/png", "image/jpeg"}:
        raise DomainError("Референс должен быть JPG или PNG.")
    data = await file.read(10_000_001)
    if len(data) > 10_000_000:
        raise DomainError("Референс превышает 10 MB.")
    asset = storage.put(project_id, data, file.content_type or "", {"source": "owner_reference"})
    return {"asset_id": asset}


@routes.get("/api/assistant/tasks/{request_id}/media")
def task_media(request_id: str):
    from telok.models import MediaJob

    task = assistant.details(request_id, integrations.owner())
    with transaction() as session:
        return [
            row_dict(j)
            for j in session.execute(
                select(MediaJob).where(
                    MediaJob.project_id == task["project_id"], MediaJob.request_id == request_id
                )
            ).scalars()
        ]


@routes.get("/api/assistant/tasks/{request_id}/video")
def task_video(request_id: str):
    from telok import storage

    task = assistant.details(request_id, integrations.owner())
    asset = task["result"].get("video_asset_id")
    if not asset:
        raise DomainError("Видео ещё не собрано.")
    data, meta = storage.read(asset, task["project_id"])
    return Response(
        data, media_type=meta["mime"], headers={"Content-Disposition": 'inline; filename="telok-video.mp4"'}
    )
