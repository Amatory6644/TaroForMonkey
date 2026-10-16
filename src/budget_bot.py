"""Read-only Telegram reports from a local G-pals instance. No third-party dependencies."""

import argparse
import contextlib
import ipaddress
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class IntegrationError(Exception):
    pass


class StopPolling(IntegrationError):
    pass


class SessionExpired(IntegrationError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, payload, timeout=30, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        url,
        json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read(2_000_000))
    except urllib.error.HTTPError as exc:
        if exc.code == 409 and url.startswith("https://api.telegram.org/"):
            raise StopPolling(
                "Другой процесс уже получает команды бота. Остановите его перед запуском отчётов."
            ) from None
        if exc.code == 401:
            if url.startswith("https://api.telegram.org/"):
                raise StopPolling("Авторизация отклонена. Проверьте локальную настройку токена.") from None
            raise SessionExpired("Сессия G-pals истекла.") from None
        raise IntegrationError("Сервис отклонил запрос. Проверьте настройки и повторите команду.") from None
    except (OSError, ValueError):
        # Never include the exception/URL: Telegram's URL contains the bot token.
        raise IntegrationError("Сервис недоступен. Проверьте интернет и запуск G-pals.") from None


def validate_local_url(value):
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme != "http" or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError
        if parts.hostname != "localhost" and not ipaddress.ip_address(parts.hostname).is_loopback:
            raise ValueError
        if parts.path not in ("", "/"):
            raise ValueError
    except (ValueError, TypeError):
        raise IntegrationError(
            "GPALS_URL должен быть локальным адресом, например http://127.0.0.1:8000."
        ) from None
    return value.rstrip("/")


class LocalReports:
    def __init__(self, base_url="http://127.0.0.1:8000", transport=request_json):
        self.base_url = validate_local_url(base_url)
        self.transport = transport
        self.headers = None

    def get(self, period):
        # Every request is a local HTTP read; this script cannot modify financial data.
        # A dedicated local session avoids relying on cookies from a browser tab.
        path = self.base_url + "/api/reports/summary?" + urllib.parse.urlencode({"period": period})
        for attempt in range(2):
            if self.headers is None:
                session = self.transport(self.base_url + "/api/session", {}, 10)
                self.headers = {"X-Session": session["session_id"], "X-CSRF": session["csrf_token"]}
            try:
                result = self.transport(path, None, 15, headers=self.headers)
                if not isinstance(result.get("text"), str):
                    raise IntegrationError("G-pals вернул некорректный отчёт.")
                return result["text"]
            except SessionExpired:
                self.headers = None
        raise IntegrationError("Перезапустите G-pals и повторите запрос.")


def chunks(text, size=3500):
    """Split below Telegram's 4096 UTF-16-unit limit, including emoji in category names."""
    chunk, units = "", 0
    for char in text:
        width = len(char.encode("utf-16-le")) // 2
        if units + width > size:
            yield chunk
            chunk, units = "", 0
        chunk += char
        units += width
    if chunk:
        yield chunk


HELP = (
    "G-pals — отчёты по запросу:\n"
    "/budget — текущий месяц и остатки лимитов\n"
    "/today — расходы сегодня\n/week — расходы за текущую неделю\n"
    "/report 2026-10 — выбранный месяц\n"
    "Для дня и недели остатки лимитов показываются за текущий месяц.\n"
    "Запись расходов через этого обработчика не выполняется."
)


def command_period(message, username=""):
    words = message.strip().split()
    if not words:
        return None
    command, _, mention = words[0].casefold().partition("@")
    if mention and mention != username.casefold():
        return None
    if command in ("/start", "/help"):
        return "help"
    if command in ("/budget", "/month") and len(words) == 1:
        return "current"
    if command in ("/today", "/week") and len(words) == 1:
        return command[1:]
    if command == "/report":
        if len(words) == 1:
            return "current"
        if (
            len(words) == 2
            and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", words[1])
            and not words[1].startswith("0000")
        ):
            return words[1]
        return "usage"
    return None


