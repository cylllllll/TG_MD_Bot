from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from md_channel_bot.bot import BOT_COMMANDS, MarkdownChannelBot
from md_channel_bot.config import Settings
from md_channel_bot.store import PendingStore
from md_channel_bot.telegram_api import PublicChannelMessage, TelegramAPIError


class FakeClient:
    def __init__(self) -> None:
        self.rich_messages: list[dict[str, Any]] = []
        self.text_messages: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []
        self.reply_markup_edits: list[dict[str, Any]] = []
        self.copied_messages: list[dict[str, Any]] = []
        self.forwarded_messages: list[dict[str, Any]] = []
        self.forward_message_results: dict[int, dict[str, Any]] = {}
        self.deleted_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.commands: list[dict[str, str]] | None = None
        self.public_latest_message_ids: dict[str, int] = {}
        self.public_latest_messages: dict[str, PublicChannelMessage] = {}
        self.public_latest_requests: list[str] = []
        self.chats: dict[int | str, dict[str, Any]] = {}

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

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        return True

    def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        if chat_id not in self.chats:
            raise TelegramAPIError("getChat", None, "not configured")
        return self.chats[chat_id]

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

    def forward_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        disable_notification: bool = True,
    ) -> dict[str, Any]:
        self.forwarded_messages.append(
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            }
        )
        result = self.forward_message_results.get(message_id)
        if result is None:
            raise TelegramAPIError("forwardMessage", None, "not configured")
        return result

    def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return True

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

    def send_text(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.text_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
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

    def get_public_channel_latest_message_id(self, channel: str) -> int:
        return self.get_public_channel_latest_message(channel).message_id

    def get_public_channel_latest_message(self, channel: str) -> PublicChannelMessage:
        self.public_latest_requests.append(channel)
        if channel in self.public_latest_messages:
            return self.public_latest_messages[channel]
        if channel not in self.public_latest_message_ids:
            raise TelegramAPIError("getPublicChannelLatestMessageId", None, "not configured")
        return PublicChannelMessage(message_id=self.public_latest_message_ids[channel])


class BotTests(unittest.TestCase):
    def test_register_bot_commands_includes_edit_argument_hint(self) -> None:
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

            bot.register_bot_commands()

            self.assertEqual(client.commands, BOT_COMMANDS)
            commands_by_name = {item["command"]: item for item in client.commands or []}
            self.assertIn("URL 或消息 ID", commands_by_name["edit"]["description"])
            self.assertIn("最新消息", commands_by_name["recent"]["description"])

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
            self.assertEqual(store.get_published_markdown(-100123, 2), "# Hello")
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
            store.record_published_message(-1001326206584, 777, "# Original\n\nold body")
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/edit https://t.me/c/1326206584/777",
                }
            )

            self.assertEqual(client.forwarded_messages[-1]["message_id"], 777)
            self.assertEqual(len(client.copied_messages), 1)
            edit_data = client.copied_messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

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
            self.assertEqual(store.get_published_markdown(-1001326206584, 777), "# Updated\n\nnew body")

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

    def test_edit_command_without_recorded_markdown_sends_no_missing_original_notice(self) -> None:
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
            self.assertEqual(client.text_messages, [])

    def test_edit_command_reads_rich_message_source_from_forwarded_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-1001326206584,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.forward_message_results[777] = {
                "message_id": 88,
                "rich_message": {
                    "blocks": [
                        {
                            "type": "blockquote",
                            "blocks": [
                                {"type": "paragraph", "text": "第一段"},
                                {"type": "paragraph", "text": "第二段"},
                            ],
                            "credit": {
                                "type": "url",
                                "text": "Source",
                                "url": "https://example.com",
                            },
                        }
                    ]
                },
            }
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

            self.assertEqual(client.forwarded_messages[-1]["message_id"], 777)
            self.assertEqual(client.deleted_messages[-1], {"chat_id": 42, "message_id": 88})
            self.assertEqual(client.copied_messages, [])
            self.assertEqual(
                client.text_messages[-1]["text"],
                '<pre>&lt;blockquote&gt;第一段\n\n第二段\n&lt;cite&gt;&lt;a href="https://example.com"&gt;Source&lt;/a&gt;&lt;/cite&gt;&lt;/blockquote&gt;</pre>',
            )
            self.assertEqual(client.text_messages[-1]["parse_mode"], "HTML")
            self.assertIsNotNone(client.text_messages[-1]["reply_markup"])
            self.assertIsNone(store.get_published_markdown(-1001326206584, 777))

    def test_edit_command_copies_forwarded_non_rich_message_with_caption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-1001326206584,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.forward_message_results[6586] = {
                "message_id": 88,
                "photo": [{"file_id": "photo-id", "width": 1179, "height": 662}],
                "caption": "普通图片消息说明",
            }
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/edit https://t.me/c/1326206584/6586",
                }
            )

            self.assertEqual(client.forwarded_messages[-1]["message_id"], 6586)
            self.assertEqual(client.deleted_messages[-1], {"chat_id": 42, "message_id": 88})
            self.assertEqual(client.text_messages, [])
            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(client.copied_messages[0]["message_id"], 6586)
            self.assertIsNotNone(client.copied_messages[0]["reply_markup"])
            self.assertIsNone(store.get_published_markdown(-1001326206584, 6586))

    def test_edit_command_ignores_recorded_markdown_when_forwarded_message_is_not_rich(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-1001326206584,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.forward_message_results[6586] = {
                "message_id": 88,
                "photo": [{"file_id": "photo-id", "width": 1179, "height": 662}],
                "caption": "普通图片消息说明",
            }
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            store.record_published_message(-1001326206584, 6586, "旧版本误缓存的 caption")
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/edit https://t.me/c/1326206584/6586",
                }
            )

            self.assertEqual(client.forwarded_messages[-1]["message_id"], 6586)
            self.assertEqual(client.deleted_messages[-1], {"chat_id": 42, "message_id": 88})
            self.assertEqual(client.text_messages, [])
            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(store.get_published_markdown(-1001326206584, 6586), "旧版本误缓存的 caption")

    def test_recent_command_starts_edit_for_latest_recorded_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            store.record_published_message(-100123, 12, "# Older")
            store.record_published_message(-100123, 15, "# Latest")
            store.record_published_message(-100123, 12, "# Older edited")
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/recent",
                }
            )

            self.assertEqual(
                client.text_messages[0]["text"],
                "读取当前频道信息失败：not configured 已改用本地记录的最近消息 ID：15",
            )
            self.assertEqual(client.forwarded_messages[-1]["message_id"], 15)
            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(client.copied_messages[-1]["message_id"], 15)
            self.assertIsNotNone(client.copied_messages[-1]["reply_markup"])

    def test_recent_command_fetches_latest_id_from_configured_public_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.chats[-100123] = {"id": -100123, "username": "PlayStationNewssss"}
            client.public_latest_message_ids["PlayStationNewssss"] = 18
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            store.record_published_message(-100123, 18, "# Latest from web")
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/recent",
                }
            )

            self.assertEqual(client.public_latest_requests, ["PlayStationNewssss"])
            self.assertEqual(client.forwarded_messages[-1]["message_id"], 18)
            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(client.copied_messages[-1]["message_id"], 18)
            self.assertIsNotNone(client.copied_messages[-1]["reply_markup"])

    def test_recent_command_uses_public_html_when_original_markdown_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.chats[-100123] = {"id": -100123, "username": "PlayStationNewssss"}
            client.public_latest_messages["PlayStationNewssss"] = PublicChannelMessage(
                message_id=18,
                html='<blockquote>body\n<cite><a href="https://example.com">Source</a></cite></blockquote>',
            )
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/recent",
                }
            )

            self.assertEqual(client.copied_messages, [])
            self.assertEqual(
                client.text_messages[0]["text"],
                '<pre>&lt;blockquote&gt;body\n&lt;cite&gt;&lt;a href="https://example.com"&gt;Source&lt;/a&gt;&lt;/cite&gt;&lt;/blockquote&gt;</pre>',
            )
            self.assertEqual(client.text_messages[0]["parse_mode"], "HTML")
            self.assertIsNotNone(client.text_messages[0]["reply_markup"])

    def test_recent_command_ignores_public_html_when_forwarded_message_is_not_rich(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                bot_token="token",
                allowed_user_ids={42},
                channel_id=-100123,
                pending_store_path=str(Path(tmp_dir) / "pending.json"),
            )
            client = FakeClient()
            client.chats[-100123] = {"id": -100123, "username": "PlayStationNewssss"}
            client.public_latest_messages["PlayStationNewssss"] = PublicChannelMessage(
                message_id=6586,
                html="<blockquote>网页兜底内容</blockquote>",
            )
            client.forward_message_results[6586] = {
                "message_id": 88,
                "photo": [{"file_id": "photo-id", "width": 1179, "height": 662}],
                "caption": "普通图片消息说明",
            }
            store = PendingStore(settings.pending_store_path, ttl_seconds=60)
            bot = MarkdownChannelBot(settings, client, store)  # type: ignore[arg-type]

            bot.handle_message(
                {
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/recent",
                }
            )

            self.assertEqual(client.public_latest_requests, ["PlayStationNewssss"])
            self.assertEqual(client.forwarded_messages[-1]["message_id"], 6586)
            self.assertEqual(client.deleted_messages[-1], {"chat_id": 42, "message_id": 88})
            self.assertEqual(client.text_messages, [])
            self.assertEqual(len(client.copied_messages), 1)
            self.assertEqual(client.copied_messages[0]["message_id"], 6586)
            self.assertIsNotNone(client.copied_messages[0]["reply_markup"])

    def test_recent_command_requires_recorded_message(self) -> None:
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
                    "message_id": 10,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "/recent",
                }
            )

            self.assertEqual(client.copied_messages, [])
            self.assertEqual(
                client.text_messages[-1]["text"],
                "读取当前频道信息失败：not configured 也没有本地已记录消息可兜底，请用 /edit 消息ID。",
            )


if __name__ == "__main__":
    unittest.main()
