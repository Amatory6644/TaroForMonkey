import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select, text

from telok.db import engine, transaction, uid
from telok.domain import queue
from telok.models import Audit, Publication
from tests.test_invariants import outgoing

ROOT = Path(__file__).resolve().parents[1]


def wait(check, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError("Runtime condition timed out")


def start_worker(version="test-v1", crash=False, editorial_fixture=False):
    env = os.environ.copy()
    env["TELOK_TEST_CRASH_SEND"] = "1" if crash else "0"
    env["TELOK_TEST_EDITORIAL_FIXTURE"] = "1" if editorial_fixture else "0"
    log_dir = ROOT / "artifacts" / "runtime"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / (uid() + ".log")).open("w", encoding="utf-8")
    p = subprocess.Popen(
        [sys.executable, "-m", "tests.runtime_worker", version],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    p.telok_log = log

    def ready():
        if p.poll() is not None:
            raise AssertionError("Worker exited; see artifacts/runtime logs")
        try:
            with engine.connect() as conn:
                return conn.execute(text("SELECT COUNT(*) FROM dbos.queues")).scalar() >= 2
        except Exception:
            return False

    wait(ready)
    time.sleep(0.4)
    return p


def stop_worker(p):
    if p.poll() is None:
        p.terminate()
        p.wait(timeout=10)
    p.telok_log.close()


def status(workflow_id):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM dbos.workflow_status WHERE workflow_uuid=:id"), {"id": workflow_id}
        ).scalar()


@pytest.mark.integration
def test_atomic_enqueue_checkpoint_recovery_and_delayed_cancel():
    p = start_worker()
    probe_id = uid()
    workflow_id = "probe:" + probe_id
    try:
        with transaction() as session:
            queue(session, "telok_probe", workflow_id, {"id": probe_id, "sleep": 6})

        def checkpoint():
            with transaction() as session:
                return any(
                    a.payload.get("probe_id") == probe_id
                    for a in session.execute(select(Audit).where(Audit.event == "probe_step")).scalars()
                )

        wait(checkpoint)
        stop_worker(p)
        p = start_worker()
        wait(lambda: status(workflow_id) == "SUCCESS")
        with transaction() as session:
            hits = [
                a
                for a in session.execute(select(Audit).where(Audit.event == "probe_step")).scalars()
                if a.payload.get("probe_id") == probe_id
            ]
        assert len(hits) == 1

        rollback_id = "rollback:" + uid()
        with pytest.raises(RuntimeError):
            with transaction() as session:
                queue(session, "telok_probe", rollback_id, {"id": uid()})
                raise RuntimeError("rollback")
        assert status(rollback_id) is None
    finally:
        stop_worker(p)


@pytest.mark.integration
def test_physical_send_crash_then_replay_never_resends(project):
    p = start_worker(crash=True)
    pub, version = outgoing(project)
    workflow_id = "send-crash:" + uid()
    try:
        with transaction() as session:
            queue(session, "test_send", workflow_id, {"id": pub, "project_id": project["id"]})
        wait(lambda: p.poll() is not None)
        assert p.returncode == 86
        p.telok_log.close()
        with transaction() as session:
            assert session.get(Publication, pub).status == "SENDING"
        p = start_worker(crash=False)
        wait(lambda: status(workflow_id) == "SUCCESS")
        with transaction() as session:
            assert session.get(Publication, pub).status == "UNKNOWN"
            sends = [
                a
                for a in session.execute(select(Audit).where(Audit.event == "test_external_send")).scalars()
                if a.payload.get("id") == pub
            ]
        assert len(sends) == 1
    finally:
        stop_worker(p)


@pytest.mark.integration
def test_version_upgrade_preserves_pending_old_execution():
    p = start_worker("test-v1")
    workflow_id = "version:" + uid()
    probe_id = uid()
    p2 = None
    try:
        with transaction() as session:
            queue(session, "telok_probe", workflow_id, {"id": probe_id, "sleep": 5})
        wait(lambda: status(workflow_id) == "PENDING")
        time.sleep(0.4)
        stop_worker(p)
        p2 = start_worker("test-v2")
        time.sleep(0.7)
        assert status(workflow_id) == "PENDING"
        p = start_worker("test-v1")
        wait(lambda: status(workflow_id) == "SUCCESS")
    finally:
        stop_worker(p)
        if p2:
            stop_worker(p2)


