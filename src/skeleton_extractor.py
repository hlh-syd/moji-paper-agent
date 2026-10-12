"""
写作骨架提取 Agent（模板论文 → 叙事形式，通道 B）
=====================================================
从模板论文中蒸馏**与研究内容无关的写作骨架**：体裁、宏观叙事弧、各章
修辞移动（moves）、递进规则、篇幅占比、跨章承接关系、修辞规范。

方法论：体裁分析 / 移动分析（Swales CARS、IMRaD move structure）——
LLM 标注修辞移动已被验证可靠（Yu et al. 2025, LLM-assisted move annotation）。

两阶段单 Agent：
  阶段1 分章标注：每章 → 功能角色 + 论证动作序列 + 段落模式；
  阶段2 骨架蒸馏：全量标注 + 目录 → 谓词化骨架 JSON（严格 schema）。

防串味（内容隔离）：实体黑名单校验——骨架中禁止出现模板论文的专有实体
（方法名/缩写/标题术语），命中即打回重写；仍命中则删除违规条目并标记。

兜底：LLM 失败 → rule_based_skeleton（按体裁预设移动 + 字数占比归一）。
"""
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import AppConfig, get_config_manager

logger = logging.getLogger(__name__)

REF_DIR = Path(__file__).resolve().parents[1] / "reference_papers"

# 规范槽位（体裁级容器）——映射与匹配都用它
CANON_SLOTS = ("摘要", "引言", "综述", "方法", "结果", "讨论", "结论")

_SLOT_KEYWORDS = {
    "摘要": ("摘要", "abstract"),
    "引言": ("引言", "前言", "introduction", "背景"),
    "综述": ("综述", "回顾", "literature"),
    "方法": ("方法", "材料", "模型", "设计", "数据", "method", "patient", "队列", "统计"),
    "结果": ("结果", "发现", "实验", "评估", "result", "表现", "验证"),
    "讨论": ("讨论", "discussion"),
    "结论": ("结论", "总结", "展望", "conclusion"),
}

_STOP_CJK = set("的了与和在对基于及其对于通过利用分析研究方法结果目的结论背景数据"
                "应用相关系统模型实验设计评估影响特征表达构建预测意义价值问题讨论我们本文"
                "本章小结材料资料临床患者标本队列样本回顾性前瞻性不同及其之间以及其他以上以下")

# 规则兜底：各规范槽位的通用移动序列（学位论文/期刊通用体裁）
_PRESET_MOVES = {
    "摘要": ["背景一句 → 目的 → 方法（关键设计）→ 主结果（含关键数字位）→ 结论一句",
             "不引文献、不缩写未定义、数字与正文一致"],
    "引言": ["领域重要性（临床+流行病学证据）", "既有手段与局限（按技术演进排列 2-3 类）",
             "空缺陈述（本研究要填什么）", "贡献预告（与空缺一一对应）"],
    "综述": ["按主题分块（每块 = 一个子领域）", "每块：代表性工作 → 方法要点 → 局限",
             "收束到与本文最相关的缺口"],
    "方法": ["按数据流顺序：来源 → 入排标准 → 预处理 → 特征 → 建模 → 验证 → 指标",
             "每个方法给参数、软件与引用", "统计方案独立成节（检验+多重校正）"],
    "结果": ["主结果先行（回答核心问题）", "分层/亚组分析（每层一个预设问题）",
             "与既往研究对照", "敏感性/稳健性分析收尾"],
    "讨论": ["主发现 paraphrase（不复述数字）", "与文献逐条对照（解释异同）",
             "机制解释 → 临床/应用意义", "局限性（诚实列举）", "展望（可行动）"],
    "结论": ["一句话回答研究问题", "核心数字复现", "适用边界", "一句展望"],
}


def canonical_slot(title: str) -> str:
    """章节标题 → 规范槽位（模糊匹配）"""
    t = (title or "").lower()
    for slot, keys in _SLOT_KEYWORDS.items():
        if any(k in t for k in keys):
            return slot
    return "其他"


