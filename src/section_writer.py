"""
分节写作与重生成模块
- 按大纲的单节生成正文：项目画像 + 方法论文检索 + 写作指导检索 + 宪法约束
- 支持流式输出、单节重生成、中文字数统计回填、全文 docx 导出
"""
import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

from src import fact_guard
from src.reference_manager import ReferenceManager
from src.config import AppConfig, get_config_manager
from src.domain import DOMAINS, DomainProfile, resolve_domain, load_domain_prompt
from src.outline_generator import OutlineGenerator, re_safe_name

logger = logging.getLogger(__name__)

from src import scoping

PROJECTS_DIR = scoping.legacy_projects_dir()  # 兼容旧引用；实际路径走 scoping


class SectionWriter:
    """分节写作器"""

    def __init__(self, llm_manager, vector_store_manager, config: Optional[AppConfig] = None,
                 outline_generator: Optional[OutlineGenerator] = None):
        self.llm_manager = llm_manager
        self.vsm = vector_store_manager
        self.config = config or get_config_manager().get_config()
        self.outline_gen = outline_generator or OutlineGenerator(llm_manager, vector_store_manager, self.config)
        from src.reference_manager import ReferenceManager
        self.refmgr = ReferenceManager()
        self._briefs_cache: Dict[str, dict] = {}  # domain key -> 分节简报

    # ---------- 领域解析 ----------
    def _dom(self, profile: dict, outline: Optional[dict] = None) -> DomainProfile:
        """写作阶段的领域：画像 domain > 大纲 domain > 自动检测"""
        key = ((profile or {}).get("domain") or "").strip()
        if not key and outline:
            key = (outline.get("domain") or "").strip()
        if key in DOMAINS:
            return DOMAINS[key]
        return resolve_domain(profile)

    def _briefs_for(self, dom: DomainProfile) -> dict:
        """加载该领域的分节写作简报（缺失回落基础 section_briefs.json）"""
        if dom.key not in self._briefs_cache:
            path = Path(__file__).parent / "prompts" / dom.briefs_file
            try:
                self._briefs_cache[dom.key] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                base = Path(__file__).parent / "prompts" / "section_briefs.json"
                try:
                    self._briefs_cache[dom.key] = json.loads(base.read_text(encoding="utf-8"))
                except Exception:
                    self._briefs_cache[dom.key] = {}
        return self._briefs_cache[dom.key]

    # ---------- 上下文构建 ----------
    def _build_section_context(self, profile: dict, outline: dict, section: dict,
                               extra_materials: str = "",
                               reference_evidence: str = "",
                               skeleton_brief: str = "") -> str:
        """组装单节写作的全部上下文（reference_evidence: RAG-Anything 检索的参考文献
        多模态证据——图表/公式/表格的原文事实；skeleton_brief: 模板论文写作骨架的
        本节叙事形式约束——修辞移动/递进规则/跨章承接，内容无关）"""
        profile_text = OutlineGenerator.compress_profile(profile, max_chars=12000)

        cited_methods = section.get("cited_methods", []) or \
            [m.get("name_zh") for m in profile.get("methods", [])[:12]]
        # P2 多查询检索：beats/kp 逐条查询合并去重
        queries = [section.get("title", "")]
        for b in section.get("beats", []):
            if b.get("kb_query"):
                queries.append(str(b["kb_query"]))
        for kp in section.get("key_points", [])[:4]:
            queries.append(kp[:40])
        method_papers = self.outline_gen._retrieve_method_papers(cited_methods, k=6,
                                                                 extra_queries=queries)
        writing_guidance = self.outline_gen._retrieve_writing_guidance_multi(
            queries, k=3)

        # 已写好的其他节标题与一句话论证（保证衔接，不塞全文）
        siblings = "\n".join(
            f"- {s['title']}: {'已完成' if s.get('content') else '未完成'}"
            for s in outline.get("sections", []))

        # 递进衔接：上一节全文（扩容截断）+ 前文各节数字摘要（含数字/图表引用的句子）
        secs = outline.get("sections", [])
        cur_idx = next((i for i, s in enumerate(secs) if s.get("id") == section.get("id")), None)
        progression = ""
        if cur_idx is not None and cur_idx > 0:
            prev = secs[cur_idx - 1]
            prev_content = (prev.get("content") or "").strip()
            if prev_content:
                progression += (
                    f"\n## 上一节「{prev['title']}」全文（用于递进衔接，开头自然承接，禁止重复其内容）"
                    + "\n" + prev_content[:12000]
                    + ("\n……（上一节内容过长已截断，但全部关键数字见下）" if len(prev_content) > 12000 else ""))
            earlier = []
            for s in secs[:cur_idx]:
                digest = self._numeric_digest(s.get("content") or "")
                if digest:
                    earlier.append(f"- {s['title']}：{digest}")
            if earlier:
                progression += "\n## 前文各节数字摘要（保持递进与口径一致，避免重复）\n" + "\n".join(earlier)

        # 结果→讨论专用通道：讨论节必须回扣结果节的具体数值
        if cur_idx is not None and "讨论" in (section.get("title") or ""):
            result_secs = [s for s in secs if s.get("content")
                           and any(k in (s.get("title") or "") for k in ("结果", "发现"))]
            for rs in result_secs:
                rc = (rs.get("content") or "").strip()
                progression += (
                    f"\n## 结果节「{rs['title']}」全文（讨论的回扣对象：分层面逐条讨论时"
                    f"必须引用这里的具体数值与图表编号，禁止泛泛而谈）\n" + rc[:15000])
            kr = profile.get("key_results") or []
            if kr:
                table = "\n".join(
                    f"- {r.get('metric')} = {r.get('value')}"
                    for r in kr[:60])
                progression += "\n## 关键结果数字表（讨论回扣的口径基准）\n" + table

        parts = [
            f"## 一句话论证（全文锚点）\n{outline.get('one_sentence_argument', '')}",
            f"\n## 全文章节状态\n{siblings}",
        ]
        # 研究项目主体（理解 Agent 产出）：研究顺序/核心脚本/结果图表路径/创新点的权威上下文
        project_body = (outline.get("project_body") or "").strip()
        if project_body:
            parts.insert(0, "# 研究项目主体（最高优先级：章节组织与内容组织以此为准）\n"
                         + project_body[:12000])
        parts += [
            f"\n## 项目画像（唯一事实来源）\n{profile_text}",
            f"\n## 本节相关信息（方法原论文，引用支撑，含 GB/T 著录）\n{method_papers[:6000]}",
            f"\n## 本节写作指导\n{writing_guidance[:2000]}",
        ]
        if skeleton_brief:
            parts.append("\n## 写作骨架约束（模板论文的叙事形式：只约束论证动作与组织方式，"
                         "一切事实性内容必须来自项目画像与证据，禁止从模板取内容）\n"
                         + skeleton_brief)
        if reference_evidence:
            parts.append(
                "\n## 参考文献多模态证据（RAG-Anything 检索：他人论文图表/公式/表格的原文事实，"
                "仅供借鉴表述与核对口径，禁止照抄原文）\n" + reference_evidence)
        if progression:
            parts.append(progression)
        # P1 引用池：全文统一编号，只允许引用池内条目（摘要/结论不引文献）
        title = section.get("title", "")
        if not any(k in title for k in ("摘要", "结论")):
            pool = self.refmgr.build_global_pool(outline)
            min_cite = next((v for k, v in (("材料与方法", 15), ("方法", 15),
                                            ("讨论", 12), ("前言", 10),
                                            ("结果", 10), ("发现", 10))
                             if k in title), 8)
            candidates = self.refmgr.suggest_candidates(section, pool, k=12)
            parts.append("\n## 全文统一引用池（正文用 [n] 引用标记）\n"
                         + self.refmgr.pool_prompt_text(pool, section, candidates,
                                                        min_citations=min_cite))
        if extra_materials:
            parts.append(f"\n## 作者补充材料（会话中注入的文件/要求）\n{extra_materials[:4000]}")
        return "\n".join(parts)

    _NUM_SENT_RE = re.compile(
        r"[^。！？\n]*?(?:\d+\.\d+|P\s*[<＜=]\s*0?\.\d+|95%\s*CI|HR|C指数|AUC|图\d|表\d)[^。！？\n]*[。！？]?")

    @staticmethod
    def _protected_spans(text: str) -> list:
        """判重豁免的原子区段：图片markdown、表格行、公式——它们内部的
        逐字重复（共享路径前缀/表头）是正常形态，绝不允许被交叠消除拦截腰截断。
        引文 [n] 不在豁免之列：判重前会被等长掩码（见 _mask_citations），
        这样含引文的重复段照常清除且不会切走闭合括号。"""
        spans = []
        spans.extend((m.start(), m.end()) for m in re.finditer(r"!\[[^\]]*\]\([^)]*\)", text))
        spans.extend((m.start(), m.end()) for m in re.finditer(r"^\|.*$", text, re.M))
        spans.extend((m.start(), m.end()) for m in re.finditer(r"\$\$.*?\$\$", text, re.S))
        spans.sort()
        return spans

    @staticmethod
    def _mask_citations(text: str) -> str:
        """引文 [n]/[n,m] → 等长 # 掩码（索引与原文一一对应）。
        重复检测在掩码串上进行：同一文本的重复段掩码后仍逐字相同（可检出），
        不同引文的段落掩码不同（不误合并）；删除区间按索引回贴原文执行。"""
        return re.sub(r"\[\d{1,3}(?:[,，\-–]\d{1,3})*\]", lambda m: "#" * len(m.group(0)), text)

    @staticmethod
    def _dedup_repeated_blocks(text: str, min_block: int = 80) -> tuple:
        """删除 ≥min_block 字的逐字重复大块（保留首次出现）。
        用于清理瞬时错误重试时模型复写"续写参考片段"造成的交叠重复。
        扫描在等长引文掩码串上进行，删除区间按索引回贴原文。"""
        n = len(text)
        if n < min_block * 3:
            return text, 0
        step = max(20, min_block // 2)
        masked = SectionWriter._mask_citations(text)
        spans = SectionWriter._protected_spans(masked)

        def _hits(s, e):
            return any(s < se and e > ss for ss, se in spans)

        seen: dict = {}
        kills: list = []
        removed = 0
        i = 0
        while i + min_block <= n:
            if _hits(i, i + min_block):
                i += step  # 原子区段（图片/表格/公式）不参与逐字判重
                continue
            h = hash(masked[i:i + min_block])
            j = seen.get(h)
            if j is None:
                seen[h] = i
                i += step
                continue
            # 命中：双向扩展重复区间（第二处从 i 起，第一处从 j 起）
            e1, e2 = i + min_block, j + min_block
            while e1 < n and e2 < i and masked[e1] == masked[e2]:
                e1 += 1
                e2 += 1
            s2 = i
            while s2 > 0 and j > 0 and text[s2 - 1] == text[j - 1] and j - 1 < s2 - 1:
                s2 -= 1
                j -= 1
            # 只删除第二处出现（i 起点），扩展到行边界更整齐
            while s2 > 0 and text[s2 - 1] not in "\n":
                s2 -= 1
            while e1 < n and text[e1] not in "\n":
                e1 += 1
            if e1 > n:
                e1 = n
            if not _hits(s2, e1):  # 删除区间触及原子区段 → 放弃该次删除
                kills.append((s2, e1))
                removed += 1
            i = e1 + 1
        if not kills:
            return text, 0
        out, pos = [], 0
        for s, e in sorted(kills):
            if s < pos:  # 区间交叠（嵌套重复）跳过
                continue
            out.append(text[pos:s])
            pos = max(pos, e)
        out.append(text[pos:])
        clean = "".join(out)
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean, removed

    @staticmethod
    def _dedup_heading_blocks(text: str) -> tuple:
        """重复标题的块（标题→下一标题）只保留字数最多的一份（前份常为截断版）"""
        lines = text.split("\n")
        # 定位所有标题行
        marks = [(i, re.sub(r"\s+", "", ln.lstrip("#").strip()))
                 for i, ln in enumerate(lines) if re.match(r"^#{1,4}\s+\S", ln)]
        if not marks:
            return text, 0
        # 按（归一化）标题分组（论文章节标题应当唯一；保留字数最多的块）
        from collections import defaultdict
        groups = defaultdict(list)
        bounds = []
        for gi, (i, title) in enumerate(marks):
            end = marks[gi + 1][0] if gi + 1 < len(marks) else len(lines)
            bounds.append((i, end, title))
            if len(title) >= 2:
                groups[title].append(len(bounds) - 1)
        kills = set()
        for title, idxs in groups.items():
            if len(idxs) < 2:
                continue
            blocks = [(bi, bounds[bi][1] - bounds[bi][0]) for bi in idxs]
            keep = max(blocks, key=lambda x: x[1])[0]
            for bi, _ in blocks:
                if bi != keep:
                    for ln in range(bounds[bi][0], bounds[bi][1]):
                        kills.add(ln)
        if not kills:
            return text, 0
        out = [ln for i, ln in enumerate(lines) if i not in kills]
        clean = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
        return clean, len([t for t, ix in groups.items() if len(ix) >= 2])

    @staticmethod
    def _dedup_similar_paragraphs(text: str, window: int = 5, threshold: float = 0.8) -> tuple:
        """删除与近邻段高度相似的段落（续写/重试交叠的模糊重复）；
        额外处理"截断句段+完整段"模式：短段是长段前缀子集时删短段"""
        from difflib import SequenceMatcher
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        out = []
        removed = 0
        for p in paras:
            drop = False
            if p.startswith("|") or p.startswith("!["):  # 表格块/图片嵌入块不参与相似判重
                out.append(p)
                continue
            if len(p) >= 40:
                for q in out[-window:]:
                    if len(q) < 40:
                        continue
                    if SequenceMatcher(None, q, p).quick_ratio() >= threshold + 0.02:
                        if SequenceMatcher(None, q, p).ratio() >= threshold:
                            drop = True
                            removed += 1
                            break
                    # 首句相同的双段（续写分叉的典型形态）→ 删短的一份
                    def _first_sent(t):
                        m = re.match(r"^[^。！？]{15,}。", t)
                        return m.group(0) if m else None
                    fs_p, fs_q = _first_sent(p), _first_sent(q)
                    if fs_p and fs_p == fs_q:
                        if len(p) <= len(q):
                            drop = True
                        else:
                            out = [x for x in out if x is not q]
                        removed += 1
                        break
                    # 截断段 p 是已有段 q 的前缀子集（或反之）→ 删短的一份
                    short, long_ = (p, q) if len(p) < len(q) else (q, p)
                    if (len(long_) > len(short) * 1.25 and len(short) >= 40
                            and SequenceMatcher(None, short, long_[:len(short)]).ratio() >= 0.85):
                        if short is p:
                            drop = True
                        else:
                            out = [x for x in out if x is not q]
                        removed += 1
                        break
            if not drop:
                out.append(p)
        if not removed:
            return text, 0
        return "\n\n".join(out), removed

    @staticmethod
    def _dedup_repeated_sentences(text: str, window: int = 6) -> tuple:
        """段内句子级去重：续写复写常表现为同一段里句子重复/前缀复写"""
        from difflib import SequenceMatcher
        out_paras = []
        removed = 0
        for para in re.split(r"\n\s*\n", text):
            if len(para) < 120 or para.lstrip().startswith(("#", "|", "!")):
                out_paras.append(para)
                continue
            sents = [s for s in re.split(r"(?<=[。！？!?])", para) if s.strip()]
            kept = []
            for s in sents:
                drop = False
                if len(s) >= 15:
                    for t in kept[-window:]:
                        if len(t) < 15:
                            continue
                        if SequenceMatcher(None, t, s).ratio() >= 0.82:
                            drop = True
                            removed += 1
                            break
                if not drop:
                    kept.append(s)
            out_paras.append("".join(kept))
        if not removed:
            return text, 0
        return re.sub(r"\n{3,}", "\n\n", "\n\n".join(out_paras)), removed

    @staticmethod
    def _dedup_mid_sentence(text: str, win: int = 40) -> tuple:
        """句中交叠消除：≥win 字的逐字重复子串（续写复写的洋葱层），
        扩展到句子边界后删除第二份；迭代至无命中。图片/表格/公式区段豁免；
        引文经等长掩码参与判重（含引文的洋葱重复照样清除）。"""
        removed = 0
        while True:
            masked = SectionWriter._mask_citations(text)
            n = len(text)
            spans = SectionWriter._protected_spans(masked)

            def _hits(s, e):
                return any(s < se and e > ss for ss, se in spans)

            seen = {}
            hit = None
            i = 0
            while i + win <= n:
                if _hits(i, i + win):
                    i += 1
                    continue
                h = hash(masked[i:i + win])
                j = seen.get(h)
                if j is None:
                    seen[h] = i
                    i += 1  # 步长1：副本起点不对齐网格也能命中
                    continue
                # 双向逐字扩展（掩码串上比较：引文以 # 参与匹配）
                e1, e2 = i + win, j + win
                while e1 < n and masked[e1] == masked[e2]:
                    e1 += 1
                    e2 += 1
                s1, s2 = j, i
                while s2 > 0 and s1 > 0 and masked[s2 - 1] == masked[s1 - 1] and s1 - 1 < s2 - 1:
                    if masked[s2 - 1] in "。！？!?；;\n":  # 句界停驻：句号留给前一份
                        break
                    s2 -= 1
                    s1 -= 1
                if s2 < n - 1 and text[s2] in "。！？!?；;":
                    s2 += 1  # 被删段开头的句号属于前句，不随删
                # 各自扩到句末（。！？后）
                def _sent_end(k):
                    while k < n and text[k - 1] not in "。！？!?":
                        k += 1
                    return min(k, n)
                e1 = _sent_end(e1)
                if not _hits(s2, e1):  # 命中区间触及原子区段 → 放弃（继续向后扫描）
                    hit = (s2, e1)
                    break
                i += win  # 豁免拒绝后必须前进，否则同一位置无限重测（死循环）
            if hit is None:
                return text, removed
            text = text[:hit[0]] + text[hit[1]:]
            removed += 1
            text = re.sub(r"\n{3,}", "\n\n", text)

    @classmethod
    def _numeric_digest(cls, content: str, max_sents: int = 40) -> str:
        """提取一节中含数字/统计量/图表引用的句子——作为该节的数字摘要传给后续节"""
        if not content:
            return ""
        sents = [s.strip() for s in cls._NUM_SENT_RE.findall(content) if len(s.strip()) >= 8]
        text = "；".join(sents[:max_sents])
        return text[:4000]

    # ---------- 引用池扩容 + 小节级图表资源分配 ----------
    def _ensure_method_pool(self, outline: dict, profile: dict) -> None:
        """把画像方法（∩注册表且有论文条目）并入 outline.method_pool——
        引用池全文共享 40-60 条的事实基础；持久化随 save_outline 落盘"""
        known = set(outline.get("method_pool") or [])
        with_paper = set(self.refmgr.names_with_papers())
        for m in profile.get("methods", []):
            for k in (m.get("name_zh"), m.get("name_en")):
                if k and k in with_paper and k not in known:
                    known.add(k)
                    outline.setdefault("method_pool", []).append(k)

    @staticmethod
    def _cn_keys(text: str) -> set:
        """中文文本 → 2-gram 关键词集合（小节↔图表/结果的粗粒度相关性匹配）"""
        keys = set()
        for w in re.split(r"[^\u4e00-\u9fffA-Za-z0-9.]+", text or ""):
            if len(w) >= 4:
                keys.update(w[i:i + 2] for i in range(len(w) - 1))
            elif len(w) >= 2:
                keys.add(w)
        return keys

    @staticmethod
    def _table_eligible(t: dict) -> bool:
        """结果表可否转写为出版表格：排除配置文件与逐样本源数据，
        json 需 ≥3 个数值键（指标字典），csv 需 ≤25 行且非样本清单"""
        f = (t.get("file") or "").lower()
        d = (t.get("dir") or "").lower()
        if "config" in f or d.startswith("config"):
            return False
        pv = t.get("preview") or ""
        if not pv or len(pv) > 2200:
            return False
        if (t.get("format") or "").lower() == "csv":
            head = pv.splitlines()[0] if "\n" in pv else pv
            if any(col in head for col in ("SAMPLE_ID", "PATIENT_ID", "sample_id", "patient_id")):
                return False
            try:
                return int(t.get("rows") or 0) <= 25
            except (TypeError, ValueError):
                return False
        return len(re.findall(r'"\w+"\s*:\s*-?\d+\.?\d*', pv)) >= 3

    @classmethod
    def _assign_unit_resources(cls, profile: dict, section: dict, units: list) -> dict:
        """为多级章的写作单元确定性分配图片与结果表（含章内连续编号起点）。
        - 图片：png/jpg 优先、同文件主干去重、带视觉描述者优先且全覆盖
        - 结果表：json 指标字典优先，按小节标题/要点 2-gram 相关性匹配，零分轮转兜底
        返回 {unit_idx: {"figures": [...], "tables": [...], "fig_start": int, "tbl_start": int}}"""
        title = section.get("title", "")
        wants_fig = any(k in title for k in ("结果", "发现", "方法"))
        wants_tbl = any(k in title for k in ("结果", "发现"))
        eligible = [i for i, u in enumerate(units)
                    if u.get("node") is not None and (u.get("target") or 0) > 0
                    and "小结" not in (u.get("title") or "")]
        if not eligible or not (wants_fig or wants_tbl):
            return {}
        unit_keys = {i: cls._cn_keys((units[i].get("title") or "") + " "
                                     + " ".join(units[i]["node"].get("key_points") or []))
                     for i in eligible}

        def _best_unit(src_text: str, assign: dict, cap: int, force: bool):
            src = cls._cn_keys(src_text)
            scored = sorted(((len(src & unit_keys[i]), -eligible.index(i), i)
                             for i in eligible), reverse=True)
            for s, _, i in scored:
                if s > 0 and len(assign[i]) < cap:
                    return i
            if force:
                for s, _, i in scored:  # 零分轮转兜底（保证全覆盖）
                    if len(assign[i]) < cap:
                        return i
            return None

        res = {}
        fig_assign = {i: [] for i in eligible}
        tbl_assign = {i: [] for i in eligible}
        if wants_fig:
            fig_cap = 4 if any(k in title for k in ("结果", "发现")) else 2
            seen_stem = set()
            fig_pool = []
            for f in sorted(profile.get("figures") or [],
                            key=lambda f: (0 if (f.get("desc") or "").strip() else 1,
                                           0 if (f.get("rel") or f.get("path") or "").lower().endswith(
                                               (".png", ".jpg", ".jpeg")) else 1)):
                p = Path(f.get("rel") or f.get("path") or "")
                if p.stem.lower() in seen_stem:
                    continue
                seen_stem.add(p.stem.lower())
                fig_pool.append(f)
            for f in fig_pool:
                has_desc = bool((f.get("desc") or "").strip())
                src_text = " ".join([f.get("name") or "", f.get("rel") or "",
                                     f.get("desc") or ""])
                i = _best_unit(src_text, fig_assign, fig_cap, force=has_desc)
                if i is not None:
                    fig_assign[i].append(f)
        if wants_tbl:
            tbls = [t for t in (profile.get("result_tables") or []) if cls._table_eligible(t)]
            for t in tbls:
                src_text = " ".join([t.get("dir") or "", t.get("file") or "",
                                     (t.get("preview") or "")[:200]])
                i = _best_unit(src_text, tbl_assign, 2, force=True)
                if i is not None:
                    tbl_assign[i].append(t)
        # 章内连续编号：按单元顺序累积
        fig_n = tbl_n = 0
        for i, u in enumerate(units):
            figs, tbls = fig_assign.get(i) or [], tbl_assign.get(i) or []
            if not figs and not tbls:
                continue
            res[i] = {"figures": figs, "tables": tbls,
                      "fig_start": fig_n + 1, "tbl_start": tbl_n + 1}
            fig_n += len(figs)
            tbl_n += len(tbls)
        logger.info(f"[resources] {title}: {fig_n} 图 / {tbl_n} 表 分配到 {len(res)} 个单元")
        return res

    @staticmethod
    def _unit_resource_spec(ures: dict, ch_no: int) -> str:
        """单元的图表任务说明书（注入 part_spec）：图片嵌入语法 + 表格逐字转录要求"""
        lines = []
        figs, tbls = ures.get("figures") or [], ures.get("tables") or []
        if figs:
            starts = "、".join(f"图{ch_no}-{ures['fig_start'] + k}" for k in range(len(figs)))
            lines.append(f"- 本小节必须嵌入并引用以下 {len(figs)} 张图片（编号依次为 {starts}，"
                         f"除这些图外不得嵌入其他图片）：")
            for k, f in enumerate(figs, 0):
                no = f"图{ch_no}-{ures['fig_start'] + k}"
                rel = f.get("rel") or f.get("path")
                desc = (f.get("desc") or "").strip()
                hint = f"（看图实录：{desc[:90]}）" if desc else ""
                lines.append(f"  {no}：`![{no} {(f.get('name') or '')}]({rel})`{hint}——"
                             f"正文以「如图{no}所示」引用并解读 1-2 句，图片路径必须逐字照抄")
        if tbls:
            starts = "、".join(f"表{ch_no}-{ures['tbl_start'] + k}" for k in range(len(tbls)))
            lines.append(f"- 本小节必须把下列 {len(tbls)} 个结果文件逐字转录为 markdown 三线表"
                         f"（表头行、|---|分隔行、数据行；编号依次为 {starts}）：")
            for k, t in enumerate(tbls, 0):
                no = f"表{ch_no}-{ures['tbl_start'] + k}"
                lines.append(f"  【{no} ← {t.get('dir')}/{t.get('file')}】")
                lines.append("  " + (t.get("preview") or "")[:1000].replace("\n", "\n  "))
                lines.append(f"  要求：表格上方一行粗体题注「**{no} 标题（自拟，说明该表内容）**」；"
                             f"行列与数值必须与上述原文完全一致（保留原精度，禁止增删行、改数、四舍五入）；"
                             f"json 指标按「指标|数值（95%CI）」整理，英文键名译为中文指标名；"
                             f"正文以「具体结果见{no}」指向并解读 1-2 句")
        return "\n".join(lines) + "\n" if lines else ""

    # ---------- 生成 ----------
    LONG_SECTION_THRESHOLD = 4000   # 超过此目标字数的节自动拆分多次生成
    PART_TARGET = 3500              # 每次生成的目标字数（避免超出单次输出上限）

    # 瞬时错误标记（网络抖动/限流/网关波动）——可自动重试
    _TRANSIENT = ("connection", "timeout", "timed out", "network", "429",
                  "500", "502", "503", "504", "overloaded", "rate limit",
                  "reset", "eof", "incomplete", "api connection error")

    @classmethod
    def _is_transient(cls, err: Exception) -> bool:
        s = str(err).lower()
        return any(m in s for m in cls._TRANSIENT)

    # 重试重置标记（仅 restart_marker 模式）：消费方收到后应清空本段已积累文本重新开始
    RESTART = "\x00RESTART\x00"

    @staticmethod
    def _overlap_len(a: str, b: str, cap: int = 300) -> int:
        """a 尾部与 b 头部的最长逐字重叠长度（≤cap）"""
        best = 0
        for k in range(min(cap, len(a), len(b)), 20, -1):
            if a[-k:] == b[:k]:
                return k
        return best

    async def _stream_llm(self, system_prompt: str, user_prompt: str, sink: list,
                          restart_marker: bool = False):
        """流式调用主模型；瞬时错误自动重试（最多3次）。
        restart_marker=True（章节主生成）：重试改为静默完整重生成，
        若模型从头重写则发 RESTART 标记让消费方重置本段——彻底杜绝续写乱序/交叠。"""
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = self.llm_manager.get_main_llm()
        delay = 2
        attempt = 0
        while attempt < 3:
            attempt += 1
            try:
                messages = [SystemMessage(content=system_prompt),
                            HumanMessage(content=user_prompt)]
                # 小块合并下发（≥80字符才flush），把SSE事件数压低一个量级，
                # 避免长节（结果节3000+块）把前端渲染拖死
                buf, buf_len = [], 0
                async for chunk in llm.astream(messages):
                    piece = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                    if piece:
                        buf.append(piece)
                        buf_len += len(piece)
                        if buf_len >= 80:
                            out = "".join(buf)
                            buf, buf_len = [], 0
                            sink.append(out)
                            yield out
                if buf:
                    out = "".join(buf)
                    sink.append(out)
                    yield out
                return  # 成功
            except Exception as e:
                # 关键：异常时把 buf 残留先落地——保证 sink 是已输出内容的完整前缀，
                # 否则续写会从错误断点开始，造成乱序拼接
                if buf:
                    out = "".join(buf)
                    buf, buf_len = [], 0
                    sink.append(out)
                    yield out
                if attempt >= 3 or not self._is_transient(e):
                    raise
                logger.warning(f"LLM 调用瞬时失败（第{attempt}次）: {e}；{delay}s 后重试")
                await asyncio.sleep(delay)
                delay *= 2
                partial = "".join(sink)
                if not partial:
                    continue  # 没有任何输出，直接整体重试
                if not restart_marker:
                    # 非主生成场景（整段替换式调用）：续写提示即可，重复影响有限
                    user_prompt = (user_prompt +
                                   f"\n\n【续写模式】你此前因网络中断已输出部分正文，其最后片段为：\n"
                                   f"{partial[-400:]}\n"
                                   f"该片段仅用于定位中断位置——其中任何字句都不得再次输出。"
                                   f"请从该片段结束处的下一个字继续写正文，禁止重复，禁止任何说明性开场白。")
                    continue
                # 主生成场景：静默完整重生成到 shadow，成功后智能拼接，杜绝乱序/交叠
                shadow_buf = []
                try:
                    async for chunk in llm.astream(messages):
                        piece = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                        if piece:
                            shadow_buf.append(piece)
                except Exception as e2:
                    if attempt >= 3 or not self._is_transient(e2):
                        raise
                    continue
                shadow = "".join(shadow_buf)
                if not shadow:
                    continue
                tail = partial[-300:]
                if tail and tail in shadow:
                    # 模型从头重写且包含旧结尾 → 从旧结尾最后出现处接续
                    idx = shadow.rfind(tail)
                    shadow = shadow[idx + len(tail):]
                elif len(shadow) >= len(partial):
                    # 模糊判定从头重写（生成随机性导致非逐字相同）
                    from difflib import SequenceMatcher
                    k = min(len(partial), 300, len(shadow))
                    if k >= 60 and SequenceMatcher(None, partial[:k], shadow[:k]).ratio() >= 0.6:
                        yield self.RESTART
                else:
                    # 正常续接：裁掉与 partial 尾部的交叠
                    ov = self._overlap_len(partial, shadow)
                    if ov:
                        shadow = shadow[ov:]
                if shadow:
                    sink.append(shadow)
                    yield shadow
                return

    async def write_section_stream(
        self,
        profile: dict,
        outline: dict,
        section_id: str,
        extra_materials: str = "",
        user_instruction: str = "",
        only_subsection: "str | None" = None,
    ) -> AsyncGenerator[Dict, None]:
        """流式生成单节正文；长节按小节/分部拆分多次生成，不足自动续写；结束回填大纲"""
        section = self._find_section(outline, section_id)
        if section is None:
            yield {"type": "error", "content": f"未找到节: {section_id}"}
            return

        constitution = self._load_prompt("constitution.md")
        dom = self._dom(profile, outline)
        style = load_domain_prompt(dom, dom.style_prompt)
        template_snippet = self._section_template_snippet(section.get("title", ""), dom)
        self._ensure_method_pool(outline, profile)  # 引用池扩容：画像方法并入 method_pool
        # 参考文献多模态证据（RAG-Anything）：节标题+要点作检索主题，失败优雅降级为空
        reference_evidence = ""
        rag_engine = getattr(self, "rag_engine", None)
        if rag_engine is not None:
            topic_parts = [section.get("title", "")]
            topic_parts += [str(kp)[:60] for kp in (section.get("key_points") or [])[:3]]
            reference_evidence = await rag_engine.retrieve_evidence(
                " ".join(p for p in topic_parts if p))
        # 写作骨架（通道B）：本节槽位的叙事形式约束（无骨架时为空）
        skeleton_brief = ""
        if outline.get("reference_skeleton"):
            from src.skeleton_extractor import SkeletonExtractor
            skeleton_brief = SkeletonExtractor.brief_for_section(
                outline.get("reference_skeleton"), section)
        # 上下文组装含同步知识库检索（Chroma+Ollama 嵌入），放线程池执行：
        # Ollama 卡顿/重试时事件循环保持响应，进度轮询与界面不被拖死
        context = await asyncio.to_thread(
            self._build_section_context, profile, outline, section, extra_materials,
            reference_evidence=reference_evidence, skeleton_brief=skeleton_brief)
        logger.info(f"[write_section] {section.get('title')} 上下文 {len(context)} 字符, "
                    f"含上一节: {'上一节「' in context}")
        # P2 分节写作简报（按节名模糊匹配，简报随领域包走）
        brief = ""
        for key, b in self._briefs_for(dom).items():
            if key in section.get("title", "") or section.get("title", "") in key:
                parts_b = ["# 本节写作简报（必须逐条执行）",
                           "## 职责（成段展开，按序组织）"]
                parts_b += [f"{i}. {x}" for i, x in enumerate(b.get("职责", []), 1)]
                if b.get("骨架"):
                    parts_b.append("## 段落骨架（正文按此顺序成段）\n" + b["骨架"])
                if b.get("图表句式"):
                    parts_b.append("## 图表引用句式（照此格式）\n" + b["图表句式"])
                if b.get("引用要求"):
                    parts_b.append("## 文献引用要求（硬性）\n" + b["引用要求"])
                parts_b.append("## 禁忌\n" + "\n".join(f"- {x}" for x in b.get("禁忌", [])))
                brief = "\n".join(parts_b)
                break
        system_prompt = f"""{constitution}

# {dom.style_header}
{style}

# 本节章节模板（只含本节要求，逐条执行）
{template_snippet}"""
        if brief:
            system_prompt += "\n\n" + brief

        target = section.get("target_words") or 1000
        # 多级小节树（E1/E3）：字符串旧格式就地升级为对象树
        subtree = section.get("subsections") or []
        if subtree and not isinstance(subtree[0], dict):
            subtree = OutlineGenerator._normalize_subsections(
                section.get("id", "sec"), subtree, target, section.get("title", ""))
            section["subsections"] = subtree
            self.outline_gen.save_outline(profile.get("project_name", "project"), outline)

        # 写作单元序列：level2（无三级）→ 单元；level2（有三级）→ 标题行 + 各 level3 单元
        units = []
        try:
            ch_no = int(re.sub(r"\D", "", section_id) or "1")
        except ValueError:
            ch_no = 1
        if subtree and target > self.LONG_SECTION_THRESHOLD:
            for i, sub in enumerate(subtree, 1):
                l3 = sub.get("subsections") or []
                if l3:
                    units.append({"heading": f"### {ch_no}.{i} {sub.get('title', '')}",
                                  "node": None, "title": sub.get("title", ""),
                                  "target": 0})
                    for j, child in enumerate(l3, 1):
                        units.append({"heading": f"#### {ch_no}.{i}.{j} {child.get('title', '')}",
                                      "node": child, "title": child.get("title", ""),
                                      "target": child.get("target_words") or 600})
                else:
                    units.append({"heading": f"### {ch_no}.{i} {sub.get('title', '')}",
                                  "node": sub, "title": sub.get("title", ""),
                                  "target": sub.get("target_words") or 600})
            if only_subsection:
                # 局部重生成：其余单元必须有已生成内容（不过滤 units，走复用分支拼接）
                missing = [u["title"] for u in units
                           if u["node"] is not None and u["node"].get("id") != only_subsection
                           and not (u["node"].get("content") or "").strip()]
                if missing:
                    yield {"type": "error",
                           "content": "以下小节尚未生成，请先完整生成本章：" + "、".join(missing[:5])}
                    return
        elif target > self.LONG_SECTION_THRESHOLD:
            import math
            n = max(2, math.ceil(target / self.PART_TARGET))
            units = [{"heading": None, "node": None, "title": None,
                      "target": max(600, round(target / n))}] * 1
            units = [{"heading": None, "node": None, "title": None,
                      "target": max(600, round(target / n))} for _ in range(n)]
        else:
            units = [{"heading": None, "node": None, "title": None, "target": target}]

        self.llm_manager.get_main_llm()  # 前置校验：主模型未配置时快速失败

        beats = section.get("beats") or []
        # 修2/修3：小节级图表分配（含章内连续编号起点）；修4：单元级数字守卫的事实基准
        unit_resources = self._assign_unit_resources(profile, section, units) if subtree else {}
        facts = fact_guard.extract_facts(profile)
        base_spec = (
            f"本次只写一节：\n"
            f"- 节名：{section['title']}（本节总目标约 {target} 字）\n"
            f"- 递进要求：开头自然承接上一节末尾，全文向前推进，不重复前文已述内容\n"
        )
        if beats:
            beat_lines = []
            for bi, b in enumerate(beats, 1):
                beat_lines.append(
                    f"  {bi}) {b.get('claim', '')}（支撑：{b.get('support', '')}；"
                    f"约 {b.get('words', '?')} 字）")
            base_spec += "- 本节写作纲要（逐段遵循，claim 即各段主题句）：\n" + "\n".join(beat_lines) + "\n"
        else:
            base_spec += f"- 要点：{json.dumps(section.get('key_points', []), ensure_ascii=False)}\n"
        base_spec += f"- 证据文件：{json.dumps(section.get('evidence', []), ensure_ascii=False)}\n"
        if user_instruction:
            base_spec += f"- 用户要求：{user_instruction}\n"

        full_parts: list = []
        written_tail = ""
        unit_fixes = 0
        repair_notes: list = []
        for idx, unit in enumerate(units):
            part_target = max(600, unit["target"] or 600)
            # 已生成单元（局部重生成模式）直接复用
            if unit["node"] is not None and only_subsection \
                    and (unit["node"].get("content") or "").strip() \
                    and unit["node"].get("id") != only_subsection:
                full_parts.append(unit["node"]["content"].strip())
                continue
            if unit["node"] is None and unit.get("heading") and unit["target"] == 0 \
                    and only_subsection:
                full_parts.append(unit["heading"])  # 纯标题行（分组标题）
                continue
            if unit["node"] is None and unit.get("heading") and unit["target"] == 0:
                full_parts.append(unit["heading"])  # 纯标题行（分组标题）
                continue

            part_spec = base_spec + f"- 本次目标：约 {part_target} 字（中文字符，允许±15%）\n"
            if unit["title"] is not None:
                kps = unit["node"].get("key_points") or []
                part_spec += (f"- 本次只写小节「{unit['title']}」，以该小节标题行开头，"
                              f"只写该小节内容\n")
                if kps:
                    part_spec += f"- 本小节要点：{json.dumps(kps, ensure_ascii=False)}\n"
            ures = unit_resources.get(idx)
            if ures:
                part_spec += self._unit_resource_spec(ures, ch_no)
            if idx > 0:
                part_spec += (f"- 这是本章的第 {idx + 1}/{len(units)} 个写作单元：从上一单元结束处自然续写，"
                              f"禁止重复已写内容。\n上一单元末尾（衔接参考）：\n{written_tail}\n")

            user_prompt = (f"{context}\n\n# 任务\n{part_spec}\n"
                           f"直接输出该部分正文（markdown），不要解释，不要代码块包裹。\n"
                           f"全部数字取自画像/结果表/前文已写内容；缺证据的要点改写为不依赖该数字的定性表述，"
                           f"禁止输出 [待补充]、假设清单或缺失输入清单。")

            part_text = ""
            try:
                if idx > 0:
                    yield {"type": "chunk", "content": "\n\n"}
                sink: list = []
                async for piece in self._stream_llm(system_prompt, user_prompt, sink,
                                                    restart_marker=True):
                    if piece == self.RESTART:
                        # 网络中断后模型从头重写：清空本段重来，前端提示重写中
                        part_text = ""
                        yield {"type": "chunk", "content": "\n\n（网络波动，本段已完整重写…）\n\n"}
                        continue
                    part_text += piece
                    yield {"type": "chunk", "content": piece}
            except Exception as e:
                logger.error(f"分节生成失败: {e}")
                yield {"type": "error", "content": str(e)}
                return

            # 字数不足自动续写（每部分最多补 2 轮）
            part_words = count_cn_words(part_text)
            rounds = 0
            while part_words < part_target * 0.8 and rounds < 2 and part_text.strip():
                rounds += 1
                profile_snapshot = OutlineGenerator.compress_profile(profile, max_chars=3000)
                cont = (
                    f"## 项目画像快照（续写时数字与术语必须与此一致）\n{profile_snapshot}\n"
                    f"继续为论文「{section['title']}」"
                    f"{('小节「' + (unit['title'] or '') + '」') if unit['title'] else ''}写作：当前仅 {part_words} 字，目标约 {part_target} 字。"
                    f"请从中断处直接续写正文补足内容，禁止重复已有句子，禁止任何解释性开场白，"
                    f"不得编造数据。已写末尾：\n{part_text[-400:]}"
                )
                part_text += "\n"
                yield {"type": "chunk", "content": "\n"}
                try:
                    sink2: list = []
                    cont_parts = []
                    async for piece in self._stream_llm(system_prompt, cont, sink2,
                                                        restart_marker=True):
                        if piece == self.RESTART:
                            cont_parts = []
                            continue
                        cont_parts.append(piece)
                    text = "".join(cont_parts)
                    if text:
                        # 防模型复写"已写末尾"参考片段：裁掉与 part_text 的交叠/复写
                        tail_ref = part_text.rstrip()[-400:]
                        if tail_ref and tail_ref in text:
                            text = text[text.rfind(tail_ref) + len(tail_ref):]
                        else:
                            ov = self._overlap_len(part_text, text)
                            if ov:
                                text = text[ov:]
                        if text.strip():
                            part_text += text
                            yield {"type": "chunk", "content": text}
                except Exception as e:
                    logger.warning(f"续写失败（按已有内容继续）: {e}")
                    break
                part_words = count_cn_words(part_text)
                if part_words >= part_target * 0.9:
                    break

            # 小节标题规范化：程序化编号（### N.M / #### N.M.K），杜绝模型漏写/错编号
            if unit.get("heading") and unit["title"]:
                body = part_text.strip()
                lines = body.split("\n")
                if lines and lines[0].lstrip().startswith("#"):
                    lines[0] = unit["heading"]
                    part_text = "\n".join(lines)
                else:
                    part_text = unit["heading"] + "\n" + body

            # 修4 单元级数字守卫：单元小、修复便宜且不伤小节结构（多级章整章修复被跳过，
            # 数字问题在此就地解决一轮）
            unit_issues = fact_guard.check_numbers(part_text, facts)
            if unit_issues:
                fix_user = (f"以下是论文小节的正文草稿：\n\n{part_text}\n\n"
                            + fact_guard.repair_prompt(unit_issues)
                            + "\n\n只输出修正后的该小节完整正文：首行小节标题原样保留，"
                              "markdown 表格与图片语法、[n] 引用标记原样保留，其余内容一字不改。")
                fixed_parts = []
                try:
                    async for piece in self._stream_llm(system_prompt, fix_user, fixed_parts,
                                                        restart_marker=True):
                        if piece == self.RESTART:
                            fixed_parts.clear()
                            continue
                        fixed_parts.append(piece)
                except Exception as e:
                    logger.warning(f"单元数字修复失败（保留原稿）: {e}")
                fixed_text = "".join(fixed_parts).strip()
                if fixed_text:
                    re_i = fact_guard.check_numbers(fixed_text, facts)
                    if len(re_i) < len(unit_issues):
                        unit_fixes += len(unit_issues) - len(re_i)
                        part_text = fixed_text
                        logger.info(f"[unit-guard] 小节「{unit['title'] or ''}」"
                                    f"数字修复 {len(unit_issues)}→{len(re_i)}")

            # 修2/修3 完备性守卫：分配的图表必须齐全——缺图片按路径、缺表格按表号判定，
            # 一轮补齐（明确列出缺项与原文，模型只需插入，不需重写）
            if ures:
                fix_lines = ["以下小节草稿缺少指定的图表任务，必须补齐"
                             "（在正文最合适的位置插入；已有内容一字不改）："]
                for k, f in enumerate(ures["figures"]):
                    no = f"图{ch_no}-{ures['fig_start'] + k}"
                    rel = f.get("rel") or f.get("path")
                    if rel and rel not in part_text:
                        fix_lines.append(
                            f"- 缺失图片 {no}：`![{no} {f.get('name', '')}]({rel})`"
                            f"（路径逐字照抄），正文加「如图{no}所示」并解读 1 句")
                for k, t in enumerate(ures["tables"]):
                    no = f"表{ch_no}-{ures['tbl_start'] + k}"
                    if no not in part_text:
                        fix_lines.append(f"- 缺失表格 {no} ← {t.get('dir')}/{t.get('file')}，原文：")
                        fix_lines.append((t.get("preview") or "")[:1000])
                        fix_lines.append(f"  逐字转录为三线表（数值原样），题注「**{no} 标题**」，"
                                         f"正文以「具体结果见{no}」指向")
                if len(fix_lines) > 1:
                    fix_user = (f"小节草稿：\n\n{part_text}\n\n# 补齐任务\n"
                                + "\n".join(fix_lines)
                                + "\n\n直接输出补齐后的完整小节正文（markdown），不要解释。")
                    comp_parts = []
                    try:
                        async for piece in self._stream_llm(system_prompt, fix_user,
                                                            comp_parts, restart_marker=True):
                            if piece == self.RESTART:
                                comp_parts.clear()
                                continue
                            comp_parts.append(piece)
                    except Exception as e:
                        logger.warning(f"图表补齐轮失败（保留原稿）: {e}")
                    comp_text = "".join(comp_parts).strip()
                    if comp_text and count_cn_words(comp_text) > count_cn_words(part_text) * 0.8:
                        before = sum(1 for k, f in enumerate(ures["figures"])
                                     if (f.get("rel") or f.get("path")) not in part_text) \
                            + sum(1 for k, t in enumerate(ures["tables"])
                                  if f"表{ch_no}-{ures['tbl_start'] + k}" not in part_text)
                        after = sum(1 for k, f in enumerate(ures["figures"])
                                    if (f.get("rel") or f.get("path")) not in comp_text) \
                            + sum(1 for k, t in enumerate(ures["tables"])
                                  if f"表{ch_no}-{ures['tbl_start'] + k}" not in comp_text)
                        if after < before:
                            part_text = comp_text
                            repair_notes.append(f"图表补齐 {before - after} 项")

            # 单元内容回写小节节点（局部重生成/目录进度用）
            if unit["node"] is not None:
                unit["node"]["content"] = part_text.strip()
                unit["node"]["actual_words"] = count_cn_words(part_text)
                unit["node"]["status"] = "drafted" if part_text.strip() else "pending"
            full_parts.append(part_text)
            written_tail = part_text[-500:]

        full = "\n\n".join(p for p in full_parts if p.strip())

        # ---- D2 字数双向约束：超写 >118% 触发分块压缩（长节单次压缩受输出上限限制）。
        # 口径=纯散文字数（表格转录与图片嵌入不计入——它们是任务产物不是废话）----
        if self._prose_words(full) > target * 1.18 and count_cn_words(full) > 800:
            full = await self._compress_oversize(full, target, system_prompt)

        # ---- P1 硬保障：引用校验收敛 + 数字守卫（自动修复一轮） ----
        pool = self.refmgr.build_global_pool(outline)
        full = self.refmgr.sanitize_citations(full, pool)
        vres = self.refmgr.verify_citations(full, pool)
        issues = fact_guard.check_numbers(full, facts)
        if unit_fixes:
            repair_notes.append(f"单元级数字修复 {unit_fixes} 处")
        if issues:
            if subtree:
                # 多级小节章：单元级守卫已就地处理，残余问题留给小节级🔄/人工编辑
                repair_notes.append(f"残余数字问题 {len(issues)} 处（多级章跳过整章修复）")
            else:
                repair_prompt = fact_guard.repair_prompt(issues)
                fix_user = (f"以下是本节当前正文：\n\n{full}\n\n{repair_prompt}\n\n"
                            "只输出修正后的完整本节正文（markdown），其余内容保持不变。")
                fixed_parts = []
                try:
                    async for piece in self._stream_llm(system_prompt, fix_user, fixed_parts,
                                                        restart_marker=True):
                        if piece == self.RESTART:
                            fixed_parts.clear()  # 从头重写：清空旧片段
                            continue
                        fixed_parts.append(piece)
                except Exception as e:
                    logger.warning(f"数字修复轮失败（保留原稿）: {e}")
                fixed_text = "".join(fixed_parts).strip()
                if fixed_text:
                    re_issues = fact_guard.check_numbers(fixed_text, facts)
                    if len(re_issues) < len(issues):
                        repair_notes.append(f"数字修复完成（{len(issues)}→{len(re_issues)}）")
                        full = fixed_text
                        issues = re_issues
                    else:
                        repair_notes.append("数字修复未改善，保留原稿")
        if vres["invalid"]:
            repair_notes.append(f"越界引用编号已收敛：{vres['invalid'][:8]}")

        # ---- 终检防线（所有 LLM 轮次之后）：交叠/重复五级清除 + 草稿清扫 ----
        full, n_h = self._dedup_heading_blocks(full)
        full, n_p = self._dedup_similar_paragraphs(full)
        full, n_s = self._dedup_repeated_sentences(full)
        full, n_dedup = self._dedup_repeated_blocks(full)
        full, n_mid = self._dedup_mid_sentence(full)
        if n_h or n_p or n_s or n_dedup or n_mid:
            repair_notes.append(
                f"去重：标题块{n_h}/相似段{n_p}/重复句{n_s}/逐字块{n_dedup}/句中交叠{n_mid}")
        full, n_struct = self._strip_inline_struct_blocks(full)
        if n_struct:
            repair_notes.append(f"清除章内小结/参考文献块 {n_struct} 处")
        full, n_removed = self._sanitize_draft(full)
        if n_removed:
            repair_notes.append(f"清扫中间产物 {n_removed} 处")

        word_count = count_cn_words(full)
        section["content"] = full
        section["actual_words"] = word_count
        section["status"] = "drafted"
        section["written_at"] = datetime.now().isoformat()
        section["fact_issues"] = len(issues)
        section["citations_ok"] = not vres["invalid"]
        self.outline_gen.save_outline(profile.get("project_name", "project"), outline)

        yield {"type": "section_done", "section_id": section_id,
               "actual_words": word_count, "target_words": target,
               "fact_issues": len(issues), "citations_ok": not vres["invalid"],
               "citations": len(vres.get("cited") or []),
               "repairs": repair_notes}

    @staticmethod
    def _sync_units_from_content(section: dict, content: str) -> None:
        """整章正文按标题行（### N.M / #### N.M.K）回填小节节点（修复轮重写后保持同步）"""
        subs = section.get("subsections") or []
        if not subs or not isinstance(subs[0], dict):
            return
        nodes = []  # (heading_prefix, node)
        for sub in subs:
            for child in (sub.get("subsections") or []):
                nodes.append((f"####", child))
            if not (sub.get("subsections")):
                nodes.append(("###", sub))
        # 简化匹配：按小节标题文本在 content 中找标题行
        remaining = content
        for _, node in nodes:
            title = node.get("title", "")
            if not title:
                continue
            pat = r"^#{3,4}\s*[\d.]*\s*" + re.escape(title) + r"\s*$"
            m = re.search(pat, remaining, re.M)
            if not m:
                continue
            start = m.start()
            nxt = re.search(r"^#{3,4}\s", remaining[m.end():], re.M)
            end = m.end() + (nxt.start() if nxt else len(remaining) - m.end())
            node["content"] = remaining[start:end].strip()
            node["actual_words"] = count_cn_words(node["content"])
            node["status"] = "drafted"

    @staticmethod
    def _prose_words(text: str) -> int:
        """纯散文字数：剔除图片markdown与表格行（压缩判断专用口径）"""
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        t = "\n".join(ln for ln in t.split("\n") if not ln.lstrip().startswith("|"))
        return count_cn_words(t)

    async def _compress_oversize(self, full: str, target: int, system_prompt: str) -> str:
        """超写节分块压缩：按段落聚成 ~3000 字块逐块压（单块输出压力小，可靠）。
        压缩比 = 目标/实际（含 8% 余量），保数字/图表引用/引用标记/标题行。"""
        cur = self._prose_words(full)
        ratio = target / max(cur, 1)
        logger.warning(f"[compress] 进入压缩判断: {cur} 字 / 目标 {target} / ratio {ratio:.2f}")
        if ratio > 0.85 or cur <= 800:
            return full  # 超写不严重，不值得压（信息损失风险 > 收益）
        blocks, buf, buf_w = [], [], 0
        for para in full.split("\n\n"):
            w = count_cn_words(para)
            if buf and buf_w + w > 3000:
                blocks.append("\n\n".join(buf))
                buf, buf_w = [], 0
            buf.append(para)
            buf_w += w
        if buf:
            blocks.append("\n\n".join(buf))
        out_blocks = []
        for b in blocks:
            b_words = self._prose_words(b)
            b_target = max(300, round(b_words * ratio * 1.08))
            is_heading = b.lstrip().startswith("#")
            is_table = b.lstrip().startswith("|")
            has_image = "![" in b
            if is_table or has_image or b_words <= b_target * 1.15 or (is_heading and b_words < 200):
                out_blocks.append(b)  # 表格/图片块原样保留（转录产物不许压缩，
                # 图片块压缩会导致 LLM 重写时路径截断与正文熔接）
                continue
            comp_user = (
                f"以下正文片段当前 {b_words} 字，压缩到约 {b_target} 字：\n\n{b}\n\n"
                f"# 要求\n- 保留全部数字、统计量、图表引用（图N-M/表N-M）与引用标记 [n]，一个不能丢\n"
                f"- 删除重复表述、过渡冗句、过细的过程展开\n"
                f"- 标题行（#开头）原样保留\n"
                f"- 直接输出压缩后的片段正文，不要解释、不要代码块")
            comp_parts = []
            try:
                async for piece in self._stream_llm(system_prompt, comp_user, comp_parts):
                    comp_parts.append(piece)
            except Exception as e:
                logger.warning(f"分块压缩失败（保留该块）: {e}")
            comp = "".join(comp_parts).strip()
            if comp and b_target * 0.5 <= self._prose_words(comp) < b_words:
                out_blocks.append(comp)
            else:
                out_blocks.append(b)
        result = "\n\n".join(out_blocks)
        logger.info(f"[compress] {cur} → {self._prose_words(result)} 字（目标 {target}）")
        return result if self._prose_words(result) < cur else full

    async def write_section(
        self, profile: dict, outline: dict, section_id: str,
        extra_materials: str = "", user_instruction: str = "",
    ) -> Dict:
        """非流式生成单节"""
        full = []
        async for ev in self.write_section_stream(profile, outline, section_id,
                                                  extra_materials, user_instruction):
            if ev["type"] == "chunk":
                full.append(ev["content"])
            elif ev["type"] == "error":
                return {"ok": False, "error": ev["content"]}
            elif ev["type"] == "section_done":
                # 用落盘的干净正文（RESTART 重写/清扫后），而非流式 chunk 拼接
                sec = self._find_section(outline, section_id)
                content = (sec or {}).get("content") or "".join(full)
                return {"ok": True, "actual_words": ev["actual_words"],
                        "target_words": ev["target_words"], "content": content}
        return {"ok": False, "error": "生成中断"}

    # ---------- 自查 ----------
    async def self_review(self, profile: dict, outline: dict) -> str:
        """按自查清单分章审稿并聚合（修复长文 24000 截断漏审问题）"""
        from langchain_core.messages import HumanMessage

        dom = self._dom(profile, outline)
        checklist = self._load_prompt("self_review.md")
        if dom.review_addendum:  # 领域附加清单（如医学 TRIPOD/统计报告）
            addendum = self._load_prompt(dom.review_addendum)
            if addendum:
                checklist = checklist + "\n\n" + addendum
        llm = self.llm_manager.get_main_llm()
        report_parts = []
        totals = {"通过": 0, "警告": 0, "不通过": 0}
        for sec in outline.get("sections", []):
            content = (sec.get("content") or "").strip()
            if not content:
                continue
            # F 组（写作骨架符合度）：本节槽位的骨架简报动态注入
            sk_addendum = ""
            if outline.get("reference_skeleton"):
                from src.skeleton_extractor import SkeletonExtractor
                b = SkeletonExtractor.brief_for_section(outline["reference_skeleton"], sec)
                if b:
                    sk_addendum = ("\n# 本节写作骨架（F 组检查依据：对照该简报逐项核查）\n" + b)
            prompt = f"""你是{dom.reviewer}。请只针对下面这一个章节，
按清单逐项检查（与该章无关的项可跳过），输出格式按清单末尾的"输出格式"。

# 检查清单
{checklist}
{sk_addendum}

# 章节：{sec['title']}（目标 {sec.get('target_words')} 字 / 实际 {sec.get('actual_words', 0)} 字）

{content[:24000]}"""
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            report_parts.append(f"### {sec['title']}\n\n{text}")
            import re as _re
            m = _re.search(r"(\d+)\s*通过\s*/\s*(\d+)\s*警告\s*/\s*(\d+)\s*不通过", text)
            if m:
                totals["通过"] += int(m.group(1))
                totals["警告"] += int(m.group(2))
                totals["不通过"] += int(m.group(3))
        header = (f"# 全文自查汇总\n\n"
                  f"总计：{totals['通过']} 通过 / {totals['警告']} 警告 / {totals['不通过']} 不通过\n\n"
                  f"---\n\n")
        return header + "\n\n".join(report_parts)

    async def bestof_section(
        self, profile: dict, outline: dict, section_id: str,
        n: int = 3, instruction: str = "",
    ) -> Dict:
        """关键节 Best-of-N：生成 N 个候选 → 审稿评分表择优 → weaknesses 并入一轮修订。
        复用 write_section_stream 的事件流，最终产出最优稿（含 enhance_scores 记录）。"""
        sec = self._find_section(outline, section_id)
        if sec is None:
            return {"ok": False, "error": f"未找到节: {section_id}"}
        candidates = []
        import asyncio as _aio
        # 顺序生成 N 个候选（避免并发节流冲突），只留正文
        for k in range(n):
            parts = []
            async for ev in self.write_section_stream(profile, outline, section_id,
                                                      instruction, ""):
                if ev["type"] == "chunk":
                    parts.append(ev["content"])
            text = "".join(parts).strip()
            if text:
                candidates.append({"k": k + 1, "text": text,
                                   "words": count_cn_words(text)})
            logger.info(f"[bestof] 候选 {k + 1}/{n}: {len(text)} 字符")
        if not candidates:
            return {"ok": False, "error": "所有候选生成都失败了"}
        # 审稿评分
        scores = await self._score_candidates(section_id, candidates, profile)
        best = max(candidates, key=lambda c: scores.get(c["k"], {}).get("overall", 0))
        sec["content"] = best["text"]
        sec["actual_words"] = best["words"]
        sec["status"] = "drafted"
        sec["bestof"] = {"n": len(candidates),
                         "scores": {str(k): v.get("overall") for k, v in scores.items()},
                         "weaknesses": scores.get(best["k"], {}).get("weaknesses", [])}
        self.outline_gen.save_outline(profile.get("project_name", "project"), outline)
        return {"ok": True, "best_k": best["k"], "words": best["words"],
                "scores": {str(k): v.get("overall") for k, v in scores.items()},
                "weaknesses": scores.get(best["k"], {}).get("weaknesses", [])}

    async def _score_candidates(self, section_id: str, candidates: list,
                                profile: Optional[dict] = None) -> dict:
        """审稿评分表：每候选打分（总体/数据真实性/逻辑性/写作规范 + weaknesses）"""
        from langchain_core.messages import HumanMessage
        dom = self._dom(profile or {})
        llm = self.llm_manager.get_main_llm()
        out = {}
        for c in candidates:
            prompt = f"""你是{dom.reviewer}。对下面这份「{section_id}」章节候选稿打分。
只输出 JSON：{{"overall": 1-10整数, "数据真实性": 1-10, "逻辑性": 1-10, "写作规范": 1-10,
"weaknesses": ["具体弱点1", "弱点2"]}}
标准：数字具体且自洽、结构清晰、语言规范、无AI味。不要输出其他内容。

候选稿：
{c['text'][:18000]}"""
            try:
                resp = await llm.ainvoke([HumanMessage(content=prompt)])
                content = resp.content if isinstance(resp.content, str) else str(resp.content)
                import json as _json
                import re as _re
                m = _re.search(r"\{.*\}", content, _re.S)
                data = _json.loads(m.group(0)) if m else {}
                out[c["k"]] = {"overall": int(data.get("overall", 0)),
                               "weaknesses": data.get("weaknesses", [])}
            except Exception as e:
                logger.warning(f"候选 {c['k']} 评分失败: {e}")
                out[c["k"]] = {"overall": 0, "weaknesses": []}
        return out

    async def revise_section_stream(self, profile: dict, outline: dict,
                                    section_id: str, diagnoses: list):
        """P3 定向修订：程序化诊断清单 + 原文 → LLM 修复（全节重写，流式）"""
        sec = self._find_section(outline, section_id)
        if sec is None or not (sec.get("content") or "").strip():
            yield {"type": "error", "content": f"节不存在或无正文: {section_id}"}
            return

        constitution = self._load_prompt("constitution.md")
        dom = self._dom(profile, outline)
        style = load_domain_prompt(dom, dom.style_prompt)
        diag_text = "\n".join(
            f"- [{d.get('type')}] {d.get('detail', '')}\n  修复方向：{d.get('fix_hint', '')}"
            for d in diagnoses)
        system_prompt = f"""{constitution}

# {dom.style_header}
{style}"""
        user_prompt = f"""以下是论文「{sec['title']}」一节的程序化诊断结果：

{diag_text}

# 原文
{sec.get('content', '')[:20000]}

# 任务
只针对上述诊断问题做定向修订：
- 逐条修复每个诊断项（术语统一、删除重复、修正数字为画像基准值）
- 除此之外的内容**一字不改**（不重写、不润色、不调序）
- 直接输出修订后的完整本节正文（markdown），不要解释

诊断清单可能包含误报：若某诊断项与原文事实不符，保持原文即可，但不得借机改动其他内容。
注意：诊断信息是系统内部的修订指引，绝不能把诊断条目、核查说明或修订过程写进正文。"""

        collected = []
        try:
            async for piece in self._stream_llm(system_prompt, user_prompt, collected):
                yield {"type": "chunk", "content": piece}
        except Exception as e:
            yield {"type": "error", "content": str(e)}
            return
        full = "".join(collected).strip()
        if full:
            # D5：修订输出与主生成同等后处理（清扫 + 引用收敛 + 数字守卫复核）
            full, _ = self._sanitize_draft(full)
            pool = self.refmgr.build_global_pool(outline)
            full = self.refmgr.sanitize_citations(full, pool)
            re_issues = fact_guard.check_numbers(full, fact_guard.extract_facts(profile))
            sec["content"] = full
            sec["actual_words"] = count_cn_words(full)
            sec["fact_issues"] = len(re_issues)
            sec["written_at"] = datetime.now().isoformat()
            self.outline_gen.save_outline(profile.get("project_name", "project"), outline)
        yield {"type": "revise_done", "section_id": section_id,
               "actual_words": sec.get("actual_words", 0)}

    # ---------- 导出 ----------
    CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

    # 字体规格（与《最终版.docx》逐项一致）：中文黑体标题/宋体正文，英文数字统一 Times New Roman
    FONT_EN = "Times New Roman"
    FONT_CN_BODY = "宋体"
    FONT_CN_HEAD = "黑体"
    # 标题层级字号（pt）：题名18 / 章16（居中）/ 节14 / 三级12
    HEAD_SIZES = {1: 18, 2: 16, 3: 14, 4: 12}

    # docx 导出排版默认值（=config.yaml export_style 默认；网页「排版」面板经路由查询参数覆盖；
    # 「一键套入模板」写入 projects/{proj}.style.json 的扩展键在此合并——None 表示不改默认）
    DEFAULT_EXPORT_STYLE = {
        "body_font_px": 16, "line_height": 1.5, "first_indent_em": 2.0,
        "body_align": "justify", "table_align": "center", "table_width": "full",
        "table_font_px": 14, "cell_align": "center", "fig_width_pct": 100,
        # ---- 模板扩展键（格式档案编译产物；默认 = 系统规格）----
        "font_cn_body": "宋体", "font_cn_head": "黑体", "font_en": "Times New Roman",
        "h1_pt": None, "h2_pt": None, "h3_pt": None, "h4_pt": None,
        "h1_center": None,   # None=沿用默认（1/2 级居中）；True/False 显式指定
        "caption_pt": None, "caption_font_cn": None,
        "eq_font_pt": None,
        "margin_top_cm": None, "margin_bottom_cm": None,
        "margin_left_cm": None, "margin_right_cm": None,
        "header_text": None, "header_rule": None, "footer_page_number": None,
        # 前置部分结构（封面/声明/目录/中英摘要页）——格式档案 front_matter 编译产物
        "front_matter": None,
    }

    # 草稿清扫：占位句/假设清单小节/修订过程残留（宪法已禁止产生，此为兜底防线）
    _SANITIZE_HEADING_RE = re.compile(
        r"^#{1,4}\s*(?:假设与缺失输入|缺失输入清单|假设清单|待补充清单|假设与缺口).*$", re.M)
    _SANITIZE_PROC_RE = re.compile(
        r"^(?:[-*•]?\s*)?(?:诊断|核查|复核|修订说明|执行说明|本轮修订|审查意见)\s*[:：][^\n]*$", re.M)

    # 章内参考文献标题行（## 参考文献（本节引用）/ **References** 等形态）
    _REF_HEAD_LINE_RE = re.compile(
        r"^[ \t]*(?:#{1,4}[ \t]+|\*\*)[ \t]*(?:参考文献|引用文献|本节引用|References?)"
        r"(?:（[^）]*）)?[^\n]*?(?:\*\*)?[ \t]*$")
    # 引用池复述行：「见全文统一引用池编号：[1][2]…」/ 裸「[1][2][3]」清单行
    _POOL_LINE_RE = re.compile(
        r"^[ \t]*(?:[^\n\[\]]{0,24}[:：])?[ \t]*(?:\[\d{1,3}\][\s，,、]*){2,}$")
    # 「本章/本节小结」标题行（正文保留，仅去标题）
    _SUMMARY_HEADING_RE = re.compile(
        r"^[ \t]*(?:#{1,4}[ \t]+|\*\*)[ \t]*(?:本章|本节|本章内容)?小结"
        r"(?:[^\n*]*)(?:\*\*)?[ \t]*$")
    # 标题行以「…与/和/及 本章小结」收尾（如「### 3.7 统计软件与本章小结」）→ 剥掉小结尾巴
    _SUMMARY_TAIL_RE = re.compile(
        r"^([ \t]*#{1,4}[ \t]+\S[^\n]*?)[与和及][ \t]*(?:本章|本节)小结[ \t]*(\**)[ \t]*$")

    @classmethod
    def _strip_inline_struct_blocks(cls, text: str):
        """期刊体例保底清洗（LLM 各轮之后）：删除正文内 LLM 自行输出的
        ①「参考文献/本节引用」标题块（含紧随的空行/引用池复述行/[n] 条目行）
        ②孤立的引用池复述行 ③「本章/本节小结」标题行（正文保留，仅去标题）。
        文末统一参考文献列表由 export_md/export_docx 的 compose_references_section
        生成，不受影响。返回 (清洗后文本, 删除块计数)。"""
        if not text:
            return text, 0
        lines = text.split("\n")
        out, removed = [], 0
        ref_item = re.compile(r"^[ \t]*\[\d{1,3}\][^\n]*$")
        i, n = 0, len(lines)
        while i < n:
            ln = lines[i]
            if cls._REF_HEAD_LINE_RE.match(ln):
                removed += 1
                i += 1
                # 吞并标题块紧随的空行/引用池行/[n] 条目行（至首个正文行止）
                while i < n and (not lines[i].strip()
                                 or cls._POOL_LINE_RE.match(lines[i])
                                 or ref_item.match(lines[i])):
                    if lines[i].strip():
                        removed += 1
                    i += 1
                continue
            if cls._POOL_LINE_RE.match(ln):
                removed += 1
                i += 1
                continue
            if cls._SUMMARY_HEADING_RE.match(ln):
                removed += 1
                i += 1
                continue
            m_tail = cls._SUMMARY_TAIL_RE.match(ln)
            if m_tail:
                out.append(m_tail.group(1) + (m_tail.group(2) or ""))
                removed += 1
                i += 1
                continue
            out.append(ln)
            i += 1
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
        return text, removed

    @classmethod
    def _sanitize_draft(cls, text: str):
        """清除不该出现在论文正文里的中间产物；返回 (清洗后文本, 删除项计数)"""
        if not text:
            return text, 0
        removed = 0
        # 1) 剥离「假设与缺失输入」类小节：###/## 标题式 或 **粗体** 式，到节尾/分隔线
        def _drop_section(m):
            nonlocal removed
            removed += 1
            return ""
        text = re.sub(
            r"(?:^#{2,4}[^\n]*|^---\s*$\n?\*\*|\*\*)(?:假设与缺失输入|缺失输入清单|假设清单|待补充清单)[^\n]*\n(?:(?!^#{1,4}\s|\*\*假设)(?:[^\n]*\n?))*",
            _drop_section, text, flags=re.M)
        # 1b) 粗体行式（**假设与缺失输入清单** 单独成段，后跟条目直到空行+新段落）
        text = re.sub(
            r"^\*\*(?:假设与缺失输入|缺失输入清单|假设清单)[^\n]*\*\*\s*$\n(?:(?!^\*\*)(?:[-*•][^\n]*\n?|诊断[^\n]*\n?))*",
            _drop_section, text, flags=re.M)
        # 2) 删除修订/核查过程残留行
        text, n = cls._SANITIZE_PROC_RE.subn("", text)
        removed += n
        # 3) 删除含 [待补充 的句子（保留同段其余句子）
        out_paras = []
        for para in re.split(r"\n\s*\n", text):
            if "[待补充" in para:
                kept = []
                for sent in re.split(r"(?<=[。！？!?])", para):
                    if "[待补充" not in sent:
                        kept.append(sent)
                seg = "".join(kept).strip()
                if seg:
                    out_paras.append(seg)
                    removed += 1
            else:
                out_paras.append(para)
        text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(out_paras)).strip()
        # 4) 孤儿引文修复：去重删除可能吞掉引文的闭合括号（遗留 "[39"）——
        # 编号完整的补回 "]"；"[0.4, 0.7]" 类区间记号（后随 "."）不动
        def _reclose(m):
            nxt = text[m.end():m.end() + 1]
            if nxt == "]" or nxt == ".":
                return m.group(0)
            nonlocal removed
            removed += 1
            return m.group(0) + "]"
        text = re.sub(r"\[\d{1,3}(?:[,，\-–]\d{1,3})*(?=\s|[^\d.,\]])", _reclose, text)
        return text, removed

    @staticmethod
    def _strip_dup_heading(content: str, title: str) -> str:
        """内容首行若为与节标题重复的 '#标题' 行则剔除"""
        import re as _re
        lines = content.split("\n")
        for i, ln in enumerate(lines[:3]):
            s = ln.strip()
            m = _re.match(r"^#{1,4}\s*(.+)$", s)
            if m:
                head = m.group(1).strip()
                if head == title or head in title or title in head:
                    lines.pop(i)
                    return "\n".join(lines).strip()
            if s and not m:
                break
        return content

    def compose_full_markdown(self, outline: dict) -> str:
        """高质量 Markdown：中文序号章节、元信息头、未生成节要点提示"""
        sections = outline.get("sections", [])
        drafted = sum(1 for s in sections if s.get("status") == "drafted")
        total_actual = sum(s.get("actual_words") or 0 for s in sections)
        meta = [
            f"> **一句话论证**：{outline.get('one_sentence_argument', '')}",
            ">",
            f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} · "
            f"总目标 {outline.get('total_target_words', '?')} 字 · "
            f"已完成 {drafted}/{len(sections)} 节 · 累计 {total_actual} 字",
        ]
        parts = [f"# {outline.get('paper_title') or outline.get('project_id', '论文')}", "", *meta, "", "---", ""]
        for i, sec in enumerate(sections):
            num = self.CN_NUM[i] if i < len(self.CN_NUM) else str(i + 1)
            if sec.get("content"):
                parts.append(f"## {num}、{sec['title']}")
                parts.append("")
                clean, _ = self._sanitize_draft(sec["content"].strip())
                clean = self._strip_dup_heading(clean, sec["title"])
                # 内容内的 #/## 降为 ###，保持"章节=唯一二级标题"的层级
                fixed = []
                for ln in clean.split("\n"):
                    s = ln.lstrip()
                    if s.startswith("## ") or s.startswith("# ") or s in ("##", "#"):
                        fixed.append("###" + s[1:].lstrip("# ") if s.startswith("##") else "###" + s[1:])
                    else:
                        fixed.append(ln)
                parts.append("\n".join(fixed))
            else:
                parts.append(f"## {num}、{sec['title']}（未生成 · 目标 {sec['target_words']} 字）")
                parts.append("")
                if sec.get("key_points"):
                    parts.append("> **待生成**，本节要点：")
                    for kp in sec["key_points"]:
                        parts.append(f"> - {kp}")
                else:
                    parts.append("> **待生成**")
            parts.append("")
            parts.append("---")
            parts.append("")
        # P1 参考文献节：全局编号收敛 + GB/T 7714 条目（仅收录正文实际引用的）
        pool = self.refmgr.build_global_pool(outline)
        for sec in sections:
            if sec.get("content"):
                sec["content"] = self.refmgr.sanitize_citations(sec["content"], pool)
        refs = self.refmgr.compose_references_section(outline, pool)
        if refs:
            parts.append(refs)
        return "\n".join(parts).rstrip() + "\n"

    def _ensure_reference_docx(self, style: dict = None) -> Path:
        """pandoc 通道样式表：宋体正文/黑体标题/Times New Roman 英文（与《最终版》一致）。
        样式归一化每次调用幂等执行（旧缓存文件同样被修复），style 同步字号行距。"""
        import subprocess
        from docx import Document
        from docx.shared import Pt
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        ref = Path(__file__).resolve().parents[1] / "assets" / "reference_cn.docx"
        if not ref.exists():
            ref.parent.mkdir(parents=True, exist_ok=True)
            base = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"],
                                  capture_output=True, timeout=30).stdout
            ref.write_bytes(base)

        def _set_style(name, ascii_f, east, size, bold=None):
            try:
                st = doc.styles[name]
            except KeyError:
                return
            st.font.name = ascii_f
            st.font.size = Pt(size)
            if bold is not None:
                st.font.bold = bold
            rpr = st.element.get_or_add_rPr()
            rf = rpr.find(qn("w:rFonts"))
            if rf is None:
                rf = OxmlElement("w:rFonts")
                rpr.append(rf)
            rf.set(qn("w:eastAsia"), east)

        try:
            doc = Document(str(ref))
            # 正文类样式（pandoc 段落默认 First Paragraph/Body Text/Block Text）
            body_names = ("Normal", "Body Text", "First Paragraph", "Block Text", "Compact")
            for name in body_names:
                _set_style(name, self.FONT_EN, self.FONT_CN_BODY, 12)
            # 标题层级（题名 Title / H1 章 / H2 节 / H3 三级）
            _set_style("Title", self.FONT_EN, self.FONT_CN_HEAD, 18, bold=True)
            _set_style("Heading 1", self.FONT_EN, self.FONT_CN_HEAD, 16, bold=True)
            _set_style("Heading 2", self.FONT_EN, self.FONT_CN_HEAD, 14, bold=True)
            _set_style("Heading 3", self.FONT_EN, self.FONT_CN_HEAD, 12, bold=True)
            try:
                from docx.enum.text import WD_LINE_SPACING
                doc.styles["Normal"].paragraph_format.line_spacing_rule = \
                    WD_LINE_SPACING.ONE_POINT_FIVE
            except Exception:
                pass
            # 排版设置同步（字号/行距幂等）
            if style:
                st = {k: v for k, v in style.items() if v is not None}
                body_pt = round(float(st.get("body_font_px", 16)) * 0.75, 1)
                line_h = max(1.0, float(st.get("line_height", 1.5)))
                for name in ("Normal", "Body Text", "First Paragraph", "Block Text"):
                    try:
                        s = doc.styles[name]
                        s.font.size = Pt(body_pt)
                        s.paragraph_format.line_spacing = line_h
                    except KeyError:
                        continue
            doc.save(str(ref))
        except Exception as e:
            logger.warning(f"reference.docx 样式归一化失败（沿用现状）: {e}")
        return ref

    def export_docx_pandoc(self, outline: dict, out_path=None, style: dict = None) -> str:
        """Pandoc 导出：原生 OMML 公式 + 嵌入图片 + 表格。
        已知限制：表格位置/单元格对齐由 pandoc 按 markdown 列对齐推导，style 不强制。"""
        import shutil
        import subprocess
        if not shutil.which("pandoc"):
            raise RuntimeError("pandoc 不可用")
        self.renumber_captions(outline)  # 题注统一 图{章}-{序}
        ref = self._ensure_reference_docx(style)
        safe = re_safe_name(outline.get("project_id", "论文"))
        md_path = scoping.projects_dir() / f"{safe}.论文草稿.md"
        md_path.write_text(self.compose_full_markdown(outline), encoding="utf-8")
        out = Path(out_path) if out_path else scoping.projects_dir() / f"{safe}.论文草稿.docx"
        out.parent.mkdir(parents=True, exist_ok=True)
        resource = outline.get("project_path") or "."
        cmd = ["pandoc", "-f", "markdown-yaml_metadata_block", str(md_path), "-o", str(out),
               "--resource-path", str(scoping.projects_dir()) + ";" + str(resource) + ";.",
               "--reference-doc", str(ref)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "ignore")[:400]
            raise RuntimeError(f"pandoc 转换失败: {err}")
        return str(out)

    @staticmethod
    def renumber_captions(outline: dict) -> None:
        """按章节重排图片题注：![原alt](path) → ![图{章}-{序} 原alt](path)。
        已带 图x-y 编号的保持不变。就地修改 sections。"""
        for ci, sec in enumerate(outline.get("sections", []), 1):
            content = sec.get("content") or ""
            if not content:
                continue
            counter = {"n": 0}

            def _repl(m, ci=ci, counter=counter):
                alt = m.group(1).strip()
                path = m.group(2)
                if re.match(r"^图\d+-\d+", alt):
                    return m.group(0)
                counter["n"] += 1
                new_alt = f"图{ci}-{counter['n']} {alt}".strip()
                return f"![{new_alt}]({path})"

            sec["content"] = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _repl, content)

    def export_md(self, outline: dict, out_path=None) -> str:
        self.renumber_captions(outline)
        safe = re_safe_name(outline.get("project_id", "论文"))
        out = Path(out_path) if out_path else scoping.projects_dir() / f"{safe}.论文草稿.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.compose_full_markdown(outline), encoding="utf-8")
        return str(out)

    @staticmethod
    def _docx_resolve_image(src: str, project_path: str):
        from src.project_indexer import IMAGE_EXTS
        cand = []
        s = src.strip()
        if re.match(r"^[A-Za-z]:[\\/]", s):
            cand.append(Path(s))
        else:
            root = Path(project_path).resolve() if project_path else None
            if root:
                cand.append(root / s)
                cand.append(root.parent / s)
        for c in cand:
            try:
                c = c.resolve()
            except OSError:
                continue
            if c.is_file() and c.suffix.lower() in IMAGE_EXTS:
                return c
        return None

    @staticmethod
    def _compute_fig_width(img: Path, max_w: float) -> float:
        """图片自适应宽度（学术规范：图宽≤版心；竖图限高；小图不放大）：
        - 读取像素尺寸与 DPI → 物理尺寸（DPI 元数据异常按 96 兜底）
        - 横图(ratio<0.85)：min(物理宽, 版心宽)，物理宽可信时保持原大
        - 方图(0.85~1.25)：内容通常不需全宽 → 版心的 78%
        - 竖图(ratio≥1.25)：按最大高度 6.0" 反推，宽度上限 4.6"
        - 终检：高度不得超过 6.0"（A4 版心高约 9.2"，留题注与邻文余量）"""
        try:
            from PIL import Image
            with Image.open(img) as im:
                px_w, px_h = im.size
                dpi = im.info.get("dpi") or (96, 96)
            dw = dpi[0] if isinstance(dpi[0], (int, float)) and 1 < dpi[0] < 10000 else 96
            dh = dpi[1] if isinstance(dpi[1], (int, float)) and 1 < dpi[1] < 10000 else 96
            nat_w, nat_h = px_w / dw, px_h / dh
        except Exception:
            return max_w
        if nat_w <= 0.1 or nat_h <= 0.1:
            return max_w
        ratio = nat_h / nat_w
        max_h = 6.0
        w = min(nat_w, max_w)          # 小图不放大，大图收进版心
        if ratio >= 1.25:              # 竖图（流程图常见）：限宽限高
            w = min(max_w, max_h / ratio, 4.6)
            w = max(w, 2.2)
        elif ratio >= 0.85:            # 方图：收窄到版心 78%
            if w >= max_w:
                w = max_w * 0.78
        if w * ratio > max_h:          # 终检：高度兜底
            w = max_h / ratio
        return round(w, 2)

    @staticmethod
    def _fm_footer_page(sec, fmt: str = None, start: int = None):
        """给节装页脚居中页码（可指定罗马格式 / 起始编号）"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        sec.footer.is_linked_to_previous = False
        fp = sec.footer.paragraphs[0] if sec.footer.paragraphs else sec.footer.add_paragraph()
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fp.paragraph_format.first_line_indent = None
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), " PAGE ")
        r_el = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        szel = OxmlElement("w:sz")
        szel.set(qn("w:val"), "18")  # 9pt 页码
        rpr.append(szel)
        r_el.append(rpr)
        t_el = OxmlElement("w:t")
        t_el.text = "1"
        r_el.append(t_el)
        fld.append(r_el)
        fp._p.append(fld)
        if fmt or start is not None:
            sect_pr = sec._sectPr
            pg = sect_pr.find(qn("w:pgNumType"))
            if pg is None:
                pg = OxmlElement("w:pgNumType")
                sect_pr.append(pg)
            if fmt:
                pg.set(qn("w:fmt"), fmt)
            if start is not None:
                pg.set(qn("w:start"), str(start))

    @staticmethod
    def _docx_page_setup(doc, st: dict, body_east: str = "宋体",
                         en_font: str = "Times New Roman"):
        """页面边距 + 页眉文字/横线 + 页脚页码（模板套入项；键缺省时一律不改）。
        多节文档（前置部分模板）：第 1 节=前置（罗马页码、封面首页无页眉页码），
        末节=正文（阿拉伯页码重新起算、页眉）。"""
        from docx.shared import Cm, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        try:
            secs = list(doc.sections)
            for sec in secs:
                for key, attr in (("margin_top_cm", "top_margin"),
                                  ("margin_bottom_cm", "bottom_margin"),
                                  ("margin_left_cm", "left_margin"),
                                  ("margin_right_cm", "right_margin")):
                    v = st.get(key)
                    if v:
                        setattr(sec, attr, Cm(float(v)))
            multi = len(secs) > 1
            body_sec = secs[-1] if multi else secs[0]
            if multi:
                front = secs[0]
                front.different_first_page_header_footer = True  # 封面页干净（无页眉页码）
                SectionWriter._fm_footer_page(front, fmt="upperRoman")
                SectionWriter._fm_footer_page(body_sec, start=1)
            elif st.get("footer_page_number"):
                SectionWriter._fm_footer_page(secs[0])
            header_text = st.get("header_text")
            if header_text:
                if multi:
                    body_sec.header.is_linked_to_previous = False
                header = body_sec.header
                hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
                hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = hp.add_run(str(header_text))
                r.font.size = Pt(9)
                r.font.name = en_font
                r._element.rPr.rFonts.set(qn("w:eastAsia"), body_east)
                if st.get("header_rule"):
                    ppr = hp._p.get_or_add_pPr()
                    pbdr = OxmlElement("w:pBdr")
                    bottom = OxmlElement("w:bottom")
                    bottom.set(qn("w:val"), "single")
                    bottom.set(qn("w:sz"), "6")
                    bottom.set(qn("w:space"), "1")
                    bottom.set(qn("w:color"), "000000")
                    pbdr.append(bottom)
                    ppr.append(pbdr)
        except Exception as e:
            logger.warning(f"页面模板设置失败（沿用默认页面）: {e}")

    # ---------- 前置部分（模板复刻）：封面 / 声明 / 目录 / 摘要页 ----------
    @staticmethod
    def _fm_add_cover(doc, run_font, st: dict, outline: dict,
                      head_font: str, body_pt: float):
        """封面页：优先按模板捕获的版式逐行重放（字号/加粗/居中/字体/分页原样），
        题目替换为本论文题名、英文题名与姓名类信息为预留占位；
        无版式数据时回落为简易封面（标题 + 字段下划线）"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
        fm = st.get("front_matter") or {}
        title = outline.get("paper_title") or outline.get("project_id", "论文标题")
        layout = fm.get("cover_layout") or []
        if layout:
            for item in layout:
                if not item.get("t"):
                    doc.add_paragraph()  # 模板留白行
                    continue
                text = item["t"]
                if item.get("T") == "cn_title":
                    text = title                      # 题目 → 本论文题名
                elif item.get("T") == "en_title":
                    text = "＿" * 16                   # 英文题名 → 预留位
                par = doc.add_paragraph()
                if item.get("c", True):
                    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                par.paragraph_format.space_after = Pt(6)
                r = par.add_run(text)
                run_font(r, east=item.get("e") or head_font,
                         size=item.get("sz") or max(body_pt, 14),
                         bold=bool(item.get("b")))
                if item.get("br"):
                    doc.add_page_break()
            doc.add_page_break()
            return
        # ---- 回落：简易封面 ----
        title_pt = float(fm.get("cover_title_pt") or 22.0)
        fields = fm.get("cover_fields") or ["研究生姓名", "导师姓名", "专业", "完成日期"]
        for _ in range(3):
            doc.add_paragraph()
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = par.add_run(title)
        run_font(r, east=head_font, size=title_pt, bold=True)
        for _ in range(5):
            doc.add_paragraph()
        for f in fields:
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            par.paragraph_format.space_after = Pt(12)
            r = par.add_run(f"{f}：＿＿＿＿＿＿＿＿＿＿")
            run_font(r, size=max(body_pt, 14))
        doc.add_page_break()

    @staticmethod
    def _fm_add_declaration(doc, run_font, fm: dict, head_font: str,
                            body_pt: float, line_h: float, indent_pt: float):
        """声明页：复刻模板的法定固定文本（属版式而非研究内容）；
        他人姓名等个人信息脱敏为占位下划线"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
        text = fm.get("declaration_text") or ""
        text = re.sub(r"(姓名[：:]\s*|作者[：:]\s*)[\u4e00-\u9fff·]{2,4}", r"\1＿＿＿", text)
        text = re.sub(r"(专业\s+)[\u4e00-\u9fff]{2,4}(\s+在)", r"\1＿＿＿\2", text)
        text = re.sub(r"(指导教师[：:]\s*|导师[：:]\s*)[\u4e00-\u9fff]{2,4}", r"\1＿＿＿", text)
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_before = Pt(24)
        par.paragraph_format.space_after = Pt(18)
        r = par.add_run("声  明")
        run_font(r, east=head_font, size=max(body_pt + 4, 16), bold=True)
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            par.paragraph_format.line_spacing = line_h
            if indent_pt > 0:
                par.paragraph_format.first_line_indent = Pt(indent_pt)
            r = par.add_run(ln)
            run_font(r, size=body_pt)
        doc.add_page_break()

    @staticmethod
    def _fm_add_toc(doc, run_font, head_font: str, head_pt: float):
        """目录页：插入 Word 目录域（打开后全选 F9 / 右键更新域自动生成）"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_after = Pt(18)
        r = par.add_run("目  录")
        run_font(r, east=head_font, size=head_pt, bold=True)
        fld_par = doc.add_paragraph()
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), ' TOC \\o "1-3" \\h \\z \\u ')
        r_el = OxmlElement("w:r")
        t_el = OxmlElement("w:t")
        t_el.text = "（目录域：全选正文后按 F9，或右键「更新域」自动生成目录）"
        r_el.append(t_el)
        fld.append(r_el)
        fld_par._p.append(fld)
        doc.add_page_break()

    @staticmethod
    def _fm_add_abstract(doc, run_font, head_font: str, sec, render_md,
                         heading: str, style: dict):
        """摘要页：模板标题样式 + 独立成页（内容来自大纲摘要节；未生成给提示）"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt, RGBColor
        hpt = float((style or {}).get("heading_size_pt") or 16)
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_before = Pt(6)
        par.paragraph_format.space_after = Pt(14)
        r = par.add_run(heading)
        run_font(r, east=head_font, size=hpt, bold=True)
        if sec is not None and (sec.get("content") or "").strip():
            render_md(sec.get("content") or "")
        else:
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = par.add_run(f"（{heading} 正文待生成：完成对应章节后重新导出将自动排入此页）")
            run_font(r, size=10.5, italic=True, color=RGBColor(0x80, 0x80, 0x80))
            par = doc.add_paragraph()
            r = par.add_run("关键词：＿＿＿＿＿；＿＿＿＿＿；＿＿＿＿＿")
            run_font(r, size=10.5, bold=True)
        doc.add_page_break()

    @staticmethod
    def _fm_add_abstract_placeholder(doc, run_font, head_font: str):
        """英文摘要占位页：模板要求 ABSTRACT 而大纲尚无英文摘要节时提醒补齐"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt, RGBColor
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_after = Pt(14)
        r = par.add_run("ABSTRACT")
        run_font(r, east=head_font, size=16, bold=True)
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r = par.add_run("（本模板含英文摘要页：完成正文后，可在右侧会话中让助手将中文摘要"
                        "译写为英文摘要，或将本页替换为英文摘要节后重新导出。）")
        run_font(r, size=10.5, italic=True, color=RGBColor(0x80, 0x80, 0x80))
        par = doc.add_paragraph()
        r = par.add_run("Key words: ＿＿＿＿＿; ＿＿＿＿＿; ＿＿＿＿＿")
        run_font(r, size=10.5, bold=True)
        doc.add_page_break()

    @staticmethod
    def _docx_add_image(doc, run_font, src: str, alt: str, project_path: str,
                        width_in: float = 5.6, caption_east: str = "黑体",
                        caption_size: float = 10.5):
        from docx.shared import Inches, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        img = SectionWriter._docx_resolve_image(src, project_path)
        if img is None:
            par = doc.add_paragraph()
            r = par.add_run(f"[图片缺失: {src}]")
            run_font(r, size=10, italic=True)
            return
        try:
            w = SectionWriter._compute_fig_width(img, width_in)
            doc.add_picture(str(img), width=Inches(w))
            # add_picture 挂在文档末段：居中 + 间距 + 清缩进
            par = doc.paragraphs[-1]
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            par.paragraph_format.first_line_indent = Pt(0)
            par.paragraph_format.space_before = Pt(6)
            par.paragraph_format.space_after = Pt(3)
        except Exception as e:
            logger.warning(f"图片嵌入失败 {img}: {e}")
            return
        if alt:
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            par.paragraph_format.first_line_indent = Pt(0)
            par.paragraph_format.space_before = Pt(0)
            par.paragraph_format.space_after = Pt(12)
            r = par.add_run(alt)
            run_font(r, east=caption_east, size=caption_size, bold=True)

    # ---------- 公式：LaTeX → OMML（Word 原生可编辑公式） ----------
    # Unicode 上下标/常见符号 → LaTeX（模型偶发输出 Unicode 近似公式，导出端兜底归一）
    _SUP_MAP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
                "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-", "ⁿ": "n", "ᵏ": "k", "ᵀ": "T"}
    _SUB_MAP = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6",
                "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-", "ᵢ": "i", "ⱼ": "j",
                "ₐ": "a", "ₙ": "n", "ₖ": "k", "ₘ": "m", "ₚ": "p", "ₛ": "s", "ₜ": "t"}
    _SYM_MAP = {"×": r"\times ", "÷": r"\div ", "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ",
                "±": r"\pm ", "≈": r"\approx ", "∈": r"\in ", "∝": r"\propto ",
                "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ", "∞": r"\infty ",
                "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
                "Δ": r"\Delta ", "ε": r"\varepsilon ", "θ": r"\theta ", "λ": r"\lambda ",
                "μ": r"\mu ", "π": r"\pi ", "σ": r"\sigma ", "τ": r"\tau ", "χ": r"\chi ",
                "ω": r"\omega ", "φ": r"\varphi ", "ρ": r"\rho "}
    _OMML_CACHE: dict = {}          # 同一导出内相同公式只转换一次

    @classmethod
    def _latex_preprocess(cls, tex: str):
        """LaTeX 预清洗：提取 \\tag 编号 + Unicode/全角符号归一。返回 (tex, tag)"""
        tag = ""
        m = re.search(r"\\tag\s*\{([^}]*)\}", tex)
        if m:
            tag = m.group(1).strip()
            tex = tex[:m.start()] + tex[m.end():]
        tex = tex.translate(str.maketrans("＝＋－（）［］", "=+-()[]"))
        tex = tex.replace("（", "(").replace("）", ")")
        # 连续 Unicode 上标 → ^{...}，连续下标 → _{...}
        def _sup_run(m):
            return "^{" + "".join(cls._SUP_MAP.get(c, c) for c in m.group(0)) + "}"
        def _sub_run(m):
            return "_{" + "".join(cls._SUB_MAP.get(c, c) for c in m.group(0)) + "}"
        tex = re.sub(f"[{''.join(cls._SUP_MAP)}]+", _sup_run, tex)
        tex = re.sub(f"[{''.join(cls._SUB_MAP)}]+", _sub_run, tex)
        for k, v in cls._SYM_MAP.items():
            if k in tex:
                tex = tex.replace(k, v)
        # 公式里的裸中文包进 \text{}（如 C(临床+RNA)）；已有 \text{} 掩码保护不重复包
        protected = []

        def _mask(m):
            protected.append(m.group(0))
            return f"\x00{len(protected) - 1}\x00"

        tex = re.sub(r"\\text\s*\{[^}]*\}", _mask, tex)
        tex = re.sub(r"([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z+]*)", r"\\text{\1}", tex)
        for i, seg in enumerate(protected):
            tex = tex.replace(f"\x00{i}\x00", seg)
        return tex.strip(), tag

    @staticmethod
    def _ensure_omml_ns(omml: str) -> str:
        """给 oMath 片段补命名空间声明（mathml2omml/pandoc 输出常缺根声明）"""
        m_ns = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
        w_ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        i = omml.find(">")
        if i < 0:
            return omml
        head = omml[:i]
        add = ""
        if "xmlns:m=" not in head:
            add += " " + m_ns
        if "xmlns:w=" not in head and "<w:" in omml:
            add += " " + w_ns
        return omml[:i] + add + omml[i:] if add else omml

    _MML2OMML_CANDIDATES = [
        r"C:/Program Files/Microsoft Office/root/Office16/MML2OMML.XSL",
        r"C:/Program Files (x86)/Microsoft Office/root/Office16/MML2OMML.XSL",
        r"C:/Program Files (x86)/Microsoft Office/Office16/MML2OMML.XSL",
        r"C:/Program Files (x86)/Microsoft Office/Office15/MML2OMML.XSL",
    ]

    @staticmethod
    def _strip_omathpara(omml: str) -> str:
        """归一：无论来源，统一剥成裸 <m:oMath> 片段（pandoc/XSL 会包一层
        <m:oMathPara> 显示包装器；制表位编号排版需要公式与 tab run 同段混排）"""
        m = re.search(r"(<m:oMath(?:\s[^>]*)?>[\s\S]*?</m:oMath>)", omml)
        return m.group(1) if m else omml

    @classmethod
    def _tex_to_omml(cls, tex: str):
        """LaTeX → OMML 片段（裸 m:oMath XML 字符串）。三级转换链，全败返回 None：
        ① latex2mathml + mathml2omml（纯 Python，快）
        ② latex2mathml + 微软官方 MML2OMML.XSL（Office 自带，覆盖更全）
        ③ pandoc 单公式转换（LaTeX 覆盖最强，aligned/环境级公式）"""
        key = tex.strip()
        if key in cls._OMML_CACHE:
            return cls._OMML_CACHE[key]

        def _cache(v):
            cls._OMML_CACHE[key] = v
            return v

        # ① 纯 Python 链
        try:
            import latex2mathml.converter as l2m
            import mathml2omml
            omml = mathml2omml.convert(l2m.convert(tex))
            if "<m:oMath" in omml:
                return _cache(cls._strip_omathpara(omml))
        except Exception:
            pass
        # ② Office 官方 XSL
        try:
            from lxml import etree
            import latex2mathml.converter as l2m
            xsl = next((p for p in cls._MML2OMML_CANDIDATES if Path(p).exists()), None)
            if xsl:
                dom = etree.fromstring(l2m.convert(tex).encode("utf-8"))
                out = str(etree.XSLT(etree.parse(xsl))(dom))
                if "<m:oMath" in out:
                    return _cache(cls._strip_omathpara(out))
        except Exception:
            pass
        # ③ pandoc 兜底
        try:
            import shutil as _sh
            import subprocess as _sp
            import tempfile as _tf
            import zipfile as _zf
            if _sh.which("pandoc"):
                with _tf.TemporaryDirectory() as td:
                    texf = Path(td) / "eq.tex"
                    outf = Path(td) / "eq.docx"
                    texf.write_text(f"$${tex}$$", encoding="utf-8")
                    proc = _sp.run(["pandoc", "-f", "latex", "-t", "docx",
                                    str(texf), "-o", str(outf)],
                                   capture_output=True, timeout=60)
                    if proc.returncode == 0 and outf.exists():
                        xml = _zf.ZipFile(outf).read("word/document.xml").decode("utf-8")
                        m = re.search(r"<m:oMath(?:\s[^>]*)?>[\s\S]*?</m:oMath>", xml)
                        if m:
                            return _cache(m.group(0))
        except Exception:
            pass
        return _cache(None)

    @staticmethod
    def _append_omml(par, omml: str):
        from docx.oxml import parse_xml
        par._p.append(parse_xml(SectionWriter._ensure_omml_ns(omml)))

    @staticmethod
    def _docx_add_equation(doc, run_font, tex: str):
        """独立公式：LaTeX → OMML 原生 Word 公式。
        有编号(\\tag 或尾部 (x-x))：制表位排版——公式居中、编号右顶格（学位论文标准式样）。
        转换全败时降级为斜体文本（并记日志）。"""
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
        from docx.shared import Inches, Pt
        tex, tag = SectionWriter._latex_preprocess(tex)
        # 尾部裸编号兜底：$$...$$ (2-9) / （2-9）
        if not tag:
            m = re.search(r"[（(]\s*[\d]+[-–—][\d]+\s*[)）]\s*$", tex)
            if m:
                tag = m.group(0).strip("（）() ")
                tex = tex[:m.start()].strip()
        omml = SectionWriter._tex_to_omml(tex)
        par = doc.add_paragraph()
        pf = par.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.space_before = Pt(6)
        pf.space_after = Pt(6)
        if omml is not None:
            if tag:
                # 版心宽约 5.6"：居中制表位在 2.8"，右对齐制表位在 5.6"
                pf.tab_stops.add_tab_stop(Inches(2.8), WD_TAB_ALIGNMENT.CENTER)
                pf.tab_stops.add_tab_stop(Inches(5.6), WD_TAB_ALIGNMENT.RIGHT)
                par.add_run("\t")
                SectionWriter._append_omml(par, omml)
                r = par.add_run(f"\t({tag})")
                run_font(r, size=11)
            else:
                par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                SectionWriter._append_omml(par, omml)
        else:
            logger.warning(f"公式转换失败（降级为文本）: {tex[:60]}")
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = par.add_run(tex)
            run_font(r, size=11, italic=True)

    def export_docx(self, outline: dict, out_path=None, style: dict = None) -> str:
        """学术排版导出：黑体标题/宋体正文/首行缩进/表格/占位符高亮。
        style 可覆盖排版默认值（键见 DEFAULT_EXPORT_STYLE，由网页「排版」面板或 config 提供）。"""
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        st = dict(self.DEFAULT_EXPORT_STYLE)
        if style:
            st.update({k: v for k, v in style.items() if v is not None})
        body_pt = round(float(st["body_font_px"]) * 0.75, 1)
        table_pt = round(float(st["table_font_px"]) * 0.75, 1)
        indent_pt = round(body_pt * float(st["first_indent_em"]), 1)
        line_h = max(1.0, float(st["line_height"]))
        body_left = st.get("body_align") == "left"
        tbl_align_left = st.get("table_align") == "left"
        tbl_auto = st.get("table_width") == "auto"
        cell_left = st.get("cell_align") == "left"
        fig_in = round(5.6 * float(st["fig_width_pct"]) / 100.0, 2)
        # 模板扩展键（一键套入格式档案后生效；缺省回落系统规格）
        body_east = st.get("font_cn_body") or SectionWriter.FONT_CN_BODY
        head_font = st.get("font_cn_head") or SectionWriter.FONT_CN_HEAD
        en_font = st.get("font_en") or SectionWriter.FONT_EN
        head_sizes = {lv: (st.get(f"h{lv}_pt") or self.HEAD_SIZES.get(lv, 12))
                      for lv in (1, 2, 3, 4)}
        caption_pt = st.get("caption_pt") or 10.5
        caption_east = st.get("caption_font_cn") or "黑体"

        BLACK = RGBColor(0x1A, 0x1A, 0x1A)
        GRAY = RGBColor(0x80, 0x80, 0x80)
        ORANGE = RGBColor(0xE0, 0x8A, 0x2E)
        self.renumber_captions(outline)  # 题注统一 图{章}-{序}

        doc = Document()
        normal = doc.styles["Normal"]
        normal.font.name = en_font
        normal.font.size = Pt(body_pt)  # 默认 16px → 12pt 小四
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), body_east)
        pf = normal.paragraph_format
        pf.line_spacing = line_h
        pf.space_after = Pt(0)

        def run_font(run, east=None, ascii_f=None, size=None,
                     bold=False, italic=False, color=BLACK):
            if east is None:
                east = body_east
            if ascii_f is None:
                ascii_f = en_font
            if size is None:
                size = body_pt
            run.font.name = ascii_f
            run.font.size = Pt(size)
            run.bold = bold
            run.italic = italic
            if color is not None:
                run.font.color.rgb = color
            run._element.rPr.rFonts.set(qn("w:eastAsia"), east)

        inline_re = None

        def add_inline(par, text, size=None, color=BLACK):
            """解析 **粗体** / *斜体* / `代码` / [n]上标引用 / [待补充：..] 内联标记"""
            import re as _re
            nonlocal inline_re
            if size is None:
                size = body_pt
            if inline_re is None:
                inline_re = _re.compile(
                    r"(\*\*.+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`|\$[^$\n]+\$"
                    r"|\[待补充：[^\]]*\]"
                    r"|\[\d{1,3}(?:[,，\-–]\d{1,3})*\])")
            for seg in inline_re.split(text):
                if not seg:
                    continue
                if seg.startswith("**") and seg.endswith("**"):
                    r = par.add_run(seg[2:-2]); run_font(r, size=size, bold=True)
                elif seg.startswith("`") and seg.endswith("`"):
                    r = par.add_run(seg[1:-1]); run_font(r, ascii_f="Consolas",
                                                         size=max(9, size - 1.5),
                                                         color=RGBColor(0x50, 0x50, 0x50))
                elif seg.startswith("$") and seg.endswith("$") and len(seg) > 2:
                    # 行内公式：$...$ → 行内 OMML（原生可编辑）
                    tex, _ = SectionWriter._latex_preprocess(seg[1:-1])
                    omml = SectionWriter._tex_to_omml(tex)
                    if omml is not None:
                        SectionWriter._append_omml(par, omml)
                    else:
                        r = par.add_run(seg[1:-1]); run_font(r, size=size, italic=True)
                elif seg.startswith("[待补充"):
                    r = par.add_run(seg); run_font(r, size=size, bold=True, color=ORANGE)
                elif _re.fullmatch(r"\[\d{1,3}(?:[,，\-–]\d{1,3})*\]", seg):
                    r = par.add_run(seg)  # GB/T 7714 上标引文（与最终版一致）
                    run_font(r, size=size)
                    r.font.superscript = True
                elif seg.startswith("*") and seg.endswith("*") and len(seg) > 2:
                    r = par.add_run(seg[1:-1]); run_font(r, size=size, italic=True)
                else:
                    r = par.add_run(seg); run_font(r, size=size, color=color)

        def add_body_para(text, indent=True, color=BLACK, italic=False):
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.LEFT if body_left else WD_ALIGN_PARAGRAPH.JUSTIFY
            par.paragraph_format.line_spacing = line_h
            if indent and indent_pt > 0:
                par.paragraph_format.first_line_indent = Pt(indent_pt)
            if italic:
                r = par.add_run(text); run_font(r, italic=True, color=color)
            else:
                add_inline(par, text, color=color)

        def add_heading(text, level):
            """标题层级：默认题名18/章16（居中）/节14/三级12（黑体，英文 Times）；
            模板套入后字号/字体/对齐跟随格式档案"""
            par = doc.add_paragraph()
            par.paragraph_format.space_before = Pt(12 if level <= 2 else 8)
            par.paragraph_format.space_after = Pt(6)
            size = head_sizes.get(level, 12)
            center = True if level <= 2 else False
            if level in (1, 2) and st.get("h1_center") is not None:
                center = bool(st.get("h1_center")) if level == 1 else center
            if center:
                par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = par.add_run(text)
            run_font(r, east=head_font, size=size, bold=True)

        def add_table(rows):
            if not rows or not rows[0]:
                return
            ncols = max(len(r) for r in rows)
            table = doc.add_table(rows=len(rows), cols=ncols)
            table.style = "Table Grid"
            # 表格位置：居中 / 左对齐
            table.alignment = (WD_TABLE_ALIGNMENT.LEFT if tbl_align_left
                               else WD_TABLE_ALIGNMENT.CENTER)
            # 表格宽度：full=撑满页宽(pct 5000)；auto=适应内容
            tbl_pr = table._tbl.tblPr
            tbl_w = tbl_pr.find(qn("w:tblW"))
            if tbl_w is None:
                tbl_w = OxmlElement("w:tblW")
                tbl_pr.append(tbl_w)
            if tbl_auto:
                tbl_w.set(qn("w:w"), "0")
                tbl_w.set(qn("w:type"), "auto")
                table.autofit = True
            else:
                tbl_w.set(qn("w:w"), "5000")
                tbl_w.set(qn("w:type"), "pct")
            # 三线表（GB 学术规范）：仅保留顶线/表头下线/底线，无竖线与内横线
            borders = OxmlElement("w:tblBorders")
            for edge, sz, val in (("top", 12, "single"), ("bottom", 12, "single"),
                                  ("left", 0, "none"), ("right", 0, "none"),
                                  ("insideH", 0, "none"), ("insideV", 0, "none")):
                el = OxmlElement(f"w:{edge}")
                el.set(qn("w:val"), val)
                if val != "none":
                    el.set(qn("w:sz"), str(sz))
                    el.set(qn("w:color"), "000000")
                borders.append(el)
            tbl_pr.append(borders)
            # 跨页表现：首行重复表头（tblHeader）+ 行内禁止跨页断裂（cantSplit）
            for ri, row in enumerate(table.rows):
                tr_pr = row._tr.get_or_add_trPr()
                cant = OxmlElement("w:cantSplit")
                tr_pr.append(cant)
                if ri == 0:
                    th = OxmlElement("w:tblHeader")
                    tr_pr.append(th)
            for i, row in enumerate(rows):
                for j in range(ncols):
                    cell = table.cell(i, j)
                    text = row[j] if j < len(row) else ""
                    par = cell.paragraphs[0]
                    par.alignment = (WD_ALIGN_PARAGRAPH.LEFT if cell_left
                                     else WD_ALIGN_PARAGRAPH.CENTER)
                    add_inline(par, text, size=table_pt)
                    if i == 0:
                        for r in par.runs:
                            r.bold = True
                        # 表头下线
                        tc_pr = cell._tc.get_or_add_tcPr()
                        tc_borders = OxmlElement("w:tcBorders")
                        bottom = OxmlElement("w:bottom")
                        bottom.set(qn("w:val"), "single")
                        bottom.set(qn("w:sz"), "6")
                        bottom.set(qn("w:color"), "000000")
                        tc_borders.append(bottom)
                        tc_pr.append(tc_borders)
            doc.add_paragraph()

        def render_markdown(content: str):
            import re as _re
            lines = content.split("\n")
            para_buf = []
            table_buf = []
            eq_buf = []
            in_eq = False

            def flush():
                if para_buf:
                    text = "".join(para_buf).strip()
                    if text:
                        add_body_para(text)
                    para_buf.clear()

            for line in lines:
                s = line.strip()
                # ---- 多行公式块：$$ 开启/闭合（闭合行触发转换）----
                if in_eq:
                    if s.endswith("$$"):
                        rest = s[:-2]
                        if rest.strip():
                            eq_buf.append(rest)
                        in_eq = False
                        self._docx_add_equation(doc, run_font, "\n".join(eq_buf).strip())
                        eq_buf = []
                    else:
                        eq_buf.append(s)
                    continue
                if s.startswith("$$"):
                    body = s[2:]
                    if body.endswith("$$"):
                        body = body[:-2]
                        flush()
                        if body.strip():
                            self._docx_add_equation(doc, run_font, body.strip())
                    else:      # 块开启（如 $$\begin{aligned}）
                        flush()
                        in_eq = True
                        if body.strip():
                            eq_buf.append(body)
                    continue
                if not s:
                    flush(); continue
                if s in ("---", "***", "___", "--- —"):
                    continue  # 分隔线不进 Word
                m_img = re.match(r"!\[([^\]]*)\]\(([^)\s]+)\)", s)
                if m_img:
                    flush()
                    self._docx_add_image(doc, run_font, m_img.group(2), m_img.group(1),
                                         outline.get("project_path", ""), width_in=fig_in,
                                         caption_east=caption_east, caption_size=caption_pt)
                    continue
                if s.startswith("|"):
                    cells = [c.strip() for c in s.strip("|").split("|")]
                    if all(_re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    table_buf.append(cells)
                    continue
                if table_buf:
                    add_table(table_buf); table_buf.clear()
                if s.startswith("####"):
                    flush(); add_heading(s.lstrip("# ").strip(), 4)
                elif s.startswith("###") or s.startswith("##") or s.startswith("#"):
                    flush(); add_heading(s.lstrip("# ").strip(), 3)
                elif s.startswith(("-", "*")) and len(s) > 2 and s[1] in " \t":
                    flush()
                    par = doc.add_paragraph(style="List Bullet")
                    par.paragraph_format.first_line_indent = Pt(0)
                    add_inline(par, s[1:].strip())
                elif _re.match(r"^\d+[.、）)]", s):
                    flush()
                    add_body_para(s)
                else:
                    para_buf.append(s)
            flush()
            if table_buf:
                add_table(table_buf)

        # ---- 文档结构 ----
        fm = st.get("front_matter") or {}
        sections = outline.get("sections", [])

        def _is_cn_abs(s):
            t = s.get("title", "")
            return "摘要" in t and "英文" not in t and "ABSTRACT" not in t.upper()

        def _is_en_abs(s):
            t = s.get("title", "")
            return "ABSTRACT" in t.upper() or "英文摘要" in t

        if fm:
            # ---- 前置部分复刻（模板结构）：封面 → 声明 → 目录 → 中文摘要 → 英文摘要 ----
            if fm.get("has_cover"):
                self._fm_add_cover(doc, run_font, st, outline, head_font, body_pt)
            if fm.get("declaration_text"):
                self._fm_add_declaration(doc, run_font, fm, head_font, body_pt,
                                         line_h, indent_pt)
            if fm.get("has_toc"):
                self._fm_add_toc(doc, run_font, head_font, head_sizes.get(2, 16))
            cn_abs = next((s for s in sections
                           if _is_cn_abs(s) and (s.get("content") or "").strip()), None)
            en_abs = next((s for s in sections
                           if _is_en_abs(s) and (s.get("content") or "").strip()), None)
            if cn_abs is not None or fm.get("has_cn_abstract"):
                self._fm_add_abstract(doc, run_font, head_font, cn_abs, render_markdown,
                                      "摘  要", fm.get("cn_abstract_style") or {})
            if en_abs is not None:
                self._fm_add_abstract(doc, run_font, head_font, en_abs, render_markdown,
                                      "ABSTRACT", fm.get("en_abstract_style") or {})
            elif fm.get("has_en_abstract"):
                self._fm_add_abstract_placeholder(doc, run_font, head_font)
            # 分节：前置（罗马页码，封面首页干净）→ 正文（阿拉伯页码重新起算）
            from docx.enum.section import WD_SECTION
            doc.add_section(WD_SECTION.NEW_PAGE)
            fronted_ids = {id(cn_abs), id(en_abs)}
            body_secs = [s for s in sections if id(s) not in fronted_ids]
        else:
            fronted_ids = set()
            body_secs = sections
            add_heading(outline.get("paper_title") or outline.get("project_id", "论文"), 1)
            if outline.get("one_sentence_argument"):
                par = doc.add_paragraph()
                par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = par.add_run(f"（核心论证：{outline['one_sentence_argument']}）")
                run_font(r, size=10.5, italic=True, color=GRAY)
            drafted = sum(1 for s in sections if s.get("status") == "drafted")
            total_actual = sum(s.get("actual_words") or 0 for s in sections)
            par = doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = par.add_run(
                f"总目标 {outline.get('total_target_words', '?')} 字 · "
                f"已完成 {drafted}/{len(sections)} 节 · 累计 {total_actual} 字 · "
                f"导出于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            run_font(r, size=9, color=GRAY)
            doc.add_paragraph()

        for i, sec in enumerate(body_secs):
            num = self.CN_NUM[i] if i < len(self.CN_NUM) else str(i + 1)
            add_heading(f"{num}、{sec['title']}", 2)
            if sec.get("content"):
                _clean, _ = self._sanitize_draft(sec["content"])
                render_markdown(self._strip_dup_heading(_clean, sec["title"]))
            else:
                add_body_para(f"[待补充：本节未生成，目标 {sec['target_words']} 字]",
                              indent=False, color=ORANGE, italic=True)
                for kp in sec.get("key_points", []):
                    par = doc.add_paragraph(style="List Bullet")
                    r = par.add_run(kp); run_font(r, size=11, color=GRAY)

        # P1 参考文献节（全局编号收敛 + GB/T 7714）
        pool = self.refmgr.build_global_pool(outline)
        for sec in outline.get("sections", []):
            if sec.get("content"):
                sec["content"] = self.refmgr.sanitize_citations(sec["content"], pool)
        refs_md = self.refmgr.compose_references_section(outline, pool)
        if refs_md:
            add_heading("参考文献", 2)
            for line in refs_md.splitlines():
                if re.match(r"^\[\d{1,3}\]", line):
                    par = doc.add_paragraph()
                    par.paragraph_format.first_line_indent = Pt(0)
                    add_inline(par, line, size=10.5)

        # 页面/页眉/页脚/分节页码：内容全部就位后统一设置（多节文档需在 add_section 之后）
        self._docx_page_setup(doc, st, body_east=body_east, en_font=en_font)
        safe = re_safe_name(outline.get("project_id", "论文"))
        out = Path(out_path) if out_path else scoping.projects_dir() / f"{safe}.论文草稿.docx"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))
        return str(out)

    def export_pdf(self, outline: dict, out_path=None, style: dict = None) -> str:
        """PDF 导出：先按排版设置产出 docx，再用本机 MS Word COM 转 PDF（保真度最高）。
        副产物 docx 同样保留在 projects/ 下。"""
        docx_path = self.export_docx(outline, out_path=out_path, style=style)
        return str(self._docx_to_pdf(Path(docx_path)))

    @staticmethod
    def _docx_to_pdf(docx_path: Path) -> Path:
        """MS Word COM → PDF；未安装 Office 时给出明确错误"""
        pdf_path = docx_path.with_suffix(".pdf")
        try:
            import pythoncom
            from win32com.client import DispatchEx
        except ImportError as e:
            raise RuntimeError("PDF 导出需要 pywin32（.venv/Scripts/python.exe -m pip install pywin32）") from e
        if not docx_path.exists():
            raise FileNotFoundError(docx_path)
        pythoncom.CoInitialize()
        word = None
        try:
            word = DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            doc = word.Documents.Open(str(docx_path.resolve()), ReadOnly=True)
            try:
                doc.ExportAsFixedFormat(str(pdf_path.resolve()), ExportFormat=17)  # wdExportFormatPDF
            finally:
                doc.Close(False)
        finally:
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()
        if not pdf_path.exists():
            raise RuntimeError(f"Word COM 转换未产出 PDF: {pdf_path}")
        return pdf_path

    # ---------- 内部 ----------
    @staticmethod
    def _find_section(outline: dict, section_id: str) -> Optional[dict]:
        for sec in outline.get("sections", []):
            if sec.get("id") == section_id or sec.get("title") == section_id:
                return sec
        return None

    @staticmethod
    def _load_prompt(name: str) -> str:
        path = Path(__file__).parent / "prompts" / name
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _section_template_snippet(title: str, dom: Optional[DomainProfile] = None) -> str:
        """从领域章节模板提取与当前节对应的 ## 小节（D2 模板拆片，避免全文稀释注意力）"""
        full = load_domain_prompt(dom, "section_templates.md") if dom \
            else SectionWriter._load_prompt("section_templates.md")
        if not full or not title:
            return full
        blocks = re.split(r"(?=^## )", full, flags=re.M)
        hits = [b for b in blocks
                if b.startswith("## ") and (title in b[3:40] or b[3:40].strip("0123456789. 、") in title)]
        if hits:
            return "".join(hits)
        # 兜底：给编号小节做一次宽松匹配（标题去"材料"等前缀差异）
        key = re.sub(r"[材料与]+", "", title)[:2]
        hits = [b for b in blocks if b.startswith("## ") and key and key in b[3:40]]
        return "".join(hits) if hits else full


def count_cn_words(text: str) -> int:
    """中文字数统计：CJK字符 + 标点 + ASCII词，近似 Word 字数"""
    import re
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    ascii_words = len(re.findall(r"[A-Za-z0-9]+(?:[-.'/][A-Za-z0-9]+)*", text))
    return cjk + ascii_words
