"""Respond to /chatid commands using Telegram Bot API getUpdates.\n\nTriggered manually after first group test.\n"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "telegram_update_state.json"


def load_offset() -> int:
    if not STATE_PATH.exists():
        return 0
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return int(payload.get("last_update_id", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def save_offset(update_id: int) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"last_update_id": update_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def send_message(token: str, chat_id: int, text: str, reply_to: int | None = None) -> None:
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_to is not None:
        payload["reply_parameters"] = {"message_id": reply_to}

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram отклонил ответ: {data}")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Не задан TELEGRAM_BOT_TOKEN.", file=sys.stderr)
        return 1

    last_update_id = load_offset()
    params = {
        "timeout": 0,
        "limit": 100,
    }
    if last_update_id:
        params["offset"] = last_update_id + 1

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Ошибка getUpdates: {exc}", file=sys.stderr)
        return 1

    if not payload.get("ok"):
        print(f"Telegram отклонил getUpdates: {payload}", file=sys.stderr)
        return 1

    updates = payload.get("result", [])
    max_update_id = last_update_id

    for update in updates:
        try:
            update_id = int(update.get("update_id", 0))
        except (TypeError, ValueError):
            continue

        if update_id > max_update_id:
            max_update_id = update_id

        message = update.get("message") or update.get("channel_post")
        if not isinstance(message, dict):
            continue

        text = str(message.get("text") or "").strip()
        command = text.split()[0].lower() if text else ""
        command = command.split("@")[0]

        if command != "/chatid":
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        chat_type = str(chat.get("type") or "unknown")
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "без названия"

        answer = (
            "🆔 ID этого Telegram-чата:\n"
            f"<code>{chat_id}</code>\n\n"
            f"Тип: {chat_type}\n"
            f"Название: {title}"
        )

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": answer,
                    "parse_mode": "HTML",
                    "reply_parameters": {"message_id": message.get("message_id")},
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError(str(result))
            print(f"Ответил на /chatid в чате {chat_id}.")
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            print(f"Не удалось ответить в чат {chat_id}: {exc}", file=sys.stderr)

    if max_update_id > last_update_id:
        save_offset(max_update_id)
        print(f"Сохранён last_update_id={max_update_id}.")
    else:
        print("Новых Telegram update нет.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
