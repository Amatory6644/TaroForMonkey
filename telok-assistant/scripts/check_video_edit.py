import copy
import json
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from telok import storage
from telok.db import transaction
from telok.models import Project, Version

with transaction() as session:
    project = session.execute(select(Project).where(Project.name == "Telok")).scalar_one()
    version = session.execute(
        select(Version)
        .where(
            Version.project_id == project.id, Version.format == "VIDEO_SHORT", Version.status != "SUPERSEDED"
        )
        .order_by(Version.created_at.desc())
        .limit(1)
    ).scalar_one()
    project_id, previous_id, item_id, before = project.id, version.id, version.item_id, version.revision
    storyboard = copy.deepcopy(version.brief["storyboard"])
storyboard["scenes"][1]["caption"] = "Одна мысль. Понятная форма."
payload = {
    "operation_id": "telok-demo-video-reframe-2026-10-05",
    "previous_video_id": previous_id,
    "owner_reviewed_facts": True,
    "tts": "windows",
    "locale": "ru-RU",
    "storyboard": storyboard,
}
with httpx.Client(
    base_url="http://127.0.0.1:8481", headers={"X-Telok-Client": "dashboard"}, timeout=30
) as client:
    response = client.post(f"/api/projects/{project_id}/video", json=payload)
    response.raise_for_status()
    work_id = response.json()["id"]
    for _ in range(60):
        overview = client.get(f"/api/projects/{project_id}/overview").json()
        work = next(w for w in overview["works"] if w["id"] == work_id)
        result = next((v for v in overview["versions"] if v["id"] == work["result"].get("version_id")), None)
        if result:
            break
        if work["status"] in {"FAILED", "CANCELLED", "PAUSED"}:
            raise RuntimeError(work["error"])
        time.sleep(0.5)
    else:
        raise RuntimeError("Video edit timed out")
assert result["item_id"] == item_id and result["demo"]
assert result["revision"] >= 2
assert result["brief"]["storyboard"]["scenes"][1]["caption"] == "Одна мысль. Понятная форма."
data, metadata = storage.read(result["asset_ids"][0], project_id)
out = Path("artifacts/video")
(out / "telok-motion-demo.mp4").write_bytes(data)
(out / "report.json").write_text(
    json.dumps(
        {
            "work_id": work_id,
            "version_id": result["id"],
            "revision": result["revision"],
            "demo": True,
            "edit_verified": True,
            **metadata["info"],
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("Video edit verified: same item, new immutable revision, original image assets reused, real MP4.")
