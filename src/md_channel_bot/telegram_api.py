from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, error_code: int | None, description: str) -> None:
        self.method = method
        self.error_code = error_code
        self.description = description
        super().__init__(f"{method} failed: {error_code or 'unknown'} {description}")


@dataclass(frozen=True)
class TelegramClient:
    token: str
    api_base: str
    request_timeout_seconds: int = 70

    def get_me(self) -> dict[str, Any]:
        return self.request("getMe", {})

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        return bool(self.request("deleteWebhook", {"drop_pending_updates": drop_pending_updates}))

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

    def send_text(self, chat_id: int | str, text: str, reply_to_message_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
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
