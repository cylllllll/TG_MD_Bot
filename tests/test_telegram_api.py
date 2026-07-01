from __future__ import annotations

import unittest

from md_channel_bot.telegram_api import (
    _extract_public_channel_message_html,
    _extract_public_channel_message_ids,
    _parse_public_channel_username,
)


class TelegramAPITests(unittest.TestCase):
    def test_parse_public_channel_username_from_links(self) -> None:
        self.assertEqual(_parse_public_channel_username("https://t.me/PlayStationNewssss"), "PlayStationNewssss")
        self.assertEqual(_parse_public_channel_username("https://t.me/s/PlayStationNewssss"), "PlayStationNewssss")
        self.assertEqual(_parse_public_channel_username("@PlayStationNewssss"), "PlayStationNewssss")

    def test_extract_public_channel_message_ids(self) -> None:
        html = """
        <div class="tgme_widget_message" data-post="PlayStationNewssss/12"></div>
        <a href="https://t.me/PlayStationNewssss/15">open</a>
        <a href="/PlayStationNewssss/14">open</a>
        """

        self.assertEqual(_extract_public_channel_message_ids(html, "PlayStationNewssss"), [12, 14, 15])

    def test_extract_public_channel_message_html_preserves_rich_tags(self) -> None:
        html = """
        <div class="tgme_widget_message" data-post="PlayStationNewssss/18">
          <div class="tgme_widget_message_text js-message_text" dir="auto">
            <blockquote>正文<br><br><cite><a href="https://example.com">Source</a></cite></blockquote>
          </div>
        </div>
        """

        self.assertEqual(
            _extract_public_channel_message_html(html, "PlayStationNewssss", 18),
            '<blockquote>正文\n\n<cite><a href="https://example.com">Source</a></cite></blockquote>',
        )


if __name__ == "__main__":
    unittest.main()
