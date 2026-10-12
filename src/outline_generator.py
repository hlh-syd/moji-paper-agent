"""
大纲生成 + 字数预估模块
流程：
  1) compute_targets：按期刊模板把总字数按比例分配到各节（纯函数，确定性）
  2) generate_outline：项目画像 + 写作知识库检索 + 宪法约束 → LLM 生成章节树
     （每节：要点、证据指针、目标字数；LLM 只做组织，数字与证据来自画像）
  3) 大纲以 JSON 持久化，支持单节重新生成与人工编辑

中文说明：字数预估以中文字符计（含标点），写作模块生成后回填 actual_words。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.config import AppConfig, get_config_manager
from src.domain import DomainProfile, resolve_domain, load_domain_prompt
from src import scoping

logger = logging.getLogger(__name__)

# 判定「章式节」的关键词：需要拆二级小节并补「本章小结」的章
_CHAPTERY_KEYWORDS = ("材料与方法", "方法", "结果", "发现", "前言", "讨论", "实验")

PROJECTS_DIR = scoping.legacy_projects_dir()  # 兼容旧引用；读写一律走 scoping.projects_dir()


def compute_targets(template_cfg, total_words: Optional[int] = None) -> Dict[str, int]:
    """按模板比例分配各节目标字数；caps 封顶的节超出部分按比例重分配到其余节。

    例：总字数 40000 时，摘要按比例应为 2000 字（不现实），封顶 800 后
    多出的 1200 字按比例归入前言/方法/结果/讨论。
    """
    total = total_words or template_cfg.total_words
    ratios: Dict[str, float] = template_cfg.sections
    caps: Dict[str, int] = getattr(template_cfg, "caps", None) or {}

    targets: Dict[str, Optional[int]] = {}
    capped_sum = 0
    free_keys: List[str] = []
    for key, ratio in ratios.items():
        want = ratio * total
        cap = caps.get(key)
        if cap and want > cap:
            targets[key] = cap
            capped_sum += cap
        else:
            targets[key] = None
            free_keys.append(key)

    if free_keys:
        remaining = total - capped_sum
        free_ratio = sum(ratios[k] for k in free_keys)
        for k in free_keys:
            targets[k] = max(200, round(remaining * ratios[k] / free_ratio))

    # 舍入误差归入字数最多的自由节
    diff = total - sum(v for v in targets.values() if v)
    if free_keys and diff != 0:
        targets[max(free_keys, key=lambda k: targets[k] or 0)] += diff
    return {k: v for k, v in targets.items()}


class OutlineGenerator:
    """大纲生成器"""

    def __init__(self, llm_manager, vector_store_manager, config: Optional[AppConfig] = None):
        self.llm_manager = llm_manager
        self.vsm = vector_store_manager
        self.config = config or get_config_manager().get_config()

    # ---------- 知识库检索 ----------
    def _retrieve_writing_guidance_multi(self, queries: List[str], k: int = 3) -> str:
        """P2 多查询写作指导：逐查询检索合并去重"""
        collection = self.config.vector_store.collections.writing_methodology
        merged, seen = [], set()
        for q in queries[:6]:
            if not q.strip():
                continue
            try:
                docs = self.vsm.similarity_search(collection, q, k=k)
            except Exception:
                continue
            for d in docs:
                key = (d.page_content or "")[:80]
                if key in seen:
                    continue
                seen.add(key)
                merged.append(d)
        parts = [f"【{d.metadata.get('file_name', '未知')}】\n{d.page_content[:500]}"
                 for d in merged[:8]]
        return "\n\n".join(parts)

    def _retrieve_writing_guidance(self, section_title: str, key_hint: str = "", k: int = 4) -> str:
        """从写作教学知识库检索该节的写作指导"""
        try:
            collection = self.config.vector_store.collections.writing_methodology
            docs = self.vsm.similarity_search(collection, f"{section_title} 写作 结构 {key_hint}", k=k)
            parts = []
            for d in docs:
                src = d.metadata.get("file_name", "未知")
                parts.append(f"【{src}】\n{d.page_content[:600]}")
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"写作知识库检索失败（降级为无检索）: {e}")
            return ""

    def _retrieve_method_papers(self, method_names: List[str], k: int = 8,
                                extra_queries: Optional[List[str]] = None) -> str:
        """检索方法原论文：优先按 method_id 精确取，向量检索兜底"""
        method_ids = self._names_to_method_ids(method_names)
        try:
            collection = self.config.vector_store.collections.method_papers
            docs = []
            if method_ids:
                docs = self.vsm.get_by_method_ids(collection, method_ids, limit=40)
            if not docs:
                docs = self.vsm.similarity_search(collection, " ".join(method_names[:8])[:400], k=k)
            # P2 多查询合并（按内容前80字去重）
            for q in (extra_queries or [])[:6]:
                if not q.strip():
                    continue
                try:
                    docs += self.vsm.similarity_search(collection, q, k=3)
                except Exception:
                    continue
            seen_keys, uniq = set(), []
            for d in docs:
                key = (d.page_content or "")[:80]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                uniq.append(d)
            docs = uniq
            parts = []
            for d in docs:
                src = d.metadata.get("file_name", "未知")
                parts.append(f"【{src}】\n{d.page_content[:800]}")
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"方法知识库检索失败（降级为无检索）: {e}")
            return ""

    _name2id_cache: Optional[dict] = None

    def _names_to_method_ids(self, method_names: List[str]) -> List[str]:
        """中文/英文名 -> 注册表 method_id"""
        if self._name2id_cache is None:
            try:
                import yaml
                reg_path = Path(self.config.knowledge_base.registry_dir) / "method_registry.yaml"
                with open(reg_path, "r", encoding="utf-8") as f:
                    registry = yaml.safe_load(f) or []
                cache = {}
                for m in registry:
                    if m.get("name_zh"):
                        cache[m["name_zh"]] = m["id"]
                    if m.get("name_en"):
                        cache[m["name_en"]] = m["id"]
                self._name2id_cache = cache
            except Exception:
                self._name2id_cache = {}
        return [self._name2id_cache[n.strip()] for n in method_names
                if n and n.strip() in self._name2id_cache]

    # ---------- 画像压缩 ----------
    @staticmethod
    def compress_profile(profile: dict, max_chars: int = 16000) -> str:
        """把项目画像压成 LLM 可读文本（预算制，关键信息优先）。
        优先级：关键结果数字 > 图片清单（含视觉描述）> 方法 > 结果表索引；
        结果表只留索引（完整预览由小节级资源分配注入写作单元），
        截断时宁可丢表也不丢数字与图片路径。"""
        lines = [f"# 项目画像：{profile.get('project_name', '')}"]
        lines.append("\n## 研究阶段")
        for s in (profile.get("stages") or [])[:12]:
            lines.append(f"- {s.get('stage_dir')}（{s.get('python_scripts', 0)}个脚本）："
                         f"{(s.get('summary') or '')[:120]}")
        lines.append("\n## 数据集")
        for d in profile.get("datasets", []):
            lines.append(f"- {d.get('dataset')}（{d.get('label')}）")

        def _block(title: str, body_lines: List[str], budget: int) -> str:
            body = "\n".join(body_lines)[:budget]
            return f"\n## {title}\n{body}"

        # 关键结果数字：全文数字的唯一权威来源（最高预算）
        kr_lines = []
        for r in profile.get("key_results", []):
            srcs = r.get("sources") or [{}]
            s0 = srcs[0] if isinstance(srcs[0], dict) else {"file": str(srcs[0]), "dir": ""}
            ctx = (r.get("context") or "").strip()[:80]
            kr_lines.append(f"- {r.get('metric')} = {r.get('value')}"
                            f"（{s0.get('dir', '')}/{s0.get('file', '')}）"
                            + (f"｜{ctx}" if ctx else ""))

        # 图片清单：有视觉描述的排前（预算内优先保留），路径逐字照抄
        figs = sorted(profile.get("figures") or [],
                      key=lambda f: 0 if (f.get("desc") or "").strip() else 1)
        fig_lines = []
        for f in figs[:90]:
            desc = (f.get("desc") or "").strip()
            if desc:
                fig_lines.append(f"- {f.get('rel') or f.get('path')}：{desc[:90]}")
            else:
                fig_lines.append(f"- {f.get('rel') or f.get('path')}  （{f.get('name')}）")

        m_lines = [f"- {m.get('name_zh')}（{m.get('name_en')}）[{m.get('category')}]"
                   for m in profile.get("methods", [])]
        tbl_lines = [f"- {t.get('dir')}/{t.get('file')}（{t.get('format')}，{t.get('rows')}行）"
                     for t in (profile.get("result_tables") or [])[:40]]

        lines.append(_block("关键结果数字（论文中引用必须与此一致，括号为来源目录/文件=队列或模型归属）",
                            kr_lines, int(max_chars * 0.40)))
        lines.append(_block("可用图片清单（引用语法：![图题](路径)，路径逐字照抄；「：」后为看图实录）",
                            fig_lines, int(max_chars * 0.25)))
        lines.append(_block("使用的方法", m_lines, int(max_chars * 0.08)))
        lines.append(_block("结果表索引（完整内容按小节分配注入，禁止编造行列）",
                            tbl_lines, int(max_chars * 0.15)))
        return "\n".join(lines)[:max_chars]

    # ---------- 主入口 ----------
    # ---------- 多级小节 ----------
    @classmethod
    def _is_chaptery(cls, chapter_title: str) -> bool:
        return any(k in chapter_title for k in _CHAPTERY_KEYWORDS)

    @classmethod
    def _normalize_subsections(cls, parent_id: str, raw, chapter_target: int,
                               chapter_title: str) -> list:
        """LLM 输出的 subsections → 规范对象树（两级为主、方法/结果可三级）。
        字符串条目自动转对象；小节字数与章目标对齐；章式节补「本章小结」。"""
        if not isinstance(raw, list):
            return []
        subs = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                subs.append({"title": item.strip()})
            elif isinstance(item, dict):
                t = (item.get("title") or item.get("节名") or "").strip()
                if t:
                    subs.append({
                        "title": t,
                        "target_words": item.get("target_words"),
                        "key_points": item.get("key_points") or [],
                        "subsections": item.get("subsections") or [],
                    })
        # 去重（同标题）
        seen, uniq = set(), []
        for s in subs:
            if s["title"] not in seen:
                seen.add(s["title"])
                uniq.append(s)
        subs = uniq
        if not subs:
            return []
        # 章式节补本章小结
        if cls._is_chaptery(chapter_title) \
                and not any("小结" in s["title"] for s in subs):
            subs.append({"title": "本章小结", "target_words": 300, "key_points": [],
                         "subsections": []})
        # 字数分配：有给的用，缺失/总和偏差大则按比例归一到章目标
        given = []
        for s in subs:
            try:
                v = int(float(s.get("target_words") or 0))
            except (TypeError, ValueError):
                v = 0
            s["target_words"] = max(0, v)
            given.append(s["target_words"])
        total_given = sum(given)
        if total_given <= 0:
            share = max(400, chapter_target // max(len(subs), 1))
            for s in subs:
                s["target_words"] = share
            total_given = share * len(subs)
        if abs(total_given - chapter_target) > chapter_target * 0.25:
            k = chapter_target / total_given
            for s in subs:
                s["target_words"] = max(200, round(s["target_words"] * k))
        # id / level / 状态 / 三级规范化（三级限长章）
        allow_l3 = chapter_target >= 6000
        for i, s in enumerate(subs, 1):
            s["id"] = f"{parent_id}_{i}"
            s["level"] = 2
            s["status"] = "pending"
            s["content"] = ""
            s["actual_words"] = 0
            l3 = s.pop("subsections", []) or []
            if allow_l3 and isinstance(l3, list) and len(l3) >= 2:
                norm_l3 = []
                for j, item in enumerate(l3, 1):
                    t = item.strip() if isinstance(item, str) else \
                        (item.get("title") or "").strip() if isinstance(item, dict) else ""
                    if not t:
                        continue
                    child = {"id": f"{s['id']}_{j}", "title": t, "level": 3,
                             "status": "pending", "content": "", "actual_words": 0,
                             "key_points": []}
                    if isinstance(item, dict):
                        try:
                            child["target_words"] = int(float(item.get("target_words") or 0))
                        except (TypeError, ValueError):
                            child["target_words"] = 0
                    norm_l3.append(child)
                if norm_l3:
                    # 三级字数归一到父小节
                    t3 = sum(c.get("target_words") or 0 for c in norm_l3)
                    if t3 <= 0:
                        share = max(250, s["target_words"] // (len(norm_l3) + 1))
                        for c in norm_l3:
                            c["target_words"] = share
                        t3 = share * len(norm_l3)
                    if t3 > s["target_words"]:
                        k = s["target_words"] / t3
                        for c in norm_l3:
                            c["target_words"] = max(180, round(c["target_words"] * k))
                    s["subsections"] = norm_l3
        return subs

    async def generate_outline(
        self,
        profile: dict,
        template_key: str = "chinese_journal",
        total_words: Optional[int] = None,
        user_requirements: str = "",
        reference_structure: Optional[dict] = None,
        project_body: str = "",
        domain: Optional[str] = None,
        reference_skeleton: Optional[dict] = None,
        reference_paper_name: Optional[str] = None,
    ) -> dict:
        """生成结构化大纲 JSON；提供 reference_structure 时借鉴其章节组织（不照搬内容）；
        project_body 为项目理解 Agent 产出的《研究项目主体》，作为最高优先级上下文；
        domain 显式指定领域（medical/cs/generic），缺省用画像 domain 字段或自动检测；
        reference_skeleton 为模板论文写作骨架（通道B 产物）——只迁移叙事形式不迁移内容"""
        dom = resolve_domain(profile, domain)
        templates = self.config.outline.templates
        if template_key not in templates:
            raise ValueError(f"未知模板: {template_key}，可选: {list(templates)}")
        template_cfg = templates[template_key]
        targets = compute_targets(template_cfg, total_words)
        order = list(targets.keys())

        profile_text = self.compress_profile(profile)
        writing_guidance = self._retrieve_writing_guidance(dom.kb_query)
        method_papers = self._retrieve_method_papers(
            [m.get("name_zh", "") for m in profile.get("methods", [])]
        )
        reference_block = ""
        if reference_structure:
            from src.reference_parser import ReferenceParser
            reference_block = (
                "\n\n## 参考论文结构分析（借鉴其章节组织与叙事方式）\n"
                "注意：只借鉴结构与写法；数据、结论、创新点一律不得照搬，必须来自本项目画像。\n"
                + ReferenceParser.compress_structure(reference_structure)
            )
        project_body_block = ""
        if project_body:
            project_body_block = (
                "\n## 研究项目主体（项目理解 Agent 产出，最高优先级：研究顺序/核心脚本/"
                "结果图表路径/创新点以此为准）\n" + project_body[:12000] + "\n"
            )
        # 写作骨架卡（通道B）：体裁级叙事形式约束——内容>形式，骨架 move 与项目事实矛盾时丢弃
        skeleton_block = ""
        skeleton_instruction = ""
        if reference_skeleton:
            from src.skeleton_extractor import SkeletonExtractor
            skeleton_block = (
                "\n\n## 写作骨架（模板论文的叙事形式，内容无关；只迁移形式）\n"
                "规则：骨架只约束章节功能与论证动作的**组织方式**；骨架槽位须映射到大纲章节"
                "（允许增删并，映射不上时以项目内容为准）；骨架中的任何具体内容词不得进入大纲。\n"
                + SkeletonExtractor.skeleton_card(reference_skeleton)
            )
            skeleton_instruction = (
                "- 输出的每个 section 可加 \"skeleton_slot\" 字段（该章对应的骨架槽位："
                "摘要/引言/综述/方法/结果/讨论/结论）；后处理会自动补全校验\n"
                "- 章节的修辞动作参考骨架对应槽位的 moves（如引言按「领域重要性→局限→空缺→"
                "贡献预告」组织 key_points）\n")

        system_prompt = self._load_prompt("constitution.md")
        template_prompt = load_domain_prompt(dom, "section_templates.md")
        section_names = "/".join(order)
        method_secs = [t for t in order if any(k in t for k in ("方法", "模型", "设计"))]
        result_secs = [t for t in order if any(k in t for k in ("结果", "实验", "发现", "评估"))]
        mirror_note = ""
        if method_secs and result_secs:
            mirror_note = (f"- **多级小节结构（重要）**：「{'、'.join(method_secs)}」与"
                           f"「{'、'.join(result_secs)}」各章必须拆分 4-7 个二级小节（subsections），\n"
                           "  每个小节目标字数之和≈章目标字数；内容复杂的小节可再嵌套 2-4 个三级小节；\n"
                           "  摘要/结论不拆小节。方法类章与结果类章的小节必须**镜像对应**\n"
                           "  （如方法「2.4 建模策略」↔结果「3.2 建模结果」），两章的最后一个小节固定为"
                           "「本章小结」（200-400字）\n")
        else:
            mirror_note = ("- **多级小节结构（重要）**：正文核心章必须拆分 4-7 个二级小节（subsections），"
                           "每个小节目标字数之和≈章目标字数；摘要/结论不拆小节\n")

        user_prompt = f"""你是一名{dom.persona}。请基于以下材料生成论文大纲。

