# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from src.context.spill import SpillStore


class SpillStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "spill"
        self.store = SpillStore(self.root, max_files=20, max_total_mb=16)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        text = "论文正文" * 100
        ref = self.store.save_text(text, namespace="chat")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.chars, len(text))
        self.assertEqual(self.store.load_text(ref.locator), text)

    def test_content_addressed_dedupe(self):
        text = "重复内容" * 50
        a = self.store.save_text(text, namespace="chat")
        b = self.store.save_text(text, namespace="chat")
        self.assertEqual(a.locator, b.locator)
        files = [p for p in self.root.rglob("*.txt")]
        self.assertEqual(len(files), 1)

    def test_namespace_sanitized(self):
        ref = self.store.save_text("x" * 10, namespace="../../evil")
        self.assertIsNotNone(ref)
        self.assertNotIn("..", ref.locator)
        self.assertTrue(self.store.load_text(ref.locator) == "x" * 10)

    def test_reject_bad_locators(self):
        self.assertIsNone(self.store.load_text("../../etc/passwd"))
        self.assertIsNone(self.store.load_text("chat/202610/zzzz.txt"))
        self.assertIsNone(self.store.load_text(""))

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.store.load_text("chat/202610/" + "a" * 20 + ".txt"))

    def test_gc_caps_file_count(self):
        for i in range(40):
            self.store.save_text(f"内容-{i}-" + "字" * 20)
        self.store.gc()
        files = list(self.root.rglob("*.txt"))
        self.assertLessEqual(len(files), int(self.store.max_files * 0.8) + 1)

    def test_fail_open_on_unwritable_root(self):
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        store = SpillStore(blocker)
        self.assertIsNone(store.save_text("内容"))

    def test_disabled_returns_none(self):
        store = SpillStore(self.root, enabled=False)
        self.assertIsNone(store.save_text("内容"))

    def test_stats(self):
        self.store.save_text("统计" * 20)
        stats = self.store.stats()
        self.assertGreaterEqual(stats["files"], 1)
        self.assertGreater(stats["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
