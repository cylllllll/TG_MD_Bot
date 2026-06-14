from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from md_channel_bot.store import PendingStore


class PendingStoreTests(unittest.TestCase):
    def test_create_get_delete_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = str(Path(tmp_dir) / "pending.json")
            store = PendingStore(path, ttl_seconds=60)

            item = store.create(user_id=123, channel_id=-1001, markdown="# Title")

            self.assertEqual(store.get(item.draft_id, user_id=123), item)
            self.assertIsNone(store.get(item.draft_id, user_id=456))

            reloaded = PendingStore(path, ttl_seconds=60)
            self.assertEqual(reloaded.get(item.draft_id, user_id=123), item)

            self.assertTrue(reloaded.delete(item.draft_id))
            self.assertIsNone(reloaded.get(item.draft_id, user_id=123))

    def test_edit_session_and_edit_draft_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = str(Path(tmp_dir) / "pending.json")
            store = PendingStore(path, ttl_seconds=60)

            session = store.create_edit_session(user_id=123, channel_id=-1001, message_id=55)
            updated_session = store.set_edit_session_stage(session.session_id, "awaiting_content")
            self.assertIsNotNone(updated_session)
            draft = store.create(
                user_id=123,
                channel_id=-1001,
                markdown="# New",
                mode="edit",
                target_message_id=55,
            )

            reloaded = PendingStore(path, ttl_seconds=60)
            reloaded_session = reloaded.get_active_edit_session(123, stage="awaiting_content")
            self.assertIsNotNone(reloaded_session)
            self.assertEqual(reloaded_session.message_id, 55)

            reloaded_draft = reloaded.get(draft.draft_id, user_id=123)
            self.assertIsNotNone(reloaded_draft)
            self.assertEqual(reloaded_draft.mode, "edit")
            self.assertEqual(reloaded_draft.target_message_id, 55)


if __name__ == "__main__":
    unittest.main()
