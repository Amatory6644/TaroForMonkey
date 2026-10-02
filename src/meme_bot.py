"""Hourly Telegram meme bot: pick the most popular fresh Reddit meme."""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

SUBREDDITS = ("memes", "dankmemes", "funny")
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "meme_history.json"
MAX_HISTORY = 200
MAX_MEDIA_BYTES = 9_000_000

USER_AGENT = (
    "TaroForMonkeyMemeBot/1.0 "
    "(GitHub Actions; hourly Telegram meme digest)"
)


def load_history() -> list[str]:
    if not HISTORY_PATH.exists():
        return []
    try:
        payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sent = payload.get("sent", [])
    return [str(item) for item in sent if item]


def save_history(sent: list[str]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps({"sent": sent[-MAX_HISTORY:]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def image_url(post: dict) -> str | None:
    raw_url = post.get("url_overridden_by_dest") or post.get("url") or ""
    raw_url = html.unescape(raw_url)
    ext = Path(urlparse(raw_url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif"}:
        return raw_url

    try:
        preview_url = post["preview"]["images"][0]["source"]["url"]
    except (KeyError, IndexError, TypeError):
        return None

    preview_url = html.unescape(preview_url)
    ext = Path(urlparse(preview_url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif"}:
        return preview_url
    return None


def fetch_subreddit(subreddit: str) -> list[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {"t": "day", "limit": 50, "raw_json": 1}
    endpoints = (
        f"https://www.reddit.com/r/{subreddit}/top.json",
        f"https://old.reddit.com/r/{subreddit}/top.json",
    )

    last_error: Exception | None = None
    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            payload = response.json()
            return [
                child.get("data", {})
                for child in payload.get("data", {}).get("children", [])
                if child.get("kind") == "t3"
            ]
        except (requests.RequestException, ValueError) as exc:
            last_error = exc

    print(f"Не удалось получить r/{subreddit}: {last_error}", file=sys.stderr)
    return []


def candidates(sent: set[str]) -> list[dict]:
    result: list[dict] = []

    for subreddit in SUBREDDITS:
        for post in fetch_subreddit(subreddit):
            post_id = str(post.get("id") or "")
            if not post_id or post_id in sent:
                continue
            if post.get("over_18") or post.get("spoiler") or post.get("stickied"):
                continue
            if post.get("is_video"):
                continue

            media_url = image_url(post)
            if not media_url:
                continue

            result.append(
                {
                    "id": post_id,
                    "title": str(post.get("title") or "Мем часа"),
                    "subreddit": str(post.get("subreddit") or subreddit),
                    "score": int(post.get("score") or 0),
                    "comments": int(post.get("num_comments") or 0),
                    "permalink": "https://www.reddit.com" + str(post.get("permalink") or ""),
                    "media_url": media_url,
                }
            )

    result.sort(key=lambda item: (item["score"], item["comments"]), reverse=True)
    return result


def download_media(url: str) -> tuple[bytes, str, str]:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        stream=True,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0]
    suffix = Path(urlparse(url).path).suffix.lower()
    filename = "meme.gif" if suffix == ".gif" else "meme.jpg"

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > MAX_MEDIA_BYTES:
            raise ValueError("Файл мема слишком большой для безопасной отправки")
        chunks.append(chunk)

    if not chunks:
        raise ValueError("Reddit вернул пустой файл")

    return b"".join(chunks), content_type, filename


def send_to_telegram(token: str, chat_id: str, item: dict, media: bytes, content_type: str, filename: str) -> None:
    title = item["title"].strip()
    if len(title) > 600:
        title = title[:597] + "..."

    caption = (
        f"😂 Мем часа\n\n"
        f"{title}\n\n"
        f"👍 {item['score']:,}  •  💬 {item['comments']:,}  •  r/{item['subreddit']}\n"
        f"🔗 {item['permalink']}"
    ).replace(",", " ")

    is_gif = filename.endswith(".gif") or content_type == "image/gif"
    method = "sendAnimation" if is_gif else "sendPhoto"
    field = "animation" if is_gif else "photo"
    api_url = f"https://api.telegram.org/bot{token}/{method}"

    response = requests.post(
        api_url,
        data={"chat_id": chat_id, "caption": caption},
        files={field: (filename, media, content_type)},
        timeout=45,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Telegram вернул некорректный ответ") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram отклонил отправку: {payload}")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Не заданы TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.", file=sys.stderr)
        return 1

    history = load_history()
    available = candidates(set(history))
    if not available:
        print("Не найдено подходящих свежих мемов.", file=sys.stderr)
        return 1

    last_error: Exception | None = None
    for item in available:
        try:
            media, content_type, filename = download_media(item["media_url"])
            send_to_telegram(token, chat_id, item, media, content_type, filename)
            history.append(item["id"])
            save_history(history)
            print(
                f"Отправлен мем r/{item['subreddit']} "
                f"(score={item['score']}, id={item['id']})."
            )
            return 0
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            print(f"Пропускаю {item['id']}: {exc}", file=sys.stderr)

    print(f"Не удалось отправить ни одного мема: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
