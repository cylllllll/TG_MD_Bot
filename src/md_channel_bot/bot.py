from __future__ import annotations

import logging
import time
from html import escape
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .store import EditSession, PendingMessage, PendingStore
from .telegram_api import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)

ACTION_SEND = "send"
ACTION_CANCEL = "cancel"
ACTION_BEGIN_EDIT = "edit"
ACTION_UPDATE_EDIT = "update"
ACTION_CANCEL_EDIT = "ecancel"
EDIT_STAGE_CONFIRM = "confirm"
EDIT_STAGE_AWAITING_CONTENT = "awaiting_content"
TEXT_MESSAGE_CHUNK_LIMIT = 3800
BOT_COMMANDS = [
    {"command": "start", "description": "开始使用 bot"},
    {"command": "help", "description": "查看使用说明"},
    {"command": "edit", "description": "编辑频道消息，参数可用 URL 或消息 ID"},
    {"command": "recent", "description": "编辑当前频道最新消息"},
]


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
        self.register_bot_commands()

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

    def register_bot_commands(self) -> None:
        try:
            self.client.set_my_commands(BOT_COMMANDS)
        except TelegramAPIError:
            logger.exception("Failed to register bot commands")

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
            self._handle_command(chat_id, user_id, text)
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

        edit_session = self.store.get_active_edit_session(user_id, stage=EDIT_STAGE_AWAITING_CONTENT)
        if edit_session is not None:
            self._create_edit_preview(chat_id, message_id, edit_session, markdown)
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

        if action in {ACTION_BEGIN_EDIT, ACTION_CANCEL_EDIT}:
            self._handle_edit_session_callback(
                callback_id=callback_id,
                action=action,
                session_id=draft_id,
                user_id=user_id,
                preview_chat_id=preview_chat_id,
                preview_message_id=preview_message_id,
            )
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

        if action == ACTION_SEND:
            if pending.mode != "publish":
                self.client.answer_callback_query(callback_id, "草稿类型不匹配。", show_alert=True)
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
            return

        if action == ACTION_UPDATE_EDIT:
            if pending.mode != "edit" or pending.target_message_id is None:
                self.client.answer_callback_query(callback_id, "草稿类型不匹配。", show_alert=True)
                return
            try:
                self._update_channel_message(pending)
            except TelegramAPIError as exc:
                logger.warning("Failed to update pending message %s: %s", draft_id, exc)
                self.client.answer_callback_query(callback_id, f"更新失败：{exc.description}", show_alert=True)
                return

            self.store.delete(draft_id)
            self._remove_preview_buttons(preview_chat_id, preview_message_id)
            self.client.answer_callback_query(callback_id, "已更新频道消息。")
            return

        self.client.answer_callback_query(callback_id, "未知操作。", show_alert=True)

    def _handle_command(self, chat_id: int | str, user_id: int, text: str) -> None:
        command, argument = _split_command(text)
        if command in {"/start", "/help"}:
            self._safe_send_text(
                chat_id,
                "发送 Markdown 文本或 .md/.markdown/.txt 文件，我会用 sendRichMessage 生成预览。"
                "确认无误后点击“发送到频道”，或点击“取消”。\n\n"
                "编辑已发布消息：发送 /edit 频道消息链接 或 /edit 消息ID。\n"
                "快速编辑当前频道最新消息：发送 /recent。",
            )
        elif command == "/edit":
            self._handle_edit_command(chat_id, user_id, argument)
        elif command == "/recent":
            self._handle_recent_command(chat_id, user_id)
        else:
            self._safe_send_text(chat_id, "未知命令。发送 /help 查看用法。")

    def _handle_edit_command(self, chat_id: int | str, user_id: int, argument: str) -> None:
        try:
            target_message_id = _parse_edit_target(argument, self.settings.channel_id)
        except UserFacingError as exc:
            self._safe_send_text(chat_id, str(exc))
            return

        self._start_edit_session(chat_id, user_id, target_message_id)

    def _handle_recent_command(self, chat_id: int | str, user_id: int) -> None:
        try:
            latest_message_id, public_html = self._get_public_channel_latest_message()
        except UserFacingError as exc:
            latest = self.store.get_latest_published_message(self.settings.channel_id)
            if latest is None:
                self._safe_send_text(chat_id, f"{exc} 也没有本地已记录消息可兜底，请用 /edit 消息ID。")
                return

            self._safe_send_text(chat_id, f"{exc} 已改用本地记录的最近消息 ID：{latest.message_id}")
            self._start_edit_session(chat_id, user_id, latest.message_id)
            return

        self._start_edit_session(chat_id, user_id, latest_message_id, fallback_original_markdown=public_html)

    def _get_public_channel_latest_message(self) -> tuple[int, str | None]:
        username = self._get_configured_channel_username()
        try:
            message = self.client.get_public_channel_latest_message(username)
        except TelegramAPIError as exc:
            raise UserFacingError(f"获取公开频道最新消息 ID 失败：{exc.description}") from exc
        return message.message_id, message.html

    def _get_configured_channel_username(self) -> str:
        if isinstance(self.settings.channel_id, str) and self.settings.channel_id.startswith("@"):
            return self.settings.channel_id[1:]

        try:
            chat = self.client.get_chat(self.settings.channel_id)
        except TelegramAPIError as exc:
            raise UserFacingError(f"读取当前频道信息失败：{exc.description}") from exc

        username = chat.get("username")
        if isinstance(username, str) and username:
            return username
        raise UserFacingError("当前配置频道没有公开 username，无法通过公开链接获取最新消息 ID。")

    def _start_edit_session(
        self,
        chat_id: int | str,
        user_id: int,
        target_message_id: int,
        fallback_original_markdown: str | None = None,
    ) -> None:
        session = self.store.create_edit_session(user_id, self.settings.channel_id, target_message_id)
        markdown = self._resolve_original_markdown(chat_id, target_message_id, fallback_original_markdown)
        if markdown is not None:
            try:
                self._send_original_markdown_edit_message(chat_id, session.session_id, markdown)
            except TelegramAPIError as exc:
                self.store.delete_edit_session(session.session_id)
                logger.warning("Failed to send original markdown edit message %s: %s", target_message_id, exc)
                self._safe_send_text(chat_id, f"发送原始内容失败：{exc.description}")
            return

        try:
            self.client.copy_message(
                chat_id=chat_id,
                from_chat_id=self.settings.channel_id,
                message_id=target_message_id,
                reply_markup=_edit_keyboard(session.session_id),
            )
        except TelegramAPIError as exc:
            self.store.delete_edit_session(session.session_id)
            logger.warning("Failed to copy channel message %s: %s", target_message_id, exc)
            self._safe_send_text(chat_id, f"读取频道消息失败：{exc.description}")
            return

    def _handle_edit_session_callback(
        self,
        callback_id: str,
        action: str,
        session_id: str,
        user_id: int,
        preview_chat_id: int | str | None,
        preview_message_id: int | None,
    ) -> None:
        session = self.store.get_edit_session(session_id, user_id=user_id)
        if session is None:
            self.client.answer_callback_query(callback_id, "编辑会话不存在或已过期。", show_alert=True)
            return

        if action == ACTION_CANCEL_EDIT:
            self.store.delete_edit_session(session_id)
            self._remove_preview_buttons(preview_chat_id, preview_message_id)
            self.client.answer_callback_query(callback_id, "已取消。")
            return

        if action != ACTION_BEGIN_EDIT:
            self.client.answer_callback_query(callback_id, "未知操作。", show_alert=True)
            return

        updated = self.store.set_edit_session_stage(session_id, EDIT_STAGE_AWAITING_CONTENT)
        if updated is None:
            self.client.answer_callback_query(callback_id, "编辑会话不存在或已过期。", show_alert=True)
            return
        self._remove_preview_buttons(preview_chat_id, preview_message_id)
        self.client.answer_callback_query(callback_id, "请发送新内容。")
        self._safe_send_text(preview_chat_id or user_id, "请发送新内容来替换消息")

    def _create_edit_preview(
        self,
        chat_id: int | str,
        reply_to_message_id: int | None,
        edit_session: EditSession,
        markdown: str,
    ) -> None:
        pending = self.store.create(
            user_id=edit_session.user_id,
            channel_id=edit_session.channel_id,
            markdown=markdown,
            mode="edit",
            target_message_id=edit_session.message_id,
        )
        try:
            self.client.send_rich_message(
                chat_id=chat_id,
                markdown=markdown,
                reply_markup=_update_keyboard(pending.draft_id),
            )
        except TelegramAPIError as exc:
            self.store.delete(pending.draft_id)
            logger.warning("Rich message edit preview failed: %s", exc)
            self._safe_send_text(chat_id, f"Rich Message 预览失败：{exc.description}", reply_to_message_id=reply_to_message_id)
            return
        self.store.delete_edit_session(edit_session.session_id)

    def _resolve_original_markdown(
        self,
        chat_id: int | str,
        message_id: int,
        fallback_original_markdown: str | None = None,
    ) -> str | None:
        markdown = self.store.get_published_markdown(self.settings.channel_id, message_id)
        if markdown is None:
            markdown = self._fetch_original_markdown_via_forward(chat_id, message_id)
            if markdown:
                self.store.record_published_message(self.settings.channel_id, message_id, markdown)
        if markdown is None and fallback_original_markdown:
            markdown = fallback_original_markdown
        return markdown

    def _send_original_markdown_edit_message(self, chat_id: int | str, session_id: str, markdown: str) -> None:
        chunks = _original_markdown_messages(markdown)
        for index, text in enumerate(chunks):
            self.client.send_text(
                chat_id=chat_id,
                text=_monospace_message(text),
                parse_mode="HTML",
                reply_markup=_edit_keyboard(session_id) if index == 0 else None,
            )

    def _fetch_original_markdown_via_forward(self, chat_id: int | str, message_id: int) -> str | None:
        try:
            message = self.client.forward_message(
                chat_id=chat_id,
                from_chat_id=self.settings.channel_id,
                message_id=message_id,
                disable_notification=True,
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to forward channel message %s for rich source: %s", message_id, exc)
            return None

        forwarded_message_id = _response_message_id(message)
        if forwarded_message_id is not None:
            try:
                self.client.delete_message(chat_id=chat_id, message_id=forwarded_message_id)
            except TelegramAPIError:
                logger.exception("Failed to delete temporary forwarded message %s", forwarded_message_id)

        return _message_to_source_markdown(message)

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
        result = self.client.send_rich_message(
            chat_id=pending.channel_id,
            markdown=pending.markdown,
        )
        message_id = _response_message_id(result)
        if message_id is not None:
            self.store.record_published_message(pending.channel_id, message_id, pending.markdown)

    def _update_channel_message(self, pending: PendingMessage) -> None:
        if pending.target_message_id is None:
            raise ValueError("target_message_id is required for edit drafts")
        self.client.edit_message_text(
            chat_id=pending.channel_id,
            message_id=pending.target_message_id,
            markdown=pending.markdown,
        )
        self.store.record_published_message(pending.channel_id, pending.target_message_id, pending.markdown)

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
        parse_mode: str | None = None,
    ) -> None:
        try:
            self.client.send_text(chat_id, text, reply_to_message_id=reply_to_message_id, parse_mode=parse_mode)
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
    if separator != ":" or action not in {
        ACTION_SEND,
        ACTION_CANCEL,
        ACTION_BEGIN_EDIT,
        ACTION_UPDATE_EDIT,
        ACTION_CANCEL_EDIT,
    } or not draft_id:
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


