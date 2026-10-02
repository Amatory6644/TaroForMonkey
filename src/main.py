"""Entry point for the scheduled Tarot Telegram bot."""

import os
import sys

import requests

from tarot import draw_card, format_message


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    missing = [name for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)) if not value]
    if missing:
        print(f"Ошибка: не заданы переменные окружения: {', '.join(missing)}", file=sys.stderr)
        return 1

    card = draw_card()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": format_message(card)}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        print(f"Ошибка при отправке сообщения в Telegram: {exc}", file=sys.stderr)
        return 1
    except ValueError:
        print("Ошибка: Telegram вернул некорректный ответ.", file=sys.stderr)
        return 1

    if not result.get("ok"):
        print("Ошибка Telegram Bot API: запрос отклонён.", file=sys.stderr)
        return 1
    print(f"Отправлена карта: {card['name']} ({card['orientation']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
