"""
文本增强模块：AI润色（polish）与 去AI味（humanize/de-AI）
- detect_ai_markers: 纯 Python 本地体检（零 LLM，即时）——套话黑名单、句长突发性、
  三联排比密度、段首连接词比例、破折号/分号密度 → 加权评分 0~100
- enhance_section_stream: 单节流式改写；首次改写前快照原文（可还原）；
  humanize 模式把体检报告喂给模型定向修复；完成后本地复扫对比分数
设计原则（来自 unslop / humanize-chinese / 九杠杆调研）：
  事实神圣（数字/统计量/术语/引用不动）、含义保真、检测先行、最小干预
"""
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

from src.config import AppConfig, get_config_manager
from src.outline_generator import OutlineGenerator
from src.section_writer import SectionWriter, count_cn_words

logger = logging.getLogger(__name__)


# ============================================================
# 本地体检器（零 LLM）
# ============================================================

# 中文 AI 套话黑名单（humanize-chinese + unslop 中文适配）
_MARKERS = [
    ("首先/其次/最后框架", r"首先[，,].{2,40}?其次[，,]|其次[，,].{2,40}?最后[，,]|首先[，,、]"),
    ("综上所述", r"综上所述|综上，"),
    ("值得注意的是", r"值得注意的是|值得注意的是[，,]|值得一提的是"),
    ("不难发现", r"不难发现|不难看出"),
    ("具有重要意义", r"具有重要意义|具有重要价值|具有重要作用"),
    ("研究表明(空引)", r"(?<![引文献据])研究表明[，,]"),
    ("此外(段首或高频)", r"此外[，,]"),
    ("总而言之", r"总而言之|总的来说[，,]"),
    ("深入探讨", r"深入探讨|深入分析[了了]"),
    ("为...提供了有力支撑", r"提供了有力支撑|提供了坚实的[^。，]{0,8}基础"),
    ("进一步(滥用)", r"进一步[验说证探阐分][明清讨释述析]"),
    ("全文旨在(模板句)", r"本[文研究]旨在"),
    ("底线的总结腔", r"由此可见[，,]"),
    ("同时也", r"同时也"),
    ("各有优劣式权衡腔", r"各有优劣|各有千秋|利弊并存"),
]

_SENT_SPLIT = re.compile(r"[。！？；]+")
_TRIPLE_PARALLEL = re.compile(r"[、，][^，。；]{1,14}[与和及、][^，。；]{1,14}[与和及、][^，。；]{1,14}[与和及、][^，。；]{1,14}[、，]")
_TRIPLE_PARALLEL_3 = re.compile(r"[^，。；\n]{2,12}[与和及][^，。；\n]{2,12}[与和及][^，。；\n]{2,12}")
_PARA_OPENERS = ("然而", "此外", "同时", "因此", "另外", "同时，", "但是", "而")


