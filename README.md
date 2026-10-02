# TaroForMonkey

Telegram-бот, который каждые 10 минут выбирает случайную карту полной колоды Rider–Waite и отправляет её в группу через GitHub Actions.

## Возможности

- полная колода из 78 карт;
- случайный выбор карты и положения (прямое/перевёрнутое, 50/50);
- русскоязычное сообщение со значением и советом;
- ручной запуск через `workflow_dispatch`;
- простая архитектура: колоду и логику выбора можно менять отдельно от отправки в Telegram.

## Настройка

1. Создайте Telegram-бота через [@BotFather](https://t.me/BotFather) и добавьте его в группу.
2. Узнайте ID группы и откройте в GitHub репозитории `Settings → Secrets and variables → Actions`.
3. Добавьте Actions secrets:
   - `TELEGRAM_BOT_TOKEN` — токен бота;
   - `TELEGRAM_CHAT_ID` — ID группы (обычно отрицательное число).
4. Включите Actions и при необходимости запустите workflow вручную на вкладке `Actions`.

## Локальный запуск

Требуется Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python src/main.py
```

При отсутствии секретов программа завершится с понятной ошибкой и не выводит токен.

## Расширение

Подготовка карты и форматирование сообщения находятся в `src/tarot.py`, а интеграция с Telegram — в `src/main.py`. Позже трактовку можно вынести в отдельный провайдер и подключить AI без изменения расписания или Telegram-слоя. Сейчас OpenAI API не используется.
