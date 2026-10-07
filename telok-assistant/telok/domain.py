import hashlib
import ipaddress
import json
import socket
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import inspect, select, text

from telok.db import now, transaction, uid
from telok.models import Audit, Context, Evidence, Project
from telok.settings import settings


class DomainError(ValueError):
    pass


def owned(session, project_id: str, actor: int) -> Project:
    project = session.get(Project, project_id)
    if not project or project.owner_id != actor:
        raise DomainError("Проект не найден или доступ запрещён.")
    return project


def record(session, project_id: str, event: str, payload: dict):
    session.add(Audit(project_id=project_id, event=event, payload=payload))


def encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return float(value)
    raise TypeError(type(value).__name__)


def row_dict(row) -> dict:
    return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}


def canonical(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def queue(session, name: str, workflow_id: str, payload, when: datetime | None = None):
    if when is not None and when.tzinfo is None:
        raise DomainError("Дата должна содержать timezone.")
    session.execute(
        text("""
        SELECT dbos.enqueue_workflow(
            workflow_name => :name, queue_name => :queue,
            positional_args => ARRAY[CAST(:payload AS JSON)],
            workflow_id => :workflow_id, application_name => 'telok-ai',
            delay_until_epoch_ms => :delay)
    """),
        {
            "name": name,
            "queue": "publishing"
            if name == "telok_publish"
            else "assistant"
            if name == "telok_assistant"
            else "media"
            if name == "telok_media"
            else "production",
            "payload": json.dumps(payload),
            "workflow_id": workflow_id,
            "delay": int(when.timestamp() * 1000) if when else None,
        },
    )


def create_project(actor: int, name: str, description: str = "") -> dict:
    if not name.strip() or len(name) > 100:
        raise DomainError("Название: от 1 до 100 символов.")
    with transaction() as session:
        project = Project(
            owner_id=actor,
            name=name.strip(),
            description=description,
            brand={
                "name": name.strip(),
                "tone": "нейтральный, ясный",
                "facts": [],
                "rules": ["Не выдумывать характеристики и достижения бренда"],
                "references": [],
                "knowledge_status": "needs_description",
            },
        )
        session.add(project)
        session.flush()
        record(session, project.id, "project_created", {"actor": actor})
        return row_dict(project)


def update_brand(project_id: str, actor: int, brand: dict, expected: int, description: str | None = None):
    with transaction() as session:
        project = owned(session, project_id, actor)
        session.refresh(project, with_for_update=True)
        if project.brand_revision != expected:
            raise DomainError("Бренд уже изменён. Обновите страницу.")
        refs = brand.get("references", [])
        from telok.models import Asset

        for ref in refs:
            asset = session.get(Asset, ref)
            if not asset or asset.project_id != project_id:
                raise DomainError("Reference принадлежит другому проекту.")
        project.brand = brand
        project.brand_revision += 1
        if description is not None:
            project.description = description
        record(session, project_id, "brand_updated", {"actor": actor, "revision": project.brand_revision})
        return row_dict(project)


def add_evidence(
    project_id: str,
    actor: int,
    claim: str,
    excerpt: str,
    url: str = "",
    kind: str = "owner_statement",
    assessment: str = "insufficient",
):
    if kind not in {"owner_statement", "external"} or assessment not in {
        "supports",
        "contradicts",
        "insufficient",
    }:
        raise DomainError("Некорректный тип evidence.")
    if not claim.strip() or not excerpt.strip():
        raise DomainError("Нужны утверждение и поддерживающий фрагмент.")
    with transaction() as session:
        owned(session, project_id, actor)
        evidence = Evidence(
            project_id=project_id, claim=claim, excerpt=excerpt, url=url, kind=kind, assessment=assessment
        )
        session.add(evidence)
        session.flush()
        record(session, project_id, "evidence_added", {"id": evidence.id, "kind": kind})
        return row_dict(evidence)


def fetch_source(url: str) -> str:
    cfg = settings()
    current = url
    with httpx.Client(timeout=12, follow_redirects=False) as client:
        for _ in range(4):
            parsed = urlparse(current)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
            ):
                raise DomainError("Research принимает только HTTPS без credentials.")
            if parsed.hostname not in cfg.research_allowed_domains:
                raise DomainError("Источник должен быть в TELOK_RESEARCH_ALLOWED_DOMAINS.")
            for entry in socket.getaddrinfo(parsed.hostname, 443):
                if not ipaddress.ip_address(entry[4][0]).is_global:
                    raise DomainError("Private/internal адрес запрещён.")
            # Exact-domain allowlist is an additional trust boundary. Do not enable untrusted wildcard domains.
            with client.stream("GET", current, headers={"User-Agent": "TelokResearch/0.1"}) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin

                    current = urljoin(current, response.headers["location"])
                    continue
                response.raise_for_status()
                if "text/" not in response.headers.get("content-type", ""):
                    raise DomainError("Research загружает только текст.")
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 1_000_000:
                        raise DomainError("Источник превышает 1 MB.")
                    parts.append(chunk)
                return b"".join(parts).decode(response.encoding or "utf-8", errors="replace")[:100_000]
    raise DomainError("Слишком много redirects.")


def set_context(actor: int, project_id: str):
    with transaction() as session:
        owned(session, project_id, actor)
        context = session.execute(select(Context).where(Context.actor_id == actor)).scalar_one_or_none()
        if not context:
            context = Context(actor_id=actor)
            session.add(context)
        context.project_id = project_id


def calendar_slots(ideas: list[dict], start: str, timezone: str) -> list[dict]:
    ZoneInfo(timezone)
    first = date.fromisoformat(start)
    return [
        {
            "id": uid(),
            "date": (first + timedelta(days=index)).isoformat(),
            "timezone": timezone,
            "state": "PLANNED",
            **idea,
        }
        for index, idea in enumerate(ideas)
    ]


def factual_checks(draft: dict, evidence: list[dict]) -> list[str]:
    by_id = {entry["id"]: entry for entry in evidence}
    failures = []
    for claim in draft.get("claims", []):
        refs = claim.get("evidence_ids", [])
        supported = False
        for ref in refs:
            entry = by_id.get(ref)
            if not entry or entry["assessment"] != "supports":
                continue
            expiry = entry.get("valid_until")
            if expiry and datetime.fromisoformat(str(expiry)) < now():
                continue
            if (
                entry.get("excerpt", "").strip()
                and entry.get("claim", "").strip().casefold() == claim["text"].strip().casefold()
            ):
                supported = True
        if not supported:
            failures.append("Нет поддерживающего evidence: " + claim["text"])
    return failures