def detect_ai_markers(text: str) -> dict:
    """本地 AI 味体检：返回 {score, markers, metrics}。零外部调用。"""
    if not text or len(text.strip()) < 50:
        return {"score": 0, "markers": [], "metrics": {},
                "note": "文本过短，不做体检"}

    body = re.sub(r"```.*?```", "", text, flags=re.S)  # 代码块不计
    # ---- 标记命中 ----
    markers = []
    for name, pat in _MARKERS:
        hits = re.findall(pat, body)
        if hits:
            m = re.search(pat, body)
            start = max(0, m.start() - 6) if m else 0
            markers.append({
                "type": name,
                "count": len(hits),
                "sample": body[start:m.end() + 10].replace("\n", " ") if m else "",
            })

    # ---- 句长突发性 ----
    plain = re.sub(r"[#*`>\[\]\(\)|\-]", "", body)
    sentences = [s.strip() for s in _SENT_SPLIT.split(plain) if len(s.strip()) >= 4]
    lens = [count_cn_words(s) for s in sentences]
    n = len(lens)
    mean_len = sum(lens) / n if n else 0
    std_len = (sum((x - mean_len) ** 2 for x in lens) / n) ** 0.5 if n else 0
    short_ratio = sum(1 for x in lens if x <= 15) / n if n else 0
    # 连续等长（±20%）窗口占比
    uniform_run = 0
    for i in range(n - 2):
        a, b, c = lens[i], lens[i + 1], lens[i + 2]
        if a and abs(b - a) / max(a, 1) < 0.2 and abs(c - b) / max(b, 1) < 0.2:
            uniform_run += 1

    # ---- 结构指标 ----
    paras = [p.strip() for p in text.split("\n\n") if p.strip() and not p.strip().startswith(("#", "|", "-", "*"))]
    opener_ratio = sum(1 for p in paras if p.lstrip().startswith(_PARA_OPENERS)) / len(paras) if paras else 0
    triple_hits = (len(_TRIPLE_PARALLEL.findall(body))
                     + len(_TRIPLE_PARALLEL_3.findall(body)))
    dash_count = body.count("——")
    semicolon_count = body.count("；")
    chars = max(len(body), 1)
    semicolon_density = semicolon_count / chars * 1000  # 每千字

    metrics = {
        "sentence_count": n,
        "mean_len": round(mean_len, 1),
        "std_len": round(std_len, 1),
        "short_ratio": round(short_ratio, 2),
        "uniform_runs": uniform_run,
        "para_count": len(paras),
        "opener_ratio": round(opener_ratio, 2),
        "triple_parallel": triple_hits,
        "dash_count": dash_count,
        "semicolon_per_k": round(semicolon_density, 1),
    }

    # ---- 加权评分（0~100，越高 AI 味越重）----
    marker_weight = sum(m["count"] for m in markers) * 4.5
    score = 0.0
    score += min(45, marker_weight)                                  # 套话
    score += min(15, uniform_run * 2.5)                              # 连续等长句
    score += max(0, 12 - std_len) * 1.0 if n > 6 else 0              # 句长标准差过低
    score += min(12, short_ratio < 0.08 and n > 6 and 12 or (0.08 - short_ratio) * 60 if short_ratio < 0.08 else 0)  # 短句稀缺
    score += min(12, opener_ratio * 24)                              # 段首连接词
    score += min(8, triple_hits * 3)                                 # 三联排比
    score += min(6, dash_count * 2) + min(4, semicolon_density / 5)  # 标点
    score = round(min(100, score))

    return {"score": score, "markers": markers, "metrics": metrics}


def _markers_report_for_prompt(report: dict) -> str:
    """把体检报告压成给 LLM 的定向修复清单"""
    lines = [f"本地体检 AI 味评分：{report['score']}/100（越低越自然）"]
    if report.get("markers"):
        lines.append("命中的 AI 痕迹（须针对性消除）：")
        for m in report["markers"]:
            lines.append(f"- {m['type']} ×{m['count']}（如：{m['sample'][:40]}…）")
    mt = report.get("metrics") or {}
    if mt:
        lines.append(
            f"结构指标：句数{mt.get('sentence_count')} 均长{mt.get('mean_len')}字 "
            f"标准差{mt.get('std_len')}（<12 过均匀） 短句占比{mt.get('short_ratio')} "
            f"连续等长{mt.get('uniform_runs')}处 段首连接词比例{mt.get('opener_ratio')} "
            f"三联排比{mt.get('triple_parallel')}处 破折号{mt.get('dash_count')}个")
    return "\n".join(lines)


# ============================================================
# 流式增强（polish / humanize）
# ============================================================

_STRENGTH_SPEC = {
    "light": "轻度：只修改命中的问题句与明显语病，其余句子原样保留；不做结构调整。",
    "standard": "标准：句子级重写打磨，段落结构基本保持，消除所有可修复的 AI 痕迹。",
    "deep": "深度：允许句段重组与段落内调序（不跨段移动内容），全面重塑表达节奏。",
}

_LONG_ENHANCE = 9000   # 超过此字数按小节切分改写