## 写作宪法（必须遵守）
{system_prompt}

## 章节结构模板（必须遵循其叙事骨架）
{template_prompt}

## 期刊字数模板
模板名: {template_cfg.name}
各节目标字数（必须严格采用这些数字作为 target_words）:
{json.dumps(targets, ensure_ascii=False, indent=2)}

## 项目画像（唯一事实来源；所有数字/方法/数据集只能从这里取）
{profile_text}
{project_body_block}
## 方法原论文知识（引用支撑材料）
{method_papers[:4000]}

## 写作方法指导（参考其结构建议）
{writing_guidance[:3000]}

{f"## 作者额外要求\\n{user_requirements}" if user_requirements else ""}
{reference_block}
{skeleton_block}

## 任务
输出一个 JSON 对象（不要 markdown 代码块包裹，直接输出 JSON）：
{{
  "one_sentence_argument": "按'在[对象]中，我们通过[方法]证明[进展]，证据是[结果]，边界是[范围]'写一句话论证",
  "sections": [
    {{
      "title": "节名（必须是模板中的节名之一：{section_names}）",
      "target_words": 该节目标字数（用上面给的数字）,
      "key_points": ["该节要点，3-6条，每条一句话；结果类节要点必须含画像中的具体数字"],
      "subsections": [
        {{"title": "小节标题（不带编号）", "target_words": 小节目标字数,
          "key_points": ["小节要点1-3条"],
          "subsections": [{{"title": "三级小节标题", "target_words": 字数}}]}}
      ],
      "evidence": [{{"file": "证据文件路径（来自画像）", "note": "该文件支撑什么"}}],
      "cited_methods": ["该节涉及的方法 name_zh（来自画像方法清单）"]
    }}
  ]
}}

