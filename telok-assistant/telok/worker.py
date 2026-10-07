import signal
import threading

from dbos import DBOS

from telok import assistant, media_workflows, workflows  # noqa: F401
from telok.settings import settings


def main():
    cfg = settings()
    DBOS(
        config={
            "name": "telok-ai",
            "system_database_url": cfg.database_url,
            "application_version": cfg.application_version,
            "executor_id": cfg.executor_id,
            "enable_patching": True,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    DBOS.register_queue("production", worker_concurrency=2)
    DBOS.register_queue("assistant", worker_concurrency=1)
    DBOS.register_queue("media", worker_concurrency=1)
    DBOS.register_queue("publishing", worker_concurrency=2)
    if DBOS.get_schedule("telok-campaign-minute") is None:
        DBOS.create_schedule(
            schedule_name="telok-campaign-minute",
            workflow_fn=workflows.campaign_job,
            schedule="* * * * *",
            cron_timezone="UTC",
            automatic_backfill=False,
        )
    event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: event.set())
    signal.signal(signal.SIGTERM, lambda *_: event.set())
    event.wait()
    DBOS.destroy()


if __name__ == "__main__":
    main()
