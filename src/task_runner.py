"""
一键生成全文任务编排器
链路：路由(POST /api/fulltext/start) → 任务分发(逐节 write_section)
      → 回调审查(本地体检 + 字数审计 + 超阈值自动去AI味修复一次)
      → 交付(汇总指标 + 大纲逐节落盘)

- 单任务互斥（同一时刻仅一个全文任务）
- 支持取消（当前节完成后停止）
- 跳过已完成节（force=True 强制重做）
- 逐节落盘：服务中途重启不丢已完成内容
"""
import asyncio
import logging
import re
import time
from datetime import datetime

from src.text_enhancer import TextEnhancer, detect_ai_markers
from src.section_writer import SectionWriter
from src.paper_checks import run_checks

logger = logging.getLogger(__name__)

ACTIVE_STATES = ("queued", "running")


class FullTextTask:
    """单个全文生成任务"""

    def __init__(self, task_id: str, project_name: str, profile: dict,
                 outline: dict, section_writer: SectionWriter,
                 enhancer: TextEnhancer, *, force: bool = False,
                 auto_humanize_threshold: int = 55,
                 only: list | None = None):
        self.task_id = task_id
        self.project_name = project_name
        self.owner = ""  # 发起用户（start 时注入；跨用户禁止查看/取消）
        self.profile = profile
        self.outline = outline
        self.sw = section_writer
        self.enhancer = enhancer
        self.force = force
        self.auto_humanize_threshold = auto_humanize_threshold
        self.review_rounds = 2
        self.bestof_n = 3
        self.bestof_sections = ["摘要", "前言", "讨论"]
        all_ids = [s["id"] for s in outline.get("sections", [])]
        self.targets = [i for i in all_ids if (not only or i in only)]
        # D3 摘要后置：摘要最后生成（基于全文数字成稿），其余保持大纲顺序
        _def_title = {sid: title for sid, title in
                      ((s.get("id"), s.get("title") or "") for s in outline.get("sections", []))}
        if not only:
            self.targets.sort(key=lambda sid: "摘要" in _def_title.get(sid, ""))
        self.state = "queued"          # queued/running/done/cancelled/error
        self.current = None
        self.events = []               # 事件流（前端轮询）
        self.sections_state = {}       # sid -> {title,state,words,ai_score,repaired,placeholders}
        self.cancel_requested = False
        self.error = None
        self.started_at = None
        self.finished_at = None
        self._async_ref = None         # 防 GC

    # ---------- 事件 ----------
    def _log(self, ev_type: str, **kw) -> dict:
        ev = {"type": ev_type, "time": datetime.now().isoformat(timespec="seconds"), **kw}
        self.events.append(ev)
        if len(self.events) > 800:
            del self.events[:200]
        logger.info(f"[fulltext {self.task_id}] {ev_type} {kw}")
        return ev

    # ---------- 状态 ----------
    def status(self) -> dict:
        done_n = sum(1 for s in self.sections_state.values() if s.get("state") in ("done", "kept"))
        total_words = sum(s.get("words") or 0 for s in self.sections_state.values()
                          if s.get("state") in ("done", "kept"))
        return {
            "task_id": self.task_id,
            "project_name": self.project_name,
            "state": self.state,
            "current": self.current,
            "total": len(self.targets),
            "finished": done_n,
            "total_words": total_words,
            "sections": [self.sections_state.get(sid) or {"id": sid, "state": "pending"}
                         for sid in self.targets],
            "events": self.events[-40:],
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def request_cancel(self):
        self.cancel_requested = True
        self._log("cancel_requested")

    # ---------- 主流程 ----------
    async def run(self):
        self.state = "running"
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._log("task_start", total=len(self.targets), force=self.force,
                  threshold=self.auto_humanize_threshold)
        try:
            for idx, sid in enumerate(self.targets):
                if self.cancel_requested:
                    self.state = "cancelled"
                    self._log("task_cancelled", at=sid)
                    break

                sec = self.sw._find_section(self.outline, sid)
                if sec is None:
                    self.sections_state[sid] = {"id": sid, "state": "error", "error": "节不存在"}
                    continue

                # 跳过已完成
                if not self.force and sec.get("status") == "drafted" and sec.get("content"):
                    self.sections_state[sid] = {
                        "id": sid, "title": sec["title"], "state": "kept",
                        "words": sec.get("actual_words") or 0,
                    }
                    self._log("section_skip", section_id=sid, title=sec["title"])
                    continue

                self.current = sid
                self._log("section_start", section_id=sid, title=sec["title"],
                          index=idx + 1, target=sec.get("target_words"))
                t0 = time.time()

                # ---- 1. 生成本节（关键节 Best-of-N 择优；其余常规生成）----
                gen_error = None
                if sec.get("title") in self.bestof_sections and self.bestof_n > 1:
                    self._log("bestof_start", section_id=sid, title=sec["title"], n=self.bestof_n)
                    bo = await self.sw.bestof_section(self.profile, self.outline, sid,
                                                      n=self.bestof_n)
                    if not bo.get("ok"):
                        gen_error = bo.get("error")
                    else:
                        self._log("bestof_done", section_id=sid,
                                  chosen=bo.get("best_k"),
                                  scores=bo.get("scores", {}))
                        # bestof 已把择优稿写入本节，跳过常规生成（避免覆盖择优结果）
                else:
                    result = await self.sw.write_section(self.profile, self.outline, sid)
                    if not result.get("ok"):
                        gen_error = result.get("error")
                if gen_error:
                    self.sections_state[sid] = {"id": sid, "title": sec["title"],
                                                "state": "error", "error": gen_error}
                    self._log("section_error", section_id=sid, error=gen_error)
                    continue
                content = sec.get("content") or ""

                # ---- 2. 回调审查 ----
                report = detect_ai_markers(content)
                score = report["score"]
                repaired = False
                if score > self.auto_humanize_threshold:
                    self._log("review", section_id=sid, ai_score=score,
                              verdict="auto_humanize",
                              markers=[m["type"] for m in report.get("markers", [])])
                    acc = []
                    async for ev in self.enhancer.enhance_section_stream(
                            self.profile, self.outline, sid,
                            mode="humanize", strength="standard", instruction=""):
                        if ev["type"] == "chunk":
                            acc.append(ev["content"])
                        elif ev["type"] == "enhance_done":
                            score = ev["after_score"]
                            repaired = True
                        elif ev["type"] == "error":
                            logger.warning(f"[fulltext] 修复失败（保留原稿）: {ev.get('content')}")
                            break
                    content = "".join(acc) or content
                else:
                    self._log("review", section_id=sid, ai_score=score, verdict="pass")

                placeholders = len(re.findall(r"\[待补充", content))

                # ---- P3 诊断→定向修订循环 ----
                diagnoses_total = 0
                prev_secs = [{"id": s["id"], "title": s.get("title", ""),
                              "content": s.get("content") or ""}
                             for s in self.outline.get("sections", []) if s["id"] != sid]
                for rnd in range(self.review_rounds):
                    chk = run_checks(sec.get("content") or "", self.outline,
                                     self.profile, prev_secs, sid)
                    if not chk["issues"]:
                        if rnd == 0:
                            self._log("diagnose", section_id=sid, issues=0, verdict="clean")
                        break
                    diagnoses_total += len(chk["issues"])
                    self._log("diagnose", section_id=sid, issues=len(chk["issues"]),
                              types=[i["type"] for i in chk["issues"]])
                    acc = []
                    async for ev in self.sw.revise_section_stream(
                            self.profile, self.outline, sid, chk["issues"]):
                        if ev["type"] == "chunk":
                            acc.append(ev["content"])
                        elif ev["type"] == "error":
                            logger.warning(f"[fulltext] 修订失败: {ev.get('content')}")
                            break
                    if acc:
                        sec["content"] = "".join(acc)

                placeholders = len(re.findall(r"\[待补充", sec.get("content") or ""))
                self.sections_state[sid] = {
                    "id": sid, "title": sec["title"], "state": "done",
                    "words": self._count_now(sid),
                    "ai_score": score, "repaired": repaired,
                    "diagnoses": diagnoses_total,
                    "placeholders": placeholders,
                    "seconds": round(time.time() - t0),
                }
                self._log("section_done", section_id=sid, title=sec["title"],
                          ai_score=score, repaired=repaired,
                          diagnoses=diagnoses_total,
                          placeholders=placeholders,
                          seconds=round(time.time() - t0))

            if self.state == "running":
                self.state = "done"
                self._log("task_end")
        except Exception as e:
            self.state = "error"
            self.error = str(e)
            self._log("task_error", error=str(e))
            logger.exception(f"[fulltext {self.task_id}] 异常终止")
        finally:
            self.current = None
            self.finished_at = datetime.now().isoformat(timespec="seconds")

    def _count_now(self, sid: str) -> int:
        sec = self.sw._find_section(self.outline, sid)
        if not sec:
            return 0
        from src.section_writer import count_cn_words
        return count_cn_words(sec.get("content") or "")


class FullTextRunner:
    """全文任务注册表（单例，单任务互斥）"""

    def __init__(self, section_writer: SectionWriter, enhancer: TextEnhancer):
        self.sw = section_writer
        self.enhancer = enhancer
        self.task: FullTextTask | None = None
        self._counter = 0

    def active(self) -> bool:
        return self.task is not None and self.task.state in ACTIVE_STATES

    def start(self, project_name: str, profile: dict, outline: dict,
              force: bool = False, auto_humanize_threshold: int = 55,
              only: list | None = None) -> dict:
        if self.active():
            return {"ok": False, "error": "已有全文任务在运行",
                    "task_id": self.task.task_id}
        self._counter += 1
        task_id = f"ft_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self._counter}"
        task = FullTextTask(task_id, project_name, profile, outline,
                            self.sw, self.enhancer, force=force,
                            auto_humanize_threshold=auto_humanize_threshold,
                            only=only)
        from src import scoping as _scoping
        task.owner = _scoping.current_owner()
        self.task = task
        task._async_ref = asyncio.create_task(task.run())
        pending = sum(1 for s in outline.get("sections", [])
                      if not (s.get("status") == "drafted" and s.get("content")))
        return {"ok": True, "task_id": task_id,
                "total": len(task.targets),
                "pending": pending if not force else len(task.targets)}

    def status(self) -> dict:
        if self.task is None:
            return {"state": "none"}
        return self.task.status()

    def cancel(self) -> dict:
        if self.task is None or not self.active():
            return {"ok": False, "error": "没有运行中的任务"}
        self.task.request_cancel()
        return {"ok": True, "task_id": self.task.task_id,
                "note": "将在当前节完成后停止"}
