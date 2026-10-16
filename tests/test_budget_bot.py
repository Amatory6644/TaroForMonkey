"""Offline tests; no actual Telegram or G-pals requests."""

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from src.budget_bot import (
    BudgetBot,
    IntegrationError,
    chunks,
    command_period,
    pair_chat,
    request_json,
    validate_local_url,
)


class FakeReports:
    def get(self, period):
        return "Осталось 6 800 рублей"


class FakeTelegram:
    def __init__(self, updates=None):
        self.updates, self.sent = updates or [], []

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        if method == "sendMessage":
            self.sent.append(payload)
        return {"ok": True, "result": {}}


def message(text="/budget", chat_id=123, kind="private", update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": kind},
            "from": {"id": chat_id},
        },
    }


class BudgetTests(unittest.TestCase):
    def test_proxy_applies_only_to_telegram(self):
        proxies = []

        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(b'{"ok":true}')

        def build(*handlers):
            proxies.append(handlers[0].proxies)
            return Opener()

        with (
            mock.patch(
                "urllib.request.getproxies",
                return_value={"https": "http://127.0.0.1:9999"},
            ),
            mock.patch("urllib.request.build_opener", side_effect=build),
        ):
            request_json("https://api.telegram.org/bot123:TEST/getMe", {})
            request_json("http://127.0.0.1:8000/api/session", {})
        self.assertEqual(proxies, [{"https": "http://127.0.0.1:9999"}, {}])

    def test_network_error_identifies_service_without_token(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(TimeoutError())
        with (
            mock.patch("urllib.request.build_opener", return_value=opener),
            self.assertRaises(IntegrationError) as caught,
        ):
            request_json("https://api.telegram.org/bot123:SECRET/getMe", {})
        self.assertIn("Telegram", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))

    def test_commands(self):
        for command, expected in [
            ("/budget", "current"),
            ("/today", "today"),
            ("/week", "week"),
            ("/report 2026-10", "2026-10"),
            ("/report 2026-99", "usage"),
            ("/budget@OtherBot", None),
            ("запиши расход 100", None),
        ]:
            with self.subTest(command=command):
                self.assertEqual(command_period(command, "TarotBot"), expected)

    def test_only_private_authorized_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            bot = BudgetBot(
                "123:TEST_TOKEN",
                123,
                FakeReports(),
                Path(directory) / "state.json",
                telegram,
            )
            self.assertFalse(bot.process(message(chat_id=456)))
            self.assertFalse(bot.process(message(kind="group")))
            self.assertEqual(telegram.sent, [])
            self.assertTrue(bot.process(message()))
            self.assertEqual(telegram.sent[0]["chat_id"], 123)

    def test_offset_prevents_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram([message(update_id=10)])
            bot = BudgetBot(
                "123:TEST_TOKEN",
                123,
                FakeReports(),
                Path(directory) / "state.json",
                telegram,
            )
            bot.poll_once()
            bot.poll_once()
            self.assertEqual(len(telegram.sent), 1)
            self.assertEqual(bot.offset(), 11)
            self.assertNotIn("TEST_TOKEN", bot.state_path.read_text())

    def test_pair_by_code_preserves_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("GPALS_URL=http://127.0.0.1:8000\n", encoding="utf-8")
            telegram = FakeTelegram(
                [
                    message("/connect wrong", update_id=1),
                    message("/connect CODE", update_id=2),
                ]
            )
            bot = BudgetBot(
                "123:TEST_TOKEN",
                1,
                FakeReports(),
                Path(directory) / "state.json",
                telegram,
            )
            ident, offset = pair_chat(bot, env, code="CODE")
            self.assertEqual((ident, offset), (123, 3))
            self.assertIn("GPALS_TELEGRAM_CHAT_ID=123", env.read_text())
            self.assertIn("GPALS_URL=http://127.0.0.1:8000", env.read_text())

    def test_unicode_chunks_and_loopback(self):
        value = "💰" * 4000
        parts = list(chunks(value))
        self.assertEqual("".join(parts), value)
        self.assertTrue(all(len(x.encode("utf-16-le")) // 2 <= 3500 for x in parts))
        with self.assertRaises(IntegrationError):
            validate_local_url("https://example.org")


if __name__ == "__main__":
    unittest.main()
