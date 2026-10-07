"""Restore only to a NEW isolated database. Keep all workers stopped."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from sqlalchemy import create_engine, text

ADMIN = "postgresql+psycopg://telok:telok-local-only@127.0.0.1:54391/postgres"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    database = "telok_restore_" + uuid4().hex[:10]
    assert re.fullmatch(r"telok_restore_[0-9a-f]{10}", database)
    output = Path("artifacts/restore") / database
    output.mkdir(parents=True, exist_ok=False)
    with ZipFile(args.archive) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        dump = archive.read("database.dump")
        assert hashlib.sha256(dump).hexdigest() == manifest["dump_sha256"]
        for asset in manifest["assets"]:
            target = (output / asset["path"]).resolve()
            assert target.is_relative_to(output.resolve())
            data = archive.read(asset["path"])
            assert hashlib.sha256(data).hexdigest() == asset["sha256"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{database}"'))
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "telok-postgres",
            "pg_restore",
            "-U",
            "telok",
            "-d",
            database,
            "--exit-on-error",
        ],
        input=dump,
        capture_output=True,
        check=True,
    )
    restored = create_engine(ADMIN.removesuffix("postgres") + database)
    with restored.begin() as conn:
        conn.execute(text("UPDATE telok.projects SET paused=true"))
        conn.execute(text("UPDATE telok.publications SET status='UNKNOWN' WHERE status='SENDING'"))
        conn.execute(
            text(
                "UPDATE telok.publications SET status='CANCELLED', revision=revision+1 WHERE status='SCHEDULED'"
            )
        )
        conn.execute(
            text(
                "UPDATE dbos.workflow_status SET status='CANCELLED' WHERE status IN ('ENQUEUED','DELAYED','PENDING')"
            )
        )
        counts = {
            name: conn.execute(text(f"SELECT count(*) FROM {name}")).scalar()
            for name in ["telok.projects", "telok.content_versions", "telok.assets", "dbos.workflow_status"]
        }
        rows = conn.execute(text("SELECT id, key, sha256 FROM telok.assets")).mappings().all()
        for row in rows:
            data = (output / "assets" / row["key"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row["sha256"]
        assert conn.execute(text("SELECT count(*) FROM telok.projects WHERE NOT paused")).scalar() == 0
        assert (
            conn.execute(text("SELECT count(*) FROM telok.publications WHERE status='SCHEDULED'")).scalar()
            == 0
        )
    report = {
        "database": database,
        "workers_started": False,
        "projects_paused": True,
        "asset_hashes_verified": len(rows),
        "counts": counts,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    restored.dispose()
    admin.dispose()
    print(json.dumps(report))


if __name__ == "__main__":
    main()