def _edit_keyboard(session_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "编辑", "callback_data": f"{ACTION_BEGIN_EDIT}:{session_id}", "style": "success"},
                {"text": "取消", "callback_data": f"{ACTION_CANCEL_EDIT}:{session_id}", "style": "danger"},
            ]
        ]
    }


def _update_keyboard(draft_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "更新到频道", "callback_data": f"{ACTION_UPDATE_EDIT}:{draft_id}", "style": "success"},
                {"text": "取消", "callback_data": f"{ACTION_CANCEL}:{draft_id}", "style": "danger"},
            ]
        ]
    }


def _original_markdown_messages(markdown: str) -> list[str]:
    chunks: list[str] = []
    remaining = markdown
    while remaining:
        chunk, remaining = _take_text_chunk(remaining, TEXT_MESSAGE_CHUNK_LIMIT)
        chunks.append(chunk)
    return chunks or [""]


def _monospace_message(text: str) -> str:
    return f"<pre>{escape(text, quote=False)}</pre>"


def _take_text_chunk(text: str, size: int) -> tuple[str, str]:
    if len(text) <= size:
        return text, ""
    newline_index = text.rfind("\n", 0, size + 1)
    if newline_index > 0:
        split_at = newline_index + 1
    else:
        split_at = size
    return text[:split_at], text[split_at:]


