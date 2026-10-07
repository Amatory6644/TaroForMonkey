import hashlib
import json

from telok.db import transaction
from telok.domain import DomainError
from telok.models import Asset


def validate_pack(pack, ctx):
    ids = [scene["scene_id"] for scene in pack["scenes"]]
    if len(set(ids)) != len(ids) or any(not i or len(i) > 40 for i in ids):
        raise DomainError("ID сцен должны быть уникальны и стабильны.")
    if sum(s["duration_seconds"] for s in pack["scenes"]) > 120:
        raise DomainError("Пакет превышает 120 секунд.")
    original = ctx.get("history", {}).get("result", {}).get("production")
    edited = ctx.get("edit_scene_ids", [])
    if original and edited:
        if ids != [s["scene_id"] for s in original["scenes"]]:
            raise DomainError("Точечная правка не может менять порядок и ID сцен.")
        if pack["shared_visual_rules"] != original["shared_visual_rules"]:
            raise DomainError("Точечная правка не может менять общие визуальные правила.")
        for before, after in zip(original["scenes"], pack["scenes"], strict=True):
            if before["scene_id"] not in edited and before != after:
                raise DomainError("Модель изменила сцену вне выбранной области: " + before["scene_id"])
    with transaction() as session:
        for scene in pack["scenes"]:
            for ref in scene["reference_asset_ids"]:
                asset = session.get(Asset, ref)
                if not asset or asset.project_id != ctx["project_id"]:
                    raise DomainError("Референс сцены не принадлежит проекту.")


def export_manifest(pack):
    rules = json.dumps(pack["shared_visual_rules"], ensure_ascii=False, sort_keys=True)
    scenes = []
    for scene in pack["scenes"]:
        digest = hashlib.sha256(
            (rules + json.dumps(scene, sort_keys=True, ensure_ascii=False)).encode()
        ).hexdigest()
        scenes.append(
            {
                "scene_id": scene["scene_id"],
                "content_hash": digest,
                "prompt": "\n".join(pack["shared_visual_rules"]) + "\n" + scene["prompt"],
                "duration_seconds": scene["duration_seconds"],
                "reference_asset_ids": scene["reference_asset_ids"],
            }
        )
    return {
        "provider": "manual_export",
        "status": "PROMPTS_READY",
        "scenes": scenes,
        "note": "Генератор не подключён. Это задания, а не готовые клипы.",
    }
