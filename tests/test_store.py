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


if __name__ == "__main__":
    unittest.main()