def _message_to_source_markdown(message: dict[str, Any]) -> str | None:
    rich_message = message.get("rich_message")
    if isinstance(rich_message, dict):
        markdown = _rich_message_to_source_markdown(rich_message)
        if markdown:
            return markdown

    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text

    caption = message.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption

    return None


def _rich_message_to_source_markdown(rich_message: dict[str, Any]) -> str | None:
    blocks = rich_message.get("blocks")
    if not isinstance(blocks, list):
        return None
    return _join_rich_blocks(blocks)


def _join_rich_blocks(blocks: list[Any]) -> str | None:
    rendered = [_rich_block_to_source_markdown(block) for block in blocks]
    rendered = [item for item in rendered if item]
    return "\n\n".join(rendered) if rendered else None


def _rich_block_to_source_markdown(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None

    block_type = block.get("type")
    if block_type == "paragraph":
        return _rich_text_to_source(block.get("text"))

    if block_type == "blockquote":
        inner_blocks = block.get("blocks")
        inner = _join_rich_blocks(inner_blocks) if isinstance(inner_blocks, list) else None
        credit = _rich_credit_to_source_markdown(block.get("credit"))
        parts = [part for part in [inner, credit] if part]
        if not parts:
            return None
        return f"<blockquote>{'\n'.join(parts)}</blockquote>"

    text = _rich_text_to_source(block.get("text"))
    return text if text else None


def _rich_credit_to_source_markdown(credit: Any) -> str | None:
    if not isinstance(credit, dict):
        return None
    text = _rich_text_to_source(credit.get("text"))
    if not text:
        return None
    if credit.get("type") == "url":
        url = credit.get("url")
        if isinstance(url, str) and url:
            return f'<cite><a href="{escape(url, quote=True)}">{text}</a></cite>'
    return f"<cite>{text}</cite>"


def _rich_text_to_source(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    if not text.strip():
        return None
    return escape(text, quote=False)


def _looks_like_markdown_file(filename: str, mime_type: str) -> bool:
    lowered = filename.lower()
    if lowered.endswith((".md", ".markdown", ".txt")):
        return True
    return mime_type.startswith("text/")


def _split_command(text: str) -> tuple[str, str]:
    command, _, argument = text.partition(" ")
    command = command.split("@", maxsplit=1)[0].lower()
    return command, argument.strip()


def _parse_edit_target(raw: str, configured_channel_id: int | str) -> int:
    target = raw.strip()
    if not target:
        raise UserFacingError("用法：/edit 频道消息链接 或 /edit 消息ID")
    if target.isdecimal():
        return _parse_message_id(target)

    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"t.me", "telegram.me"}:
        raise UserFacingError("无法识别消息地址。请发送频道消息链接，或直接发送消息 ID。")

    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts and path_parts[0] == "s":
        path_parts = path_parts[1:]

    if len(path_parts) >= 3 and path_parts[0] == "c":
        if not _matches_private_channel_link(configured_channel_id, path_parts[1]):
            raise UserFacingError("消息链接所属频道与当前配置频道不一致。")
        return _parse_message_id(path_parts[2])

    if len(path_parts) >= 2:
        if isinstance(configured_channel_id, str) and configured_channel_id.startswith("@"):
            configured_username = configured_channel_id[1:].lower()
            if path_parts[0].lower() != configured_username:
                raise UserFacingError("消息链接所属频道与当前配置频道不一致。")
        return _parse_message_id(path_parts[1])

    raise UserFacingError("无法从消息链接中解析消息 ID。")


def _parse_message_id(raw: str) -> int:
    try:
        message_id = int(raw)
    except ValueError as exc:
        raise UserFacingError("消息 ID 必须是数字。") from exc
    if message_id <= 0:
        raise UserFacingError("消息 ID 必须大于 0。")
    return message_id


def _response_message_id(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    return message_id if isinstance(message_id, int) else None


def _matches_private_channel_link(configured_channel_id: int | str, internal_id: str) -> bool:
    if not internal_id.isdecimal():
        return False
    if isinstance(configured_channel_id, int):
        configured = str(abs(configured_channel_id))
        if configured.startswith("100"):
            configured = configured[3:]
        return configured == internal_id
    return False
