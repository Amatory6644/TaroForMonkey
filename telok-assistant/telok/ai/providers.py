import time
from urllib.parse import urlparse

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI

from telok.ai import chatgpt_auth, credentials
from telok.ai.contracts import AIResult, ProviderError


def mapped_error(code, status):
    if code == "subscription_sharing_usage_limit_exceeded":
        return ProviderError(
            "PLAN_LIMIT_REACHED", "Достигнут лимит ChatGPT Plan или приложения. Задача сохранена."
        )
    if status == 401:
        return ProviderError("REAUTH_REQUIRED", "Проверьте подключение ChatGPT.")
    if status == 403:
        return ProviderError("NOT_ELIGIBLE", "Аккаунту или приложению недоступна эта возможность.")
    if status == 400:
        return ProviderError("UNSUPPORTED", "Модель не поддерживает запрошенную возможность.")
    return ProviderError("TEMPORARILY_UNAVAILABLE", "AI-сервис временно недоступен.")


class ChatGPTPlanProvider:
    def models(self):
        token = chatgpt_auth.access_token(require_budget=False)
        try:
            with httpx.Client(timeout=20, trust_env=False) as client:
                response = client.get(
                    "https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + token}
                )
            if response.status_code != 200:
                raise mapped_error("", response.status_code)
            return [
                {"id": m["slug"], "name": m.get("display_name", m["slug"])}
                for m in response.json().get("models", [])
                if m.get("visibility") == "list"
            ]
        except httpx.HTTPError:
            raise ProviderError("NETWORK", "Каталог моделей недоступен.") from None

    def generate(self, instructions, content, *, model=None, search=False, check=lambda: None, schema=None):
        token = chatgpt_auth.access_token()
        if not model:
            model = credentials.read().get("plan_model")
        if not model:
            raise ProviderError("MODEL_REQUIRED", "Выберите модель в настройках ChatGPT.")
        output, sources, usage, response_id = "", [], {}, ""
        completed, started = False, time.monotonic()
        try:
            with OpenAI(
                api_key=token,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=60,
                http_client=httpx.Client(trust_env=False),
            ) as client:
                options = {"tools": [{"type": "web_search"}]} if search else {}
                with client.responses.create(
                    model=model, instructions=instructions, input=content, store=False, stream=True, **options
                ) as stream:
                    for event in stream:
                        check()
                        if time.monotonic() - started > 240:
                            raise ProviderError("TIMEOUT", "Превышено время запроса; результат не завершён.")
                        if event.type == "response.output_text.delta":
                            output += event.delta
                            if len(output) > 100_000:
                                raise ProviderError("OUTPUT_LIMIT", "Ответ превысил допустимый размер.")
                        elif event.type == "response.completed":
                            completed = event.response.status == "completed"
                            response_id = event.response.id
                            usage = event.response.usage.model_dump() if event.response.usage else {}
                            for item in event.response.output:
                                for part in getattr(item, "content", []):
                                    for annotation in getattr(part, "annotations", []):
                                        if getattr(annotation, "type", "") == "url_citation":
                                            sources.append({"url": annotation.url, "title": annotation.title})
                        elif event.type in {"response.failed", "response.incomplete", "error"}:
                            error = getattr(getattr(event, "response", None), "error", None)
                            raise mapped_error(getattr(error, "code", ""), 500)
            if not completed or not output.strip():
                raise ProviderError("INCOMPLETE", "Модель не вернула завершённый непустой ответ.")
            return AIResult(output, "ChatGPTPlan", model, usage, response_id, sources)
        except APIStatusError as exc:
            body = exc.body if isinstance(exc.body, dict) else {}
            err = body.get("error", body)
            raise mapped_error(
                err.get("code", "") if isinstance(err, dict) else "", exc.status_code
            ) from None
        except (APIConnectionError, httpx.HTTPError):
            raise ProviderError(
                "NETWORK", "Соединение с моделью прервано; расход может быть уже учтён."
            ) from None


class LocalQwenProvider:
    def _url(self):
        url = credentials.read().get("local_url", "http://127.0.0.1:8080/v1")
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path.rstrip("/") != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderError("LOCAL_CONFIG", "Локальный сервер должен слушать loopback.")
        return url.rstrip("/")

    def models(self):
        try:
            with httpx.Client(timeout=5, trust_env=False) as client:
                response = client.get(self._url() + "/models")
                response.raise_for_status()
            return [{"id": m["id"], "name": m["id"]} for m in response.json().get("data", [])]
        except httpx.HTTPError:
            raise ProviderError("LOCAL_UNAVAILABLE", "Локальная модель не запущена.") from None

    def generate(self, instructions, content, *, model=None, search=False, check=lambda: None, schema=None):
        if search:
            raise ProviderError("LOCAL_CAPABILITY", "Локальный маршрут не выполняет веб-поиск.")
        model = model or credentials.read().get("local_model")
        if not model:
            raise ProviderError("LOCAL_UNAVAILABLE", "Выберите локальную модель.")
        converted = []
        for item in content:
            value = item.get("content", "")
            if isinstance(value, list):
                if any(part.get("type") not in {"text", "input_text"} for part in value):
                    raise ProviderError(
                        "LOCAL_CAPABILITY", "Эта локальная модель не анализирует изображения."
                    )
                value = "\n".join(part.get("text", "") for part in value)
            converted.append({"role": item["role"], "content": value})
        messages = [{"role": "system", "content": instructions}, *converted]
        try:
            check()
            with httpx.Client(timeout=240, trust_env=False) as client:
                response = client.post(
                    self._url() + "/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 6000,
                        **({"response_format": {"type": "json_object", "schema": schema}} if schema else {}),
                    },
                )
                response.raise_for_status()
            check()
            value = response.json()
            choice = value["choices"][0]
            output = choice["message"].get("content", "")
            if choice.get("finish_reason") != "stop" or not output.strip():
                raise ProviderError("INCOMPLETE", "Локальный ответ не завершён.")
            return AIResult(output, "LocalQwen", model, value.get("usage", {}))
        except httpx.HTTPError:
            raise ProviderError("LOCAL_UNAVAILABLE", "Локальный сервер не ответил.") from None
