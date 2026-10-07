"""Assistant task artifacts and independent subscription invocation ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None


def upgrade():
    def common():
        return [
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("project_id", sa.String(64), sa.ForeignKey("telok.projects.id"), nullable=False),
        ]

    op.create_table(
        "ai_invocations",
        *common(),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        schema="telok",
    )
    op.create_index("ix_ai_invocations_request_id", "ai_invocations", ["request_id"], schema="telok")
    op.create_table(
        "task_artifacts",
        *common(),
        sa.Column("request_id", sa.String(64), sa.ForeignKey("telok.requests.id"), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.UniqueConstraint("request_id", "stage", "revision"),
        schema="telok",
    )
    op.create_index("ix_task_artifacts_project_id", "task_artifacts", ["project_id"], schema="telok")
    op.create_table(
        "task_messages",
        *common(),
        sa.Column("request_id", sa.String(64), sa.ForeignKey("telok.requests.id"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id"),
        schema="telok",
    )


def downgrade():
    for name in ("task_messages", "task_artifacts", "ai_invocations"):
        op.drop_table(name, schema="telok")
