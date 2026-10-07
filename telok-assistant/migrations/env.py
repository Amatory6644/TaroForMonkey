from alembic import context

from telok import models  # noqa: F401
from telok.db import Base, engine

with engine.connect() as connection:
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS telok")
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table_schema="telok",
    )
    with context.begin_transaction():
        context.run_migrations()
