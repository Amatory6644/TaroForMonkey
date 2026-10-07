"""Reusable scene normalization and verified portrait montage."""

import json
import subprocess

from filelock import FileLock
from sqlalchemy import select

from telok import storage
from telok.db import transaction, uid
from telok.domain import DomainError, canonical, owned
from telok.media import probe
from telok.models import MediaJob, TaskArtifact, Work
from telok.production import export_manifest
from telok.settings import settings


def run(command):
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180)
    except subprocess.SubprocessError:
        raise DomainError("Монтаж не завершён. Проверьте исходные клипы.") from None


def assemble(request_id, actor):
    from telok.assistant import details

    task = details(request_id, actor)
    pack = task["result"].get("production")
    if not pack:
        raise DomainError("Производственный пакет отсутствует.")
    manifest = export_manifest(pack)
    cache = (settings().data_dir / "clip-cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    normalized = []
    for scene in manifest["scenes"]:
        with transaction() as session:
            candidates = session.execute(
                select(MediaJob)
                .where(
                    MediaJob.project_id == task["project_id"],
                    MediaJob.status == "COMPLETED",
                    MediaJob.scene_id == scene["scene_id"],
                )
                .order_by(MediaJob.created_at.desc())
            ).scalars()
            job = next((j for j in candidates if j.details.get("scene_hash") == scene["content_hash"]), None)
            if not job:
                raise DomainError("Нет актуального клипа для " + scene["scene_id"])
            asset_id = job.asset_id
        data, meta = storage.read(asset_id, task["project_id"])
        key = canonical(
            {"source": meta["sha256"], "duration": scene["duration_seconds"], "profile": "portrait-v1"}
        )
        target = cache / (key + ".mp4")
        with FileLock(str(cache / (key + ".lock")), timeout=180):
            if not target.exists():
                source = cache / (uid() + ".mp4")
                pending = cache / (uid() + ".mp4")
                try:
                    source.write_bytes(data)
                    has_audio = probe(data)["audio"]
                    args = [
                        settings().ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(source),
                    ]
                    if not has_audio:
                        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                    args += [
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0" if has_audio else "1:a:0",
                        "-vf",
                        "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x17291f,fps=24,setsar=1,tpad=stop_mode=clone:stop_duration=1",
                        "-af",
                        "apad",
                        "-t",
                        str(scene["duration_seconds"]),
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        "-movflags",
                        "+faststart",
                        str(pending),
                    ]
                    run(args)
                    probe(pending.read_bytes())
                    pending.replace(target)
                finally:
                    source.unlink(missing_ok=True)
                    pending.unlink(missing_ok=True)
        normalized.append(target)
    montage_key = canonical({"clips": [p.stem for p in normalized]})
    target = cache / (montage_key + "-final.mp4")
    with FileLock(str(cache / (montage_key + ".lock")), timeout=180):
        if not target.exists():
            concat = cache / (uid() + ".txt")
            pending = cache / (uid() + ".mp4")
            try:
                concat.write_text("\n".join("file '" + p.name + "'" for p in normalized), encoding="utf-8")
                run(
                    [
                        settings().ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "1",
                        "-i",
                        str(concat),
                        "-c",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(pending),
                    ]
                )
                probe(pending.read_bytes())
                pending.replace(target)
            finally:
                concat.unlink(missing_ok=True)
                pending.unlink(missing_ok=True)
    data = target.read_bytes()
    technical = probe(data)
    if abs(technical["duration"] - sum(s["duration_seconds"] for s in pack["scenes"])) > 1:
        raise DomainError("Финальный хронометраж не совпадает со сценарием.")
    asset = storage.put(
        task["project_id"],
        data,
        "video/mp4",
        {
            "provider": "ffmpeg-clips-v1",
            "technical": technical,
            "scene_hashes": [s["content_hash"] for s in manifest["scenes"]],
            "cache_key": montage_key,
        },
    )
    with transaction() as session:
        work = session.get(Work, request_id)
        owned(session, work.project_id, actor)
        if work.status == "CANCELLED":
            raise DomainError("Задача отменена.")
        work.result = {
            **work.result,
            "video_asset_id": asset,
            "media_status": "VIDEO_READY",
            "video_qa": {
                "technical": technical,
                "semantic": "NOT_CHECKED",
                "speech": "OWNER_REVIEW_REQUIRED",
            },
        }
        session.add(
            TaskArtifact(
                project_id=work.project_id,
                request_id=request_id,
                stage="montage",
                revision=session.query(TaskArtifact).filter_by(request_id=request_id, stage="montage").count()
                + 1,
                body={"asset_id": asset, "technical": technical},
            )
        )
    return {"asset_id": asset, "technical": technical}


def inspect_frames(request_id, actor):
    import base64

    from telok.ai.router import invoke
    from telok.assistant import details

    task = details(request_id, actor)
    asset_id = task["result"].get("video_asset_id")
    if not asset_id:
        raise DomainError("Сначала соберите видео.")
    data, meta = storage.read(asset_id, task["project_id"])
    duration = probe(data)["duration"]
    root = (settings().data_dir / "scratch").resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / (uid() + ".mp4")
    images = []
    try:
        source.write_bytes(data)
        for t in (0.05, duration * 0.25, duration * 0.5, duration * 0.75, max(0.1, duration - 0.1)):
            path = root / (uid() + ".jpg")
            try:
                run(
                    [
                        settings().ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-ss",
                        str(t),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=540:-1",
                        str(path),
                    ]
                )
                images.append(
                    {
                        "type": "input_image",
                        "image_url": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode(),
                    }
                )
            finally:
                path.unlink(missing_ok=True)
        content = [
            {
                "type": "input_text",
                "text": json.dumps(
                    {
                        "brief": task["payload"]["prompt"],
                        "pack": task["result"]["production"],
                        "coverage": "5 sampled frames, no audio",
                    },
                    ensure_ascii=False,
                ),
            },
            *images,
        ]
        result = invoke(
            task["project_id"],
            request_id,
            "Проверь пять кадров по брифу и сценарию. Укажи визуальные ошибки, выдуманные детали, логотипы и текст. Ты не видел все кадры и не слышал звук: не утверждай полную проверку видео.",
            [{"role": "user", "content": content}],
        )
        with transaction() as session:
            work = session.get(Work, request_id)
            work.result = {
                **work.result,
                "video_qa": {
                    **work.result.get("video_qa", {}),
                    "semantic": result,
                    "coverage": "5 sampled frames",
                    "speech": "OWNER_REVIEW_REQUIRED",
                },
            }
        return result
    finally:
        source.unlink(missing_ok=True)
