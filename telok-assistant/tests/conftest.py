import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = "telok_test_" + uuid4().hex[:10]
ADMIN_URL = os.environ.get("TELOK_TEST_ADMIN_URL", "postgresql+psycopg://telok:telok-local-only@127.0.0.1:54391/postgres")
admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
with admin.connect() as conn:
    conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
os.environ["TELOK_DATABASE_URL"] = ADMIN_URL.removesuffix("postgres") + TEST_DB
os.environ["TELOK_DATA_DIR"] = str(ROOT / "artifacts" / TEST_DB)
os.environ["TELOK_REASONING_PROVIDER"] = "openai_api"
os.environ["TELOK_CREDENTIALS_DIR"] = str(ROOT / "artifacts" / TEST_DB / "credentials")
os.environ["TELOK_OPENAI_API_KEY"] = ""
os.environ["TELOK_TELEGRAM_TOKEN"] = ""
os.environ["TELOK_ENV"] = "development"
os.environ["TELOK_PRICING_CONFIRMED"] = "true"
os.environ["TELOK_TASK_BUDGET_USD"] = "1"
os.environ["TELOK_DAILY_BUDGET_USD"] = "1"
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=ROOT,
    env=os.environ.copy(),
    check=True,
)

from telok.db import (  # noqa: E402 - settings must target isolated DB before application import
    engine,
    transaction,
)
from telok.domain import (  # noqa: E402 - isolated environment must precede imports
    create_project,  # noqa: E402 - settings must target isolated DB before application import
)
from telok.models import (  # noqa: E402 - settings must target isolated DB before application import
    BudgetBucket,
    ProviderAttempt,
)
from telok.settings import settings  # noqa: E402 - settings must target isolated DB before application import


@pytest.fixture(autouse=True)
def reset_budget():
    with transaction() as session:
        session.query(ProviderAttempt).delete()
        session.query(BudgetBucket).delete()
    cfg = settings()
    cfg.task_budget_usd = cfg.daily_budget_usd = 1
    cfg.pricing_confirmed = True


@pytest.fixture
def project():
    return create_project(1, "Test " + uuid4().hex[:6])


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    assert TEST_DB.startswith("telok_test_") and len(TEST_DB) == 21
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"
            ),
            {"name": TEST_DB},
        )
        conn.execute(text(f'DROP DATABASE "{TEST_DB}"'))
    admin.dispose()
