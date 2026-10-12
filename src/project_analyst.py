# -*- coding: utf-8 -*-
"""
项目理解 Agent（Project Analyst）
选择工作区后的第一任务：理解项目、理清研究顺序与骨架、标记核心脚本与结果图表路径，
对不清楚处生成弹窗问题让用户抉择，最终产出「研究项目主体」文本。
该文本不向量化，直接作为提示词工程注入大纲/写作/聊天等下游任务。
状态机: idle → analyzing → awaiting_answers → done / failed
"""
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from src import scoping

PROJECTS_DIR = scoping.legacy_projects_dir()  # 兼容旧引用；实际路径走 scoping

# 扫描预算（提示词工程，非向量化——总量必须可控）
MAX_SCRIPT_SAMPLES = 40      # 脚本头部分析数（按重要性排序后截断）
SCRIPT_HEAD_LINES = 45       # 每个脚本采样的头部行数
MAX_README_CHARS = 1200      # 每目录 README 采样
MAX_INVENTORY_CHARS = 46000  # 扫描清单总预算


def _safe(name: str) -> str:
    import re as _re
    return _re.sub(r"[^\w\-]+", "_", name).strip("_") or "project"


class ProjectAnalyst:
    """项目理解 Agent：扫描 → LLM 分析 → 弹窗澄清 → 研究项目主体文本"""

    def __init__(self, llm_manager, config=None):
        self.llm_manager = llm_manager
        self.config = config
        self._states: dict = {}  # project_name → {state, questions, error, ...}
        self._locks: dict = {}
        # 常驻分析线程：所有 LLM 调用固定在同一个事件循环上执行。
        # 临时"一线程一 loop"会在 loop 关闭后污染复用的底层连接池（Event loop is closed）
        self._loop = None
        self._thread = None
        self._ensure_worker_loop()

    def _ensure_worker_loop(self):
        if self._thread is not None and self._thread.is_alive():
            return
        import asyncio
        import threading
        self._loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True, name="project-analyst")
        self._thread.start()
        logger.info("项目理解 Agent 常驻分析线程已启动")

    def submit(self, coro):
        """把协程调度到常驻分析线程的事件循环（线程安全，立即返回 Future）。
        线程不继承 contextvars：包一层恢复发起者的 owner 上下文（资源按用户隔离）"""
        from src.observability import ThreadSnapshot
        snap = ThreadSnapshot.capture()

        async def _with_ctx():
            snap.restore()
            return await coro
        self._ensure_worker_loop()
        import asyncio
        return asyncio.run_coroutine_threadsafe(_with_ctx(), self._loop)

    def _fresh_llm(self):
        """每次调用新建 LLM 客户端：理解 Agent 跑在自建后台线程/事件循环里，
        复用共享实例会把绑定在其它 loop 的 httpx 连接池带进来导致挂死"""
        try:
            from langchain_openai import ChatOpenAI
            cfg = self.config or self.llm_manager.config
            model = (getattr(cfg.models, "analyst_llm", "") or cfg.models.llm)
            from src.observability import attach_llm_observer
            return attach_llm_observer(ChatOpenAI(
                model=model,
                api_key=cfg.zhipu.resolve_api_key(),
                base_url=cfg.zhipu.base_url,
                temperature=cfg.models.temperature,
                max_tokens=cfg.models.max_tokens,
                timeout=cfg.zhipu.timeout,
                streaming=False,
                extra_body={"thinking": {"type": "disabled"}},
            ), )
        except Exception:
            return self.llm_manager.get_main_llm()

    # ---------- 状态管理 ----------
    def state(self, name: str) -> dict:
        from src.scoping import owner_key
        return self._states.setdefault(owner_key(name), {"state": "idle"})

    def _set(self, name: str, **kw):
        st = self.state(name)
        st.update(kw)
        return st

    # ---------- 第一步：确定性扫描（无 LLM，秒级） ----------
    SKIP_DIR_NAMES = {"__pycache__", ".git", ".venv", "venv", "node_modules",
                      ".pytest_cache", ".idea", ".vscode", "legacy", "backup"}

    def scan(self, project_path: str) -> dict:
        """目录扫描 → 结构化清单（脚本/文档/结果/图片/目录骨架）"""
        root = Path(project_path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"项目目录不存在: {root}")
        code, docs, figures, data_files = [], [], [], []
        dir_readmes: dict = {}
        dir_filecount: dict = {}
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            parts = p.parts
            if any(part in self.SKIP_DIR_NAMES for part in parts):
                continue
            d = p.parent.relative_to(root).as_posix() if p.parent != root else "."
            dir_filecount[d] = dir_filecount.get(d, 0) + 1
            ext = p.suffix.lower()
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            if ext in (".py", ".ipynb", ".r", ".rmd"):
                code.append({"path": rel, "lines": self._count_lines(p), "size": size})
            elif ext in (".md", ".txt"):
                docs.append({"path": rel, "size": size})
                if p.name.lower() in ("readme.md", "readme.txt", "说明.md", "redeme.md"):
                    dir_readmes.setdefault(d, []).append(p)
            elif ext in (".png", ".jpg", ".jpeg", ".svg", ".tif", ".tiff"):
                figures.append({"path": rel})
            elif ext in (".csv", ".json", ".xlsx", ".h5", ".pkl", ".parquet", ".rds"):
                data_files.append({"path": rel, "size": size})

        # 目录骨架（含文件计数，体现项目结构）
        dirs = sorted(dir_filecount, key=lambda d: (d.count("/"), d))
        tree_lines = [f"{d}/  ({dir_filecount[d]} 文件)" for d in dirs[:120]]

        inv = {
            "root": str(root),
            "tree": tree_lines,
            "scripts": code,
            "docs": [d for d in docs if d["size"] < 500_000][:80],
            "figures": figures[:150],
            "data_files": [d for d in data_files if d["size"] < 20_000_000][:80],
            "readmes": {},
        }
        # README 采样（目录自述是理解项目的第一手材料）
        for d, ps in list(dir_readmes.items())[:30]:
            text = ""
            for p in ps[:1]:
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")[:MAX_README_CHARS]
                except Exception:
                    pass
            inv["readmes"][d] = text
        # 脚本头部分析（docstring/导入/主流程），按行数降序取前 N（大脚本通常是主流程）
        heads = []
        for c in sorted(code, key=lambda x: -x["lines"])[:MAX_SCRIPT_SAMPLES]:
            head = self._script_head(root / c["path"])
            heads.append({"path": c["path"], "lines": c["lines"], "head": head})
        inv["script_heads"] = heads
        return inv

    @staticmethod
    def _count_lines(p: Path) -> int:
        try:
            with open(p, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    @staticmethod
    def _script_head(p: Path) -> str:
        """脚本头部采样：docstring + import + 主函数签名——足矣判断脚本角色"""
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return ""
        keep, imports, defs = [], [], []
        for ln in lines[:SCRIPT_HEAD_LINES]:
            s = ln.strip()
            if s.startswith(('"""', "'''", "#!")) or (keep and not s):
                keep.append(ln)
                if len(keep) >= 6 and s.endswith(('"""', "'''")):
                    break
                continue
            if s.startswith(("import ", "from ")):
                imports.append(s[:110])
            elif s.startswith(("def ", "class ", "if __name__")):
                defs.append(s[:110])
        out = keep[:8] + imports[:14] + defs[:14]
        return "\n".join(out)[:1500]

    def render_inventory(self, inv: dict) -> str:
        """扫描清单 → LLM 可读文本（预算内）"""
        parts = [f"# 项目根目录\n{inv['root']}", "\n# 目录骨架（目录/ 文件数）"]
        parts += [f"- {t}" for t in inv["tree"]]
        parts.append("\n# README 自述（按目录）")
        for d, text in inv["readmes"].items():
            parts.append(f"## {d}/\n{text}")
        parts.append("\n# 脚本清单（头部采样：docstring/imports/主要函数）")
        for s in inv["script_heads"]:
            parts.append(f"## {s['path']}  ({s['lines']} 行)\n{s['head']}")
        other = [s["path"] for s in inv["scripts"]
                 if s["path"] not in {h["path"] for h in inv["script_heads"]}]
        if other:
            parts.append("（其余脚本，仅列名）: " + "、".join(other[:40]))
        parts.append("\n# 文档清单")
        parts += [f"- {d['path']}" for d in inv["docs"][:50]]
        parts.append("\n# 数据/结果文件")
        parts += [f"- {d['path']} ({d['size'] // 1024}KB)" for d in inv["data_files"][:60]]
        parts.append("\n# 图片文件")
        parts += [f"- {f['path']}" for f in inv["figures"][:80]]
        text = "\n".join(parts)
        return text[:MAX_INVENTORY_CHARS]

    # ---------- 第二步：LLM 分析（产出理解草案 + 澄清问题） ----------
    ANALYZE_PROMPT = """你是资深科研项目管理人。下面是一个研究项目的目录扫描清单与关键数字候选。
请通读并理解这个项目在做什么、按什么顺序执行、哪些是核心、产出了什么。

核心原则：**凡是你不能从材料确凿推断的，必须变成 question 让用户拍板——
不允许把含糊的东西写进理解结果、再让用户事后修改。** 数字归属是重点：
同一个指标（如 C指数、F1、mAP）在不同数据集/模型/阶段常有多个值，材料无法唯一确定论文主口径时，
必须出一个 question 让用户选择，而不是罗列所有值。

只输出 JSON（不要代码块包裹）：
{
  "understood": "100-200字：项目做了什么（数据/对象/方法路线/产出），已从材料中确认的事实",
  "pipeline": [{"stage": "阶段名", "purpose": "该阶段目的", "scripts": ["关键脚本相对路径"], "outputs": ["关键产出相对路径"]}],
  "core_scripts": [{"path": "相对路径", "role": "一句话职责", "importance": "core|support|unused"}],
  "result_map": [{"path": "相对路径", "kind": "表|图|指标|模型", "what": "内容一句话"}],
  "key_numbers": [{"metric": "指标名", "value": "数值", "context": "口径：数据集/队列+模型+阶段（材料可确证时填写）", "source": "来源文件相对路径", "status": "confirmed|uncertain"}],
  "innovations": ["创新点候选（每条带支撑证据路径）"],
  "questions": [{"id": "q1", "title": "模糊处标题", "detail": "为什么需要用户确认", "type": "single|multi|text", "options": ["选项A", "选项B"]}]
}

要求：
- questions 3-7 个，只问真正影响理解的不确定处。三类必问：
  ①数字归属——某指标在候选里出现多个值且材料无法唯一确定论文主口径（type=multi，
  options=候选口径如「C指数0.7293(TCGA组合臂)」「mAP0.682(COCO测试集)」等，让用户勾选哪些是论文主结果）；
  ②版本主次——多个目录/脚本像同一分析的不同版本，哪个是最终版；③创新点主次。
  能从材料确凿推断的不要问。
- key_numbers：把「关键数字候选」中每条归位——材料能确证口径的标 confirmed 并写明
  context（数据集/模型/阶段）与 source；无法确证的标 uncertain（它们将由用户澄清或降级到遗留项）。
  context 必须具体到「哪个数据集/队列+哪个模型+什么阶段」，禁止只写指标名。
- options 给出你从材料中推测的候选（2-4个）；type=text 时不给 options。
- result_map 收录对论文重要的结果文件（指标json/结果表csv/关键图），不要收录中间过程文件。
- core_scripts.importance：core=论文工作流必经；support=辅助/复现；unused=看起来已废弃或测试。

# 项目扫描清单
"""

    async def analyze(self, name: str, project_path: str) -> dict:
        """完整分析（扫描 + LLM）；提交到常驻分析线程"""
        try:
            self._set(name, state="analyzing", started_at=datetime.now().isoformat())
            inv = self.scan(project_path)
            inventory = self.render_inventory(inv)
            numbers_block = self._profile_numbers_block(name)
            if numbers_block:
                inventory += "\n\n" + numbers_block
            from langchain_core.messages import HumanMessage
            llm = self._fresh_llm()
            resp = await llm.ainvoke([HumanMessage(content=self.ANALYZE_PROMPT + inventory)])
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            data = self._parse_json(content)
            if not data.get("pipeline"):
                raise ValueError("分析结果缺少 pipeline 字段")
            questions = data.get("questions") or []
            self._set(name, state="awaiting_answers" if questions else "analyzed",
                      analysis=data, inventory=inventory,
                      project_path=project_path,
                      questions=questions)
            from src.observability import log_span
            log_span("analyst.analyze", kind="node",
                     in_={"name": name, "path": project_path},
                     out={"state": self.state(name).get("state"),
                          "questions": len(questions),
                          "pipeline_stages": len(data.get("pipeline") or []),
                          "core_scripts": len(data.get("core_scripts") or [])})
            return self.state(name)
        except Exception as e:
            logger.error(f"项目理解分析失败: {e}", exc_info=True)
            self._set(name, state="failed", error=str(e)[:400])
            return self.state(name)

    # ---------- 第三步：用户回答后合成「研究项目主体」 ----------
    FINALIZE_PROMPT = """你是资深科研项目管理人。基于项目扫描清单、初步分析与用户澄清回答，
撰写该项目的《研究项目主体》文档。这是一份供论文写作大模型使用的权威上下文——
所有下游任务（大纲/正文/聊天）都将以它为准理解项目。用词必须具体，路径逐字来自材料。

输出 markdown（直接输出，不要代码块包裹），结构如下：

# 研究项目主体：{项目名}

## 一、研究概述
（3-5句：研究问题、对象与数据、方法路线、主要产出）

## 二、研究顺序与骨架
（按执行顺序编号；每阶段一行：**N. 阶段名** —— 目的 ｜ 关键脚本 `路径` ｜ 产出 `路径`）

## 三、核心脚本清单
（表格：| 脚本路径 | 职责 | 重要性 core/support |；support 放最后，unused 不列）

## 四、结果与图表对应清单
（表格：| 路径 | 类型(图/表/指标) | 内容 | 所属阶段 |；只列论文级产出）

## 五、关键结果数字
{数字摘要}

## 六、创新点
（编号列表，每条附支撑证据路径）

## 七、用户确认的决策记录
（逐条记录用户在澄清环节给出的决定）

## 八、遗留不确定项
（用户未能回答/材料无法确认的点，写明影响范围）

第五节硬规则——这是用户最敏感的部分：
- 只列**归属已确认**的数字：材料确证（status=confirmed）或用户在澄清环节勾选确认的。
- 每条格式：**指标 = 值**（口径：数据集/队列+模型+阶段 ← 来源文件）。口径必须具体，
  禁止出现没有口径的孤立数值。
- 归属不清的数字（status=uncertain 且用户未确认）**一律不进第五节**，
  改为在第八节遗留不确定项中列明「XX 指标存在多个候选值，需用户指定论文主口径」。
- 禁止输出「写作时需回查核实」「具体对应关系待确认」之类的注释——含糊的数字不是内容，
  是本该在澄清环节问完的待办。
- 数字总量收敛到论文主结果级（10-20 条），不搬运全部候选。

# 用户澄清回答（权威，优先级高于一切推测）
{answers_text}

# 初步分析草案（可修正其判断，但事实以扫描清单为准）
{analysis_json}

# 项目扫描清单
"""

    async def finalize(self, name: str, answers: list) -> dict:
        st = self.state(name)
        if st.get("state") not in ("awaiting_answers", "analyzed", "done"):
            return {"error": f"当前状态 {st.get('state')} 不可合成（需先完成分析）"}
        self._set(name, state="finalizing")  # 进入合成态（失败会转为 failed）
        try:
            inv_text = st.get("inventory") or ""
            analysis = st.get("analysis") or {}
            # 用户答案 → 文本
            q_by_id = {q.get("id"): q for q in st.get("questions") or []}
            ans_lines = []
            for a in answers or []:
                q = q_by_id.get(a.get("id")) or {}
                val = a.get("text") or "、".join(a.get("choices") or [])
                if val:
                    ans_lines.append(f"- {q.get('title') or a.get('id')}：{val}"
                                     f"（{q.get('detail', '')}）")
            answers_text = "\n".join(ans_lines) or "（用户未提供额外澄清，按草案推断并在遗留项中说明）"
            # 关键数字摘要：分析阶段已归位的 key_numbers（confirmed/uncertain），
            # 而非原始候选——未确认的数字按规则降级到遗留项
            kn = analysis.get("key_numbers") or []
            if kn:
                kn_lines = [
                    f"- {k.get('metric')} = {k.get('value')}｜口径：{k.get('context') or '（未确认）'}"
                    f"｜来源：{k.get('source') or '？'}｜status={k.get('status', 'uncertain')}"
                    for k in kn[:40]]
                num_summary = "\n".join(kn_lines)
            else:
                num_summary = self._profile_numbers(name)
            prompt = (self.FINALIZE_PROMPT
                      .replace("{项目名}", name)
                      .replace("{数字摘要}", num_summary)
                      .replace("{answers_text}", answers_text)
                      .replace("{analysis_json}", json.dumps(analysis, ensure_ascii=False)[:8000])
                      + inv_text)
            from langchain_core.messages import HumanMessage
            llm = self._fresh_llm()
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            body = resp.content if isinstance(resp.content, str) else str(resp.content)
            body = body.strip()
            if len(body) < 300:
                raise ValueError("主体文本过短，疑似生成失败")
            self.save_body(name, body)
            self._set(name, state="done", body=body,
                      finalized_at=datetime.now().isoformat())
            return self.state(name)
        except Exception as e:
            logger.error(f"研究主体合成失败: {e}", exc_info=True)
            self._set(name, state="failed", error=str(e)[:400])
            return self.state(name)

    def _profile_numbers(self, name: str) -> str:
        pf = scoping.projects_dir() / f"{_safe(name)}.profile.json"
        if not pf.exists():
            return "（尚无画像数字）"
        try:
            prof = json.loads(pf.read_text(encoding="utf-8"))
            lines = []
            for r in (prof.get("key_results") or [])[:40]:
                lines.append(f"- {r.get('metric')} = {r.get('value')}")
            return "\n".join(lines) or "（画像无关键数字）"
        except Exception:
            return "（画像读取失败）"

    def _profile_numbers_block(self, name: str) -> str:
        """画像关键数字 → 带来源的分析输入块（供归位与提问）"""
        pf = scoping.projects_dir() / f"{_safe(name)}.profile.json"
        if not pf.exists():
            return ""
        try:
            prof = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            return ""
        lines = ["# 关键数字候选（正则提取，口径归属待确认；括号=来源目录/文件）"]
        for r in (prof.get("key_results") or [])[:60]:
            srcs = r.get("sources") or [{}]
            s0 = srcs[0] if isinstance(srcs[0], dict) else {"file": str(srcs[0]), "dir": ""}
            ctx = (r.get("context") or "").strip()[:70]
            lines.append(f"- {r.get('metric')} = {r.get('value')}"
                         f"（{s0.get('dir', '')}/{s0.get('file', '')}）"
                         + (f"｜原文：{ctx}" if ctx else ""))
        return "\n".join(lines)

    # ---------- 持久化 ----------
    def body_path(self, name: str) -> Path:
        return scoping.projects_dir() / f"{_safe(name)}.project_body.md"

    def save_body(self, name: str, body: str) -> Path:
        scoping.projects_dir().mkdir(parents=True, exist_ok=True)
        p = self.body_path(name)
        p.write_text(body, encoding="utf-8")
        return p

    def load_body(self, name: str) -> Optional[str]:
        p = self.body_path(name)
        if not p.exists():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    # ---------- 下游服务：主体文本 → 提示词 ----------
    def body_prompt(self, name: str, max_chars: int = 12000) -> str:
        """供大纲/写作/聊天注入的研究主体上下文；不存在时返回空串"""
        body = self.load_body(name)
        if not body:
            return ""
        return body[:max_chars]

    # ---------- 工具 ----------
    @staticmethod
    def _parse_json(content: str) -> dict:
        m = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
        if m:
            content = m.group(1)
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return {}
