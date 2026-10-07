import json
from pathlib import Path

from sqlalchemy import select

from telok import storage
from telok.db import transaction
from telok.models import Asset, Project, Version
from telok.video import render_release

with transaction() as session:
    project = session.execute(select(Project).where(Project.name == "Telok")).scalar_one()
    previous = session.execute(
        select(Version)
        .where(Version.project_id == project.id, Version.format == "VIDEO_SHORT")
        .order_by(Version.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    images = (
        session.execute(
            select(Version)
            .where(Version.project_id == project.id, Version.format == "IMAGE_POST", Version.demo.is_(True))
            .order_by(Version.created_at)
        )
        .scalars()
        .all()
    )
    project_id, owner, image_ids = project.id, project.owner_id, [v.asset_ids[0] for v in images[:3]]
if previous:
    with transaction() as session:
        meta = session.get(Asset, previous.asset_ids[0]).info
    if meta.get("audio_alignment") != "per_scene_tts":
        previous = None
if previous:
    result = {"id": previous.id, "asset_ids": previous.asset_ids}
else:
    lines = [
        ("Дайте идее немного пространства.", "Хорошая идея начинается с простого вопроса."),
        ("Сначала мысль. Затем форма.", "Выберите одну мысль и дайте ей понятную форму."),
        ("Что обсудим дальше?", "Оставьте место для следующей идеи."),
    ]
    result = render_release(
        project_id,
        owner,
        {
            "tts": "windows",
            "locale": "ru-RU",
            "storyboard": {
                "title": "Telok · motion demo с настоящей озвучкой",
                "scenes": [
                    {"image_asset_id": asset, "duration": 8, "narration": line[1], "caption": line[0]}
                    for asset, line in zip(image_ids, lines, strict=True)
                ],
            },
        },
    )
data, metadata = storage.read(result["asset_ids"][0], project_id)
output = Path("artifacts/video")
output.mkdir(parents=True, exist_ok=True)
(output / "telok-motion-demo.mp4").write_bytes(data)
(output / "report.json").write_text(
    json.dumps({"version_id": result["id"], "demo": True, **metadata["info"]}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"Real MP4 saved: {output.resolve() / 'telok-motion-demo.mp4'}; demo remains blocked from publishing.")
