import os
import signal
import sys
import threading

from dbos import DBOS

from telok import (
    publisher,
    workflows,  # noqa: F401
)
from telok.db import transaction
from telok.domain import record

if os.environ.get("TELOK_TEST_EDITORIAL_FIXTURE") == "1":
    from telok import providers, storage

    def fixture_text(project_id, request_id, role, ctx, schema, images=None):
        if role == "producer":
            return {
                "text": "Новый нейтральный вопрос." if ctx["kind"] == "edit" else "Нейтральный вопрос.",
                "claims": [],
                "visual": {"prompt": "Fixture image", "required_references": [], "exclusions": []},
                "rationale": "Deterministic fixture; not a live provider.",
            }
        if role == "reviewer":
            return {"findings": [], "summary": "Fixture review; not a live provider."}
        if role == "ideas":
            return {
                "ideas": [
                    {
                        "title": f"Concept {i}",
                        "hook": "Question",
                        "goal": "Discuss",
                        "format": "IMAGE_POST",
                        "rationale": "Fixture",
                    }
                    for i in range(ctx["count"])
                ]
            }
        if role == "director":
            return {"ranked_indexes": list(range(len(ctx["ideas"]))), "rationale": "Fixture"}
        raise ValueError("Unexpected fixture role")

    def fixture_image(project_id, request_id, spec):
        import io

        from PIL import Image

        image = io.BytesIO()
        Image.new("RGB", (128, 128), "#cad9c0").save(image, format="PNG")
        with transaction() as session:
            record(session, project_id, "fixture_image_call", {"request_id": request_id})
        return storage.put(project_id, image.getvalue(), "image/png", {"provider": "test-fixture"})

    providers.text = fixture_text
    providers.image = fixture_image


@DBOS.step(name="test_external_step", retries_allowed=False)
def external_step(payload: dict):
    def transport(manifest, files):
        with transaction() as session:
            record(session, payload["project_id"], "test_external_send", {"id": payload["id"]})
        if os.environ.get("TELOK_TEST_CRASH_SEND") == "1":
            os._exit(86)
        return {"message_id": 999, "chat_id": -1001234567}

    return publisher.send(payload["id"], 1, transport)


@DBOS.workflow(name="test_send", serialization_type="portable_json")
def send_job(payload: dict):
    return external_step(payload)


version = sys.argv[1] if len(sys.argv) > 1 else "test-v1"
DBOS(
    config={
        "name": "telok-ai",
        "system_database_url": os.environ["TELOK_DATABASE_URL"],
        "application_version": version,
        "executor_id": "test-executor-" + version,
        "run_admin_server": False,
        "enable_patching": True,
    }
)
DBOS.launch()
DBOS.register_queue("production", worker_concurrency=2)
DBOS.register_queue("publishing", worker_concurrency=2)
stopped = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stopped.set())
signal.signal(signal.SIGINT, lambda *_: stopped.set())
stopped.wait()
DBOS.destroy()
