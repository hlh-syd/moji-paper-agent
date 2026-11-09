# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from src.context import shadow as shadow_mod


def _msgs(pairs):
    return [{"role": r, "content": c} for r, c in pairs]


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_prefix_digest_stable_and_sensitive(self):
        msgs = _msgs([("user", "改第三章"), ("assistant", "好的")])
        d1 = shadow_mod.prefix_digest(msgs, 2)
        self.assertEqual(d1, shadow_mod.prefix_digest(msgs, 2))
        self.assertNotEqual(d1, shadow_mod.prefix_digest(msgs, 1))
        changed = _msgs([("user", "改第四章"), ("assistant", "好的")])
        self.assertNotEqual(d1, shadow_mod.prefix_digest(changed, 2))

    def test_resolve_valid(self):
        msgs = _msgs([("user", "a"), ("assistant", "b")])
        sh = shadow_mod.Shadow(through_index=2,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 2),
                               summary_md="摘要")
        self.assertIsNotNone(shadow_mod.resolve(sh, msgs))

    def test_resolve_invalid_after_edit(self):
        msgs = _msgs([("user", "a"), ("assistant", "b")])
        sh = shadow_mod.Shadow(through_index=2,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 2),
                               summary_md="摘要")
        edited = _msgs([("user", "a"), ("assistant", "改过")])
        self.assertIsNone(shadow_mod.resolve(sh, edited))

    def test_resolve_invalid_when_prefix_truncated(self):
        msgs = _msgs([("user", str(i)) for i in range(6)])
        sh = shadow_mod.Shadow(through_index=6,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 6),
                               summary_md="摘要")
        self.assertIsNone(shadow_mod.resolve(sh, msgs[-3:]))

    def test_append_does_not_invalidate(self):
        msgs = _msgs([("user", "a"), ("assistant", "b")])
        sh = shadow_mod.Shadow(through_index=2,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 2),
                               summary_md="摘要")
        longer = msgs + _msgs([("user", "新问题")])
        self.assertIsNotNone(shadow_mod.resolve(sh, longer))

    def test_sidecar_roundtrip(self):
        msgs = _msgs([("user", "a"), ("assistant", "b")])
        sh = shadow_mod.Shadow(through_index=2,
                               prefix_digest=shadow_mod.prefix_digest(msgs, 2),
                               summary_json={"primary_request": "改论文"},
                               summary_md="摘要内容")
        shadow_mod.save_sidecar("sess-1", sh, self.base)
        loaded = shadow_mod.load_sidecar("sess-1", self.base)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.through_index, 2)
        self.assertEqual(loaded.summary_md, "摘要内容")

    def test_sidecar_isolated_per_session(self):
        sh = shadow_mod.Shadow(through_index=1, prefix_digest="d", summary_md="s")
        shadow_mod.save_sidecar("sess-a", sh, self.base)
        self.assertIsNone(shadow_mod.load_sidecar("sess-b", self.base))

    def test_corrupt_sidecar_ignored(self):
        path = shadow_mod.sidecar_path("sess-x", self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(shadow_mod.load_sidecar("sess-x", self.base))

    def test_mismatched_session_id_ignored(self):
        sh = shadow_mod.Shadow(through_index=1, prefix_digest="d", summary_md="s")
        shadow_mod.save_sidecar("sess-a", sh, self.base)
        path = shadow_mod.sidecar_path("sess-a", self.base)
        text = path.read_text(encoding="utf-8").replace("sess-a", "sess-z")
        path.write_text(text, encoding="utf-8")
        self.assertIsNone(shadow_mod.load_sidecar("sess-a", self.base))


if __name__ == "__main__":
    unittest.main()
