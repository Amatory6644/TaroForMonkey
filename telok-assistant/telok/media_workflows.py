from dbos import DBOS

from telok import media
from telok.db import transaction
from telok.domain import DomainError, record
from telok.models import Work


@DBOS.step(name="media_submit_v1", retries_allowed=False)
def submit(payload):
    return media.submit(payload["request_id"], payload["actor"], payload["scene_id"])


@DBOS.step(name="media_poll_v1", retries_allowed=False)
def poll(job_id, actor):
    return media.poll(job_id, actor)


@DBOS.step(name="media_montage_v1", retries_allowed=False)
def montage(payload):
    from telok.clip_montage import assemble

    return assemble(payload["request_id"], payload["actor"])


@DBOS.step(name="media_qa_v1", retries_allowed=False)
def qa(payload):
    from telok.clip_montage import inspect_frames

    return inspect_frames(payload["request_id"], payload["actor"])


@DBOS.step(name="media_error_v1")
def failed(payload, error):
    with transaction() as session:
        work = session.get(Work, payload["request_id"])
        if work:
            work.result = {**work.result, "media_error": error}
            record(
                session, work.project_id, "media_workflow_stopped", {"request_id": work.id, "error": error}
            )


@DBOS.workflow(name="telok_media", serialization_type="portable_json")
def run(payload):
    try:
        if payload["action"] == "local_all":
            from telok.assistant import details

            task = details(payload["request_id"], payload["actor"])
            for scene in task["result"]["production"]["scenes"]:
                local_studio_step({**payload, "action": "local_frame", "scene_id": scene["scene_id"]})
            result = local_studio_step({**payload, "action": "storyboard"})
            deliver_storyboard(payload)
            return result
        if payload["action"] in {"local_frame", "storyboard"}:
            result = local_studio_step(payload)
            if payload["action"] == "storyboard":
                deliver_storyboard(payload)
            return result
        if payload["action"] == "montage":
            return montage(payload)
        if payload["action"] == "qa":
            return qa(payload)
        if payload["action"] == "all":
            from telok.assistant import details

            task = details(payload["request_id"], payload["actor"])
            for scene in task["result"]["production"]["scenes"]:
                child = {**payload, "action": "generate", "scene_id": scene["scene_id"]}
                result = run_scene(child)
                if result.get("status") != "COMPLETED":
                    return result
            result = montage(payload)
            deliver_video(payload)
            return result
        return run_scene(payload)
    except Exception as exc:
        error = str(exc) if isinstance(exc, DomainError) else "Media step failed (" + type(exc).__name__ + ")"
        failed(payload, error)
        return {"status": "PAUSED", "error": error}


def run_scene(payload):
    job = submit(payload)
    if job["status"] in {"UNKNOWN", "SUBMITTING", "PREPARING"}:
        return job
    for _ in range(360):
        with transaction() as session:
            work = session.get(Work, payload["request_id"])
            if work.status == "CANCELLED":
                return {"status": "LOCAL_CANCELLED", "note": "External provider may still finish/charge"}
        job = poll(job["id"], payload["actor"])
        if job["status"] in {"COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"}:
            return job
        DBOS.sleep(10)
    return {"status": "POLLING_PAUSED", "job_id": job["id"]}


@DBOS.step(name="media_deliver_video_v1", retries_allowed=False)
def deliver_video(payload):
    from telok import storage
    from telok.assistant import details
    from telok.integrations import send_video
    from telok.models import TaskMessage

    task = details(payload["request_id"], payload["actor"])
    chat_id = task["payload"].get("chat_id")
    if chat_id and task["status"] != "CANCELLED":
        data, meta = storage.read(task["result"]["video_asset_id"], task["project_id"])
        mid = send_video(chat_id, data)
        if mid:
            with transaction() as session:
                session.add(
                    TaskMessage(
                        project_id=task["project_id"], request_id=task["id"], chat_id=chat_id, message_id=mid
                    )
                )


@DBOS.step(name="local_studio_v1", retries_allowed=False)
def local_studio_step(payload):
    from telok import local_studio

    if payload["action"] == "storyboard":
        return local_studio.storyboard(payload["request_id"], payload["actor"])
    return local_studio.generate(
        payload["request_id"], payload["actor"], payload["scene_id"], payload.get("seed", 0)
    )


@DBOS.step(name="local_storyboard_deliver_v1", retries_allowed=False)
def deliver_storyboard(payload):
    from telok import storage
    from telok.assistant import details
    from telok.integrations import send_video

    task = details(payload["request_id"], payload["actor"])
    if task["payload"].get("chat_id") and task["status"] != "CANCELLED":
        data, _ = storage.read(task["result"]["storyboard_asset_id"], task["project_id"])
        send_video(task["payload"]["chat_id"], data)
