from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)

PUBLIC_CHANNEL_PREVIEW_TIMEOUT_SECONDS = 8


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, error_code: int | None, description: str) -> None:
        self.method = method
        self.error_code = error_code
        self.description = description
        super().__init__(f"{method} failed: {error_code or 'unknown'} {description}")


@dataclass(frozen=True)
class PublicChannelMessage:
    message_id: int
    html: str | None = None


@dataclass(frozen=True)
class TelegramClient:
    token: str
    api_base: str
    request_timeout_seconds: int = 70

    def get_me(self) -> dict[str, Any]:
        return self.request("getMe", {})

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        return bool(self.request("deleteWebhook", {"drop_pending_updates": drop_pending_updates}))

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        return bool(self.request("setMyCommands", {"commands": commands}))

    def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        result = self.request("getChat", {"chat_id": chat_id})
        if not isinstance(result, dict):
            raise TelegramAPIError("getChat", None, "unexpected result type")
        return result

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self.request("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(result, list):
            raise TelegramAPIError("getUpdates", None, "unexpected result type")
        return result

    def send_text(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.request("sendMessage", payload)

    def send_rich_message(
        self,
        chat_id: int | str,
        markdown: str,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": {"markdown": markdown},
            "disable_notification": disable_notification,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.request("sendRichMessage", payload)

    def copy_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self.request("copyMessage", payload)
        if not isinstance(result, dict):
            raise TelegramAPIError("copyMessage", None, "unexpected result type")
        return result

    def forward_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        disable_notification: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
            "disable_notification": disable_notification,
        }
        result = self.request("forwardMessage", payload)
        if not isinstance(result, dict):
            raise TelegramAPIError("forwardMessage", None, "unexpected result type")
        return result

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        markdown: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": {"markdown": markdown},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.request("editMessageText", payload)

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        return self.request("editMessageReplyMarkup", payload)

    def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        return bool(self.request("deleteMessage", {"chat_id": chat_id, "message_id": message_id}))

    def get_public_channel_latest_message_id(self, channel: str) -> int:
        return self.get_public_channel_latest_message(channel).message_id

    def get_public_channel_latest_message(self, channel: str) -> PublicChannelMessage:
        username = _parse_public_channel_username(channel)
        url = f"https://t.me/s/{urllib.parse.quote(username)}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            timeout = min(self.request_timeout_seconds, PUBLIC_CHANNEL_PREVIEW_TIMEOUT_SECONDS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise TelegramAPIError("getPublicChannelLatestMessageId", exc.code, exc.reason) from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError("getPublicChannelLatestMessageId", None, str(exc.reason)) from exc

        message_ids = _extract_public_channel_message_ids(body, username)
        if not message_ids:
            raise TelegramAPIError("getPublicChannelLatestMessageId", None, "未在公开频道预览页找到消息 ID")
        message_id = max(message_ids)
        return PublicChannelMessage(
            message_id=message_id,
            html=_extract_public_channel_message_html(body, username, message_id),
        )

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text[:200]
        return bool(self.request("answerCallbackQuery", payload))

    def get_file(self, file_id: str) -> dict[str, Any]:
        result = self.request("getFile", {"file_id": file_id})
        if not isinstance(result, dict):
            raise TelegramAPIError("getFile", None, "unexpected result type")
        return result

    def download_file(self, file_path: str) -> bytes:
        quoted_path = urllib.parse.quote(file_path, safe="/")
        url = f"{self.api_base}/file/bot{self.token}/{quoted_path}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.debug("Telegram file download error body: %s", body)
            raise TelegramAPIError("downloadFile", exc.code, body or exc.reason) from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError("downloadFile", None, str(exc.reason)) from exc

    def request(self, method: str, payload: dict[str, Any], timeout: int | None = None) -> Any:
        url = f"{self.api_base}/bot{self.token}/{method}"
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _http_error_to_api_error(method, exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(method, None, str(exc.reason)) from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TelegramAPIError(method, None, "invalid JSON response") from exc

        if not data.get("ok"):
            raise TelegramAPIError(method, data.get("error_code"), data.get("description", "unknown error"))
        return data.get("result")


def _http_error_to_api_error(method: str, status_code: int, body: str) -> TelegramAPIError:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return TelegramAPIError(method, status_code, body or "HTTP error")
    return TelegramAPIError(method, data.get("error_code", status_code), data.get("description", body))


def _parse_public_channel_username(channel: str) -> str:
    value = channel.strip()
    if not value:
        raise TelegramAPIError("getPublicChannelLatestMessageId", None, "缺少公开频道链接或用户名")
    if value.startswith("@"):
        value = value[1:]
    elif "://" in value:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"t.me", "telegram.me"}:
            raise TelegramAPIError("getPublicChannelLatestMessageId", None, "无法识别公开频道链接")
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts and path_parts[0] == "s":
            path_parts = path_parts[1:]
        if len(path_parts) != 1:
            raise TelegramAPIError("getPublicChannelLatestMessageId", None, "请发送公开频道主页链接，例如 https://t.me/channel")
        value = path_parts[0]
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        raise TelegramAPIError("getPublicChannelLatestMessageId", None, "公开频道用户名格式无效")
    return value


def _extract_public_channel_message_ids(html: str, username: str) -> list[int]:
    escaped_username = re.escape(username)
    patterns = [
        rf'data-post="{escaped_username}/(\d+)"',
        rf"https://t\.me/{escaped_username}/(\d+)",
        rf"/{escaped_username}/(\d+)",
    ]
    message_ids: set[int] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            message_ids.add(int(match.group(1)))
    return sorted(message_ids)


def _extract_public_channel_message_html(html: str, username: str, message_id: int) -> str | None:
    parser = _TelegramPreviewParser(username=username, target_message_id=message_id)
    parser.feed(html)
    message_html = parser.messages.get(message_id)
    if message_html is None:
        return None
    return _trim_message_html(message_html)


def _trim_message_html(message_html: str) -> str:
    lines = [line.rstrip() for line in message_html.strip().splitlines()]
    return "\n".join(lines).strip()


class _TelegramPreviewParser(HTMLParser):
    def __init__(self, username: str, target_message_id: int) -> None:
        super().__init__(convert_charrefs=True)
        self.username = username.lower()
        self.target_message_id = target_message_id
        self.messages: dict[int, str] = {}
        self._active_message_id: int | None = None
        self._message_depth = 0
        self._capture_id: int | None = None
        self._capture_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = _attrs_to_dict(attrs)
        data_post = attrs_map.get("data-post")
        message_id = self._parse_data_post(data_post)

        if message_id is not None:
            self._active_message_id = message_id
            self._message_depth = 1
        elif self._active_message_id is not None:
            self._message_depth += 1

        if self._capture_id is not None:
            self._append_start_tag(tag, attrs_map)
            return

        if self._active_message_id == self.target_message_id and _is_message_text_div(tag, attrs_map):
            self._capture_id = self._active_message_id
            self._capture_depth = 1
            self._chunks = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capture_id is not None:
            self._append_start_tag(tag, _attrs_to_dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if self._capture_id is not None:
            if self._capture_depth <= 1:
                self.messages[self._capture_id] = "".join(self._chunks)
                self._capture_id = None
                self._capture_depth = 0
                self._chunks = []
            else:
                self._append_end_tag(tag)
                self._capture_depth -= 1

        if self._active_message_id is not None:
            self._message_depth -= 1
            if self._message_depth <= 0:
                self._active_message_id = None
                self._message_depth = 0

    def handle_data(self, data: str) -> None:
        if self._capture_id is not None:
            self._chunks.append(escape(data, quote=False))

    def _parse_data_post(self, data_post: str | None) -> int | None:
        if not data_post:
            return None
        username, separator, raw_message_id = data_post.partition("/")
        if separator != "/" or username.lower() != self.username or not raw_message_id.isdecimal():
            return None
        return int(raw_message_id)

    def _append_start_tag(self, tag: str, attrs: dict[str, str]) -> None:
        normalized = _normalized_start_tag(tag, attrs)
        if normalized is not None:
            self._chunks.append(normalized)
        if tag.lower() != "br":
            self._capture_depth += 1

    def _append_end_tag(self, tag: str) -> None:
        normalized = _normalized_end_tag(tag)
        if normalized is not None:
            self._chunks.append(normalized)


def _attrs_to_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): value or "" for key, value in attrs}


def _is_message_text_div(tag: str, attrs: dict[str, str]) -> bool:
    if tag.lower() != "div":
        return False
    classes = set(attrs.get("class", "").split())
    return "tgme_widget_message_text" in classes


def _normalized_start_tag(tag: str, attrs: dict[str, str]) -> str | None:
    tag = tag.lower()
    if tag == "br":
        return "\n"
    if tag == "a":
        href = attrs.get("href")
        if not href:
            return "<a>"
        return f'<a href="{escape(href, quote=True)}">'
    if tag in {"blockquote", "cite", "b", "strong", "i", "em", "u", "s", "code", "pre"}:
        return f"<{tag}>"
    return ""


def _normalized_end_tag(tag: str) -> str | None:
    tag = tag.lower()
    if tag in {"a", "blockquote", "cite", "b", "strong", "i", "em", "u", "s", "code", "pre"}:
        return f"</{tag}>"
    return ""
