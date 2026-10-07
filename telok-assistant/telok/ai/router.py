import json
import time
from dataclasses import asdict

from sqlalchemy import select

from telok.ai import credentials
from telok.ai.contracts import ProviderError
from telok.ai.providers import ChatGPTPlanProvider, LocalQwenProvider
from telok.db import transaction, uid
from telok.models import AIInvocation, Project


def invoke(project_id, request_id, instructions, content, *, search=False, schema=None):
    from telok.providers import preflight

    preflight(project_id, request_id)
    cfg = credentials.read()
    route = cfg.get("route", "CHATGPT_ONLY")
    with transaction() as session:
        session.execute(select(Project).where(Project.id == project_id).with_for_update()).scalar_one()
        if (
            session.query(AIInvocation)
            .filter(AIInvocation.request_id == request_id, AIInvocation.status == "STARTED")
            .count()
        ):
            raise ProviderError(
                "UNKNOWN_PREVIOUS_ATTEMPT",
                "Предыдущий вызов не сверён после остановки. Создайте новую явную попытку.",
            )
        count = session.query(AIInvocation).filter(AIInvocation.request_id == request_id).count()
        if count >= 12:
            raise ProviderError("TASK_LIMIT", "Достигнут лимит 12 вызовов на задачу.")
        attempt = AIInvocation(
            id=uid(),
            project_id=project_id,
            request_id=request_id,
            provider=route,
            status="STARTED",
            details={},
        )
        session.add(attempt)
        attempt_id = attempt.id
    started = time.monotonic()
    last_check = [0.0]

    def check():
        if time.monotonic() - last_check[0] > 1:
            preflight(project_id, request_id)
            last_check[0] = time.monotonic()

    try:
        provider = LocalQwenProvider() if route == "LOCAL_ONLY" else ChatGPTPlanProvider()
        fallback = False
        try:
            result = provider.generate(instructions, content, search=search, check=check, schema=schema)
        except ProviderError as exc:
            if (
                route != "AUTO"
                or search
                or exc.code not in {"PLAN_LIMIT_REACHED", "TEMPORARILY_UNAVAILABLE", "NETWORK"}
            ):
                raise
            with transaction() as session:
                old = session.get(AIInvocation, attempt_id)
                old.status = "UNKNOWN"
                old.details = {"code": exc.code, "fallback_requested": True}
                if session.query(AIInvocation).filter(AIInvocation.request_id == request_id).count() >= 12:
                    raise ProviderError("TASK_LIMIT", "Лимит попыток исчерпан до fallback.") from None
                attempt = AIInvocation(
                    id=uid(),
                    project_id=project_id,
                    request_id=request_id,
                    provider="LocalQwen",
                    status="STARTED",
                    details={"fallback_from": attempt_id},
                )
                session.add(attempt)
                attempt_id = attempt.id
            result = LocalQwenProvider().generate(instructions, content, check=check, schema=schema)
            fallback = True
        preflight(project_id, request_id)
        with transaction() as session:
            attempt = session.get(AIInvocation, attempt_id)
            attempt.status = "COMPLETED"
            attempt.provider = result.provider
            attempt.details = {
                "model": result.model,
                "usage": result.usage,
                "response_id": result.response_id,
                "seconds": round(time.monotonic() - started, 2),
                "fallback": fallback,
            }
        return {**asdict(result), "fallback": fallback}
    except Exception as exc:
        with transaction() as session:
            attempt = session.get(AIInvocation, attempt_id)
            attempt.status = "UNKNOWN"
            attempt.details = {
                "code": exc.code if isinstance(exc, ProviderError) else type(exc).__name__,
                "seconds": round(time.monotonic() - started, 2),
            }
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError("PROVIDER_FAILED", "Шаг AI не завершён; технический статус сохранён.") from None


def structured(project_id, request_id, instructions, content, schema, json_schema=None):
    json_schema = json_schema or schema.model_json_schema()
    prompt = instructions + "\nВерни только JSON по схеме:\n" + json.dumps(json_schema, ensure_ascii=False)
    messages = list(content)
    for attempt in range(2):
        result = invoke(project_id, request_id, prompt, messages, schema=json_schema)
        try:
            raw = result["text"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            return schema.model_validate_json(raw).model_dump()
        except ValueError as exc:
            if attempt:
                raise ProviderError("INVALID_OUTPUT", "Ответ дважды не прошёл проверку формата.") from None
            messages = [
                *content,
                {"role": "assistant", "content": result["text"]},
                {
                    "role": "user",
                    "content": "Исправь данные JSON, не возвращай саму схему. Ошибки проверки: "
                    + str(exc)[:3000],
                },
            ]
