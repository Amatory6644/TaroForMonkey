"""Scene generation journal. Unknown submissions are never blindly repeated."""

import base64
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
from urllib.parse import urlparse

import httpx

from telok import budget, storage
from telok.ai import credentials
from telok.db import transaction, uid
from telok.domain import DomainError, canonical, owned, row_dict
from telok.models import MediaJob
from telok.settings import settings

ENDPOINT = "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"


def probe(data: bytes):
    root = (settings().data_dir / "scratch").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (uid() + ".mp4")
    try:
        path.write_bytes(data)
        result = subprocess.run(
            [
                settings().ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=True,
        )
        meta = json.loads(result.stdout)
        video = next(s for s in meta["streams"] if s["codec_type"] == "video")
        duration = float(meta["format"]["duration"])
        if not 0 < duration <= 125 or video["width"] > 4096 or video["height"] > 4096:
            raise DomainError("Клип превышает допустимую длительность или dimensions.")
        return {
            "duration": duration,
            "width": video["width"],
            "height": video["height"],
            "codec": video["codec_name"],
            "audio": any(s["codec_type"] == "audio" for s in meta["streams"]),
        }
    except (subprocess.SubprocessError, KeyError, StopIteration, ValueError):
        raise DomainError("MP4 не прошёл техническую проверку.") from None
    finally:
        path.unlink(missing_ok=True)


def pack_scene(request_id, actor, scene_id):
    from telok.assistant import details

    task = details(request_id, actor)
    pack = task["result"].get("production")
    if not pack:
        raise DomainError("У задачи нет производственного пакета.")
    scene = next((s for s in pack["scenes"] if s["scene_id"] == scene_id), None)
    if not scene:
        raise DomainError("Сцена не найдена.")
    from telok.production import export_manifest

    manifest = next(s for s in export_manifest(pack)["scenes"] if s["scene_id"] == scene_id)
    return task, scene, manifest


def import_clip(request_id, actor, scene_id, data):
    if len(data) > 50_000_000:
        raise DomainError("Клип превышает 50 MB.")
    task, scene, manifest = pack_scene(request_id, actor, scene_id)
    technical = probe(data)
    if abs(technical["duration"] - scene["duration_seconds"]) > 1:
        raise DomainError(
            "Длительность клипа отличается от сцены более чем на секунду. Измените сцену или клип."
        )
    cache = canonical({"scene": manifest["content_hash"], "file": hashlib.sha256(data).hexdigest()})
    with transaction() as session:
        owned(session, task["project_id"], actor)
        existing = (
            session.query(MediaJob)
            .filter_by(project_id=task["project_id"], cache_key=cache, provider="manual")
            .first()
        )
        if existing:
            return row_dict(existing)
    asset = storage.put(
        task["project_id"],
        data,
        "video/mp4",
        {
            "source": "owner_import",
            "technical": technical,
            "scene_id": scene_id,
            "scene_hash": manifest["content_hash"],
        },
    )
    with transaction() as session:
        job = MediaJob(
            project_id=task["project_id"],
            request_id=request_id,
            scene_id=scene_id,
            cache_key=cache,
            provider="manual",
            status="COMPLETED",
            provider_job_id="",
            asset_id=asset,
            attempt_id="",
            details={"scene_hash": manifest["content_hash"], "technical": technical},
        )
        session.add(job)
        session.flush()
        return row_dict(job)


def submit(request_id, actor, scene_id):
    task, scene, manifest = pack_scene(request_id, actor, scene_id)
    cfg = credentials.read().get("seedance", {})
    if not cfg.get("api_key") or not cfg.get("enabled") or not cfg.get("model"):
        raise DomainError("Seedance не подключён. Можно экспортировать промпты и импортировать MP4.")
    reserve = settings().seedance_call_reserve_usd
    if not settings().pricing_confirmed or reserve <= 0:
        raise DomainError("Для Seedance нужен подтверждённый денежный reserve и лимиты.")
    duration = scene["duration_seconds"]
    if duration != int(duration) or not 4 <= duration <= 30:
        raise DomainError("Для этого адаптера нужны сцены длительностью 4–30 целых секунд.")
    content = [{"type": "text", "text": manifest["prompt"]}]
    for ref in scene["reference_asset_ids"]:
        data, meta = storage.read(ref, task["project_id"])
        if meta["mime"] not in {"image/png", "image/jpeg"}:
            raise DomainError("Seedance references должны быть изображениями.")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:" + meta["mime"] + ";base64," + base64.b64encode(data).decode()},
                "role": "reference_image",
            }
        )
    body = {
        "model": cfg["model"],
        "content": content,
        "duration": int(duration),
        "ratio": "9:16",
        "resolution": "720p",
        "generate_audio": True,
    }
    cache = canonical({"body": body, "scene_hash": manifest["content_hash"]})
    with transaction() as session:
        project = owned(session, task["project_id"], actor)
        session.refresh(project, with_for_update=True)
        if project.paused:
            raise DomainError("Проект на паузе.")
        previous = (
            session.query(MediaJob)
            .filter_by(project_id=project.id, cache_key=cache, provider="seedance")
            .first()
        )
        if previous:
            return row_dict(previous)
        job = MediaJob(
            id=uid(),
            project_id=project.id,
            request_id=request_id,
            scene_id=scene_id,
            cache_key=cache,
            provider="seedance",
            status="PREPARING",
            provider_job_id="",
            asset_id="",
            attempt_id="",
            details={"scene_hash": manifest["content_hash"], "model": cfg["model"]},
        )
        session.add(job)
        job_id = job.id
    try:
        attempt_id = budget.reserve(task["project_id"], request_id, "seedance:" + cfg["model"], reserve)
        with transaction() as session:
            job = session.get(MediaJob, job_id)
            job.attempt_id = attempt_id
            job.status = "SUBMITTING"
        from telok.providers import preflight

        preflight(task["project_id"], request_id)
        with httpx.Client(timeout=45, trust_env=False) as client:
            response = client.post(ENDPOINT, json=body, headers={"Authorization": "Bearer " + cfg["api_key"]})
        if response.status_code not in {200, 201}:
            raise DomainError("Seedance не подтвердил заказ. Сверьте кабинет провайдера.")
        provider_id = response.json().get("id")
        if not provider_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", provider_id):
            raise DomainError("Seedance не вернул корректный job ID.")
        with transaction() as session:
            job = session.get(MediaJob, job_id)
            job.provider_job_id = provider_id
            job.status = "QUEUED"
            return row_dict(job)
    except Exception:
        with transaction() as session:
            job = session.get(MediaJob, job_id)
            job.status = "UNKNOWN"
        if job.attempt_id:
            budget.uncertain(job.attempt_id)
        raise DomainError(
            "Заказ не подтверждён. Повтор не выполнен; проверьте журнал и кабинет Seedance."
        ) from None