class TextEnhancer:
    """AI润色 / 去AI味"""

    def __init__(self, section_writer: SectionWriter,
                 config: Optional[AppConfig] = None):
        self.sw = section_writer
        self.config = config or get_config_manager().get_config()

    # ---------- 体检 ----------
    def check_section(self, outline: dict, section_id: str) -> dict:
        sec = self.sw._find_section(outline, section_id)
        if sec is None:
            raise KeyError(section_id)
        report = detect_ai_markers(sec.get("content") or "")
        report["section_id"] = section_id
        report["has_content"] = bool(sec.get("content"))
        return report

    # ---------- 改写 ----------
    def _split_for_enhance(self, content: str) -> list:
        """>9000字按 ### 边界切块（保留块头），否则整节一块"""
        if len(content) <= _LONG_ENHANCE:
            return [content]
        lines = content.split("\n")
        blocks, cur = [], []
        for ln in lines:
            if ln.startswith("###") and cur:
                blocks.append("\n".join(cur))
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            blocks.append("\n".join(cur))
        # 合并过小的块
        merged = []
        for b in blocks:
            if merged and len(b) < 600:
                merged[-1] += "\n" + b
            else:
                merged.append(b)
        return merged or [content]

    @staticmethod
    def _extract_locked_tokens(content: str) -> str:
        """提取需要锁定的要素清单（喂给模型作为不可改动白名单）"""
        nums = sorted(set(re.findall(r"\d+(?:\.\d+)?(?:%|×10[^，。；\s]{0,6})?", content)))[:40]
        cites = sorted(set(re.findall(r"\[\d+(?:[,，\-–]\s*\d+)*\]", content)))[:20]
        terms = sorted(set(re.findall(
            r"(?:Coxnet|LOCO|ComBat|Uno|Harrell|Breslow|Efron|glmnet|mRMR|MissForest|"
            r"KNN|MICE|GAIN|XGBoost|TRIPOD|GSEA|MSigDB|MSIsensor|MANTIS|CPTAC|TCGA|GSE\d{4,6}|"
            r"EPV|IPCW|IBS|RMST|AUC|FDR|McNemar|PERMANOVA|kBET|VIF|RINT|WGAN|E-value)", content)))[:40]
        parts = []
        if nums:
            parts.append("数字与统计量（全部原样保留）：" + "、".join(nums))
        if cites:
            parts.append("引用标记（原位保留）：" + "、".join(cites))
        if terms:
            parts.append("术语与方法名（不改写）：" + "、".join(terms))
        return "\n".join(parts)

    async def enhance_section_stream(
        self,
        profile: dict,
        outline: dict,
        section_id: str,
        mode: str = "humanize",
        strength: str = "standard",
        instruction: str = "",
    ) -> AsyncGenerator[Dict, None]:
        """流式改写单节。事件：chunk / enhance_done / error"""
        if mode not in ("polish", "humanize"):
            yield {"type": "error", "content": f"未知模式: {mode}"}
            return
        sec = self.sw._find_section(outline, section_id)
        if sec is None:
            yield {"type": "error", "content": f"未找到节: {section_id}"}
            return
        content = (sec.get("content") or "").strip()
        if not content:
            yield {"type": "error", "content": "该节尚未生成正文，无法增强（请先生成本节）"}
            return
        if strength not in _STRENGTH_SPEC:
            strength = "standard"

        # 快照原文（首次增强前），支持还原
        if not sec.get("pre_enhance_content"):
            sec["pre_enhance_content"] = content

        before_report = detect_ai_markers(content)
        before_score = before_report["score"]

        constitution = self.sw._load_prompt("constitution.md")
        guide = self.sw._load_prompt("de_ai_guide.md" if mode == "humanize" else "polish_guide.md")
        mode_name = "去AI味改写" if mode == "humanize" else "AI润色"

        system_prompt = f"""{constitution}

# {mode_name}规则
{guide}"""

        # 分块
        blocks = self._split_for_enhance(content)
        out_parts: list = []
        self.sw and None  # no-op keep
        yield {"type": "meta", "mode": mode, "strength": strength,
               "blocks": len(blocks), "before_score": before_score}

        for bi, block in enumerate(blocks):
            locked = self._extract_locked_tokens(block)
            spec = (
                f"# 任务\n对以下论文「{sec['title']}」正文片段执行{mode_name}（强度：{_STRENGTH_SPEC[strength]}）。\n"
                + (f"- 用户附加要求：{instruction}\n" if instruction else "")
            )
            if mode == "humanize":
                spec += f"# 本片段定向体检报告\n{_markers_report_for_prompt(detect_ai_markers(block))}\n\n"
            spec += f"# 不可改动的锁定要素\n{locked or '（本片段未检出数字/引用/术语，同样不得新增）'}\n\n"
            spec += (
                "# 输出要求\n"
                "- 直接输出改写后的正文（markdown，保留原有标题层级与段落划分、表格原样保留）\n"
                "- 不增删事实、不新增主张/解释/文献；含义保真\n"
                f"- 长度与原文相当（{len(block)} 字符左右，±10%）\n"
                + ("- 只输出正文，不要任何解释、前后缀、代码块包裹。\n"
                   if bi == 0 else
                   "- 这是多片段连续改写的后续片段：直接从该片段开头继续输出，不要重复或衔接语。\n")
            )
            user_prompt = f"{spec}\n\n# 待改写片段\n{block}"

            sink: list = []
            try:
                async for piece in self.sw._stream_llm(system_prompt, user_prompt, sink):
                    out_parts.append(piece)
                    yield {"type": "chunk", "content": piece}
            except Exception as e:
                logger.error(f"增强改写失败: {e}")
                yield {"type": "error", "content": str(e)}
                return

        full = "".join(out_parts).strip()

        # ---- P1 锁字校验：改写不得丢失原文数字/引用标记；漂移→修复一轮→回滚 ----
        pre = sec.get("pre_enhance_content") or ""

        def _key_tokens(s: str):
            nums = re.findall(r"\d+\.\d+|\d{2,5}", s)
            cites = re.findall(r"\[\d{1,3}\]", s)
            return nums, cites

        pre_nums, pre_cites = _key_tokens(pre)
        post_nums, post_cites = _key_tokens(full)
        lost_nums = [n for n in pre_nums if n not in post_nums]
        lost_cites = [c for c in pre_cites if c not in post_cites]
        if (lost_nums or lost_cites) and pre:
            yield {"type": "status",
                   "content": f"⚠ 改写丢失关键要素（数字 {len(lost_nums)} / 引用 {len(lost_cites)}），自动修复…"}
            repair = (
                "你上一轮的改写丢失了以下关键数字/引用标记，必须原样补回（位置尽量对应原意）：\n"
                + (f"缺失数字：{lost_nums[:20]}\n" if lost_nums else "")
                + (f"缺失引用：{lost_cites[:20]}\n" if lost_cites else "")
                + f"\n改写稿：\n{full}\n\n只输出补回后的完整正文，不得改动其他内容。")
            fixed_parts = []
            try:
                async for piece in self.sw._stream_llm(
                        "你是文本修复器：只补回缺失的数字与引用标记，其余一字不改。",
                        repair, fixed_parts):
                    pass
                fixed_text = "".join(fixed_parts).strip()
                f_nums, f_cites = _key_tokens(fixed_text)
                still = ([n for n in pre_nums if n not in f_nums]
                         + [c for c in pre_cites if c not in f_cites])
                if not still:
                    full = fixed_text
                    logger.info("锁字修复成功")
                else:
                    logger.warning(f"锁字修复仍缺失 {len(still)}，回滚增强前快照")
                    full = pre
            except Exception as e:
                logger.warning(f"锁字修复失败，回滚快照: {e}")
                full = pre

        after_report = detect_ai_markers(full)

        # ---- P2 闭环降AI（v22）：复检不达标 → 自动追加定向重写轮（最多 3 轮总） ----
        # 思路吸收自 baibaiAIGC 多轮迭代：检测→改写→复检循环；每轮只针对剩余痕迹补刀，
        # 并重新过锁字校验（数字/引用丢失→回滚该轮），绝不牺牲事实保真换低分
        _LOOP_THRESHOLD = 55  # 与全文流水线 auto_humanize 阈值一致
        # ---- 本地蒸馏模型深改轮（humanize-9b via Ollama）：规则引擎前的重型改写 ----
        if mode == "humanize" and after_report["score"] > _LOOP_THRESHOLD:
            try:
                from src.local_humanize import available as _lh_avail, rewrite as _lh_rewrite
                if _lh_avail():
                    yield {"type": "status",
                           "content": "🧠 本地蒸馏模型 humanize-9b 深度改写中…"}
                    deep = await asyncio.to_thread(_lh_rewrite, content)
                    if deep:
                        _rd = detect_ai_markers(deep)
                        if _rd["score"] < after_report["score"]:
                            full = deep
                            after_report = _rd
                            yield {"type": "status",
                                   "content": f"🧠 蒸馏模型改写完成：AI味 → {_rd['score']} 分"}
                        else:
                            yield {"type": "status",
                                   "content": "🧠 蒸馏模型改写未见改善，转规则补刀"}
                    else:
                        yield {"type": "status",
                               "content": "🧠 蒸馏模型输出未通过锁字校验，转规则补刀"}
            except Exception as e:
                logger.warning("本地蒸馏引擎不可用（跳过）: %s", e)
        _round = 1
        _best_full, _best_report = full, after_report  # 多轮取最优：变差则回退最佳轮
        while (mode == "humanize" and _round < 3
               and after_report["score"] > _LOOP_THRESHOLD):
            _round += 1
            remaining = _markers_report_for_prompt(after_report)
            yield {"type": "status",
                   "content": f"🔁 闭环降AI 第 {_round} 轮：复检 {after_report['score']} 分仍高于 {_LOOP_THRESHOLD}，针对剩余痕迹定向重写…"}
            parts2: list = []
            blocks2 = self._split_for_enhance(full)
            try:
                for bi, block in enumerate(blocks2):
                    locked2 = self._extract_locked_tokens(block)
                    spec2 = (
                        "# 任务\n"
                        f"这是「{sec['title']}」的第 {_round} 轮去AI味补刀：上一轮改写后复检仍检出以下痕迹。"
                        "**只修改命中痕迹的句子（句式骨架级修复），其余句子一字不动**；"
                        "句式变换优先于同义词替换；不得拆散自然长句。\n"
                        f"# 剩余体检报告\n{remaining}\n\n"
                        f"# 不可改动的锁定要素\n{locked2 or '（同样不得新增数字/引用）'}\n\n"
                        "# 输出要求\n- 直接输出该片段改写后的正文（markdown，保留标题层级与段落划分）\n"
                        f"- 长度与原片段相当（{len(block)} 字符左右，±10%）\n"
                        "- 只输出正文，不要任何解释。\n\n"
                        f"# 待改写片段\n{block}")
                    async for piece in self.sw._stream_llm(system_prompt, spec2, parts2):
                        yield {"type": "chunk", "content": piece}
                cand = "".join(parts2).strip()
                # 本轮锁字校验：丢失即回滚（保持上一轮结果）
                c_nums, c_cites = _key_tokens(cand)
                lost2 = ([n for n in pre_nums if n not in c_nums]
                         + [c for c in pre_cites if c not in c_cites])
                if lost2:
                    logger.warning("闭环第 %d 轮丢失 %d 个锁定要素，回滚该轮", _round, len(lost2))
                    yield {"type": "status",
                           "content": f"⚠ 第 {_round} 轮改写丢失关键要素，已回滚（保留上一轮结果）"}
                    break
                full = cand
                after_report = detect_ai_markers(full)
                if after_report["score"] < _best_report["score"]:
                    _best_full, _best_report = full, after_report
            except Exception as e:
                logger.warning("闭环降AI第 %d 轮失败（保留上一轮结果）: %s", _round, e)
                break
        # 未达标但已到轮次上限：采用历史最优轮（防止越改越差）
        if after_report["score"] > _best_report["score"]:
            logger.info("闭环降AI采用最优轮：%d → %d", after_report["score"], _best_report["score"])
            yield {"type": "status",
                   "content": f"ℹ 已达轮次上限，采用效果最优的一轮（AI味 {_best_report['score']} 分）"}
            full, after_report = _best_full, _best_report

        after_score = after_report["score"]

        sec["content"] = full
        sec["actual_words"] = count_cn_words(full)
        sec["enhance_mode"] = mode
        sec["enhance_strength"] = strength
        sec["enhance_scores"] = {"before": before_score, "after": after_score}
        sec["written_at"] = datetime.now().isoformat()
        self.sw.outline_gen.save_outline(profile.get("project_name", "project"), outline)

        yield {"type": "enhance_done", "section_id": section_id,
               "actual_words": sec["actual_words"],
               "before_score": before_score, "after_score": after_score,
               "markers_after": after_report.get("markers", [])}


def get_enhancer(section_writer: SectionWriter, config=None) -> TextEnhancer:
    return TextEnhancer(section_writer, config)