要求：
- sections 覆盖模板全部节，顺序：{'→'.join(order)}
{skeleton_instruction}{mirror_note}- evidence 必须真实来自项目画像中的文件；找不到证据的要点直接不写，不得编造
- cited_methods 用于后续方法文献引用，必须逐字来自画像方法清单
- 若提供参考论文：小节划分与展开顺序参考其章节组织（但节名仍用模板节名，内容只来自本项目）
{f"- 领域要求：{dom.outline_hint}" if dom.outline_hint else ""}
"""

        llm = self.llm_manager.get_main_llm()
        from langchain_core.messages import HumanMessage
        outline = None
        last_err = ""
        for attempt in range(2):  # P1：解析失败带错误反馈重试一次
            resp = await llm.ainvoke([HumanMessage(content=user_prompt)])
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            outline = self._parse_outline_json(content)
            if outline.get("sections"):
                break
            last_err = content[:200]
            truncated = not content.rstrip().endswith("}")
            logger.warning(f"大纲 JSON 解析失败（第 {attempt + 1} 次，"
                           f"{'输出被截断' if truncated else '非法 JSON'}），带反馈重试")
            if truncated:
                feedback = ("\n\n# 重要：上一次输出因超长被截断，无法解析。请大幅压缩输出体积：\n"
                            "- one_sentence_argument 压缩到 60 字以内\n"
                            "- 每章 key_points ≤3 条、每条 ≤25 字\n"
                            "- 每章 subsections 3-5 个，小节只给 title 与 target_words，"
                            "key_points ≤2 条且每条 ≤20 字\n"
                            "- 严格只输出 JSON 对象，禁止任何解释文字")
            else:
                feedback = (f"\n\n# 重要：上一次输出无法解析为合法 JSON（错误片段：{last_err}）。\n"
                            "请严格只输出一个合法 JSON 对象：以 {{ 开始、}} 结束，"
                            "sections 为数组，每项含 title/key_points/subsections/evidence/cited_methods，"
                            "不要任何解释文字或代码块。")
            user_prompt = user_prompt + feedback
        if not outline.get("sections"):
            raise ValueError(f"大纲 JSON 两次解析均失败，最后错误: {last_err[:160]}")

        # P1 语义校验：cited_methods ⊆ 画像方法；key_points 非空兜底
        known_methods = {m.get("name_zh") for m in profile.get("methods", [])}
        for sec in outline.get("sections", []):
            if sec.get("cited_methods"):
                sec["cited_methods"] = [c for c in sec["cited_methods"] if c in known_methods]
            if not sec.get("key_points"):
                sec["key_points"] = [f"（待补充：{sec.get('title', '本节')}要点）"]

        # 结构校验 + 字数回填（LLM 数字以模板为准）
        order = list(targets.keys())
        sections = []
        known_titles = set()
        for sec in outline.get("sections", []):
            title = (sec.get("title") or "").strip()
            matched = next((t for t in order if t in title or title in t), None)
            if matched is None:
                matched = next((t for t in order if t not in known_titles), order[-1])
            known_titles.add(matched)
            sections.append({
                "id": f"sec_{len(sections)+1}",
                "title": matched,
                "llm_title_detail": title,
                "target_words": targets.get(matched, 300),
                "actual_words": 0,
                "status": "pending",
                "key_points": sec.get("key_points", []),
                "subsections": self._normalize_subsections(
                    f"sec_{len(sections)+1}", sec.get("subsections", []),
                    targets.get(matched, 300), matched),
                "evidence": sec.get("evidence", []),
                "cited_methods": sec.get("cited_methods", []),
                "content": "",
            })
        # 未覆盖的模板节补空壳
        for t in order:
            if t not in known_titles and t != "其他":
                sections.append({
                    "id": f"sec_{len(sections)+1}", "title": t, "llm_title_detail": t,
                    "target_words": targets.get(t, 300), "actual_words": 0, "status": "pending",
                    "key_points": [], "subsections": [], "evidence": [], "cited_methods": [], "content": "",
                })

        # P2 深大纲：beats + 复审（失败降级；原地变异 sections）
        try:
            await self.enrich_outline_deep({"sections": sections}, profile, dom)
        except Exception as e:
            logger.warning(f"深大纲跳过: {e}")

        # 骨架-大纲对齐（硬校验）：槽位映射落盘 + 未覆盖槽位警告（不阻断）
        skeleton_alignment = None
        if reference_skeleton:
            from src.skeleton_extractor import SkeletonExtractor
            skeleton_alignment = SkeletonExtractor.match_slots(sections, reference_skeleton)
            for w in skeleton_alignment.get("warnings", [])[:4]:
                logger.info(f"[骨架对齐] {w}")

        result = {
            "project_id": profile.get("project_name", "project"),
            "project_path": profile.get("project_path"),
            "template": template_key,
            "template_name": template_cfg.name,
            "domain": dom.key,
            "total_target_words": sum(targets.values()),
            "one_sentence_argument": outline.get("one_sentence_argument", ""),
            "sections": sections,
            "generated_at": datetime.now().isoformat(),
        }
        if reference_skeleton:
            # 骨架随大纲持久化：分节写作/自查的叙事形式约束（写作链路复用）
            result["reference_skeleton"] = reference_skeleton
            result["reference_paper"] = reference_paper_name or ""
            result["skeleton_alignment"] = skeleton_alignment
            logger.info(f"大纲已注入写作骨架（{len(reference_skeleton.get('sections', []))} 槽位，"
                        f"未覆盖槽位: {skeleton_alignment.get('unmatched_slots') or '无'}）")
        if project_body:
            result["project_body"] = project_body  # 研究主体随大纲持久化，写作链路复用
            logger.info(f"大纲已注入研究项目主体（{len(project_body)} 字符）")
        logger.info(f"大纲生成完成: {len(result['sections'])} 节, 目标 {result['total_target_words']} 字")
        return result

    async def enrich_outline_deep(self, outline: dict, profile: dict,
                                  dom: Optional[DomainProfile] = None) -> dict:
        """P2 深大纲：为每节生成 beats（主张/支撑/检索词/字数），并做一轮大纲复审。
        失败时静默保留原 key_points（降级不阻断）。"""
        dom = dom or resolve_domain(profile)
        import re as _re
        llm = self.llm_manager.get_main_llm()
        from langchain_core.messages import HumanMessage

        facts = []
        for r in profile.get("key_results", []):
            facts.append(f"{r.get('metric')}={r.get('value')}")
        figs = [f.get("rel") or f.get("path") for f in (profile.get("figures") or [])[:30]]
        methods = [m.get("name_zh") for m in profile.get("methods", [])][:40]

        sections_brief = []
        for i, sec in enumerate(outline.get("sections", []), 1):
            sections_brief.append({
                "index": i, "title": sec.get("title"),
                "target_words": sec.get("target_words"),
                "key_points": sec.get("key_points", [])[:6],
            })

        prompt = f"""你是{dom.persona}。下面是论文大纲骨架与项目事实材料。
