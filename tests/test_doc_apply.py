# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from src import doc_check, scoping

BEFORE = ("人工智能技术在医学影像领域的应用日益广泛，"
          "本文对此进行了系统研究并取得了一定成果。")
AFTER = ("医学影像中的人工智能应用越来越多，"
         "本文系统梳理了这一方向并得到了一些结论。")


def _sections():
    return [
        {"id": "s1", "title": "前言", "content": "开头。" + BEFORE + "中间。"},
        {"id": "s2", "title": "讨论",
         "content": "甲。" + BEFORE + "乙。" + BEFORE + "丙。"},
    ]


def _doc():
    return {"id": "20260101_abcd123456", "filename": "r.docx",
            "paragraphs": [BEFORE], "marks": [],
            "history": [{"action": "humanize_rewrite",
                         "changes": [{"idx": 0, "before": BEFORE, "after": AFTER}]}]}


class SpanTest(unittest.TestCase):
    def test_count_all_hits(self):
        secs = _sections()
        self.assertEqual(doc_check.count_in_text(secs[0]["content"], BEFORE), 1)
        self.assertEqual(doc_check.count_in_text(secs[1]["content"], BEFORE), 2)

    def test_replace_all(self):
        content = "甲。" + BEFORE + "乙。" + BEFORE + "丙。"
        out, n = doc_check.replace_all_in_text(content, BEFORE, AFTER)
        self.assertEqual(n, 2)
        self.assertNotIn(BEFORE, out)
        self.assertEqual(out.count(AFTER), 2)
        self.assertTrue(out.startswith("甲。"))

    def test_punctuation_absorbed_without_overlap(self):
        content = "『" + BEFORE + "』" + BEFORE + "。"
        spans = doc_check.find_spans(content, BEFORE)
        self.assertEqual(len(spans), 2)
        self.assertLessEqual(spans[0][1], spans[1][0])

    def test_short_before_is_rejected(self):
        self.assertEqual(doc_check.count_in_text("很短", "很短"), 0)

    def test_max_hits_guard(self):
        content = (BEFORE + "；") * 5
        out, n = doc_check.replace_all_in_text(content, BEFORE, AFTER, max_hits=2)
        self.assertEqual(n, -1)
        self.assertEqual(out, content)


class PlanCommitTest(unittest.TestCase):
    def test_plan_applies_all_hits(self):
        plan = doc_check.plan_apply(_doc(), _sections())
        op = plan["ops"][0]
        self.assertEqual(op["action"], "apply")
        self.assertEqual(op["expected_total"], 3)
        self.assertEqual(plan["summary"]["applicable"], 1)
        self.assertTrue(plan["plan_hash"])

    def test_plan_not_found(self):
        secs = [{"id": "s1", "content": "完全无关的正文内容，没有任何重合段落存在。" * 2}]
        plan = doc_check.plan_apply(_doc(), secs)
        self.assertEqual(plan["ops"][0]["action"], "skip")
        self.assertEqual(plan["ops"][0]["reason"], "not_found")
        self.assertEqual(plan["summary"]["applicable"], 0)

    def test_plan_unique_strategy_marks_ambiguous(self):
        plan = doc_check.plan_apply(_doc(), _sections(), strategy="unique")
        self.assertEqual(plan["ops"][0]["reason"], "ambiguous")

    def test_plan_too_many_hits(self):
        plan = doc_check.plan_apply(_doc(), _sections(), max_hits=2)
        self.assertEqual(plan["ops"][0]["reason"], "too_many_hits")

    def test_commit_replaces_everywhere(self):
        secs = _sections()
        doc = _doc()
        plan = doc_check.plan_apply(doc, secs)
        result = doc_check.commit_apply(doc, secs, plan, project="p")
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["receipt"]["summary"]["hits"], 3)
        self.assertEqual(secs[1]["content"].count(AFTER), 2)
        self.assertNotIn(BEFORE, secs[0]["content"])
        self.assertIn("前言", result["sections"])
        self.assertEqual(result["receipt"]["paper_rev_before"],
                         plan["paper_rev"])

    def test_second_commit_is_noop(self):
        secs = _sections()
        doc = _doc()
        plan = doc_check.plan_apply(doc, secs)
        doc_check.commit_apply(doc, secs, plan)
        again = doc_check.commit_apply(doc, secs, plan)
        self.assertEqual(again["applied"], 0)
        self.assertEqual(again["receipt"]["skipped"][0]["reason"],
                         "changed_since_plan")

    def test_commit_skips_when_paper_drifted(self):
        secs = _sections()
        doc = _doc()
        plan = doc_check.plan_apply(doc, secs)
        secs[1]["content"] = "甲。" + BEFORE + "乙。丙。"   # 少了一处
        result = doc_check.commit_apply(doc, secs, plan)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["receipt"]["skipped"][0]["reason"],
                         "changed_since_plan")

    def test_paper_revision_changes_with_content(self):
        a = _sections()
        b = _sections()
        self.assertEqual(doc_check.paper_revision(a), doc_check.paper_revision(b))
        b[0]["content"] += "改动"
        self.assertNotEqual(doc_check.paper_revision(a),
                            doc_check.paper_revision(b))

    def test_first_strategy_replaces_once(self):
        secs = _sections()
        doc = _doc()
        plan = doc_check.plan_apply(doc, secs, strategy="first")
        result = doc_check.commit_apply(doc, secs, plan)
        self.assertEqual(result["receipt"]["summary"]["hits"], 1)
        self.assertEqual(secs[1]["content"].count(AFTER), 0)


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)
        self._old_base = scoping.BASE_DIR
        scoping.BASE_DIR = self._base

    def tearDown(self):
        scoping.BASE_DIR = self._old_base
        self._tmp.cleanup()

    def test_append_and_read(self):
        doc_id = "20260101_eventtest01"
        doc_check.append_event(doc_id, "import_report", {"paras": 3})
        doc_check.append_event(doc_id, "apply_commit", {"applied": 1})
        events = doc_check.read_events(doc_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["seq"], 1)
        self.assertEqual(events[1]["seq"], 2)
        self.assertEqual(events[0]["type"], "import_report")
        self.assertEqual(events[1]["payload"]["applied"], 1)

    def test_read_limit_returns_tail(self):
        doc_id = "20260101_eventtest02"
        for i in range(10):
            doc_check.append_event(doc_id, "tick", {"i": i})
        events = doc_check.read_events(doc_id, limit=3)
        self.assertEqual([e["payload"]["i"] for e in events], [7, 8, 9])

    def test_missing_log(self):
        self.assertEqual(doc_check.read_events("20260101_nothere01"), [])

    def test_atomic_save_doc(self):
        doc = _doc()
        doc_check.save_doc(doc)
        loaded = doc_check.load_doc(doc["id"])
        self.assertEqual(loaded["id"], doc["id"])
        self.assertFalse(any(p.name.endswith(".tmp")
                             for p in (self._base / "uploads").rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
