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
    mode: str = "publish"
    target_message_id: int | None = None


@dataclass(frozen=True)
class EditSession:
    session_id: str
    user_id: int
    channel_id: int | str
    message_id: int
    stage: str
    created_at: float


@dataclass(frozen=True)
class PublishedMessage:
    channel_id: int | str
    message_id: int
    markdown: str
    updated_at: float


class PendingStore:
    def __init__(self, path: str, ttl_seconds: int) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingMessage] = {}
        self._edit_sessions: dict[str, EditSession] = {}
        self._published_messages: dict[str, PublishedMessage] = {}
        self._load()
        self.cleanup()

    def create(
        self,
        user_id: int,
        channel_id: int | str,
        markdown: str,
        mode: str = "publish",
        target_message_id: int | None = None,
    ) -> PendingMessage:
        self.cleanup()
        draft_id = secrets.token_urlsafe(12)
        item = PendingMessage(
            draft_id=draft_id,
            user_id=user_id,
            channel_id=channel_id,
            markdown=markdown,
            created_at=time.time(),
            mode=mode,
            target_message_id=target_message_id,
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

    def create_edit_session(self, user_id: int, channel_id: int | str, message_id: int) -> EditSession:
        self.cleanup()
        self.clear_user_edit_sessions(user_id)
        session_id = secrets.token_urlsafe(12)
        session = EditSession(
            session_id=session_id,
            user_id=user_id,
            channel_id=channel_id,
            message_id=message_id,
            stage="confirm",
            created_at=time.time(),
        )
        self._edit_sessions[session_id] = session
        self._save()
        return session

    def get_edit_session(
        self,
        session_id: str,
        user_id: int | None = None,
        stage: str | None = None,
    ) -> EditSession | None:
        session = self._edit_sessions.get(session_id)
        if session is None:
            return None
        if self._is_expired(session):
            self.delete_edit_session(session_id)
            return None
        if user_id is not None and session.user_id != user_id:
            return None
        if stage is not None and session.stage != stage:
            return None
        return session

    def get_active_edit_session(self, user_id: int, stage: str | None = None) -> EditSession | None:
        self.cleanup()
        sessions = [
            session
            for session in self._edit_sessions.values()
            if session.user_id == user_id and (stage is None or session.stage == stage)
        ]
        if not sessions:
            return None
        return max(sessions, key=lambda session: session.created_at)

    def set_edit_session_stage(self, session_id: str, stage: str) -> EditSession | None:
        session = self.get_edit_session(session_id)
        if session is None:
            return None
        updated = EditSession(
            session_id=session.session_id,
            user_id=session.user_id,
            channel_id=session.channel_id,
            message_id=session.message_id,
            stage=stage,
            created_at=session.created_at,
        )
        self._edit_sessions[session_id] = updated
        self._save()
        return updated

    def delete_edit_session(self, session_id: str) -> bool:
        removed = self._edit_sessions.pop(session_id, None) is not None
        if removed:
            self._save()
        return removed

    def clear_user_edit_sessions(self, user_id: int) -> int:
        session_ids = [
            session_id
            for session_id, session in self._edit_sessions.items()
            if session.user_id == user_id
        ]
        for session_id in session_ids:
            self._edit_sessions.pop(session_id, None)
        if session_ids:
            self._save()
        return len(session_ids)

    def record_published_message(
        self,
        channel_id: int | str,
        message_id: int,
        markdown: str,
    ) -> PublishedMessage:
        item = PublishedMessage(
            channel_id=channel_id,
            message_id=message_id,
            markdown=markdown,
            updated_at=time.time(),
        )
        self._published_messages[_published_message_key(channel_id, message_id)] = item
        self._save()
        return item

    def get_published_markdown(self, channel_id: int | str, message_id: int) -> str | None:
        item = self._published_messages.get(_published_message_key(channel_id, message_id))
        return item.markdown if item is not None else None

    def get_latest_published_message(self, channel_id: int | str) -> PublishedMessage | None:
        messages = [
            item
            for item in self._published_messages.values()
            if item.channel_id == channel_id
        ]
        if not messages:
            return None
        return max(messages, key=lambda item: item.message_id)

    def cleanup(self) -> int:
        expired_ids = [draft_id for draft_id, item in self._items.items() if self._is_expired(item)]
        for draft_id in expired_ids:
            self._items.pop(draft_id, None)
        expired_session_ids = [
            session_id
            for session_id, session in self._edit_sessions.items()
            if self._is_expired(session)
        ]
        for session_id in expired_session_ids:
            self._edit_sessions.pop(session_id, None)
        if expired_ids or expired_session_ids:
            self._save()
        return len(expired_ids) + len(expired_session_ids)

    def _is_expired(self, item: PendingMessage | EditSession) -> bool:
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
        edit_sessions = payload.get("edit_sessions", {})
        loaded_sessions: dict[str, EditSession] = {}
        for session_id, raw in edit_sessions.items():
            session = _decode_edit_session(session_id, raw)
            if session is not None:
                loaded_sessions[session_id] = session
        self._edit_sessions = loaded_sessions
        published_messages = payload.get("published_messages", {})
        loaded_published: dict[str, PublishedMessage] = {}
        for raw in published_messages.values():
            item = _decode_published_message(raw)
            if item is not None:
                loaded_published[_published_message_key(item.channel_id, item.message_id)] = item
        self._published_messages = loaded_published

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
                    "mode": item.mode,
                    "target_message_id": item.target_message_id,
                }
                for draft_id, item in self._items.items()
            },
            "edit_sessions": {
                session_id: {
                    "user_id": session.user_id,
                    "channel_id": session.channel_id,
                    "message_id": session.message_id,
                    "stage": session.stage,
                    "created_at": session.created_at,
                }
                for session_id, session in self._edit_sessions.items()
            },
            "published_messages": {
                key: {
                    "channel_id": item.channel_id,
                    "message_id": item.message_id,
                    "markdown": item.markdown,
                    "updated_at": item.updated_at,
                }
                for key, item in self._published_messages.items()
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
            mode=str(raw.get("mode", "publish")),
            target_message_id=_optional_int(raw.get("target_message_id")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _decode_edit_session(session_id: str, raw: Any) -> EditSession | None:
    if not isinstance(raw, dict):
        return None
    try:
        return EditSession(
            session_id=session_id,
            user_id=int(raw["user_id"]),
            channel_id=raw["channel_id"],
            message_id=int(raw["message_id"]),
            stage=str(raw["stage"]),
            created_at=float(raw["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _decode_published_message(raw: Any) -> PublishedMessage | None:
    if not isinstance(raw, dict):
        return None
    try:
        return PublishedMessage(
            channel_id=raw["channel_id"],
            message_id=int(raw["message_id"]),
            markdown=str(raw["markdown"]),
            updated_at=float(raw["updated_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    return int(raw)


def _published_message_key(channel_id: int | str, message_id: int) -> str:
    return f"{channel_id}:{message_id}"
