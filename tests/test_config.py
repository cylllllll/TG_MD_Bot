from __future__ import annotations

import unittest

from md_channel_bot.config import ConfigError, load_settings, parse_chat_id, parse_int_set


class ConfigTests(unittest.TestCase):
    def test_load_settings_from_env_mapping(self) -> None:
        settings = load_settings(
            {
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "TELEGRAM_ALLOWED_USER_IDS": "11, 22",
                "TELEGRAM_CHANNEL_ID": "-1001234567890",
            }
        )

        self.assertEqual(settings.bot_token, "123:abc")
        self.assertEqual(settings.allowed_user_ids, {11, 22})
        self.assertEqual(settings.channel_id, -1001234567890)

    def test_parse_channel_username(self) -> None:
        self.assertEqual(parse_chat_id("@example_channel", "TELEGRAM_CHANNEL_ID"), "@example_channel")

    def test_reject_empty_allowed_users(self) -> None:
        with self.assertRaises(ConfigError):
            parse_int_set("", "TELEGRAM_ALLOWED_USER_IDS")


if __name__ == "__main__":
    unittest.main()
