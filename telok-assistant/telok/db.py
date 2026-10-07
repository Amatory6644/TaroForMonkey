from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from telok.settings import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings().database_url, pool_pre_ping=True, pool_size=6, max_overflow=4)
Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction():
    with Session.begin() as session:
        yield session


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)
