"""Export this development database and immutable assets; never include secrets."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from telok import storage
from telok.db import transaction
from telok.models import Asset
from telok.settings import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cfg = settings()
    if cfg.database_url != "postgresql+psycopg://telok:telok-local-only@127.0.0.1:54391/telok":
        raise SystemExit(
            "This helper is only for the named local development database. Use the production runbook for pg_dump/S3."
        )
    target = Path(args.output).resolve()
    if target.exists():
        raise SystemExit("Backup destination exists; choose a new filename.")
    target.parent.mkdir(parents=True, exist_ok=True)
    dump = subprocess.run(
        ["docker", "exec", "telok-postgres", "pg_dump", "-U", "telok", "-d", "telok", "-Fc"],
        capture_output=True,
        check=True,
    ).stdout
    with transaction() as session:
        assets = [(asset.id, asset.key) for asset in session.execute(select(Asset)).scalars()]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": "domain_and_dbos",
        "dump_sha256": hashlib.sha256(dump).hexdigest(),
        "assets": [],
    }
    with ZipFile(target, "x", ZIP_DEFLATED) as archive:
        archive.writestr("database.dump", dump)
        for asset_id, key in assets:
            data, meta = storage.read(asset_id)
            name = "assets/" + key
            archive.writestr(name, data)
            manifest["assets"].append({"id": asset_id, "path": name, "sha256": meta["sha256"]})
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Backup created: {target}; {len(assets)} verified assets. Copy this archive off-host.")


if __name__ == "__main__":
    main()
