"""对话 Agent 操控论文：动作协议 + 流式解析 + 执行器。

模型在聊天回复中输出动作块：
    <<<PAPER_ACTION
    {"action": "edit_section", "section": "结果", "content": "...", "note": "补充了亚组分析"}
    PAPER_ACTION>>>
后端流式拦截（不显示原始块）、按序执行，并把执行结果回灌给模型继续对话。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

ACTION_OPEN = "<<<PAPER_ACTION"
ACTION_CLOSE = "PAPER_ACTION>>>"

# 排版设置合法键与取值范围（与前端 PAPER_STYLE_DEFAULTS / config.export_style 一致）
LAYOUT_KEYS = {
    "body_font_px": (14, 18), "line_height": (1.5, 2.2), "first_indent_em": (0, 2),
    "table_font_px": (12, 15), "fig_width_pct": (50, 100),
}
LAYOUT_ENUMS = {
    "body_align": {"justify", "left"}, "table_align": {"center", "left"},
    "table_width": {"full", "auto"}, "cell_align": {"center", "left"},
}

ACTIONS_PROMPT = """

# 论文操控能力（重要）
你不仅可以"给建议"，还可以**直接操控用户当前打开的论文项目**。当用户的请求涉及查看或修改论文时，输出动作块（执行结果会回传给你）：

<<<PAPER_ACTION
{"action": "动作名", ...参数}
PAPER_ACTION>>>

- 一次回复可输出多个动作块，按顺序执行；动作块之间的普通文字会照常显示给用户。
- 动作执行后你会收到下一轮"动作执行结果"，届时可继续输出动作块，或用简短中文总结你做了什么（不要再复述全文）。
- 仅当用户明确要求查看/修改论文时才输出动作块；纯闲聊、咨询、问答题不要输出。
- 用户明确给出要写入的具体文字时（如"追加这句：……"、"把X改成Y"、"删掉第二段"），**必须原文执行** append_section/edit_section，不要索要更多材料、不要改写用户给定的文字；只有当任务需要生成全新分析内容而缺乏数据时，才先向用户要材料。

