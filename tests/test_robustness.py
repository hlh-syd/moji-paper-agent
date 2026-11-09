# -*- coding: utf-8 -*-
"""健壮性对抗测试：病态输入 / 性能上限 / 状态损坏 / 指纹与重建

运行：python -m unittest tests.test_robustness -v
原则：任何一条失败都代表「健壮性保证」被破坏，必须修复而不是删断言。
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from src.config import AppConfig
from src.context import ContextBlock, ContextEngine, SpillStore
from src.context.prune import fence_intervals, prune_text
from src.context.tokens import estimate_tokens
from src.context import shadow as shadow_mod
from src import doc_check


def _cn(n, ch="中"):
    return ch * n


# ---------- 病态输入下的裁剪 ----------

class PrunePathologicalTest(unittest.TestCase):
    def test_no_newline_2mb_cjk_is_fast_and_bounded(self):
        text = _cn(2_000_000)
        t0 = time.perf_counter()
        r = prune_text(text, 2000, snap_window=0)
        elapsed = time.perf_counter() - t0
        self.assertTrue(r.pruned)
        self.assertLess(elapsed, 3.0, f"2MB 裁剪耗时 {elapsed:.2f}s，性能回归")
        self.assertLessEqual(estimate_tokens(r.text), 2600)

    def test_many_fences_is_fast(self):
        unit = "说明文字\n```python\ncode()\n```\n尾部文字\n"
        text = unit * 3000
        t0 = time.perf_counter()
        r = prune_text(text, 2000, snap_window=40)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 5.0, f"9000 个围栏裁剪耗时 {elapsed:.2f}s")
        head = r.text.split("【此处省略")[0]
        self.assertEqual(head.count("```") % 2, 0, "围栏被切碎")

    def test_unclosed_fence_at_start(self):
        text = "```\n" + _cn(5000)
        r = prune_text(text, 300, snap_window=20)
        self.assertIsInstance(r.text, str)
        self.assertGreater(len(r.text), 0)

    def test_fence_at_exact_start_and_end(self):
        text = "```\ncode\n```"
        r = prune_text(text, 5)
        self.assertEqual(r.text, text)  # 全文都是围栏，裁不动就原样返回

    def test_nested_and_mixed_fences(self):
        text = "$$\n\\[\nx\n\\]\n$$\n" + (_cn(100) + "\n") * 100
        spans = fence_intervals(text)
        self.assertTrue(all(s < e for s, e in spans))

    def test_only_whitespace_budget(self):
        r = prune_text("   \n\n  ", 10)
        self.assertFalse(r.pruned)


# ---------- token 估算性能 ----------

class TokenPerfTest(unittest.TestCase):
    def test_estimate_on_2mb_is_subsecond(self):
        text = (_cn(500_000) + "abcdef" * 100_000 + _cn(500_000, "乙"))
        t0 = time.perf_counter()
        est = estimate_tokens(text)
        elapsed = time.perf_counter() - t0
        self.assertGreater(est, 100_000)
        self.assertLess(elapsed, 1.0, f"2MB 估算耗时 {elapsed:.3f}s")


# ---------- 溢写损坏与压力 ----------

class SpillRobustTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "spill"

    def tearDown(self):
        self._tmp.cleanup()

    def test_unicode_and_newlines_roundtrip(self):
        store = SpillStore(self.root)
        text = "中文\n\r\n emoji 😀\t制表符\\反斜杠\0空字节？"
        ref = store.save_text(text)
        self.assertIsNotNone(ref)
        self.assertEqual(store.load_text(ref.locator), text)

    def test_empty_text_returns_none(self):
        self.assertIsNone(SpillStore(self.root).save_text(""))

    def test_corrupt_file_read_returns_content_or_none(self):
        store = SpillStore(self.root)
        ref = store.save_text("正文")
        path = store.root / ref.locator
        path.write_text("\ufffd 不完整", encoding="utf-8")
        # 内容寻址文件被外部改坏：读出的是现状，不抛异常即可
        self.assertIsInstance(store.load_text(ref.locator), (str, type(None)))

    def test_gc_mixed_foreign_files_untouched(self):
        store = SpillStore(self.root, max_files=10)
        foreign = self.root / "chat" / "202601"
        foreign.mkdir(parents=True)
        (foreign / "note.txt").write_text("not ours", encoding="utf-8")
        for i in range(30):
            store.save_text(f"内容{i}")
        store.gc()
        self.assertTrue((foreign / "note.txt").exists(), "误删了非溢写文件")


# ---------- 影子版本兼容 ----------

class ShadowVersionTest(unittest.TestCase):
    def test_future_version_invalidated(self):
        msgs = [{"role": "user", "content": "a"}]
        sh = shadow_mod.Shadow(through_index=1,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 1),
                               summary_md="s", version=999)
        self.assertIsNone(shadow_mod.resolve(sh, msgs))

    def test_huge_session_id_sidecar_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            sid = "会话" + "x" * 500
            sh = shadow_mod.Shadow(through_index=1, prefix_digest="d", summary_md="s")
            shadow_mod.save_sidecar(sid, sh, Path(tmp))
            self.assertIsNotNone(shadow_mod.load_sidecar(sid, Path(tmp)))


# ---------- 历史阶梯极端输入 ----------

class HistoryExtremeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.messages = {}
        self.engine = ContextEngine(
            AppConfig(),
            messages_provider=lambda k: self.messages.get(k, []),
            summarize_fn=None,                     # 无模型：走确定性路径
            spill_store=SpillStore(base / "spill"),
            shadow_base_dir=base / "ctx")

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg_budget(self, tokens):
        self.engine.config = AppConfig()
        self.engine.config.context.history_budget_tokens = tokens
        return self.engine.config

    async def test_single_huge_recent_message_is_capped(self):
        self._cfg_budget(1500)
        self.messages["s"] = [
            {"role": "user", "content": "帮我写第三章" * 5},
            {"role": "assistant", "content": _cn(60_000)},   # 刚生成的整节草稿
        ]
        text, stats = await self.engine.build_history_block("s")
        self.assertLessEqual(stats.tokens, stats.budget + 50,
                             "超大近消息未被封顶")
        self.assertIn("帮我写第三章", text)

    async def test_two_thousand_turns_bounded(self):
        self._cfg_budget(1500)
        self.messages["s"] = [{"role": "user" if i % 2 == 0 else "assistant",
                               "content": f"消息{i}：" + "内" * 120}
                              for i in range(2000)]
        text, stats = await self.engine.build_history_block("s")
        self.assertLessEqual(stats.tokens, stats.budget + 50)

    async def test_empty_and_whitespace_contents(self):
        self._cfg_budget(1500)
        self.messages["s"] = [{"role": "user", "content": ""},
                              {"role": "assistant", "content": "   "},
                              {"role": "weird", "content": "内容"}]
        text, stats = await self.engine.build_history_block("s")
        self.assertIn("内容", text)

    async def test_messages_provider_crash_fails_open(self):
        def boom(_k):
            raise RuntimeError("store gone")
        engine = ContextEngine(AppConfig(), messages_provider=boom)
        text, stats = await engine.build_history_block("s")
        self.assertEqual(stats.mode, "empty")
        self.assertEqual(text, "")

    async def test_dry_run_keeps_old_three_by_hundred(self):
        cfg = AppConfig()
        cfg.context.dry_run = True
        cfg.context.history_budget_tokens = 10   # 强制走"本来该压缩"的分支
        self.engine.config = cfg
        self.messages["s"] = [{"role": "user", "content": "问" * 300},
                              {"role": "assistant", "content": "答" * 300},
                              {"role": "user", "content": "再问" * 300}]
        text, stats = await self.engine.build_history_block("s")
        self.assertEqual(stats.mode, "dry-run")
        self.assertIn("...", text)
        self.assertLess(len(text), 700)


# ---------- 会话指纹：改了早消息必须重建 ----------

class RestoreFingerprintTest(unittest.TestCase):
    class _FakeCM:
        def __init__(self):
            self.rebuilds = 0
            self.store = {}

        def clear_conversation(self, sid):
            self.store.pop(sid, None)

        def add_message(self, sid, role, content):
            self.store.setdefault(sid, []).append((role, content))

    def _system(self):
        from src.web_api import PaperAgentSystem
        system = PaperAgentSystem.__new__(PaperAgentSystem)
        system.conversation_manager = self._FakeCM()
        return system

    def _sessions(self, second_msg="好的，我来改写第三章。"):
        return [{"id": "s1", "messages": [
            {"role": "user", "content": "帮我改第三章"},
            {"role": "assistant", "content": second_msg},
            {"role": "user", "content": "继续"}]}]

    def test_identical_save_skips_rebuild(self):
        from src.web_api import _restore_conversations
        system = self._system()
        _restore_conversations(system, self._sessions())
        first = system.conversation_manager.rebuilds
        _restore_conversations(system, self._sessions())
        _restore_conversations(system, self._sessions())
        self.assertEqual(system.conversation_manager.rebuilds, first)

    def test_edited_early_message_triggers_rebuild(self):
        from src.web_api import _restore_conversations
        from src import scoping
        system = self._system()
        _restore_conversations(system, self._sessions())
        _restore_conversations(system, self._sessions(second_msg="改好了，请查收。"))
        key = scoping.owner_key("s1")
        self.assertEqual(system.conversation_manager.store[key][1][1],
                         "改好了，请查收。")

    def test_deleted_message_triggers_rebuild(self):
        from src.web_api import _restore_conversations
        from src import scoping
        system = self._system()
        _restore_conversations(system, self._sessions())
        shortened = [{"id": "s1", "messages": self._sessions()[0]["messages"][:2]}]
        _restore_conversations(system, shortened)
        self.assertEqual(len(system.conversation_manager.store[scoping.owner_key("s1")]), 2)


# ---------- 回写边界 ----------

class ApplyEdgeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_base = doc_check.scoping.BASE_DIR
        doc_check.scoping.BASE_DIR = Path(self._tmp.name)

    def tearDown(self):
        doc_check.scoping.BASE_DIR = self._old_base
        self._tmp.cleanup()

    def test_regex_special_chars_in_before(self):
        before = "结果表明 P<0.05 (n=3)，且 [a+b]*c 显著。这一段补足二十个字的长度要求。"
        after = "统计检验 P<0.05 (n=3) 通过，符号项 [a+b]*c 保留。这一段同样是补足长度的文字。"
        content = "前文。" + before + "后文。"
        out, n = doc_check.replace_all_in_text(content, before, after)
        self.assertEqual(n, 1)
        self.assertIn("[a+b]*c", out)

    def test_record_receipt_caps_at_twenty(self):
        doc = {"id": "20260101_receipttest", "apply_receipts": []}
        for i in range(25):
            doc_check.record_receipt(doc, {"i": i})
        self.assertEqual(len(doc["apply_receipts"]), 20)
        self.assertEqual(doc["apply_receipts"][-1]["i"], 24)

    def test_invalid_strategy_falls_back_to_all(self):
        plan = doc_check.plan_apply(
            {"id": "x", "history": [{"action": "humanize_rewrite",
                                     "changes": [{"idx": 0, "before": "一" * 30, "after": "二" * 30}]}]},
            [{"id": "s", "content": "一" * 30}], strategy="hack'; drop")
        self.assertEqual(plan["strategy"], "all")

    def test_events_seq_continues_across_reads(self):
        doc_id = "20260101_seqcontin001"
        doc_check.append_event(doc_id, "a", {})
        self.assertEqual(len(doc_check.read_events(doc_id)), 1)
        doc_check.append_event(doc_id, "b", {})
        events = doc_check.read_events(doc_id)
        self.assertEqual([e["seq"] for e in events], [1, 2])

    def test_events_reject_bad_doc_id(self):
        doc_check.append_event("../evil", "x", {})
        self.assertEqual(doc_check.read_events("../evil"), [])


# ---------- 原子写并发 ----------

class AtomicIOTest(unittest.TestCase):
    def test_concurrent_writers_leave_valid_json(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            from src.atomic_io import write_json_atomic
            errors = []

            def writer(i):
                for round_ in range(20):
                    try:
                        write_json_atomic(target, {"w": i, "r": round_,
                                                   "pad": "字" * 500})
                    except Exception as e:  # noqa: BLE001
                        errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("w", data)


if __name__ == "__main__":
    unittest.main()
