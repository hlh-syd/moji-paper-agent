# -*- coding: utf-8 -*-
import unittest

from src.context.tokens import (chars_for_tokens, count_cjk, cjk_ratio,
                                estimate_message_tokens,
                                estimate_messages_tokens, estimate_tokens,
                                resolve_budget, resolve_model_window,
                                truncate_to_tokens)


class TokenEstimateTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_chinese_density(self):
        est = estimate_tokens("中" * 1000)
        self.assertGreaterEqual(est, 600)
        self.assertLessEqual(est, 900)

    def test_ascii_density(self):
        self.assertEqual(estimate_tokens("a" * 400), 100)

    def test_monotonic(self):
        self.assertGreater(estimate_tokens("中文abc" * 100),
                           estimate_tokens("中文abc" * 10))

    def test_mixed_density_is_linear(self):
        # 200 个汉字 + 300 个 ASCII 字 ≈ 200/1.3 + 300/4
        mixed = estimate_tokens(("中" * 200) + ("a" * 300))
        self.assertAlmostEqual(
            mixed,
            estimate_tokens("中" * 200) + estimate_tokens("a" * 300),
            delta=2)

    def test_cjk_ratio(self):
        self.assertAlmostEqual(cjk_ratio("中文测试"), 1.0)
        self.assertAlmostEqual(cjk_ratio("abcdef"), 0.0)
        self.assertAlmostEqual(cjk_ratio("中文abcd"), 2 / 6)

    def test_count_cjk_includes_fullwidth_punct(self):
        self.assertEqual(count_cjk("中文，：abc"), 4)

    def test_long_text_sampling_is_deterministic(self):
        text = ("甲" * 3000) + ("b" * 3000) + ("乙" * 3000)
        self.assertEqual(estimate_tokens(text), estimate_tokens(text))
        self.assertGreater(estimate_tokens(text), 1000)

    def test_message_overhead(self):
        self.assertGreater(estimate_message_tokens({"role": "user", "content": "hi"}),
                           estimate_tokens("hi"))
        self.assertEqual(
            estimate_messages_tokens([{"role": "user", "content": "a"},
                                      {"role": "assistant", "content": "b"}]),
            estimate_message_tokens({"role": "user", "content": "a"})
            + estimate_message_tokens({"role": "assistant", "content": "b"}))


class BudgetTest(unittest.TestCase):
    def test_resolve_defaults(self):
        b = resolve_budget(128000, 16384, 8192, 0.8)
        self.assertEqual(b.hard_limit, 103424)
        self.assertEqual(b.soft_limit, 102400)
        self.assertLessEqual(b.soft_limit, b.hard_limit)

    def test_soft_never_exceeds_hard(self):
        b = resolve_budget(16000, 8000, 4000, 0.8)
        self.assertLessEqual(b.soft_limit, b.hard_limit)
        self.assertGreaterEqual(b.hard_limit, 1024)

    def test_window_floor(self):
        self.assertGreaterEqual(resolve_budget(100, 0, 0, 0.8).window, 2048)

    def test_model_window_override(self):
        self.assertEqual(
            resolve_model_window("deepseek-4.1-flash", default_window=64000,
                                 overrides={"deepseek-4.1-flash": 128000}),
            128000)
        self.assertEqual(
            resolve_model_window("unknown", default_window=64000), 64000)


class TruncateTest(unittest.TestCase):
    def test_noop_when_under_budget(self):
        self.assertEqual(truncate_to_tokens("短文本", 100), "短文本")

    def test_truncates_long_text(self):
        text = "中" * 10000
        out = truncate_to_tokens(text, 100, suffix="…")
        self.assertLess(len(out), len(text))
        self.assertLessEqual(estimate_tokens(out), 130)
        self.assertTrue(out.endswith("…"))

    def test_chars_for_tokens(self):
        text = "中" * 1000
        chars = chars_for_tokens(text, 100)
        self.assertGreater(chars, 100)
        self.assertLess(chars, 150)


if __name__ == "__main__":
    unittest.main()