def _cjk_runs(text: str) -> list:
    """连续中文段（黑名单素材）"""
    return [r for r in re.findall(r"[\u4e00-\u9fff]{2,10}", text or "")]


def build_entity_blacklist(structure: dict, text: str) -> list:
    """专有实体黑名单：方法名 + 缩写 + 高频长词 + 标题术语（排除通用学术词）"""
    terms = set()
    for m in structure.get("methods_used", []) or []:
        for tok in re.split(r"[^\w\u4e00-\u9fff]+", str(m)):
            tok = tok.strip()
            if len(tok) >= 3 and tok not in _STOP_CJK:
                terms.add(tok)
    for acr in re.findall(r"\b[A-Z]{2,10}\d*\b", text or ""):
        terms.add(acr)
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text or ""):
        if (text or "").count(tok) >= 4 and tok.lower() not in (
                "with", "from", "that", "this", "were", "have", "using", "between",
                "based", "which", "respectively", "analysis", "model", "models",
                "data", "study", "results", "methods", "table", "figure", "using"):
            terms.add(tok)
    title = structure.get("title") or ""
    for run in _cjk_runs(title):
        if run not in _STOP_CJK and len(run) >= 2:
            terms.add(run)
    # 高频中文长词（≥3 字，出现在正文 ≥8 次）视作专有实体
    cnt = Counter(re.findall(r"[\u4e00-\u9fff]{3,6}", text or ""))
    for term, c in cnt.items():
        if c >= 8 and not any(term.startswith(s) or s in term for s in
                              ("研究所", "研究生", "准确性", "稳定性", "有效性", "可靠")):
            # 排除纯通用学术词组
            if not all(ch in _STOP_CJK for ch in term):
                terms.add(term)
    return sorted(t for t in terms if len(t) >= 2)[:120]


def _find_violations(skeleton: dict, blacklist: list) -> list:
    """骨架序列化后扫描黑名单命中"""
    blob = json.dumps(skeleton, ensure_ascii=False)
    return [t for t in blacklist if t in blob]


def _sanitize_skeleton(skeleton: dict, blacklist: list) -> dict:
    """删除含黑名单实体的 moves/progression/links（保留骨架结构）"""
    def _clean_str(s):
        return s if not any(t in s for t in blacklist) else None

    removed = []
    for sec in skeleton.get("sections", []) or []:
        moves = sec.get("moves") or []
        kept = [m for m in moves if _clean_str(str(m))]
        if len(kept) != len(moves):
            removed.extend(str(m) for m in moves if m not in kept)
        sec["moves"] = kept
        for field in ("progression", "role"):
            v = sec.get(field)
            if v and _clean_str(str(v)) is None:
                removed.append(str(v))
                sec[field] = "（已清洗：原文含模板专有内容）"
    links = skeleton.get("cross_section_links") or []
    kept_links = []
    for lk in links:
        note = str(lk.get("note") or lk.get("type") or "")
        if _clean_str(note) is not None:
            kept_links.append(lk)
        else:
            removed.append(note)
    skeleton["cross_section_links"] = kept_links
    if removed:
        skeleton["sanitized"] = True
        skeleton["sanitized_note"] = f"已剔除 {len(removed)} 条含模板专有实体的条目"
    return skeleton