class BudgetBot:
    def __init__(self, token, chat_id, reports, state_path, transport=request_json):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise IntegrationError("Укажите корректный TELEGRAM_BOT_TOKEN в локальном .env.")
        if not re.fullmatch(r"[1-9]\d*", str(chat_id)):
            raise IntegrationError(
                "GPALS_TELEGRAM_CHAT_ID должен быть ID вашего личного чата, положительным числом."
            )
        self.token, self.chat_id, self.reports = token, int(chat_id), reports
        self.state_path, self.transport = Path(state_path), transport
        self.username = ""
        # Offset is scoped to this token/chat without persisting the token itself.
        import hashlib

        self.scope = hashlib.sha256((token + ":" + str(chat_id)).encode()).hexdigest()

    def api(self, method, payload, timeout=30):
        value = self.transport(f"https://api.telegram.org/bot{self.token}/{method}", payload, timeout)
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise IntegrationError("Telegram отклонил запрос. Проверьте настройку бота.")
        return value.get("result")

    def offset(self):
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            return int(saved["offset"]) if saved["scope"] == self.scope else 0
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def save_offset(self, offset):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"offset": offset, "scope": self.scope}), encoding="utf-8")
        temporary.replace(self.state_path)

    def prepare(self):
        self.username = self.api("getMe", {})["username"]
        if self.api("getWebhookInfo", {}).get("url"):
            raise StopPolling(
                "У бота уже настроен webhook. Отчёты нужно подключить к его обработчику; webhook не изменён."
            )

    def process(self, update):
        message = update.get("message", {})
        chat = message.get("chat", {})
        sender = message.get("from", {})
        if (
            chat.get("type") != "private"
            or chat.get("id") != self.chat_id
            or sender.get("id") != self.chat_id
        ):
            return False
        value = message.get("text")
        if not isinstance(value, str):
            return False
        period = command_period(value, self.username)
        if period is None:
            return False
        if period in ("help", "usage"):
            text = HELP
        else:
            try:
                text = self.reports.get(period)
            except IntegrationError:
                text = "Не удалось получить сводку. Проверьте, что G-pals включён на домашнем ПК, и повторите команду."
        for part in chunks(text):
            self.api("sendMessage", {"chat_id": self.chat_id, "text": part})
        return True

    def poll_once(self):
        offset = self.offset()
        updates = self.api(
            "getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, 30
        )
        if not isinstance(updates, list):
            raise IntegrationError("Telegram вернул некорректный список команд.")
        for update in updates:
            ident = update.get("update_id")
            if type(ident) is not int or ident < offset:
                continue
            self.process(update)
            offset = ident + 1
            self.save_offset(offset)


@contextlib.contextmanager
def single_instance(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise StopPolling("Этот обработчик уже запущен.") from None
    try:
        yield
    finally:
        handle.close()


def load_local_env(path):
    allowed = {"TELEGRAM_BOT_TOKEN", "GPALS_TELEGRAM_CHAT_ID", "GPALS_URL"}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in allowed:
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def save_chat_id(path, ident):
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() != "GPALS_TELEGRAM_CHAT_ID"]
    lines.append(f"GPALS_TELEGRAM_CHAT_ID={ident}")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def pair_chat(bot, env_path, code=None, clock=time.monotonic):
    code = code or secrets.token_urlsafe(9)
    expected = "/connect " + code
    print("В течение 2 минут отправьте СВОЕМУ боту в ЛИЧНОМ чате:\n" + expected, flush=True)
    until, offset = clock() + 120, 0
    while clock() < until:
        updates = bot.api("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, 30)
        for update in updates:
            ident = update.get("update_id")
            if type(ident) is not int or ident < offset:
                continue
            offset = ident + 1
            message = update.get("message", {})
            chat, sender = message.get("chat", {}), message.get("from", {})
            if (
                chat.get("type") == "private"
                and type(chat.get("id")) is int
                and chat["id"] > 0
                and sender.get("id") == chat["id"]
                and isinstance(message.get("text"), str)
                and secrets.compare_digest(message["text"].strip().encode(), expected.encode())
            ):
                save_chat_id(env_path, chat["id"])
                return chat["id"], offset
    raise StopPolling("Время привязки истекло. Запустите ещё раз с --pair.")


def main(argv=None):
    # PowerShell 5 pipelines may default to cp1251, which cannot encode the ruble sign.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Read-only G-pals reports using the existing Telegram bot")
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument(
        "--state", type=Path, default=Path(__file__).resolve().parents[1] / ".gpals-local/offset.json"
    )
    parser.add_argument("--preview", action="store_true", help="Print the local report; no Telegram requests")
    parser.add_argument("--pair", action="store_true", help="Bind a private chat by a random one-time code")
    args = parser.parse_args(argv)
    load_local_env(args.env)
    try:
        reports = LocalReports(os.environ.get("GPALS_URL", "http://127.0.0.1:8000"))
        if args.preview:
            print(reports.get("current"))
            return 0
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("GPALS_TELEGRAM_CHAT_ID", "")
        bot = BudgetBot(token, "1" if args.pair else chat_id, reports, args.state)
        with single_instance(args.state.with_suffix(".lock")):
            bot.prepare()
            if args.pair:
                chat_id, offset = pair_chat(bot, args.env)
                username = bot.username
                bot = BudgetBot(token, chat_id, reports, args.state)
                bot.username = username
                bot.save_offset(offset)
                bot.api(
                    "sendMessage",
                    {
                        "chat_id": bot.chat_id,
                        "text": "Личный чат привязан к G-pals. Для сводки отправьте /budget.",
                    },
                )
            print(
                "Отчёты G-pals включены. Команда /budget в разрешённом личном чате. Ctrl+C — остановить.",
                flush=True,
            )
            while True:
                try:
                    bot.poll_once()
                except StopPolling:
                    raise
                except IntegrationError as exc:
                    print(str(exc), file=sys.stderr, flush=True)
                    time.sleep(5)
    except KeyboardInterrupt:
        return 0
    except IntegrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
