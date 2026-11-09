# -*- coding: utf-8 -*-
"""状态库纯逻辑单测（不触 MySQL）：拆分/规范化/时间解析 + 文件回退路径回环"""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src import scoping, workspace_store
from src.state_store import _norm_messages, _split_state, _to_dt, _to_iso


class SplitStateTest(unittest.TestCase):
    def test_strips_sessions_and_rev(self):
        prefs, sessions = _split_state({
            "rev": 7, "sessions": [{"id": "chat_a", "messages": []}],
            "theme": "light", "font_size": 18, "active_project": "P",
        })
        self.assertEqual(prefs, {"theme": "light", "font_size": 18,
                                 "active_project": "P"})
        self.assertEqual([s["id"] for s in sessions], ["chat_a"])

    def test_filters_invalid_sessions(self):
        _, sessions = _split_state({
            "sessions": [{"id": "ok"}, "junk", None, {}, {"no_id": 1}],
        })
        self.assertEqual([s["id"] for s in sessions], ["ok"])

    def test_empty_state(self):
        prefs, sessions = _split_state(None)
        self.assertEqual(prefs, {})
        self.assertEqual(sessions, [])


class NormMessagesTest(unittest.TestCase):
    def test_seq_and_coercion(self):
        rows = _norm_messages([
            {"role": "user", "content": "你好"},
            "junk",
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": "答",
             "attachments": ["a.md", "b.docx"]},
        ])
        self.assertEqual(rows[0], (0, "user", "你好", None))
        self.assertEqual(rows[1], (1, "assistant", "", None))
        self.assertEqual(rows[2], (2, "assistant", "答", '["a.md", "b.docx"]'))
        self.assertEqual(len(rows), 3)  # "junk" 非法项被跳过，seq 连续无空洞

    def test_empty(self):
        self.assertEqual(_norm_messages(None), [])


class TimeTest(unittest.TestCase):
    def test_epoch_ms(self):
        d = _to_dt(1700000000000)
        self.assertIsInstance(d, datetime)
        self.assertEqual(_to_iso(d), d.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_garbage_returns_none(self):
        self.assertIsNone(_to_dt(None))
        self.assertIsNone(_to_dt(""))
        self.assertIsNone(_to_dt("not-a-date"))
        self.assertIsNone(_to_dt(9e15))  # 超界时间戳
        self.assertEqual(_to_iso("2024-05-06T07:08:09"), "2024-05-06T07:08:09")


class FileFallbackTest(unittest.TestCase):
    """MySQL 不可达时的降级路径：state.json 读写回环（BASE_DIR 指向临时目录）"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_base = scoping.BASE_DIR
        scoping.BASE_DIR = Path(self._tmp.name)
        scoping.set_request_owner("tester")

    def tearDown(self):
        scoping.BASE_DIR = self._old_base
        scoping.set_request_owner("")
        self._tmp.cleanup()

    def test_roundtrip(self):
        state = {"rev": 3, "theme": "light",
                 "sessions": [{"id": "chat_1", "name": "会话", "messages": []}]}
        saved_to = workspace_store._save_state_file(state)
        self.assertTrue(saved_to.endswith("tester.state.json"))
        loaded = workspace_store._load_state_file()
        self.assertEqual(loaded["rev"], 3)
        self.assertEqual(loaded["sessions"][0]["id"], "chat_1")
        self.assertEqual(json.loads(Path(saved_to).read_text(encoding="utf-8")),
                         state)

    def test_missing_returns_none(self):
        self.assertIsNone(workspace_store._load_state_file())


if __name__ == "__main__":
    unittest.main()
