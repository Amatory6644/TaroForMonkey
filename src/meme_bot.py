"""Hourly Telegram meme bot with Russian translation."""

from __future__ import annotations

import html
import io
import json
import os
import re
import sys
from collections import OrderedDict
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
    "TaroForMonkeyMemeBot/1.3 "
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
                    "title": str(item.get("title") or "Мем"),
                    "subreddit": str(item.get("subreddit") or subreddit),
                    "score": int(item.get("ups") or 0),
                    "permalink": post_link,
                    "media_url": url,
                }
            )

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


def clean_ocr_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"[^A-Za-z0-9%$€£'\".,!?():;+\-/&@# ]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" |")
    return line


def ocr_candidate(image: Image.Image, psm: int) -> tuple[str, float]:
    data = pytesseract.image_to_data(
        image,
        lang="eng",
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )

    lines: OrderedDict[tuple[int, int, int], list[str]] = OrderedDict()
    confidences: list[float] = []

    count = len(data.get("text", []))
    for i in range(count):
        word = clean_ocr_line(str(data["text"][i] or ""))
        if not word or not re.search(r"[A-Za-z0-9]", word):
            continue

        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            confidence = -1.0

        if confidence < 28:
            continue

        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        lines.setdefault(key, []).append(word)
        confidences.append(confidence)

    text_lines = []
    for words in lines.values():
        line = clean_ocr_line(" ".join(words))
        alpha_tokens = re.findall(r"[A-Za-z]+", line)
        alpha_chars = sum(len(token) for token in alpha_tokens)

        # Drop OCR debris such as "ay", "B", "im", "YE".
        if alpha_chars < 4:
            continue
        if len(alpha_tokens) == 1 and len(alpha_tokens[0]) < 4:
            continue

        text_lines.append(line)

    text = "\n".join(text_lines).strip()
    if not text:
        return "", 0.0

    words = re.findall(r"[A-Za-z0-9%]+", text)
    alpha_chars = len(re.findall(r"[A-Za-z]", text))
    weird_chars = len(re.findall(r"[^A-Za-z0-9\s%$€£'\".,!?():;+\-/&@#]", text))
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    score = avg_conf
    score += min(len(words), 30) * 1.3
    score += min(alpha_chars / 10, 12)
    score -= weird_chars * 5
    return text[:1800], score


def extract_text_from_image(media: bytes) -> str:
    try:
        with Image.open(io.BytesIO(media)) as image:
            image.seek(0)
            base = image.convert("RGB")

            # Upscaling helps meme captions with outlined fonts and compressed JPEGs.
            scale = 2 if max(base.size) < 2200 else 1
            if scale > 1:
                base = base.resize(
                    (base.width * scale, base.height * scale),
                    Image.Resampling.LANCZOS,
                )

            gray = ImageOps.autocontrast(ImageOps.grayscale(base))
            threshold = gray.point(lambda p: 255 if p >= 155 else 0)

            variants = (base, gray, threshold)
            attempts: list[tuple[str, float]] = []

            for variant in variants:
                for psm in (6, 11):
                    text, score = ocr_candidate(variant, psm)
                    if text:
                        attempts.append((text, score))

            if not attempts:
                return ""

            best_text, best_score = max(attempts, key=lambda item: item[1])
            print(f"OCR score={best_score:.1f}: {best_text[:700]}")
            return best_text
    except Exception as exc:
        print(f"OCR не смог прочитать мем: {exc}", file=sys.stderr)
        return ""


def translate_to_russian(text: str) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if not re.search(r"[A-Za-z]{2,}", text):
        return text

    # A single request for the whole meme gives a much more natural translation
    # than translating OCR fragments separately.
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "ru",
                "dt": "t",
                "q": text[:1200],
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(
            str(chunk[0])
            for chunk in payload[0]
            if isinstance(chunk, list) and chunk and chunk[0]
        ).strip()
        if translated:
            return translated
    except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
        print(f"Google Translate недоступен: {exc}", file=sys.stderr)

    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:480], "langpair": "en|ru"},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        translated = html.unescape(
            str(payload.get("responseData", {}).get("translatedText") or "")
        ).strip()
        if translated and "MYMEMORY WARNING" not in translated.upper():
            return translated
    except (requests.RequestException, ValueError) as exc:
        print(f"MyMemory перевод недоступен: {exc}", file=sys.stderr)

    return ""


def build_caption(item: dict, media: bytes) -> str:
    image_text = extract_text_from_image(media)

    # Prefer the actual joke from the picture. Use the Reddit title only as fallback.
    source_text = image_text if len(re.findall(r"[A-Za-z]{2,}", image_text)) >= 3 else item["title"]
    translated = translate_to_russian(source_text)

    if not translated:
        translated = "Не удалось автоматически перевести текст этого мема."

    caption = f"{translated}\n\n🔗 {item['permalink']}"
    if len(caption) > 1024:
        link = f"\n\n🔗 {item['permalink']}"
        allowed = max(100, 1024 - len(link) - 3)
        translated = translated[:allowed].rstrip() + "..."
        caption = f"{translated}{link}"
    return caption


def send_to_telegram(
    token: str,
    chat_id: str,
    media: bytes,
    content_type: str,
    filename: str,
    caption: str,
) -> None:
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
                    media,
                    content_type,
                    filename,
                    caption,
                )

            history.append(item["id"])
            save_history(history)
            print(f"Отправлен мем id={item['id']} в {len(chat_ids)} чата.")
            return 0
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            print(f"Пропускаю {item['id']}: {exc}", file=sys.stderr)

    print(f"Не удалось отправить ни одного мема: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