@pytest.mark.integration
def test_durable_editorial_image_then_text_edit_and_calendar(project):
    from telok import editorial
    from telok.models import Plan, Version, Work

    p = start_worker("test-v3", editorial_fixture=True)
    try:
        work = editorial.request_work(project["id"], 1, "generate", {"brief": "Test", "format": "IMAGE_POST"})
        wait(lambda: status(work["workflow_id"]) == "SUCCESS")
        with transaction() as session:
            request = session.get(Work, work["id"])
            assert request.status == "READY_FOR_REVIEW", request.error
            first = session.get(Version, request.result["version_id"])
            first_id, first_assets = first.id, first.asset_ids
        edit = editorial.request_work(
            project["id"],
            1,
            "edit",
            {"version_id": first_id, "edit_scope": "text", "brief": "Change question"},
        )
        wait(lambda: status(edit["workflow_id"]) == "SUCCESS")
        with transaction() as session:
            request = session.get(Work, edit["id"])
            assert request.status == "READY_FOR_REVIEW", request.error
            second = session.get(Version, request.result["version_id"])
            assert second.revision == 2 and second.asset_ids == first_assets
            assert session.get(Version, first_id).status == "SUPERSEDED"
            calls = (
                session.execute(
                    select(Audit).where(
                        Audit.project_id == project["id"], Audit.event == "fixture_image_call"
                    )
                )
                .scalars()
                .all()
            )
            assert len(calls) == 1
        plan = editorial.request_work(project["id"], 1, "plan", {"count": 7, "start": "2026-10-31"})
        wait(lambda: status(plan["workflow_id"]) == "SUCCESS")
        with transaction() as session:
            request = session.get(Work, plan["id"])
            assert request.status == "SUCCEEDED", request.error
            slots = session.get(Plan, request.result["plan_id"]).slots
            assert len(slots) == 7 and slots[1]["date"] == "2026-11-01"
            plan_id, slot_id = request.result["plan_id"], slots[0]["id"]
        slot_work = editorial.request_work(
            project["id"], 1, "generate", {"brief": "Slot test", "plan_id": plan_id, "slot_id": slot_id}
        )
        duplicate = editorial.request_work(
            project["id"], 1, "generate", {"brief": "Duplicate", "plan_id": plan_id, "slot_id": slot_id}
        )
        assert duplicate["id"] == slot_work["id"]
        wait(lambda: status(slot_work["workflow_id"]) == "SUCCESS")
        with transaction() as session:
            slot = session.get(Plan, plan_id).slots[0]
            assert slot["version_id"] == session.get(Work, slot_work["id"]).result["version_id"]
            assert slot["state"] == "READY_FOR_REVIEW"
    finally:
        stop_worker(p)


@pytest.mark.integration
def test_delayed_jobs_survive_restart_and_old_cancel_revision_never_sends(project):
    from datetime import timedelta

    from telok import publisher
    from telok.db import now

    p = start_worker("test-v4")
    probe_id, workflow_id = uid(), "delayed:" + uid()
    try:
        with transaction() as session:
            queue(session, "telok_probe", workflow_id, {"id": probe_id}, now() + timedelta(seconds=3))
        stop_worker(p)
        p = start_worker("test-v4")
        wait(lambda: status(workflow_id) == "SUCCESS")
        with transaction() as session:
            hits = [
                a
                for a in session.execute(select(Audit).where(Audit.event == "probe_step")).scalars()
                if a.payload.get("probe_id") == probe_id
            ]
            assert len(hits) == 1
        pub, _ = outgoing(project)
        cancelled_job = "delayed-cancel:" + uid()
        with transaction() as session:
            queue(
                session,
                "test_send",
                cancelled_job,
                {"id": pub, "project_id": project["id"]},
                now() + timedelta(seconds=2),
            )
        publisher.reschedule(pub, 1, None, cancel=True)
        wait(lambda: status(cancelled_job) == "SUCCESS")
        with transaction() as session:
            assert session.get(Publication, pub).status == "CANCELLED"
            sends = [
                a
                for a in session.execute(select(Audit).where(Audit.event == "test_external_send")).scalars()
                if a.payload.get("id") == pub
            ]
            assert not sends
    finally:
        stop_worker(p)
