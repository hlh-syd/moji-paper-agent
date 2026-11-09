# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from src.config import AppConfig
from src.context.assemble import ContextEngine
from src.context.spill import SpillStore

SUMMARY = {
    "primary_request": "改写第三章并保持字数",
    "key_concepts": "AI 生成率、AIGC 概率",
    "artifacts": "projects/script.outline.json、结果节",
    "decisions": "采用分节改写，不做整篇替换",
    "errors_fixes": "9B 输出丢失数字 → 已加锁字校验",
    "pending": "第四章待改写",
    "current_work": "正在改写第三章第 2 段",
    "next_step": "继续改写并要求字数 ±10%",
}


def _pairs(n, user_chars=80, assistant_chars=160):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"第{i}问：" + "问" * user_chars})
        out.append({"role": "assistant", "content": f"第{i}答：" + "答" * assistant_chars})
    return out


class HistoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.shadow_dir = base / "context"
        self.spill = SpillStore(base / "spill")
        self.messages = {}
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self, **ctx):
        cfg = AppConfig()
        cfg.context.history_budget_tokens = ctx.pop("history_budget_tokens", 1500)
        cfg.context.compaction.keep_recent_messages = ctx.pop(
            "keep_recent_messages", 4)
        cfg.context.compaction.min_history_tokens = ctx.pop("min_history_tokens", 100)
        cfg.context.compaction.min_interval_seconds = 0
        cfg.context.compaction.enabled = ctx.pop("compaction_enabled", True)
        for key, value in ctx.items():
            setattr(cfg.context.compaction, key, value)
        return cfg

    def _engine(self, cfg, summarize=None):
        async def default_summary(text, prev):
            self.calls.append((len(text), len(prev)))
            return dict(SUMMARY)

        return ContextEngine(
            cfg,
            messages_provider=lambda key: self.messages.get(key, []),
            summarize_fn=summarize or default_summary,
            spill_store=self.spill,
            shadow_base_dir=self.shadow_dir,
        )

    async def test_empty_history(self):
        engine = self._engine(self._cfg())
        text, stats = await engine.build_history_block("s1")
        self.assertEqual(text, "")
        self.assertEqual(stats.mode, "empty")

    async def test_verbatim_under_budget(self):
        self.messages["s1"] = _pairs(1)
        engine = self._engine(self._cfg(history_budget_tokens=6000))
        text, stats = await engine.build_history_block("s1")
        self.assertEqual(stats.mode, "verbatim")
        self.assertIn("第0问", text)
        self.assertIn("第0答", text)

    async def test_compaction_creates_shadow(self):
        self.messages["s1"] = _pairs(20)
        engine = self._engine(self._cfg())
        text, stats = await engine.build_history_block("s1")
        self.assertEqual(stats.mode, "compacted")
        self.assertTrue(stats.compacted)
        self.assertIn("压缩摘要", text)
        self.assertGreater(stats.shadowed, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertLessEqual(stats.tokens, stats.budget)

    async def test_shadow_survives_restart(self):
        self.messages["s1"] = _pairs(20)
        cfg = self._cfg()
        await self._engine(cfg).build_history_block("s1")
        self.assertEqual(len(self.calls), 1)

        # 模拟服务重启：新引擎、同一 sidecar 目录、计数器归零
        before = len(self.calls)
        engine2 = self._engine(cfg)
        text, stats = await engine2.build_history_block("s1")
        self.assertEqual(stats.mode, "compacted")
        self.assertEqual(len(self.calls), before)   # 未重复调用压缩子代理
        self.assertIn("压缩摘要", text)

    async def test_compaction_failure_falls_back(self):
        self.messages["s1"] = _pairs(20)
        async def failing(text, prev):
            raise RuntimeError("model down")
        engine = self._engine(self._cfg(), summarize=failing)
        text, stats = await engine.build_history_block("s1")
        self.assertIn(stats.mode, ("pruned", "truncated"))
        self.assertFalse(stats.compacted)
        self.assertLessEqual(stats.tokens, stats.budget)

    async def test_invalid_summary_falls_back(self):
        self.messages["s1"] = _pairs(20)
        async def empty(text, prev):
            return None
        engine = self._engine(self._cfg(), summarize=empty)
        _text, stats = await engine.build_history_block("s1")
        self.assertFalse(stats.compacted)

    async def test_pending_question_excluded(self):
        pending = "这是当前问题"
        self.messages["s1"] = _pairs(1) + [{"role": "user", "content": pending}]
        engine = self._engine(self._cfg(history_budget_tokens=6000))
        text, _stats = await engine.build_history_block("s1", pending_text=pending)
        self.assertNotIn(pending, text)
        self.assertIn("第0问", text)

    async def test_compaction_disabled_uses_deterministic(self):
        self.messages["s1"] = _pairs(20)
        engine = self._engine(self._cfg(compaction_enabled=False))
        text, stats = await engine.build_history_block("s1")
        self.assertIn(stats.mode, ("pruned", "truncated"))
        self.assertEqual(self.calls, [])
        self.assertLessEqual(stats.tokens, stats.budget)
        self.assertIn("最近对话记录", text)

    async def test_manual_compact_now(self):
        self.messages["s1"] = _pairs(20)
        engine = self._engine(self._cfg())
        result = await engine.compact_now("s1")
        self.assertTrue(result["ok"])
        self.assertGreater(result["shadow"]["through_index"], 0)

    async def test_history_stats_reports_compacted(self):
        self.messages["s1"] = _pairs(20)
        cfg = self._cfg()
        engine = self._engine(cfg)
        await engine.build_history_block("s1")
        info = engine.history_stats("s1")
        self.assertEqual(info["mode"], "compacted")
        self.assertIsNotNone(info["shadow"])
        self.assertGreater(info["soft_limit"], 0)

    async def test_thirty_turn_conversation_stays_bounded(self):
        self.messages["s1"] = _pairs(30, user_chars=120, assistant_chars=240)
        engine = self._engine(self._cfg())
        text, stats = await engine.build_history_block("s1")
        self.assertLessEqual(stats.tokens, stats.budget)
        self.assertIn("压缩摘要", text)


if __name__ == "__main__":
    unittest.main()
