from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PendingMessage:
    draft_id: str
    user_id: int
    channel_id: int | str
    markdown: str
    created_at: float


class PendingStore:
    def __init__(self, path: str, ttl_seconds: int) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingMessage] = {}
        self._load()
        self.cleanup()

    def create(self, user_id: int, channel_id: int | str, markdown: str) -> PendingMessage:
        self.cleanup()
        draft_id = secrets.token_urlsafe(12)
        item = PendingMessage(
            draft_id=draft_id,
            user_id=user_id,
            channel_id=channel_id,
            markdown=markdown,
            created_at=time.time(),
        )
        self._items[draft_id] = item
        self._save()
        return item

    def get(self, draft_id: str, user_id: int | None = None) -> PendingMessage | None:
        item = self._items.get(draft_id)
        if item is None:
            return None
        if self._is_expired(item):
            self.delete(draft_id)
            return None
        if user_id is not None and item.user_id != user_id:
            return None
        return item

    def delete(self, draft_id: str) -> bool:
        removed = self._items.pop(draft_id, None) is not None
        if removed:
            self._save()
        return removed

    def cleanup(self) -> int:
        expired_ids = [draft_id for draft_id, item in self._items.items() if self._is_expired(item)]
        for draft_id in expired_ids:
            self._items.pop(draft_id, None)
        if expired_ids:
            self._save()
        return len(expired_ids)

    def _is_expired(self, item: PendingMessage) -> bool:
        return (time.time() - item.created_at) > self.ttl_seconds

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        items = payload.get("items", {})
        loaded: dict[str, PendingMessage] = {}
        for draft_id, raw in items.items():
            item = _decode_pending_message(draft_id, raw)
            if item is not None:
                loaded[draft_id] = item
        self._items = loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "items": {
                draft_id: {
                    "user_id": item.user_id,
                    "channel_id": item.channel_id,
                    "markdown": item.markdown,
                    "created_at": item.created_at,
                }
                for draft_id, item in self._items.items()
            },
        }
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, self.path)


def _decode_pending_message(draft_id: str, raw: Any) -> PendingMessage | None:
    if not isinstance(raw, dict):
        return None
    try:
        return PendingMessage(
            draft_id=draft_id,
            user_id=int(raw["user_id"]),
            channel_id=raw["channel_id"],
            markdown=str(raw["markdown"]),
            created_at=float(raw["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