def rule_based_skeleton(structure: dict) -> dict:
    """无 LLM 兜底：从 structure.json 的章节树按规范槽位生成通用骨架"""
    sections = structure.get("sections", []) or []
    slot_order, word_sum = [], 0
    per_slot = {}
    for s in sections:
        slot = canonical_slot(s.get("title", ""))
        w = int(s.get("approx_words") or 0)
        if slot not in per_slot:
            per_slot[slot] = 0
            slot_order.append(slot)
        per_slot[slot] += w
        word_sum += w
    top_slots = [sl for sl in slot_order if sl != "其他"] or ["引言", "方法", "结果", "讨论"]
    total = word_sum or 1
    sk_sections = []
    for slot in top_slots:
        sk_sections.append({
            "slot": slot, "source_titles": [s.get("title", "") for s in sections
                                            if canonical_slot(s.get("title", "")) == slot][:4],
            "role": {"引言": "漏斗式收窄", "方法": "可复现性基石", "结果": "主张-证据对齐",
                     "讨论": "解释与定位", "结论": "收束与边界", "摘要": "全文微缩",
                     "综述": "领域地图"}.get(slot, "承接"),
            "moves": _PRESET_MOVES.get(slot, ["按逻辑顺序展开", "每段一个主旨"]),
            "progression": "每小节 = 一个更窄的子问题；段模式 = 主题句 + 证据 + 过渡句",
            "word_share": round(per_slot.get(slot, 0) / total, 2),
        })
    if not any(s.get("slot") == "摘要" for s in sk_sections):
        sk_sections.insert(0, {"slot": "摘要", "source_titles": [],
                               "role": "全文微缩", "moves": _PRESET_MOVES["摘要"],
                               "progression": "目的→方法→结果→结论四句式",
                               "word_share": 0.03})
    return {
        "genre": structure.get("paper_type") or "学术论文",
        "macro_arc": "背景 → 数据与方法 → 结果 → 解释与意义 → 局限与展望",
        "sections": sk_sections,
        "cross_section_links": [
            {"from": "引言", "to": "结果", "type": "承诺-兑现", "note": "贡献预告与结果组织一一对应"},
            {"from": "方法", "to": "结果", "type": "定义先行", "note": "指标与小节在方法章定义后结果章使用"},
            {"from": "结果", "to": "讨论", "type": "回扣", "note": "讨论逐条引用结果数值"}],
        "rhetoric": {"citation_density_per_kchar": 3.0,
                     "paragraph_len_chars": [180, 260],
                     "tense_conventions": "方法过去时；结论现在时",
                     "hedging_style": "谨慎限定（提示/可能/在一定程度上）"},
        "extracted_at": datetime.now().isoformat(),
        "backend": "rule",
    }