任务（两步合为一）：
1) 复审：检查各节覆盖度、递进逻辑、图片使用机会、字数平衡；
2) 深化：为每节生成 beats（逐段写作纲要），每条 beat 含：
   - claim: 该段核心观点（一句话）
   - support: 支撑（引用项目事实编号如{dom.fact_example}/图片相对路径/方法名，必须逐字来自材料）
   - kb_query: 为写该段应检索的资料关键词（2-4个词）
   - words: 该段约多少字（各节合计≈该节 target_words）

只输出 JSON（不要代码块）：
{{"review_notes": "复审发现与修正说明（80字内）",
  "sections": [{{"index": 1, "beats": [{{"claim": "...", "support": "...", "kb_query": "...", "words": 300}}]}}]}}

# 大纲骨架
{json.dumps(sections_brief, ensure_ascii=False, indent=1)}

# 项目事实
关键结果: {'、'.join(facts[:20])}
可用图片: {'、'.join(figs[:20])}
方法清单: {'、'.join(methods[:30])}"""

        try:
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            m = _re.search(r"```(?:json)?\s*(.*?)```", content, _re.S)
            if m:
                content = m.group(1)
            start, end = content.find("{"), content.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("无 JSON")
            data = json.loads(content[start:end + 1])
            review_notes = data.get("review_notes", "")
            by_index = {s.get("index"): s.get("beats", []) for s in data.get("sections", [])
                        if isinstance(s, dict)}
            for sec in outline.get("sections", []):
                beats = by_index.get(int(_re.findall(r"\d+", sec.get("id", "0"))[0] or 0) if _re.findall(r"\d+", sec.get("id", "0")) else None)
                if beats and isinstance(beats, list) and len(beats) >= 2:
                    sec["beats"] = beats[:8]
            outline["outline_review_notes"] = review_notes
            logger.info(f"深大纲完成: review_notes={review_notes[:60]}, "
                        f"beats覆盖={sum(1 for s in outline['sections'] if s.get('beats'))}/{len(outline['sections'])}")
        except Exception as e:
            logger.warning(f"深大纲失败（降级为 key_points）: {e}")

        # P2 补漏：整体调用未覆盖的节，逐节单独深化（小提示更可靠）
        missing = [s for s in outline.get("sections", [])
                   if not s.get("beats") and (s.get("key_points") or s.get("content"))]
        for sec in missing[:6]:
            try:
                single = await self._enrich_single_section(sec, facts, figs, methods, llm)
                if single:
                    sec["beats"] = single[:8]
            except Exception as e:
                logger.warning(f"单节深化失败 {sec.get('title')}: {e}")
        return outline

    async def _enrich_single_section(self, sec: dict, facts, figs, methods, llm) -> list:
        from langchain_core.messages import HumanMessage
        import re as _re
        prompt = f"""为论文「{sec.get('title')}」一节生成逐段写作纲要 beats（3-6 条）。
