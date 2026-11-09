# -*- coding: utf-8 -*-
import unittest

from src.context.prune import (PRUNE_MARKER_TMPL, fence_intervals,
                               prune_message_content, prune_text)
from src.context.tokens import estimate_tokens


def _cn(n: int, ch: str = "甲") -> str:
    return ch * n


class PruneBasicsTest(unittest.TestCase):
    def test_empty_and_short_untouched(self):
        self.assertEqual(prune_text("", 100).text, "")
        short = "很短的文本"
        r = prune_text(short, 100)
        self.assertFalse(r.pruned)
        self.assertEqual(r.text, short)
        self.assertEqual(r.omitted_chars, 0)

    def test_long_text_is_pruned(self):
        text = "\n".join(_cn(80) for _ in range(200))
        r = prune_text(text, 400)
        self.assertTrue(r.pruned)
        self.assertIn("省略", r.text)
        self.assertGreater(r.omitted_chars, 0)
        self.assertLess(len(r.text), len(text))
        self.assertLessEqual(estimate_tokens(r.text), 560)

    def test_head_and_tail_both_kept(self):
        text = _cn(3000, "头") + _cn(3000, "中") + _cn(2000, "尾")
        r = prune_text(text, 300, snap_window=0)
        self.assertTrue(r.pruned)
        self.assertIn("头", r.text[:50])
        self.assertIn("尾", r.text[-50:])
        self.assertNotIn("中中中中中中中中中中中中中中中中", r.text)

    def test_marker_reports_omitted_count(self):
        text = "\n".join(_cn(100) for _ in range(100))
        r = prune_text(text, 200, snap_window=0)
        self.assertTrue(r.pruned)
        self.assertIn(str(r.omitted_chars), r.text)

    def test_tiny_budget_falls_back(self):
        text = _cn(5000)
        r = prune_text(text, 1)
        # 预算小到装不下省略标记时，宁可原样返回也不返回空
        self.assertEqual(r.text, text)


class PruneFenceTest(unittest.TestCase):
    def _build(self):
        head = "\n".join(_cn(20, "甲") for _ in range(50))
        code = "```python\n" + "print(123)\n" * 200 + "```"
        tail = "\n".join(_cn(20, "乙") for _ in range(50))
        return head + "\n" + code + "\n" + tail

    def test_fence_intervals(self):
        text = "a\n```\ncode\n```\nb"
        spans = fence_intervals(text)
        self.assertEqual(len(spans), 1)
        s, e = spans[0]
        self.assertEqual(text[s:s + 3], "```")
        self.assertEqual(text[e - 3:e], "```")

    def test_dollar_math_intervals(self):
        text = "前\n$$\na=b\n$$\n后"
        self.assertEqual(len(fence_intervals(text)), 1)

    def test_bracket_math_intervals(self):
        text = "前\n\\[\nx=y\n\\]\n后"
        self.assertEqual(len(fence_intervals(text)), 1)

    def test_head_does_not_split_code_fence(self):
        text = self._build()
        r = prune_text(text, 1700, snap_window=40)
        self.assertTrue(r.pruned)
        head = r.text.split("【此处省略")[0]
        self.assertEqual(head.count("```") % 2, 0)
        self.assertNotIn("```python", head or "")

    def test_protect_disabled_still_safe(self):
        text = self._build()
        r = prune_text(text, 1700, snap_window=40, protect_fences=False)
        self.assertIsInstance(r.text, str)
        self.assertGreater(len(r.text), 0)


class PruneBoundaryTest(unittest.TestCase):
    def test_crlf_lines(self):
        text = "\r\n".join(_cn(60) for _ in range(100))
        r = prune_text(text, 300)
        self.assertTrue(r.pruned)
        self.assertIn("省略", r.text)

    def test_emoji_and_surrogate_pairs(self):
        text = "😀" * 4000
        r = prune_text(text, 200, snap_window=0)
        self.assertTrue(r.pruned)
        # 不能出现半个代理对（Python str 按码点切分，这里只验证可解码）
        r.text.encode("utf-8")

    def test_no_newline_hard_cut(self):
        text = _cn(8000)
        r = prune_text(text, 200, snap_window=0)
        self.assertTrue(r.pruned)
        self.assertIn("省略", r.text)

    def test_message_cap(self):
        content = _cn(3000)
        r = prune_message_content(content, 200)
        self.assertTrue(r.pruned)
        self.assertLessEqual(estimate_tokens(r.text), 300)

    def test_marker_template_contains_placeholder(self):
        self.assertIn("{omitted}", PRUNE_MARKER_TMPL)


if __name__ == "__main__":
    unittest.main()
