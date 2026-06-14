from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from md_channel_bot.bot import MarkdownChannelBot
from md_channel_bot.config import Settings
from md_channel_bot.store import PendingStore


class FakeClient:
    def __init__(self) -> None:
        self.rich_messages: list[dict[str, Any]] = []
        self.text_messages: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []
        self.reply_markup_edits: list[dict[str, Any]] = []
        self.copied_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []

    def send_rich_message(
        self,
        chat_id: int | str,
        markdown: str,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        message = {
            "chat_id": chat_id,
            "markdown": markdown,
            "reply_markup": reply_markup,
            "disable_notification": disable_notification,
        }
        self.rich_messages.append(message)
        return {"message_id": len(self.rich_messages), "chat": {"id": chat_id}}

    def copy_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.copied_messages.append(
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            }
        )
        return {"message_id": len(self.copied_messages)}

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        markdown: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        self.edited_messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "markdown": markdown,
                "reply_markup": reply_markup,
            }
        )
        return True

    def send_text(self, chat_id: int | str, text: str, reply_to_message_id: int | None = None) -> dict[str, Any]:
        self.text_messages.append({"chat_id": chat_id, "text": text, "reply_to_message_id": reply_to_message_id})
        return {"message_id": 99}

    def answer_callback_query(self, callback_query_id: str, text: str | None = None, show_alert: bool = False) -> bool:
        self.callback_answers.append({"id": callback_query_id, "text": text, "show_alert": show_alert})
        return True

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        self.reply_markup_edits.append({"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})
        return True


class BotTests(unittest.TestCase):
    def test_message_creates_preview_and_callback_sends_to_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 1,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "# Hello",
                }
            )

            self.assertEqual(len(client.rich_messages), 1)
            self.assertEqual(client.rich_messages[0]["chat_id"], 42)
            keyboard = client.rich_messages[0]["reply_markup"]["inline_keyboard"][0]
            send_data = keyboard[0]["callback_data"]

            bot.handle_callback_query(
                {
                    "id": "cb1",
                    "from": {"id": 42},
                    "data": send_data,
                    "message": {"message_id": 2, "chat": {"id": 42}},
                }
            )

            self.assertEqual(len(client.rich_messages), 2)
            self.assertEqual(client.rich_messages[1]["chat_id"], -100123)
            self.assertEqual(client.rich_messages[1]["markdown"], "# Hello")
            self.assertEqual(client.callback_answers[-1]["text"], "已发送到频道。")
            self.assertEqual(client.reply_markup_edits[-1]["reply_markup"], {"inline_keyboard": []})

    def test_unauthorized_user_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message({"message_id": 1, "from": {"id": 77}, "chat": {"id": 77}, "text": "# Nope"})

            self.assertEqual(client.rich_messages, [])
            self.assertEqual(client.text_messages[0]["text"], "无权限使用此 bot。")

    def test_edit_flow_copies_original_previews_replacement_and_updates_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-1001326206584,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/edit https://t.me/c/1326206584/777",
                }
            )

            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(client.copied_messages[0]["from_chat_id"], -1001326206584)
            self.assertEqual(client.copied_messages[0]["message_id"], 777)
            edit_data = client.copied_messages[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

            bot.handle_callback_query(
                {
                    "id": "cb-edit",
                    "from": {"id": 42},
                    "data": edit_data,
                    "message": {"message_id": 11, "chat": {"id": 42}},
                }
            )

            self.assertEqual(client.text_messages[-1]["text"], "请发送新内容来替换消息")
            self.assertEqual(client.reply_markup_edits[-1]["reply_markup"], {"inline_keyboard": []})

            bot.handle_message(
                {
                    "message_id": 12,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "# Updated\n\nnew body",
                }
            )

            self.assertEqual(len(client.rich_messages), 1)
            self.assertEqual(client.rich_messages[0]["markdown"], "# Updated\n\nnew body")
            update_data = client.rich_messages[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

            bot.handle_callback_query(
                {
                    "id": "cb-update",
                    "from": {"id": 42},
                    "data": update_data,
                    "message": {"message_id": 13, "chat": {"id": 42}},
                }
            )

            self.assertEqual(
                client.edited_messages[-1],
                {
                    "chat_id": -1001326206584,
                    "message_id": 777,
                    "markdown": "# Updated\n\nnew body",
                    "reply_markup": None,
                },
            )
            self.assertEqual(client.callback_answers[-1]["text"], "已更新频道消息。")

    def test_edit_command_rejects_wrong_private_channel_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-1001326206584,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/edit https://t.me/c/999/777",
                }
            )

            self.assertEqual(client.copied_messages, [])
            self.assertEqual(client.text_messages[-1]["text"], "消息链接所属频道与当前配置频道不一致。")


if __name__ == "__main__":
    unittest.main()
