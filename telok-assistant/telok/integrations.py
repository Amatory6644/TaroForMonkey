import re

import httpx

from telok.ai import credentials
from telok.domain import DomainError
from telok.settings import settings


def config():
    return credentials.read().get("telegram", {})


def token():
    return config().get("token") or settings().telegram_token.get_secret_value()


def owner():
    return int(config().get("owner_id") or settings().owner_id)


def allowed():
    return [owner()] if config().get("owner_id") else settings().allowed_telegram_ids


def configure(token_value, owner_id, old_poller_stopped=False):
    if not re.fullmatch(r"[0-9]{5,15}:[A-Za-z0-9_-]{25,60}", token_value) or int(owner_id) <= 0:
        raise DomainError("Проверьте токен и положительный личный Telegram user ID.")
    if not old_poller_stopped:
        raise DomainError("Сначала остановите старый обработчик getUpdates в TaroForMonkey.")
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.get(f"https://api.telegram.org/bot{token_value}/getMe")
            value = response.json()
            hook = client.get(f"https://api.telegram.org/bot{token_value}/getWebhookInfo").json()
        if not value.get("ok"):
            raise DomainError("Telegram не принял токен.")
        if hook.get("result", {}).get("url"):
            raise DomainError("У бота активен webhook. Сначала выполните явное переключение.")
    except httpx.HTTPError:
        raise DomainError("Не удалось проверить Telegram.") from None
    credentials.update(
        telegram={
            "token": token_value,
            "owner_id": int(owner_id),
            "username": value["result"].get("username", ""),
            "old_poller_stopped": True,
        }
    )
    return {"username": value["result"].get("username", ""), "configured": True}


def send_document(chat_id, filename, data):
    if not token():
        return
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.post(
                f"https://api.telegram.org/bot{token()}/sendDocument",
                data={"chat_id": chat_id},
                files={"document": (filename, data, "application/json")},
            )
        if not response.json().get("ok"):
            raise DomainError("Файл не доставлен в Telegram.")
    except httpx.HTTPError:
        raise DomainError("Доставка файла не подтверждена.") from None


def download_file(file_id):

    try:
        with httpx.Client(timeout=25, trust_env=False) as client:
            value = client.get(
                f"https://api.telegram.org/bot{token()}/getFile", params={"file_id": file_id}
            ).json()
            if not value.get("ok"):
                raise DomainError("Telegram не отдал файл.")
            path = value["result"]["file_path"]
            if ".." in path or path.startswith("/") or "://" in path:
                raise DomainError("Некорректный путь Telegram.")
            data = bytearray()
            with client.stream("GET", f"https://api.telegram.org/file/bot{token()}/{path}") as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 10_000_000:
                        raise DomainError("Документ превышает 10 MB.")
        return bytes(data)
    except httpx.HTTPError:
        raise DomainError("Документ не загружен.") from None


def download_document(file_id, filename):
    from telok.research import extract_document

    return extract_document(download_file(file_id), filename)


def download_reference(file_id, project_id):
    from telok import storage

    return storage.put(project_id, download_file(file_id), "image/jpeg", {"source": "telegram_photo"})


def send_video(chat_id, data):
    if not token():
        return None
    try:
        with httpx.Client(timeout=90, trust_env=False) as client:
            response = client.post(
                f"https://api.telegram.org/bot{token()}/sendVideo",
                data={
                    "chat_id": chat_id,
                    "caption": "Ролик собран. Проверьте факты, звук и визуальные детали перед публикацией.",
                },
                files={"video": ("telok.mp4", data, "video/mp4")},
            )
        value = response.json()
        if not value.get("ok"):
            raise DomainError("Telegram не подтвердил видео. Автоматический повтор выключен.")
        return value["result"]["message_id"]
    except httpx.HTTPError:
        raise DomainError("Доставка видео не подтверждена. Автоматический повтор выключен.") from None
