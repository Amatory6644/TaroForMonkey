import argparse
import json

from sqlalchemy import text

from telok.db import engine
from telok.settings import settings


def main():
    parser = argparse.ArgumentParser(prog="telok")
    parser.add_argument("command", choices=["doctor", "seed", "worker", "bot"])
    args = parser.parse_args()
    if args.command == "worker":
        from telok.worker import main as worker

        worker()
    elif args.command == "bot":
        from telok.telegram import main as bot

        bot()
    elif args.command == "seed":
        from telok.demo import seed

        print(json.dumps({"project_id": seed(), "demo": True}, ensure_ascii=False))
    else:
        cfg = settings()
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            database = True
        except Exception:
            database = False
        print(
            json.dumps(
                {
                    "database": database,
                    "text_api": cfg.provider_ready(),
                    "image_api": cfg.provider_ready(True),
                    "telegram": bool(cfg.telegram_token.get_secret_value()),
                    "storage": cfg.storage,
                    "mode": cfg.env,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