class SkeletonExtractor:
    """写作骨架提取 Agent（两阶段蒸馏 + 黑名单校验 + 规则兜底）"""

    def __init__(self, llm_manager, config: Optional[AppConfig] = None):
        self.llm_manager = llm_manager
        self.config = config or get_config_manager().get_config()
        self.jobs: dict = {}  # {name: {state, error, started_at, finished_at}}

    # ---------- 主流程 ----------
    async def extract(self, structure: dict, text: str) -> dict:
        sections = structure.get("sections", []) or []
        if not sections:
            return rule_based_skeleton(structure)
        blacklist = build_entity_blacklist(structure, text or "")
        annotations = await self._stage1_annotate(structure, text)
        skeleton = await self._stage2_distill(structure, annotations, blacklist)
        if skeleton is None:
            logger.warning("骨架蒸馏失败，退回规则骨架")
            return rule_based_skeleton(structure)
        violations = _find_violations(skeleton, blacklist)
        if violations:
            skeleton = _sanitize_skeleton(skeleton, blacklist)
            skeleton["violations_cleaned"] = violations[:10]
        skeleton["extracted_at"] = datetime.now().isoformat()
        skeleton["backend"] = "llm"
        return skeleton

    # ---------- 阶段1：分章标注 ----------
    async def _stage1_annotate(self, structure: dict, text: str) -> list:
        chapters = []
        secs = structure.get("sections", []) or []
        # 全文按章节标题切 excerpt
        for i, s in enumerate(secs):
            title = (s.get("title") or "").strip()
            excerpt = ""
            if title and text:
                pos = text.find(title)
                if pos < 0:  # 容错：去空格再找
                    pos = text.find(re.sub(r"\s+", "", title)[:6])
                if pos >= 0:
                    end = len(text)
                    for t2 in [x.get("title", "") for x in secs[i + 1:i + 4]]:
                        p2 = text.find(t2, pos + len(title))
                        if p2 > 0:
                            end = min(end, p2)
                    excerpt = text[pos: min(end, pos + 900)]
            chapters.append({
                "title": title, "level": s.get("level", 1),
                "summary": (s.get("summary") or "")[:120],
                "approx_words": s.get("approx_words") or 0,
                "excerpt": excerpt[:700],
            })
        # 只保留一级章 + 重要二级（控 token）
        majors = [c for c in chapters if int(c.get("level") or 1) == 1] or chapters
        if len(majors) > 18:
            majors = majors[:18]

        payload = json.dumps(majors, ensure_ascii=False, indent=1)[:20000]
        prompt = """你是学术写作体裁分析专家（move analysis）。对下面论文的每一章做
**写作形式标注**（只标注形式，禁止记录任何具体研究内容：疾病名/基因名/数据集名/
方法名一律不得出现，用「X/Y/Z」或「该疾病/该方法」指代）。

每章输出：
{"title": "章名", "role": "该章功能（如：漏斗式收窄/可复现性基石/主张-证据对齐）",
 "moves": ["该章的论证动作序列，谓词化描述，每条一句话"],
 "paragraph_pattern": "段落组织模式（如：主题句+证据+过渡句）",
 "transition_in": "如何承接上一章（谓词化）", "transition_out": "如何引出下一章（谓词化）"}

只输出 JSON 数组（不要代码块、不要解释）。

# 论文章节（含节选）
""" + payload
        try:
            resp = await self._invoke(prompt)
            data = self._parse_json(resp, expect_list=True)
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            logger.warning(f"骨架阶段1失败: {e}")
        return []

    # ---------- 阶段2：骨架蒸馏 ----------
    async def _stage2_distill(self, structure: dict, annotations: list,
                              blacklist: list) -> Optional[dict]:
        if not annotations:
            return None
        words_hint = {}
        secs = structure.get("sections", []) or []
        for s in secs:
            slot = canonical_slot(s.get("title", ""))
            words_hint[slot] = words_hint.get(slot, 0) + int(s.get("approx_words") or 0)
        total_w = sum(words_hint.values()) or 1
        shares = {k: round(v / total_w, 2) for k, v in words_hint.items()}

        prompt = f"""你是学术写作体裁分析专家。把下面的分章标注**蒸馏成一份与研究内容无关的
写作骨架**。这是写作形式模板：所有 moves/progression 必须是谓词化描述，
**严禁出现任何专有实体**（黑名单词出现即判不合格）：{json.dumps(blacklist[:60], ensure_ascii=False)}

输出 JSON（不要代码块）：
{{
  "genre": "体裁（如：专业学位硕士论文·数据驱动型医学研究）",
  "macro_arc": "全文叙事弧一句话（背景→…→展望）",
  "sections": [
    {{"slot": "规范槽位（摘要/引言/综述/方法/结果/讨论/结论）",
      "source_titles": ["模板中对应章名"],
      "role": "该槽位功能",
      "moves": ["修辞移动序列（谓词化，2-5条）"],
      "progression": "小节递进规则（每小节=什么？段落模式？）",
      "word_share": 0.35}}
  ],
  "cross_section_links": [
    {{"from": "引言", "to": "结果", "type": "承诺-兑现",
      "note": "什么必须先写、什么必须后兑现（谓词化）"}}
  ],
  "rhetoric": {{"citation_density_per_kchar": 3.5,
    "paragraph_len_chars": [180, 260],
    "tense_conventions": "时态惯例",
    "hedging_style": "限定语气风格"}}
}}

要求：word_share 按参考占比调整后归一（≈1.0）：{json.dumps(shares, ensure_ascii=False)}
sections 覆盖全部规范槽位（模板没有的槽位省略）；links 3-6 条。

# 分章标注
{json.dumps(annotations, ensure_ascii=False, indent=1)[:14000]}
"""
        try:
            resp = await self._invoke(prompt)
            data = self._parse_json(resp)
            if not isinstance(data, dict) or not data.get("sections"):
                return None
            # 规范化
            for sec in data["sections"]:
                sec["slot"] = canonical_slot(sec.get("slot") or sec.get("source_titles", [""])[0])
                sec["moves"] = [str(m) for m in (sec.get("moves") or [])][:6]
            # word_share 归一
            shares_raw = [max(0.0, float(s.get("word_share") or 0)) for s in data["sections"]]
            ssum = sum(shares_raw) or 1.0
            for sec, v in zip(data["sections"], shares_raw):
                sec["word_share"] = round(v / ssum, 2)
            data["cross_section_links"] = [
                {"from": canonical_slot(str(l.get("from", ""))),
                 "to": canonical_slot(str(l.get("to", ""))),
                 "type": str(l.get("type", "承接")), "note": str(l.get("note", ""))[:120]}
                for l in (data.get("cross_section_links") or []) if isinstance(l, dict)
            ][:8]
            return data
        except Exception as e:
            logger.warning(f"骨架阶段2失败: {e}")
            return None

    # ---------- LLM 工具 ----------
    async def _invoke(self, prompt: str) -> str:
        from langchain_core.messages import HumanMessage
        llm = self.llm_manager.get_main_llm()
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        return resp.content if isinstance(resp.content, str) else str(resp.content)

    @staticmethod
    def _parse_json(content: str, expect_list: bool = False):
        text = content.strip()
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if m:
            text = m.group(1)
        if expect_list:
            start, end = text.find("["), text.rfind("]")
        else:
            start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("无 JSON")
        return json.loads(text[start:end + 1])

    # ---------- 后台编排 ----------
    @staticmethod
    def _key(name: str) -> str:
        from src import scoping
        return scoping.owner_key(name)

    async def run_for(self, name: str, reference_parser) -> dict:
        """上传后异步执行：structure + 全文 → 骨架 → 持久化；状态自管理（键按用户隔离）"""
        self.jobs[self._key(name)] = {"state": "running", "error": "",
                                      "started_at": datetime.now().isoformat(timespec="seconds")}
        try:
            structure = reference_parser.get_structure(name)
            if structure is None:
                raise FileNotFoundError(f"参考论文不存在: {name}")
            safe = structure.get("name", name)
            text = ""
            from src import scoping
            tp = scoping.reference_dir() / f"{safe}.text.txt"
            if tp.exists():
                text = tp.read_text(encoding="utf-8", errors="ignore")
            skeleton = await self.extract(structure, text)
            reference_parser.save_skeleton(safe, skeleton)
            self.jobs[self._key(name)] = {"state": "done", "error": "",
                                          "finished_at": datetime.now().isoformat(timespec="seconds")}
            logger.info(f"写作骨架提取完成: {name} ({len(skeleton.get('sections', []))} 槽位, "
                        f"backend={skeleton.get('backend')})")
            return skeleton
        except Exception as e:  # noqa: BLE001
            self.jobs[self._key(name)] = {"state": "error", "error": str(e)[:300],
                                          "finished_at": datetime.now().isoformat(timespec="seconds")}
            logger.warning(f"写作骨架提取失败 {name}: {e}")
            return {}

    def status(self, name: str) -> dict:
        from src import scoping
        job = self.jobs.get(self._key(name))
        if job:
            return dict(job)
        if (scoping.reference_dir() / f"{name}.skeleton.json").exists():
            return {"state": "done"}
        return {"state": "idle"}

    # ---------- 注入文本构建（供大纲/写作/自查使用） ----------
    @staticmethod
    def skeleton_card(skeleton: dict, max_chars: int = 3000) -> str:
        """大纲生成注入用：骨架卡（槽位+移动+占比+弧线）"""
        if not skeleton:
            return ""
        lines = [f"体裁：{skeleton.get('genre', '')}",
                 f"叙事弧：{skeleton.get('macro_arc', '')}", "槽位清单："]
        for sec in skeleton.get("sections", []):
            moves = "；".join(str(m)[:60] for m in (sec.get("moves") or [])[:4])
            lines.append(f"- {sec.get('slot', '?')}（占比 {sec.get('word_share', 0):.0%}，"
                         f"{sec.get('role', '')}）：{moves}")
        links = skeleton.get("cross_section_links") or []
        if links:
            lines.append("跨章承接（环环相扣）：" + "；".join(
                f"{l.get('from')}→{l.get('to')}({l.get('type')})" for l in links[:6]))
        return "\n".join(lines)[:max_chars]

    @staticmethod
    def brief_for_section(skeleton: Optional[dict], section: dict) -> str:
        """分节写作注入用：本节骨架简报（moves + 递进规则 + 承接链 + 修辞规范）"""
        if not skeleton:
            return ""
        slot = section.get("skeleton_slot") or canonical_slot(section.get("title", ""))
        target = None
        for sec in skeleton.get("sections", []):
            if sec.get("slot") == slot:
                target = sec
                break
        if target is None:
            return ""
        lines = [f"本节槽位：{slot}（{target.get('role', '')}；篇幅占比参考 "
                 f"{target.get('word_share', 0):.0%}）"]
        moves = target.get("moves") or []
        if moves:
            lines.append("修辞移动序列（按序组织本节论证动作）：")
            lines += [f"{i}. {m}" for i, m in enumerate(moves, 1)]
        if target.get("progression"):
            lines.append(f"小节递进规则：{target['progression']}")
        links = [l for l in (skeleton.get("cross_section_links") or [])
                 if l.get("to") == slot]
        if links:
            lines.append("跨章承接（本章必须兑现的承诺/回扣）：")
            lines += [f"- 来自{ l.get('from')}（{l.get('type')}）：{l.get('note')}"
                      for l in links[:4]]
        rh = skeleton.get("rhetoric") or {}
        if rh:
            bits = []
            if rh.get("paragraph_len_chars"):
                pl = rh["paragraph_len_chars"]
                bits.append(f"段落长度 {pl[0]}~{pl[-1]} 字")
            if rh.get("citation_density_per_kchar"):
                bits.append(f"引用密度 ≈{rh['citation_density_per_kchar']}/千字")
            if rh.get("tense_conventions"):
                bits.append(f"时态：{rh['tense_conventions']}")
            if rh.get("hedging_style"):
                bits.append(f"语气：{rh['hedging_style']}")
            if bits:
                lines.append("修辞规范：" + "；".join(bits))
        lines.append("（以上为模板论文的写作形式约束：只约束论证动作与组织方式，"
                     "一切事实性内容必须来自项目画像与证据，禁止从模板取内容）")
        return "\n".join(lines)

    @staticmethod
    def match_slots(outline_sections: list, skeleton: dict) -> dict:
        """大纲章节 ↔ 骨架槽位映射（硬校验）。返回映射表 + 未覆盖槽位警告"""
        if not skeleton:
            return {"mapping": [], "unmatched_slots": [], "slot_count": 0}
        skeleton_slots = [s.get("slot") for s in skeleton.get("sections", []) if s.get("slot")]
        used = {}
        mapping = []
        for sec in outline_sections:
            canon = canonical_slot(sec.get("title", ""))
            sec["skeleton_slot"] = canon
            mapped = canon if canon in skeleton_slots else None
            if mapped and mapped not in used:
                used[mapped] = sec.get("title", "")
            mapping.append({"section_id": sec.get("id"), "title": sec.get("title"),
                            "slot": canon, "matched": mapped is not None})
        unmatched = [s for s in skeleton_slots if s not in used and s != "其他"]
        return {"mapping": mapping, "unmatched_slots": unmatched,
                "slot_count": len(skeleton_slots),
                "warnings": ([f"骨架槽位「{u}」未在大纲中找到对应章节（该槽位的写作移动未被套用）"
                              for u in unmatched] if unmatched else [])}
