"""Opaque Telegram actions and pending edit context."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "contexts", sa.Column("pending_edit", sa.JSON(), nullable=False, server_default="{}"), schema="telok"
    )
    op.create_table(
        "callbacks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("telok.projects.id"), nullable=False),
        sa.Column("version_id", sa.String(64), sa.ForeignKey("telok.content_versions.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default="false"),
        schema="telok",
    )


def downgrade():
    op.drop_table("callbacks", schema="telok")
    op.drop_column("contexts", "pending_edit", schema="telok")
