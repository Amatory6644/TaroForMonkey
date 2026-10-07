"""Project foreign keys and 64-bit Telegram identities."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    for table, column, target in [
        ("requests", "project_id", "projects"),
        ("content_items", "project_id", "projects"),
        ("assets", "project_id", "projects"),
        ("content_versions", "project_id", "projects"),
        ("content_versions", "item_id", "content_items"),
        ("approvals", "version_id", "content_versions"),
        ("publications", "version_id", "content_versions"),
        ("publications", "project_id", "projects"),
        ("publication_attempts", "publication_id", "publications"),
        ("provider_attempts", "project_id", "projects"),
        ("evidence", "project_id", "projects"),
        ("plans", "project_id", "projects"),
        ("feedback", "project_id", "projects"),
    ]:
        inspector = sa.inspect(op.get_bind())
        fks = inspector.get_foreign_keys(table, schema="telok")
        if not any(fk["constrained_columns"] == [column] for fk in fks):
            op.create_foreign_key(
                f"fk_{table}_{column}",
                table,
                target,
                [column],
                ["id"],
                source_schema="telok",
                referent_schema="telok",
            )
    for table, column in [
        ("projects", "owner_id"),
        ("approvals", "actor_id"),
        ("contexts", "actor_id"),
        ("telegram_receipts", "update_id"),
    ]:
        op.alter_column(table, column, type_=sa.BigInteger(), schema="telok")


def downgrade():
    pass  # No destructive automatic rollback of live identity data.
