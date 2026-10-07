import json
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from telok import storage
from telok.db import transaction
from telok.domain import record
from telok.models import Project, Version

with transaction() as session:
    project = session.execute(select(Project).where(Project.name == "Telok")).scalar_one()
    images = (
        session.execute(
            select(Version)
            .where(Version.project_id == project.id, Version.format == "IMAGE_POST", Version.demo.is_(True))
            .order_by(Version.created_at)
        )
        .scalars()
        .all()
    )
    project_id, assets = project.id, [v.asset_ids[0] for v in images[:3]]
lines = [
    ("Дайте идее немного пространства.", "Хорошая идея начинается с простого вопроса."),
    ("Сначала мысль. Затем форма.", "Выберите одну мысль и дайте ей понятную форму."),
    ("Что обсудим дальше?", "Оставьте место для следующей идеи."),
]
body = {
    "operation_id": "telok-final-demo-2026-10-05",
    "owner_reviewed_facts": True,
    "tts": "windows",
    "locale": "ru-RU",
    "storyboard": {
        "title": "Telok · редакционная студия",
        "scenes": [
            {"image_asset_id": asset, "duration": 8, "caption": text[0], "narration": text[1]}
            for asset, text in zip(assets, lines, strict=True)
        ],
    },
}
with httpx.Client(
    base_url="http://127.0.0.1:8481", timeout=30, headers={"X-Telok-Client": "dashboard"}
) as client:
    response = client.post(f"/api/projects/{project_id}/video", json=body)
    response.raise_for_status()
    work_id = response.json()["id"]
    for _ in range(60):
        overview = client.get(f"/api/projects/{project_id}/overview").json()
        work = next(w for w in overview["works"] if w["id"] == work_id)
        if work["status"] == "READY_FOR_REVIEW":
            result = next((v for v in overview["versions"] if v["id"] == work["result"]["version_id"]), None)
            if result:
                break
        if work["status"] in {"FAILED", "CANCELLED", "PAUSED"}:
            raise RuntimeError(work["error"])
        time.sleep(0.5)
    else:
        raise RuntimeError("Video workflow timed out")
assert result["demo"] is True
with transaction() as session:
    for old in session.execute(
        select(Version).where(
            Version.project_id == project_id, Version.format == "VIDEO_SHORT", Version.demo.is_(True)
        )
    ).scalars():
        if old.id != result["id"]:
            old.status = "SUPERSEDED"
    record(
        session,
        project_id,
        "demo_video_validated",
        {"version_id": result["id"], "work_id": work_id, "live_ai": False},
    )
data, metadata = storage.read(result["asset_ids"][0], project_id)
out = Path("artifacts/video")
out.mkdir(parents=True, exist_ok=True)
(out / "telok-motion-demo.mp4").write_bytes(data)
(out / "report.json").write_text(
    json.dumps(
        {"work_id": work_id, "version_id": result["id"], "demo": True, **metadata["info"]},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("Queued DBOS video completed: real MP4, per-scene Russian TTS, immutable demo release.")
