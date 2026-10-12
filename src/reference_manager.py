# -*- coding: utf-8 -*-
"""
引用管理器：全文统一编号引用池 + GB/T 7714 参考文献生成 + 引用校验
数据源：ingestion/method_registry.yaml（唯一事实源，含真实作者/题名/期刊/年/DOI）
"""
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "ingestion" / "method_registry.yaml"


def _load_registry() -> List[dict]:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _gbt7714(paper: dict) -> str:
    """注册表 paper 元数据 → GB/T 7714 条目"""
    authors = (paper.get("authors") or "").strip()
    title = (paper.get("title") or "").strip()
    journal = (paper.get("journal") or "").strip()
    year = paper.get("year") or ""
    doi = (paper.get("doi") or "").strip()
    if not title:
        return ""
    # 作者超过 3 位取前 3 加"等"（注册表已较简，此处仅截断）
    parts = [a.strip() for a in authors.split(",") if a.strip()]
    if len(parts) > 3:
        authors = ", ".join(parts[:3]) + ", 等"
    entry = f"{authors}. {title}[J]. {journal}, {year}." if journal else f"{authors}. {title}. {year}."
    if doi:
        entry += f" DOI: {doi}."
    return entry


class ReferenceManager:
    """全文统一编号引用池"""

    def __init__(self, registry_path: Optional[Path] = None):
        self._registry: List[dict] = _load_registry()
        if registry_path:
            with open(registry_path, "r", encoding="utf-8") as f:
                self._registry = yaml.safe_load(f) or []
        # name_zh / name_en → 注册表条目
        self._by_name: Dict[str, dict] = {}
        for m in self._registry:
            for k in (m.get("name_zh"), m.get("name_en")):
                if k:
                    self._by_name[k] = m

    # ---------- 全局引用池 ----------
    def names_with_papers(self) -> List[str]:
        """注册表中带论文条目的方法名（name_zh 优先）——method_pool 的合法来源"""
        out = []
        for m in self._registry:
            if not (m.get("paper") or {}).get("title"):
                continue
            out.append(m.get("name_zh") or m.get("name_en") or "")
        return [n for n in out if n]

    def build_global_pool(self, outline: dict, cap: int = 55) -> List[dict]:
        """全文统一引用池（扩容）：各节 cited_methods（保持旧编号兼容）→
        outline.method_pool（画像方法，写作时并入）→ 注册表其余条目补足。
        返回 [{n, method_id, name, gb}]，上限 cap 条。"""
        ordered: List[str] = []
        seen = set()

        def _add(name):
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)

        for sec in outline.get("sections", []):
            for name in sec.get("cited_methods") or []:
                _add(name)
        for name in outline.get("method_pool") or []:
            _add(name)
        for m in self._registry:  # 注册表补足，保证引文广度
            if len(ordered) >= cap:
                break
            _add(m.get("name_zh"))
        pool: List[dict] = []
        for name in ordered[:cap]:
            meta = self._by_name.get(name)
            if not meta:
                continue
            gb = _gbt7714(meta.get("paper") or {})
            if not gb:
                continue
            pool.append({
                "n": len(pool) + 1,
                "method_id": meta.get("id", name),
                "name": name,
                "gb": gb,
            })
        return pool

    # 章类型 → 候选加分的注册表类目（结果/实验章引评估方法，方法章引建模方法；
    # 类目来自方法注册表，自定义注册表可沿用这些通用类目名获得加分）
    _SEC_CATEGORIES = {
        "结果": {"模型评估", "生存分析基础", "统计方法", "报告规范", "评价指标", "评估方法"},
        "发现": {"模型评估", "生存分析基础", "统计方法", "报告规范", "评价指标", "评估方法"},
        "实验": {"模型评估", "统计方法", "评价指标", "评估方法", "基准测试"},
        "方法": {"生存模型", "机器学习模型", "深度学习模型", "正则化", "数据预处理",
                "特征工程", "数据增强", "集成学习", "建模规范", "因果推断",
                "模型架构", "算法基础", "优化方法"},
    }

    def suggest_candidates(self, section: Optional[dict], pool: List[dict],
                           k: int = 12) -> set:
        """为该节挑 k 条候选引用（★本节核心）：cited_methods 命中 > 方法名出现在
        节文本（标题/要点/beats/小节标题） > 章类型类目加分，平分按池序。"""
        if not pool:
            return set()
        if not section:
            return {item["n"] for item in pool[:k]}
        text_parts = [section.get("title", ""),
                      " ".join(section.get("key_points") or []),
                      " ".join(f"{b.get('claim', '')} {b.get('support', '')}"
                               for b in section.get("beats") or [])]
        for sub in section.get("subsections") or []:
            if not isinstance(sub, dict):
                continue
            text_parts.append(sub.get("title", ""))
            text_parts.extend(sub.get("key_points") or [])
        text = " ".join(text_parts)
        title = section.get("title", "")
        cats = next((c for key, c in self._SEC_CATEGORIES.items() if key in title), set())
        cited = set(section.get("cited_methods") or [])
        scored = []
        for item in pool:
            meta = self._by_name.get(item["name"]) or {}
            score = 3.0 if item["name"] in cited else 0.0
            toks = [t for t in re.split(r"[()（）/\s、,，;；\-]+", item["name"]) if len(t) >= 2]
            score += 2.0 * sum(1 for t in toks if t in text)
            if (meta.get("category") or "") in cats:
                score += 2.0
            scored.append((-score, item["n"]))
        scored.sort()
        return {n for _, n in scored[:k]}

    def pool_prompt_text(self, pool: List[dict], section: Optional[dict] = None,
                         candidates: Optional[set] = None,
                         min_citations: int = 8) -> str:
        """注入提示词的引用池文本；candidates/section 给定时高亮该节应优先引用的条目。
        min_citations>0 时附带「事实句必带 [n]」硬性要求；<=0 用于不引文献的节（摘要/结论）。"""
        if not pool:
            return "（本项目引用池为空：正文中不得出现任何 [n] 引用标记）"
        own = candidates if candidates is not None else set()
        if section and candidates is None:
            own = {item["n"] for item in pool
                   if item["name"] in (section.get("cited_methods") or [])}
        lines = ["正文引用使用 [n] 语法（如「……已成为主流方案[3]」），n 对应下列全文统一编号；"
                 "只允许引用下列条目。"]
        if min_citations > 0:
            lines.append(
                f"硬性要求：每个事实性/方法性/与既往研究对照的断言句，句尾必须带 [n]"
                f"（优先用标 ★ 的本节核心条目，无对应支撑的判断不得硬贴）；本节 [n] 总数不低于 {min_citations} 处。")
        for item in pool:
            mark = " ★本节核心" if item["n"] in own else ""
            lines.append(f"【{item['n']}】{item['gb']}{mark}")
        return "\n".join(lines)

    # ---------- 校验 ----------
    def verify_citations(self, content: str, pool: List[dict]) -> dict:
        """正文 [n] 标记校验：超池/空池引用 → invalid 列表"""
        pool_ns = {item["n"] for item in pool}
        cited = sorted({int(n) for n in re.findall(r"\[(\d{1,3})\]", content or "")})
        invalid = [n for n in cited if n not in pool_ns]
        return {"cited": cited, "invalid": invalid,
                "ok": not invalid and bool(cited) is not False}

    # ---------- 导出：参考文献节 ----------
    def compose_references_section(self, outline: dict, pool: Optional[List[dict]] = None) -> str:
        """全文重编号后的 GB/T 7714 参考文献节（markdown）。
        只收录正文中实际以 [n] 引用过的条目，保持编号稳定。"""
        pool = pool or self.build_global_pool(outline)
        full = "\n".join(sec.get("content") or "" for sec in outline.get("sections", []))
        cited_ns = {int(n) for n in re.findall(r"\[(\d{1,3})\]", full)}
        if not cited_ns:
            return ""
        used = [item for item in pool if item["n"] in cited_ns]
        if not used:
            return ""
        lines = ["", "## 参考文献", ""]
        for item in used:
            lines.append(f"[{item['n']}] {item['gb']}")
        return "\n".join(lines) + "\n"

    # ---------- 宽松重编号（正文 [n] 超池时收敛到池内最近条目） ----------
    def sanitize_citations(self, content: str, pool: List[dict]) -> str:
        """把超出池范围的 [n] 收敛为池内最大编号（防止越界编号进入导出）"""
        max_n = len(pool)
        if max_n == 0:
            return content

        def _cap(m):
            n = int(m.group(1))
            return f"[{min(n, max_n)}]"

        return re.sub(r"\[(\d{1,3})\]", _cap, content)
