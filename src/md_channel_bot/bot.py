from __future__ import annotations

import logging
import time
from typing import Any

from .config import Settings
from .store import PendingMessage, PendingStore
from .telegram_api import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)

ACTION_SEND = "send"
ACTION_CANCEL = "cancel"


class MarkdownChannelBot:
    def __init__(self, settings: Settings, client: TelegramClient, store: PendingStore) -> None:
        self.settings = settings
        self.client = client
        self.store = store

    def run_forever(self) -> None:
        if self.settings.delete_webhook_on_start:
            self.client.delete_webhook(drop_pending_updates=False)

        me = self.client.get_me()
        logger.info("Bot started as @%s", me.get("username", me.get("id")))

        offset: int | None = None
        while True:
            try:
                updates = self.client.get_updates(offset, self.settings.poll_timeout_seconds)
            except TelegramAPIError:
                logger.exception("Failed to fetch updates")
                time.sleep(5)
                continue

            for update in updates:
                update_id = update.get("update_id")
                try:
                    self.handle_update(update)
                except Exception:
                    logger.exception("Failed to handle update %s", update_id)
                if isinstance(update_id, int):
                    offset = update_id + 1

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self.handle_message(update["message"])
        elif "callback_query" in update:
            self.handle_callback_query(update["callback_query"])

    def handle_message(self, message: dict[str, Any]) -> None:
        user_id = _message_user_id(message)
        chat_id = _message_chat_id(message)
        message_id = message.get("message_id")
        if user_id is None or chat_id is None:
            return

        if not self._is_allowed_user(user_id):
            logger.warning("Rejected message from unauthorized user_id=%s", user_id)
            self._safe_send_text(chat_id, "无权限使用此 bot。")
            return

        text = message.get("text")
        if isinstance(text, str) and text.startswith("/"):
            self._handle_command(chat_id, text)
            return

        try:
            markdown = self._extract_markdown(message)
        except UserFacingError as exc:
            self._safe_send_text(chat_id, str(exc), reply_to_message_id=message_id)
            return

        if len(markdown) > self.settings.max_rich_message_chars:
            self._safe_send_text(
                chat_id,
                f"内容过长：{len(markdown)} 字符，当前上限是 {self.settings.max_rich_message_chars}。",
                reply_to_message_id=message_id,
            )
            return

        pending = self.store.create(user_id, self.settings.channel_id, markdown)
        try:
            self.client.send_rich_message(
                chat_id=chat_id,
                markdown=markdown,
                reply_markup=_approval_keyboard(pending.draft_id),
            )
        except TelegramAPIError as exc:
            self.store.delete(pending.draft_id)
            logger.warning("Rich message preview failed: %s", exc)
            self._safe_send_text(chat_id, f"Rich Message 预览失败：{exc.description}", reply_to_message_id=message_id)

    def handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        callback_id = callback_query.get("id")
        user = callback_query.get("from") or {}
        user_id = user.get("id")
        data = callback_query.get("data")
        callback_message = callback_query.get("message") or {}
        chat = callback_message.get("chat") or {}
        preview_chat_id = chat.get("id")
        preview_message_id = callback_message.get("message_id")

        if not isinstance(callback_id, str):
            return
        if not isinstance(user_id, int) or not self._is_allowed_user(user_id):
            self.client.answer_callback_query(callback_id, "无权限。", show_alert=True)
            return

        action, draft_id = _parse_callback_data(data)
        if action is None or draft_id is None:
            self.client.answer_callback_query(callback_id, "操作已失效。", show_alert=True)
            return

        pending = self.store.get(draft_id, user_id=user_id)
        if pending is None:
            self.client.answer_callback_query(callback_id, "草稿不存在或已过期。", show_alert=True)
            return

        if action == ACTION_CANCEL:
            self.store.delete(draft_id)
            self._remove_preview_buttons(preview_chat_id, preview_message_id)
            self.client.answer_callback_query(callback_id, "已取消。")
            return

        if action != ACTION_SEND:
            self.client.answer_callback_query(callback_id, "未知操作。", show_alert=True)
            return

        try:
            self._send_pending_to_channel(pending)
        except TelegramAPIError as exc:
            logger.warning("Failed to send pending message %s: %s", draft_id, exc)
            self.client.answer_callback_query(callback_id, f"发送失败：{exc.description}", show_alert=True)
            return

        self.store.delete(draft_id)
        self._remove_preview_buttons(preview_chat_id, preview_message_id)
        self.client.answer_callback_query(callback_id, "已发送到频道。")

    def _handle_command(self, chat_id: int | str, text: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
        if command in {"/start", "/help"}:
            self._safe_send_text(
                chat_id,
                "发送 Markdown 文本或 .md/.markdown/.txt 文件，我会用 sendRichMessage 生成预览。"
                "确认无误后点击“发送到频道”，或点击“取消”。",
            )
        else:
            self._safe_send_text(chat_id, "未知命令。发送 /help 查看用法。")

    def _extract_markdown(self, message: dict[str, Any]) -> str:
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            return text

        document = message.get("document")
        if isinstance(document, dict):
            return self._download_markdown_document(document)

        raise UserFacingError("请发送 Markdown 文本，或上传 .md/.markdown/.txt 文件。")

    def _download_markdown_document(self, document: dict[str, Any]) -> str:
        filename = str(document.get("file_name") or "")
        mime_type = str(document.get("mime_type") or "")
        if not _looks_like_markdown_file(filename, mime_type):
            raise UserFacingError("只支持 .md/.markdown/.txt 或 text/* 类型文件。")

        file_size = document.get("file_size")
        if isinstance(file_size, int) and file_size > self.settings.max_document_bytes:
            raise UserFacingError(f"文件过大：当前上限是 {self.settings.max_document_bytes} bytes。")

        file_id = document.get("file_id")
        if not isinstance(file_id, str):
            raise UserFacingError("文件缺少 file_id，无法下载。")

        try:
            file_info = self.client.get_file(file_id)
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str):
                raise UserFacingError("Telegram 未返回可下载的 file_path。")
            content = self.client.download_file(file_path)
        except TelegramAPIError as exc:
            raise UserFacingError(f"文件下载失败：{exc.description}") from exc

        if len(content) > self.settings.max_document_bytes:
            raise UserFacingError(f"文件过大：当前上限是 {self.settings.max_document_bytes} bytes。")
        try:
            markdown = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UserFacingError("文件必须是 UTF-8 编码。") from exc
        if not markdown.strip():
            raise UserFacingError("文件内容为空。")
        return markdown

    def _send_pending_to_channel(self, pending: PendingMessage) -> None:
        self.client.send_rich_message(
            chat_id=pending.channel_id,
            markdown=pending.markdown,
        )

    def _remove_preview_buttons(self, chat_id: int | str | None, message_id: int | None) -> None:
        if chat_id is None or not isinstance(message_id, int):
            return
        try:
            self.client.edit_message_reply_markup(chat_id, message_id, reply_markup={"inline_keyboard": []})
        except TelegramAPIError:
            logger.exception("Failed to remove preview buttons")

    def _safe_send_text(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        try:
            self.client.send_text(chat_id, text, reply_to_message_id=reply_to_message_id)
        except TelegramAPIError:
            logger.exception("Failed to send text response")

    def _is_allowed_user(self, user_id: int) -> bool:
        return user_id in self.settings.allowed_user_ids


class UserFacingError(ValueError):
    pass


def _message_user_id(message: dict[str, Any]) -> int | None:
    user = message.get("from") or {}
    user_id = user.get("id")
    return user_id if isinstance(user_id, int) else None


def _message_chat_id(message: dict[str, Any]) -> int | str | None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if isinstance(chat_id, int | str):
        return chat_id
    return None


def _parse_callback_data(data: Any) -> tuple[str | None, str | None]:
    if not isinstance(data, str):
        return None, None
    action, separator, draft_id = data.partition(":")
    if separator != ":" or action not in {ACTION_SEND, ACTION_CANCEL} or not draft_id:
        return None, None
    return action, draft_id


def _approval_keyboard(draft_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "发送到频道", "callback_data": f"{ACTION_SEND}:{draft_id}", "style": "success"},
                {"text": "取消", "callback_data": f"{ACTION_CANCEL}:{draft_id}", "style": "danger"},
            ]
        ]
    }


def _looks_like_markdown_file(filename: str, mime_type: str) -> bool:
    lowered = filename.lower()
    if lowered.endswith((".md", ".markdown", ".txt")):
        return True
    return mime_type.startswith("text/")