可用动作：
1. get_outline —— 无参数。获取大纲结构（各节 id/标题/状态/字数）。不确定 section 时先调用。
2. read_section —— {"section": "结果"} 读取某节当前正文（id 或标题均可）。
3. edit_section —— {"section": "结果", "content": "整节新正文(Markdown)", "note": "一句变更说明"} 整节替换。改动前必须先 read_section 确认现状；保留原有图片标记 ![...](...) 与公式；数字必须与原文/画像一致，不得编造。
4. append_section —— {"section": "讨论", "text": "追加的段落(Markdown)", "note": "说明"} 在节末追加。
5. update_section_meta —— {"section": "前言", "title": "新标题", "target_words": 4000, "key_points": ["要点1", ...]}（至少给一项）。
6. set_layout —— {"table_align": "left", "body_font_px": 15, ...} 修改排版设置。可选键：body_font_px(14-18), line_height(1.5-2.2), first_indent_em(0或2), body_align(justify/left), table_align(center/left), table_width(full/auto), table_font_px(12-15), cell_align(center/left), fig_width_pct(50-100)。只传要改的键。
7. export_paper —— {"fmt": "docx"} 或 "md"。触发浏览器下载。
8. schedule_regenerate —— {"section": "结果", "instruction": "重写要求"} 安排重写某节（走完整生成管线：检索+数字守卫+去AI味，论文区会流式显示）。
"""


def build_outline_snapshot(outline: dict) -> str:
    """大纲紧凑快照（注入 system prompt，让模型知道各节 id/状态）"""
    lines = [f"论文《{outline.get('project_id', '未命名')}》"
             f"共 {len(outline.get('sections', []))} 节，总目标 {outline.get('total_target_words', '?')} 字。"]
    for i, sec in enumerate(outline.get("sections", []), 1):
        status = "已起草" if sec.get("status") == "drafted" else "待生成"
        words = sec.get("actual_words") or 0
        lines.append(f"- [{sec.get('id')}] {i}. {sec.get('title')}（{status} {words}字，"
                     f"目标{sec.get('target_words')}字）")
    return "\n".join(lines)


class ActionStreamParser:
    """流式解析：分离普通文本与动作块（块可能被切成任意小片）"""

    def __init__(self):
        self.buf = ""
        self.in_block = False

    def feed(self, piece: str):
        """返回 (可见文本, 完整动作块列表)"""
        self.buf += piece
        visible, actions = [], []
        while True:
            if not self.in_block:
                idx = self.buf.find(ACTION_OPEN)
                if idx < 0:
                    keep = len(ACTION_OPEN) - 1  # 防止开放标记被切断
                    if len(self.buf) > keep:
                        visible.append(self.buf[:-keep])
                        self.buf = self.buf[-keep:]
                    break
                visible.append(self.buf[:idx])
                self.buf = self.buf[idx + len(ACTION_OPEN):]
                self.in_block = True
            else:
                idx = self.buf.find(ACTION_CLOSE)
                if idx < 0:
                    break  # 等待闭合
                actions.append(self.buf[:idx].strip())
                self.buf = self.buf[idx + len(ACTION_CLOSE):]
                self.in_block = False
        return "".join(visible), actions

    def flush(self):
        """收尾：未闭合块原样可见（不执行），让用户看到模型意图"""
        rest, self.buf = self.buf, ""
        self.in_block = False
        return rest


def parse_action(raw: str) -> Optional[dict]:
    """动作块 JSON 解析（容忍 ```json 包裹与首尾杂讯）"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
    return None


class PaperActionExecutor:
    """在指定项目上执行论文动作"""

    MAX_CONTENT = 80000  # 单节正文上限（字符），防失控

    def __init__(self, system, project_name: str):
        self.system = system
        self.project = project_name

    # ---- 定位：id 精确 → 标题精确 → 标题唯一子串 ----
    def _resolve(self, outline: dict, ref) -> Optional[dict]:
        if not ref:
            return None
        ref = str(ref).strip()
        secs = outline.get("sections", [])
        for s in secs:
            if s.get("id") == ref:
                return s
        for s in secs:
            if s.get("title") == ref:
                return s
        hits = [s for s in secs if ref in (s.get("title") or "")]
        return hits[0] if len(hits) == 1 else None

    def _load(self) -> dict:
        outline = self.system.outline_generator.load_outline(self.project)
        if outline is None:
            raise ValueError(f"项目尚无大纲: {self.project}")
        return outline

    def _save(self, outline: dict) -> None:
        self.system.outline_generator.save_outline(self.project, outline)

    # ---- 各动作实现 ----
    def _get_outline(self, args) -> dict:
        outline = self._load()
        return {"ok": True, "summary": f"已读取大纲（{len(outline.get('sections', []))} 节）",
                "result": build_outline_snapshot(outline)}

    def _read_section(self, args, outline=None) -> dict:
        o = outline or self._load()
        sec = self._resolve(o, args.get("section"))
        if sec is None:
            return {"ok": False, "summary": f"未找到节：{args.get('section')}"}
        content = (sec.get("content") or "").strip()
        body = content[:8000] + ("…（过长截断）" if len(content) > 8000 else "")
        return {"ok": True,
                "summary": f"已读取「{sec['title']}」（{sec.get('actual_words') or 0}字）",
                "result": body or "（该节尚未生成正文）"}

    def _edit_section(self, args) -> dict:
        from src.section_writer import count_cn_words
        outline = self._load()
        sec = self._resolve(outline, args.get("section"))
        if sec is None:
            return {"ok": False, "summary": f"未找到节：{args.get('section')}"}
        content = str(args.get("content") or "")
        if len(content) > self.MAX_CONTENT:
            return {"ok": False, "summary": "content 超长，已拒绝执行"}
        before = count_cn_words(sec.get("content") or "")
        sec["content"] = content
        words = count_cn_words(content)
        sec["actual_words"] = words
        sec["status"] = "drafted" if content.strip() else "pending"
        self._save(outline)
        note = str(args.get("note") or "").strip()
        return {"ok": True,
                "summary": f"已改写「{sec['title']}」{before}→{words} 字" + (f"（{note}）" if note else ""),
                "affects": {"section_id": sec.get("id")}}

    def _append_section(self, args) -> dict:
        from src.section_writer import count_cn_words
        outline = self._load()
        sec = self._resolve(outline, args.get("section"))
        if sec is None:
            return {"ok": False, "summary": f"未找到节：{args.get('section')}"}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"ok": False, "summary": "text 为空，未执行"}
        sec["content"] = ((sec.get("content") or "").rstrip() + "\n\n" + text).strip()
        sec["actual_words"] = count_cn_words(sec["content"])
        if sec["content"]:
            sec["status"] = "drafted"
        self._save(outline)
        note = str(args.get("note") or "").strip()
        return {"ok": True,
                "summary": f"已向「{sec['title']}」追加 {count_cn_words(text)} 字" + (f"（{note}）" if note else ""),
                "affects": {"section_id": sec.get("id")}}

    def _update_section_meta(self, args) -> dict:
        outline = self._load()
        sec = self._resolve(outline, args.get("section"))
        if sec is None:
            return {"ok": False, "summary": f"未找到节：{args.get('section')}"}
        changed = []
        if args.get("title"):
            sec["title"] = str(args["title"]).strip()[:80]
            changed.append(f"标题→{sec['title']}")
        if args.get("target_words"):
            try:
                sec["target_words"] = max(100, int(args["target_words"]))
                changed.append(f"目标→{sec['target_words']}字")
            except (TypeError, ValueError):
                pass
        if isinstance(args.get("key_points"), list) and args["key_points"]:
            sec["key_points"] = [str(k)[:200] for k in args["key_points"][:12]]
            changed.append(f"要点×{len(sec['key_points'])}")
        if not changed:
            return {"ok": False, "summary": "未提供任何可更新字段"}
        self._save(outline)
        return {"ok": True, "summary": f"已更新「{sec['title']}」：{'，'.join(changed)}",
                "affects": {"section_id": sec.get("id")}}

    def _set_layout(self, args) -> dict:
        from src import workspace_store
        state = workspace_store.load_state() or {}
        current = dict(state.get("paper_style") or {})
        applied = []
        for k, v in (args or {}).items():
            if k == "action":
                continue
            if k in LAYOUT_KEYS:
                try:
                    fv = float(v)
                    lo, hi = LAYOUT_KEYS[k]
                    if lo <= fv <= hi:
                        current[k] = fv
                        applied.append(k)
                except (TypeError, ValueError):
                    continue
            elif k in LAYOUT_ENUMS and v in LAYOUT_ENUMS[k]:
                current[k] = v
                applied.append(k)
        if not applied:
            return {"ok": False, "summary": "没有合法的排版键（可用：" + "，".join(
                list(LAYOUT_KEYS) + list(LAYOUT_ENUMS)) + "）"}
        state["paper_style"] = current
        workspace_store.save_state(state)
        return {"ok": True, "summary": f"已更新排版：{ '，'.join(applied) }",
                "affects": {"layout": current}}

    def _export_paper(self, args) -> dict:
        fmt = "md" if str(args.get("fmt", "docx")).lower() == "md" else "docx"
        return {"ok": True, "summary": f"已触发导出 {fmt.upper()}（浏览器将下载）",
                "affects": {"export": fmt}}

    def _schedule_regenerate(self, args) -> dict:
        outline = self._load()
        sec = self._resolve(outline, args.get("section"))
        if sec is None:
            return {"ok": False, "summary": f"未找到节：{args.get('section')}"}
        instruction = str(args.get("instruction") or "").strip()
        return {"ok": True,
                "summary": f"已安排重写「{sec['title']}」" + (f"（{instruction[:40]}）" if instruction else ""),
                "affects": {"regen": {"section_id": sec.get("id"), "instruction": instruction}}}

    # ---- 分发 ----
    def execute(self, action: dict) -> dict:
        name = str(action.get("action") or "").strip()
        try:
            if name == "get_outline":
                return self._get_outline(action)
            if name == "read_section":
                return self._read_section(action)
            if name == "edit_section":
                return self._edit_section(action)
            if name == "append_section":
                return self._append_section(action)
            if name == "update_section_meta":
                return self._update_section_meta(action)
            if name == "set_layout":
                return self._set_layout(action)
            if name == "export_paper":
                return self._export_paper(action)
            if name == "schedule_regenerate":
                return self._schedule_regenerate(action)
            return {"ok": False, "summary": f"未知动作：{name}"}
        except Exception as e:
            logger.warning(f"动作 {name} 执行失败: {e}")
            return {"ok": False, "summary": f"执行失败：{e}"}


ACTION_LABELS = {
    "get_outline": "读取大纲", "read_section": "读取章节", "edit_section": "改写章节",
    "append_section": "追加内容", "update_section_meta": "更新章节信息", "set_layout": "调整排版",
    "export_paper": "导出论文", "schedule_regenerate": "重写章节",
}
