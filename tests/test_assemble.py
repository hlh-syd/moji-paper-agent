# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from src.config import AppConfig
from src.context.assemble import ContextBlock, ContextEngine
from src.context.spill import SpillStore
from src.context.tokens import estimate_tokens


def _cn(n: int, ch: str = "中") -> str:
    return ch * n


def _cfg(**ctx) -> AppConfig:
    cfg = AppConfig()
    for key, value in ctx.items():
        setattr(cfg.context, key, value)
    return cfg


class AssembleBlocksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.spill = SpillStore(Path(self._tmp.name) / "spill")

    def tearDown(self):
        self._tmp.cleanup()

    def _engine(self, cfg) -> ContextEngine:
        return ContextEngine(cfg, messages_provider=lambda _k: [],
                             spill_store=self.spill)

    def test_under_budget_untouched(self):
        engine = self._engine(_cfg())
        blocks = [ContextBlock("attachments", "# 注入文件", "内容" * 10, budget=5000),
                  ContextBlock("knowledge", "# 知识库参考", "参考" * 10, budget=5000)]
        text, stats = engine.assemble_blocks(blocks)
        self.assertIn("注入文件", text)
        self.assertIn("知识库参考", text)
        self.assertEqual(stats.tokens_before, stats.tokens_after)
        self.assertFalse(any(b.pruned for b in stats.blocks))

    def test_oversized_block_pruned_and_spilled(self):
        engine = self._engine(_cfg())
        block = ContextBlock("file_drops", "# 拖入的文件", "\n".join(
            _cn(120) for _ in range(100)), budget=300)
        text, stats = engine.assemble_blocks([block])
        st = stats.blocks[0]
        self.assertTrue(st.pruned)
        self.assertIn("省略", text)
        self.assertIsNotNone(st.locator)
        self.assertIsNotNone(self.spill.load_text(st.locator))

    def test_drop_lowest_priority_first(self):
        engine = self._engine(_cfg())
        raw = _cn(1300)
        blocks = [
            ContextBlock("project_body", "# 项目主体", raw, priority=0, prunable=False),
            ContextBlock("selections", "# 选段", raw, priority=1, prunable=False),
            ContextBlock("file_drops", "# 拖入文件", raw, priority=2, prunable=False),
            ContextBlock("folders", "# 文件夹", raw, priority=3, prunable=False),
        ]
        text, stats = engine.assemble_blocks(blocks, soft_limit=2500)
        self.assertIn("folders", stats.dropped)
        self.assertNotIn("project_body", stats.dropped)
        self.assertIn("项目主体", text)
        self.assertIn("未注入", text)

    def test_priority_zero_never_dropped(self):
        engine = self._engine(_cfg())
        raw = _cn(1300)
        blocks = [ContextBlock("project_body", "# 项目主体", raw, priority=0),
                  ContextBlock("folders", "# 文件夹", raw, priority=3)]
        text, stats = engine.assemble_blocks(blocks, soft_limit=100)
        self.assertNotIn("project_body", stats.dropped)
        self.assertIn("项目主体", text)

    def test_prunable_false_blocks_only_dropped(self):
        engine = self._engine(_cfg())
        raw = _cn(1300)
        blocks = [ContextBlock("project_body", "# 项目主体", raw, priority=0,
                               prunable=False),
                  ContextBlock("folders", "# 文件夹", raw, priority=3,
                               prunable=False)]
        _text, stats = engine.assemble_blocks(blocks, soft_limit=1500)
        self.assertEqual(stats.dropped, ["folders"])

    def test_dry_run_returns_original_text(self):
        engine = self._engine(_cfg(dry_run=True))
        original = "\n".join(_cn(120) for _ in range(100))
        block = ContextBlock("file_drops", "# 拖入的文件", original, budget=300)
        text, stats = engine.assemble_blocks([block])
        self.assertIn(original, text)
        self.assertTrue(stats.dry_run)
        self.assertTrue(stats.blocks[0].pruned)

    def test_disabled_is_passthrough(self):
        engine = self._engine(_cfg(enabled=False))
        original = _cn(20000)
        block = ContextBlock("file_drops", "# 拖入的文件", original, budget=100)
        text, stats = engine.assemble_blocks([block])
        self.assertIn(original, text)
        self.assertFalse(stats.blocks)


if __name__ == "__main__":
    unittest.main()