def download(url, cfg):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise DomainError("Некорректный адрес media.")
    if parsed.hostname not in cfg.get("download_domains", []):
        raise DomainError("Добавьте проверенный CDN-домен Seedance в настройки загрузки.")
    if any(not ipaddress.ip_address(i[4][0]).is_global for i in socket.getaddrinfo(parsed.hostname, 443)):
        raise DomainError("Private адрес media запрещён.")
    data = bytearray()
    with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 50_000_000:
                    raise DomainError("Media превышает 50 MB.")
    return bytes(data)


def poll(job_id, actor):
    from filelock import FileLock

    root = settings().data_dir / "media-locks"
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / (hashlib.sha256(str(job_id).encode()).hexdigest() + ".lock")), timeout=120):
        return _poll(job_id, actor)


def _poll(job_id, actor):
    with transaction() as session:
        job = session.get(MediaJob, job_id)
        if not job:
            raise DomainError("Заказ не найден.")
        owned(session, job.project_id, actor)
        if not job.provider_job_id or job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return row_dict(job)
        value = row_dict(job)
    cfg = credentials.read().get("seedance", {})
    try:
        with httpx.Client(timeout=25, trust_env=False) as client:
            response = client.get(
                ENDPOINT + "/" + value["provider_job_id"],
                headers={"Authorization": "Bearer " + cfg.get("api_key", "")},
            )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status", "unknown")
        if status == "succeeded":
            data = download(payload["content"]["video_url"], cfg)
            technical = probe(data)
            asset = storage.put(
                value["project_id"],
                data,
                "video/mp4",
                {
                    "provider": "seedance",
                    "job_id": value["provider_job_id"],
                    "scene_hash": value["details"]["scene_hash"],
                    "technical": technical,
                },
            )
            budget.finish(value["attempt_id"], payload.get("usage", {}), value["provider_job_id"])
            with transaction() as session:
                job = session.get(MediaJob, job_id)
                job.status = "COMPLETED"
                job.asset_id = asset
                job.details = {**job.details, "technical": technical}
                return row_dict(job)
        with transaction() as session:
            job = session.get(MediaJob, job_id)
            job.status = {
                "queued": "QUEUED",
                "running": "RUNNING",
                "failed": "FAILED",
                "cancelled": "CANCELLED",
            }.get(status, "UNKNOWN")
            return row_dict(job)
    except httpx.HTTPError:
        raise DomainError("Статус Seedance временно недоступен. Новый заказ не создан.") from None
