from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from telok.db import now, transaction, uid
from telok.models import BudgetBucket, ProviderAttempt
from telok.settings import settings


def reserve(project_id: str, request_id: str, provider: str, amount: float) -> str:
    cfg = settings()
    cap = Decimal(str(amount))
    if not cap.is_finite() or cap <= 0 or not cfg.pricing_confirmed:
        raise ValueError("Настройте и подтвердите консервативный резерв стоимости provider call.")
    day = now().date().isoformat()
    limits = {
        f"account:{day}": cfg.daily_budget_usd,
        f"project:{project_id}:{day}": cfg.daily_budget_usd,
        f"request:{request_id}": cfg.task_budget_usd,
    }
    with transaction() as session:
        for key in sorted(limits):
            session.execute(insert(BudgetBucket).values(id=key, spent=0, reserved=0).on_conflict_do_nothing())
            bucket = session.execute(
                select(BudgetBucket).where(BudgetBucket.id == key).with_for_update()
            ).scalar_one()
            if bucket.spent + bucket.reserved + cap > Decimal(str(limits[key])):
                raise ValueError("Денежный лимит исчерпан; новые вызовы остановлены.")
            bucket.reserved += cap
        attempt = ProviderAttempt(
            id=uid(),
            project_id=project_id,
            request_id=request_id,
            provider=provider,
            reserved=cap,
            bucket_ids=sorted(limits),
        )
        session.add(attempt)
        return attempt.id


def finish(attempt_id: str, usage: dict, provider_id: str = "", charged: float | None = None):
    # Without a verified tariff resolver, charge the conservative upper bound.
    # This is explicitly estimated, never advertised as an exact bill.
    with transaction() as session:
        attempt = session.execute(
            select(ProviderAttempt).where(ProviderAttempt.id == attempt_id).with_for_update()
        ).scalar_one()
        if attempt.status != "RESERVED":
            return
        cost = attempt.reserved if charged is None else Decimal(str(charged))
        if cost < 0:
            raise ValueError("Отрицательная стоимость недопустима.")
        for key in attempt.bucket_ids:
            bucket = session.execute(
                select(BudgetBucket).where(BudgetBucket.id == key).with_for_update()
            ).scalar_one()
            bucket.reserved -= attempt.reserved
            bucket.spent += cost
        attempt.status = "ESTIMATED" if charged is None else "SETTLED"
        attempt.charged = cost
        attempt.usage = usage
        attempt.provider_request_id = provider_id


def uncertain(attempt_id: str):
    with transaction() as session:
        attempt = session.get(ProviderAttempt, attempt_id)
        if attempt and attempt.status == "RESERVED":
            attempt.status = "UNKNOWN"  # Do not release possibly consumed money.


def settle(attempt_id: str, charged: float, provenance: str):
    from telok.models import Project

    cost = Decimal(str(charged))
    if not cost.is_finite() or cost < 0 or not provenance.strip():
        raise ValueError("Нужны неотрицательная фактическая стоимость и источник сверки.")
    with transaction() as session:
        attempt = session.execute(
            select(ProviderAttempt).where(ProviderAttempt.id == attempt_id).with_for_update()
        ).scalar_one()
        if attempt.status == "SETTLED":
            if cost != attempt.charged:
                raise ValueError("Попытка уже сверена с другой стоимостью.")
            return
        held = attempt.reserved if attempt.status in {"RESERVED", "UNKNOWN"} else Decimal(0)
        for key in sorted(attempt.bucket_ids):
            bucket = session.execute(
                select(BudgetBucket).where(BudgetBucket.id == key).with_for_update()
            ).scalar_one()
            bucket.reserved -= held
            bucket.spent += cost - attempt.charged
        if cost > attempt.reserved:
            session.get(Project, attempt.project_id).paused = True
        attempt.charged, attempt.status = cost, "SETTLED"
        attempt.usage = {**attempt.usage, "settlement_provenance": provenance}
