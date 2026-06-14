from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_STORE_PATH = "/data/pending.json"
DEFAULT_POLL_TIMEOUT_SECONDS = 30
DEFAULT_PENDING_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_RICH_MESSAGE_CHARS = 32768
DEFAULT_MAX_DOCUMENT_BYTES = 256 * 1024


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: set[int]
    channel_id: int | str
    api_base: str = DEFAULT_API_BASE
    pending_store_path: str = DEFAULT_STORE_PATH
    poll_timeout_seconds: int = DEFAULT_POLL_TIMEOUT_SECONDS
    pending_ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS
    max_rich_message_chars: int = DEFAULT_MAX_RICH_MESSAGE_CHARS
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES
    delete_webhook_on_start: bool = True


class ConfigError(ValueError):
    pass


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ

    bot_token = _required(source, "TELEGRAM_BOT_TOKEN")
    allowed_user_ids = parse_int_set(_required(source, "TELEGRAM_ALLOWED_USER_IDS"), "TELEGRAM_ALLOWED_USER_IDS")
    channel_id = parse_chat_id(_required(source, "TELEGRAM_CHANNEL_ID"), "TELEGRAM_CHANNEL_ID")

    return Settings(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        channel_id=channel_id,
        api_base=source.get("TELEGRAM_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        pending_store_path=source.get("PENDING_STORE_PATH", DEFAULT_STORE_PATH),
        poll_timeout_seconds=parse_positive_int(
            source.get("TELEGRAM_POLL_TIMEOUT_SECONDS", str(DEFAULT_POLL_TIMEOUT_SECONDS)),
            "TELEGRAM_POLL_TIMEOUT_SECONDS",
        ),
        pending_ttl_seconds=parse_positive_int(
            source.get("PENDING_TTL_SECONDS", str(DEFAULT_PENDING_TTL_SECONDS)),
            "PENDING_TTL_SECONDS",
        ),
        max_rich_message_chars=parse_positive_int(
            source.get("MAX_RICH_MESSAGE_CHARS", str(DEFAULT_MAX_RICH_MESSAGE_CHARS)),
            "MAX_RICH_MESSAGE_CHARS",
        ),
        max_document_bytes=parse_positive_int(
            source.get("MAX_DOCUMENT_BYTES", str(DEFAULT_MAX_DOCUMENT_BYTES)),
            "MAX_DOCUMENT_BYTES",
        ),
        delete_webhook_on_start=parse_bool(source.get("TELEGRAM_DELETE_WEBHOOK_ON_START", "true")),
    )


def parse_int_set(raw: str, name: str) -> set[int]:
    values: set[int] = set()
    for item in _split_csv(raw):
        try:
            values.add(int(item))
        except ValueError as exc:
            raise ConfigError(f"{name} contains a non-integer value: {item!r}") from exc
    if not values:
        raise ConfigError(f"{name} must contain at least one user id")
    return values


def parse_chat_id(raw: str, name: str) -> int | str:
    value = raw.strip()
    if not value:
        raise ConfigError(f"{name} is required")
    if value.startswith("@"):
        return value
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer chat id or @channelusername") from exc


def parse_positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0")
    return value


def parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]
