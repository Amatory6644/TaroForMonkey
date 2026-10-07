"""Scene media jobs and reusable verified clip assets."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "media_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("telok.projects.id"), nullable=False),
        sa.Column("request_id", sa.String(64), sa.ForeignKey("telok.requests.id"), nullable=False),
        sa.Column("scene_id", sa.String(40), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("provider_job_id", sa.String(150), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "cache_key", "provider"),
        schema="telok",
    )


def downgrade():
    op.drop_table("media_jobs", schema="telok")
