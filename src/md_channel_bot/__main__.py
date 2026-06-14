from __future__ import annotations

import logging
import sys

from .bot import MarkdownChannelBot
from .config import ConfigError, load_settings
from .store import PendingStore
from .telegram_api import TelegramClient


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings()
    except ConfigError as exc:
        logging.error("Invalid configuration: %s", exc)
        return 2

    client = TelegramClient(
        token=settings.bot_token,
        api_base=settings.api_base,
        request_timeout_seconds=settings.poll_timeout_seconds + 40,
    )
    store = PendingStore(settings.pending_store_path, ttl_seconds=settings.pending_ttl_seconds)
    bot = MarkdownChannelBot(settings, client, store)
    bot.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