每条：{{"claim": "该段核心观点一句话", "support": "支撑（引用事实编号/图片路径/方法名，逐字来自材料）", "kb_query": "检索关键词2-4个", "words": 该段约字数}}
只输出 JSON 数组（不要代码块）。

节名：{sec.get('title')}（目标 {sec.get('target_words')} 字）
要点：{json.dumps(sec.get('key_points', []), ensure_ascii=False)}
关键结果: {'、'.join(facts[:15])}
可用图片: {'、'.join(figs[:12])}
方法: {'、'.join(methods[:20])}"""
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = _re.search(r"```(?:json)?\s*(.*?)```", content, _re.S)
        if m:
            content = m.group(1)
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("无 JSON 数组")
        beats = json.loads(content[start:end + 1])
        return beats if isinstance(beats, list) else []

    # ---------- 持久化 ----------
    def save_outline(self, project_name: str, outline: dict) -> str:
        pdir = scoping.projects_dir()
        pdir.mkdir(parents=True, exist_ok=True)
        safe = re_safe_name(project_name)
        path = pdir / f"{safe}.outline.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)
        return str(path)

    def load_outline(self, project_name: str) -> Optional[dict]:
        safe = re_safe_name(project_name)
        path = scoping.projects_dir() / f"{safe}.outline.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_word_templates(self) -> dict:
        return {k: {"name": v.name, "total_words": v.total_words, "sections": v.sections}
                for k, v in self.config.outline.templates.items()}

    # ---------- 内部 ----------
    def _load_prompt(self, name: str) -> str:
        path = Path(__file__).parent / "prompts" / name
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _repair_truncated_json(text: str):
        """截断 JSON 修复：从尾部若干完整值边界处截断 → 栈补齐闭合符 → 尝试解析。
        超长大纲输出被 max_tokens 截断时救回前半部分（缺失章节由空壳补齐机制兜底）。"""
        if not text or len(text) < 20:
            return None
        tail = text[-1200:]
        # 候选切点：值边界（引号/闭括号/逗号之后）
        cuts = []
        for i in range(len(tail) - 1, max(len(tail) - 800, 0), -1):
            if tail[i] in '"}],':
                cuts.append(len(text) - len(tail) + i + 1)
            if len(cuts) >= 30:
                break
        for cut in cuts:
            cand = text[:cut].rstrip()
            while cand and cand[-1] not in '"}],0123456789':
                cand = cand[:-1]
            if cand.endswith(','):
                cand = cand[:-1]
            stack, in_str, esc = [], False, False
            for ch in cand:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch in "{[":
                    stack.append("}" if ch == "{" else "]")
                elif ch in "}]":
                    if stack:
                        stack.pop()
            if in_str:
                cand += '"'
            cand += "".join(reversed(stack))
            try:
                data = json.loads(cand)
                if isinstance(data, dict) and data.get("sections"):
                    return data
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _parse_outline_json(content: str) -> dict:
        """从 LLM 输出提取 JSON（容错：剥代码块/前后杂文本；截断时修复补全闭合符）"""
        text = content.strip()
        text = re_strip_codeblock(text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"大纲 JSON 解析失败（尝试截断修复）: {e}")
            repaired = OutlineGenerator._repair_truncated_json(text)
            if repaired is not None:
                logger.info(f"截断修复成功：救回 {len(repaired.get('sections', []))} 章")
                repaired["truncated_repaired"] = True
                return repaired
            logger.error(f"大纲 JSON 截断修复失败; 原文末尾200字: {content[-200:]}")
            return {"one_sentence_argument": "", "sections": []}


def re_safe_name(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]+", "_", name).strip("_")


def re_strip_codeblock(text: str) -> str:
    import re
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text
