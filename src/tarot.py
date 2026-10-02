"""Tarot deck loading, drawing, and message formatting."""

import json
import random
from pathlib import Path
from typing import Any

DECK_PATH = Path(__file__).resolve().parent.parent / "data" / "cards.json"


def load_deck() -> list[dict[str, Any]]:
    with DECK_PATH.open(encoding="utf-8") as file:
        deck = json.load(file)
    if len(deck) != 78:
        raise ValueError(f"Ожидалась колода из 78 карт, получено: {len(deck)}")
    return deck


def draw_card() -> dict[str, Any]:
    card = random.choice(load_deck()).copy()
    card["orientation"] = random.choice(("прямое", "перевёрнутое"))
    meaning_key = "upright" if card["orientation"] == "прямое" else "reversed"
    card["meaning"] = card[meaning_key]
    return card


def format_message(card: dict[str, Any]) -> str:
    emoji = "✨" if card["orientation"] == "прямое" else "🔄"
    return (
        f"🔮 Карта дня\n\n"
        f"🃏 {card['name']}\n"
        f"{emoji} Положение: {card['orientation']}\n\n"
        f"💫 Значение: {card['meaning']}\n\n"
        f"💡 Совет: {card['advice']}"
    )
