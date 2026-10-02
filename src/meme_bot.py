"""Hourly Telegram meme bot.

Fetches large fresh samples from Meme_Api (Reddit-backed), picks the item with
the highest upvote count, filters unsafe/repeated posts, and sends it to Telegram.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytesseract
import requests
from deep_translator import GoogleTranslator
from PIL import Image, ImageOps

SUBREDDITS = ("memes", "dankmemes", "funny")
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "meme_history.json"
MAX_HISTORY = 200
MAX_MEDIA_BYTES = 9_000_000
SAMPLE_SIZE = 50
EXTRA_CHAT_IDS = ("-5577576013",)

USER_AGENT = (
    "TaroForMonkeyMemeBot/1.1 "
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


def post_id_from_link(link: str) -> str:
    return Path(urlparse(link).path).name.strip()


def fetch_subreddit(subreddit: str) -> list[dict]:
    url = f"https://meme-api.com/gimme/{subreddit}/{SAMPLE_SIZE}"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Не удалось получить мемы r/{subreddit}: {exc}", file=sys.stderr)
        return []

    memes = payload.get("memes", [])
    return memes if isinstance(memes, list) else []


def media_url(item: dict) -> str | None:
    url = str(item.get("url") or "")
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif"}:
        return url

    previews = item.get("preview") or []
    for preview in reversed(previews):
        preview_url = str(preview or "")
        ext = Path(urlparse(preview_url).path).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif"}:
            return preview_url

    return None


def candidates(sent: set[str]) -> list[dict]:
    result: list[dict] = []

    for subreddit in SUBREDDITS:
        for item in fetch_subreddit(subreddit):
            if item.get("nsfw") or item.get("spoiler"):
                continue

            post_link = str(item.get("postLink") or "")
            post_id = post_id_from_link(post_link)
            if not post_id or post_id in sent:
                continue

            url = media_url(item)
            if not url:
                continue

            result.append(
                {
                    "id": post_id,
                    "title": str(item.get("title") or "Мем часа"),
                    "subreddit": str(item.get("subreddit") or subreddit),
                    "score": int(item.get("ups") or 0),
                    "permalink": post_link,
                    "media_url": url,
                }
            )

    # Approximation of "most popular now": highest upvote count in three
    # 50-item fresh samples returned by the Reddit-backed aggregator.
    result.sort(key=lambda item: item["score"], reverse=True)
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
            raise ValueError("Файл мема слишком большой для отправки")
        chunks.append(chunk)

    if not chunks:
        raise ValueError("Источник вернул пустой файл")

    return b"".join(chunks), content_type, filename



def clean_ocr_text(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\\s+", " ", raw_line).strip()
        if len(line) < 2:
            continue
        if not re.search(r"[A-Za-zА-Яа-я]", line):
            continue
        lines.append(line)
    return "\\n".join(lines)[:2400]


def extract_text_from_image(media: bytes) -> str:
    try:
        with Image.open(io.BytesIO(media)) as image:
            image.seek(0)
            frame = ImageOps.autocontrast(ImageOps.grayscale(image.convert("RGB")))
            text = pytesseract.image_to_string(frame, lang="eng", config="--psm 6")
            return clean_ocr_text(text)
    except Exception as exc:
        print(f"OCR не смог прочитать мем: {exc}", file=sys.stderr)
        return ""


def translate_to_russian(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if not re.search(r"[A-Za-z]{2,}", text):
        return text
    try:
        translated = GoogleTranslator(source="auto", target="ru").translate(text[:4500])
        return (translated or "").strip()
    except Exception as exc:
        print(f"Не удалось перевести текст: {exc}", file=sys.stderr)
        return ""


def build_caption(item: dict, media: bytes) -> str:
    image_text = extract_text_from_image(media)
    image_ru = translate_to_russian(image_text)
    title_ru = translate_to_russian(item["title"])

    parts = ["😂 Мем часа"]
    if image_ru:
        parts.append(f"🇷🇺 Перевод мема:\\n{image_ru}")
    elif title_ru:
        parts.append(f"🇷🇺 Перевод:\\n{title_ru}")
    else:
        parts.append(item["title"])

    if image_ru and title_ru:
        parts.append(f"📝 Заголовок: {title_ru}")

    parts.append(f"👍 {item['score']:,}  •  r/{item['subreddit']}".replace(",", " "))
    parts.append(f"🔗 {item['permalink']}")

    caption = "\\n\\n".join(parts)
    if len(caption) > 1024:
        caption = caption[:1021].rstrip() + "..."
    return caption


def send_to_telegram(
    token: str,
    chat_id: str,
    item: dict,
    media: bytes,
    content_type: str,
    filename: str,
) -> None:
    title = item["title"].strip()
    if len(title) > 650:
        title = title[:647] + "..."

    caption = (
        f"😂 Мем часа\n\n"
        f"{title}\n\n"
        f"👍 {item['score']:,}  •  r/{item['subreddit']}\n"
        f"🔗 {item['permalink']}"
    ).replace(",", " ")

    is_gif = filename.endswith(".gif") or content_type == "image/gif"
    method = "sendAnimation" if is_gif else "sendPhoto"
    field = "animation" if is_gif else "photo"

    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
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

    chat_ids = [chat_id, *EXTRA_CHAT_IDS]

    history = load_history()
    available = candidates(set(history))
    if not available:
        print("Не найдено подходящих свежих мемов.", file=sys.stderr)
        return 1

    last_error: Exception | None = None
    for item in available:
        try:
            media, content_type, filename = download_media(item["media_url"])
            caption = build_caption(item, media)
            for target_chat_id in chat_ids:
                send_to_telegram(
                    token,
                    target_chat_id,
                    item,
                    media,
                    content_type,
                    filename,
                    caption,
                )
            history.append(item["id"])
            save_history(history)
            print(
                f"Отправлен мем r/{item['subreddit']} "
                f"(ups={item['score']}, id={item['id']})."
            )
            return 0
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            print(f"Пропускаю {item['id']}: {exc}", file=sys.stderr)

    print(f"Не удалось отправить ни одного мема: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
