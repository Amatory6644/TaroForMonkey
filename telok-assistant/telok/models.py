from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from telok.db import Base, now, uid


class Row(Base):
    __abstract__ = True
    __table_args__ = {"schema": "telok"}
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Row):
    __tablename__ = "projects"
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    brand: Mapped[dict] = mapped_column(JSON, default=dict)
    brand_revision: Mapped[int] = mapped_column(default=1)
    channel_id: Mapped[str] = mapped_column(String(100), default="")
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Moscow")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)


class Work(Row):
    __tablename__ = "requests"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED")
    error: Mapped[str] = mapped_column(Text, default="")
    workflow_id: Mapped[str] = mapped_column(String(200), unique=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Item(Row):
    __tablename__ = "content_items"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    current_version_id: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[int] = mapped_column(default=0)


class Asset(Row):
    __tablename__ = "assets"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(300), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    info: Mapped[dict] = mapped_column(JSON, default=dict)


class Version(Row):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("item_id", "revision"), {"schema": "telok"})
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.content_items.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(30))
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    qa: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="READY_FOR_REVIEW")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Approval(Row):
    __tablename__ = "approvals"
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.content_versions.id"), unique=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_findings: Mapped[list] = mapped_column(JSON, default=list)
    policy_revision: Mapped[int] = mapped_column(default=0)


class Publication(Row):
    __tablename__ = "publications"
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.content_versions.id"), unique=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(40), default="SCHEDULED")
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_id: Mapped[str] = mapped_column(String(64), default="")
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class SendAttempt(Row):
    __tablename__ = "publication_attempts"
    publication_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.publications.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="SENDING")
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class BudgetBucket(Row):
    __tablename__ = "budget_buckets"
    spent: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)


class ProviderAttempt(Row):
    __tablename__ = "provider_attempts"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    charged: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    status: Mapped[str] = mapped_column(String(40), default="RESERVED")
    bucket_ids: Mapped[list] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_request_id: Mapped[str] = mapped_column(String(200), default="")


class Evidence(Row):
    __tablename__ = "evidence"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    claim: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="owner_statement")
    assessment: Mapped[str] = mapped_column(String(30), default="insufficient")
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Plan(Row):
    __tablename__ = "plans"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    slots: Mapped[list] = mapped_column(JSON)


class Feedback(Row):
    __tablename__ = "feedback"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")
    editor_minutes: Mapped[float] = mapped_column(default=0)


class Receipt(Row):
    __tablename__ = "telegram_receipts"
    update_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Context(Row):
    __tablename__ = "contexts"
    actor_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    project_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    pending_edit: Mapped[dict] = mapped_column(JSON, default=dict)


class Audit(Row):
    __tablename__ = "audit"
    project_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    event: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Callback(Row):
    __tablename__ = "callbacks"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"))
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.content_versions.id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class AIInvocation(Row):
    __tablename__ = "ai_invocations"
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class TaskArtifact(Row):
    __tablename__ = "task_artifacts"
    __table_args__ = (UniqueConstraint("request_id", "stage", "revision"), {"schema": "telok"})
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.requests.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(default=1)
    body: Mapped[dict] = mapped_column(JSON, default=dict)


class TaskMessage(Row):
    __tablename__ = "task_messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id"), {"schema": "telok"})
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"))
    request_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.requests.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)


class MediaJob(Row):
    __tablename__ = "media_jobs"
    __table_args__ = (UniqueConstraint("project_id", "cache_key", "provider"), {"schema": "telok"})
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.projects.id"))
    request_id: Mapped[str] = mapped_column(String(64), ForeignKey("telok.requests.id"))
    scene_id: Mapped[str] = mapped_column(String(40))
    cache_key: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    provider_job_id: Mapped[str] = mapped_column(String(150), default="")
    asset_id: Mapped[str] = mapped_column(String(64), default="")
    attempt_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
