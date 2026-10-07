import base64
import hashlib
import json
import logging
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from telok import budget, storage
from telok.domain import DomainError
from telok.settings import settings

# Bot endpoints include a credential in their URL; do not emit HTTPX request INFO logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


def text(
    project_id: str,
    request_id: str,
    role: str,
    context: dict,
    schema: type[BaseModel],
    images: list[str] | None = None,
) -> dict:
    cfg = settings()
    preflight(project_id, request_id)
    if cfg.reasoning_provider != "openai_api":
        from telok.ai.router import structured

        prompt = (Path(__file__).parent / "prompts" / f"{role}.md").read_text(encoding="utf-8")
        content = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]
        for asset_id in images or []:
            data, meta = storage.read(asset_id, project_id)
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{meta['mime']};base64,{base64.b64encode(data).decode()}",
                }
            )
        return structured(project_id, request_id, prompt, [{"role": "user", "content": content}], schema)
    if not cfg.provider_ready():
        raise ValueError("Текстовый API не настроен: нужны ключ, подтверждённый reserve и денежные лимиты.")
    prompt = (Path(__file__).parent / "prompts" / f"{role}.md").read_text(encoding="utf-8")
    content = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]
    for asset_id in images or []:
        data, meta = storage.read(asset_id, project_id)
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{meta['mime']};base64,{base64.b64encode(data).decode()}",
            }
        )
    attempt_id = budget.reserve(
        project_id, request_id, f"openai:{cfg.text_model}:{role}", cfg.text_call_reserve_usd
    )
    try:
        client = OpenAI(api_key=cfg.openai_api_key.get_secret_value(), max_retries=0, timeout=90)
        response = client.responses.parse(
            model=cfg.text_model,
            instructions=prompt,
            input=[{"role": "user", "content": content}],
            text_format=schema,
            max_output_tokens=3000,
            store=False,
        )
        if response.status != "completed" or response.output_parsed is None:
            raise ValueError("Модель отказала или вернула незавершённый структурированный ответ.")
        result = response.output_parsed.model_dump()
        usage = response.usage.model_dump() if response.usage else {}
        usage.update(
            role=role,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
            input_hash=hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
        )
        budget.finish(attempt_id, usage, response.id)
        return result
    except Exception as exc:
        budget.uncertain(attempt_id)
        raise DomainError(
            f"Provider call не завершён ({type(exc).__name__}); attempt {attempt_id}. Резерв удерживается."
        ) from None


def image(project_id: str, request_id: str, spec: dict) -> str:
    cfg = settings()
    preflight(project_id, request_id)
    if not cfg.provider_ready(image=True):
        raise ValueError("Image API не настроен: нужны ключ, image reserve и денежные лимиты.")
    reference_ids = spec.get("required_references", [])
    opened = []
    request = {
        "model": cfg.image_model,
        "prompt": spec["prompt"] + "\nAvoid: " + ", ".join(spec.get("exclusions", [])),
        "size": "1024x1024",
        "quality": "low",
        "n": 1,
    }
    # Validate references before reserving or contacting a paid provider.
    for ref in reference_ids:
        data, meta = storage.read(ref, project_id)
        if meta["mime"] not in {"image/png", "image/jpeg"}:
            raise ValueError("Image references must be PNG/JPEG.")
    attempt_id = budget.reserve(
        project_id, request_id, f"openai:{cfg.image_model}:image", cfg.image_call_reserve_usd
    )
    try:
        client = OpenAI(api_key=cfg.openai_api_key.get_secret_value(), max_retries=0, timeout=180)
        if reference_ids:
            import io

            for ref in reference_ids:
                data, meta = storage.read(ref, project_id)
                file = io.BytesIO(data)
                file.name = "reference.png" if meta["mime"] == "image/png" else "reference.jpg"
                opened.append(file)
            response = client.images.edit(image=opened, **request)
        else:
            response = client.images.generate(**request)
        if not response.data or not response.data[0].b64_json:
            raise ValueError("Provider не вернул image bytes.")
        data = base64.b64decode(response.data[0].b64_json, validate=True)
        asset = storage.put(
            project_id,
            data,
            "image/png",
            {
                "provider": cfg.image_model,
                "effective_parameters": request,
                "required_references": reference_ids,
            },
        )
        budget.finish(attempt_id, response.usage.model_dump() if response.usage else {})
        return asset
    except Exception as exc:
        budget.uncertain(attempt_id)
        raise DomainError(
            f"Provider call не завершён ({type(exc).__name__}); attempt {attempt_id}. Резерв удерживается."
        ) from None
    finally:
        for file in opened:
            file.close()


def preflight(project_id: str, request_id: str):
    from telok.db import transaction
    from telok.domain import DomainError
    from telok.models import Project, Work

    with transaction() as session:
        project = session.get(Project, project_id)
        work = session.get(Work, request_id)
        if not project or project.paused:
            raise DomainError("Проект на паузе.")
        if work and work.status == "CANCELLED":
            raise DomainError("Запрос отменён.")
