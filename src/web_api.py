"""
Web API 模块（论文写作智能体）
路由分组：
  - 系统：/api/health /api/info
  - 项目：/api/projects（扫描/注册/画像/文件树/文件读取）
  - 知识库：/api/kb（状态/构建）
  - 大纲：/api/outline（模板/生成/读取/保存/docx导出）
  - 分节：/api/sections（重生成-流式/自查）
  - 会话：/api/chat（流式问答，支持文件注入）
  - 对话管理：/api/conversation/*
  - 文档查重：/api/doccheck（上传→三引擎检测→9B改写→自动复检→下载）
"""
import asyncio
import json
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query, Body, Request, Depends
from fastapi import Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from src.config import get_config_manager
from src.llm_manager import LLMManager
from src.vector_store import VectorStoreManager
from src.project_indexer import ProjectIndexer
from src.outline_generator import OutlineGenerator, compute_targets
from src.section_writer import SectionWriter, count_cn_words
from src.reference_parser import ReferenceParser
from src.skeleton_extractor import SkeletonExtractor
from src.format_profiler import compile_export_style, human_summary
from src.text_enhancer import TextEnhancer, detect_ai_markers
from src.task_runner import FullTextRunner
from src.vision_agent import VisionAgent
from src.rag_anything_engine import RAGAnythingEngine
from src.project_indexer import IMAGE_EXTS
from src.conversation_manager import get_conversation_manager
from src.domain import list_domains, resolve_domain, load_domain_prompt
from src import auth, scoping, workspace_store
from src import observability as obs
from fastapi import Depends

logger = logging.getLogger(__name__)

# ==================== 请求/响应模型 ====================


class RegisterProjectRequest(BaseModel):
    name: str = Field(..., description="项目名称")
    path: str = Field(..., description="项目目录绝对/相对路径")
    domain: Optional[str] = Field(None, description="论文领域（medical/cs/generic），缺省自动检测")


class OutlineGenerateRequest(BaseModel):
    project_name: str
    template: str = Field("chinese_journal", description="字数模板 key")
    total_words: Optional[int] = Field(None, description="总目标字数（不传用模板默认）")
    user_requirements: str = Field("", description="作者额外要求")
    reference_paper: Optional[str] = Field(None, description="参考论文 name（已解析的），借鉴其章节组织")
    domain: Optional[str] = Field(None, description="论文领域（medical/cs/generic），缺省用画像或自动检测")


class OutlineSaveRequest(BaseModel):
    outline: dict = Field(..., description="完整大纲 JSON（可含人工编辑）")


class SectionRegenerateRequest(BaseModel):
    project_name: str
    section_id: str
    instruction: str = Field("", description="本次重生成的额外要求")
    extra_materials: str = Field("", description="注入的参考材料文本")
    subsection_id: Optional[str] = Field(None, description="仅重写该小节（多级小节局部重生成）")


class FullTextStartRequest(BaseModel):
    project_name: str
    force: bool = Field(False, description="True=全部重做；False=跳过已完成节")
    auto_humanize_threshold: int = Field(55, description="体检分超过该值自动去AI味修复")
    only: list = Field(default_factory=list, description="仅处理指定节 id（空=全部）")


class EnhanceRequest(BaseModel):
    project_name: str
    section_id: str
    mode: str = Field("humanize", description="polish=AI润色 | humanize=去AI味")
    strength: str = Field("standard", description="light | standard | deep")
    instruction: str = Field("", description="附加要求")


class RestoreRequest(BaseModel):
    project_name: str
    section_id: str


class SectionUpdateRequest(BaseModel):
    project_name: str
    section_id: str
    content: str = Field(..., description="人工编辑后的 markdown 正文")


class OutlineMetaRequest(BaseModel):
    project_name: str
    paper_title: str = Field(..., min_length=1, max_length=120, description="论文标题")


class SectionRenameRequest(BaseModel):
    project_name: str
    section_id: str
    title: str = Field(..., min_length=1, max_length=80, description="新章节标题")


class RAGIngestRequest(BaseModel):
    force: bool = Field(False, description="强制重新解析入库（忽略 manifest 去重）")
    file: Optional[str] = Field(None, description="单篇文献路径；缺省批量处理 reference_papers/*.pdf")


class RAGQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    mode: Optional[str] = Field(None, description="hybrid|local|global|naive，缺省 hybrid")


class StyleApplyRequest(BaseModel):
    project_name: str = Field(..., description="套入目标项目")
    reference_name: str = Field(..., description="模板论文名（已提取格式档案）")
    keys: Optional[list] = Field(None, description="只套用指定键（缺省套用全部可套项）")


class StyleResetRequest(BaseModel):
    project_name: str = Field(..., description="恢复系统默认排版的项目")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    project_name: Optional[str] = Field(None, description="当前打开的项目（提供后聊天 Agent 可操控该论文）")
    attachments: List[str] = Field(default_factory=list, description="注入会话的文件路径列表")
    selections: List[str] = Field(default_factory=list, description="引用的论文选段（随消息一次性注入）")
    file_drops: List[dict] = Field(default_factory=list, description="资源管理器拖入的文件内容 [{name, content}]")
    folders: List[str] = Field(default_factory=list, description="引用的文件夹路径（注入结构清单）")


class HealthCheck(BaseModel):
    status: str
    timestamp: str
    service: str
    version: str


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)


# ==================== 系统编排器 ====================


class PaperAgentSystem:
    """论文写作智能体编排器"""

    def __init__(self):
        self.config = get_config_manager().get_config()
        self.start_time = datetime.now()
        self.llm_manager = LLMManager(self.config)
        self.vsm = VectorStoreManager(self.config, self.llm_manager.get_embeddings())
        self.indexer = ProjectIndexer(self.config)
        self.reference_parser = ReferenceParser(self.llm_manager, self.config)
        self.skeleton_extractor = SkeletonExtractor(self.llm_manager, self.config)
        self.outline_generator = OutlineGenerator(self.llm_manager, self.vsm, self.config)
        self.section_writer = SectionWriter(
            self.llm_manager, self.vsm, self.config, self.outline_generator)
        self.text_enhancer = TextEnhancer(self.section_writer, self.config)
        self.fulltext = FullTextRunner(self.section_writer, self.text_enhancer)
        self.vision = VisionAgent(self.config)
        # RAG-Anything 多模态文献引擎（懒初始化：依赖未装/未启用不阻塞主服务）
        self.rag_engine = RAGAnythingEngine(self.config)
        self.section_writer.rag_engine = self.rag_engine
        self.conversation_manager = get_conversation_manager(self.config)
        from src.project_analyst import ProjectAnalyst
        self.analyst = ProjectAnalyst(self.llm_manager, self.config)
        logger.info("论文写作智能体初始化完成")

    # ---------- 项目 ----------
    def get_profile(self, project_name: str) -> dict:
        path = scoping.projects_dir() / f"{_safe(project_name)}.profile.json"
        if not path.exists():
            raise FileNotFoundError(f"项目未注册或未构建画像: {project_name}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ---------- 聊天（RAG + 文件注入） ----------
    def _build_chat_context(self, attachments: List[str]) -> str:
        parts = []
        for fp in attachments[:8]:
            try:
                info = self.indexer.read_file_preview(fp, max_chars=8000)
                if info.get("type") == "image":
                    parts.append(f"【图片文件】{info['name']}（路径: {info['path']}，注意：当前为文本通道，图片内容不可见）")
                elif info.get("type") == "binary":
                    parts.append(f"【二进制文件】{info['name']}（无法注入文本内容）")
                else:
                    parts.append(f"【附件文件】{info['name']}\n{info['content']}")
            except Exception as e:
                logger.warning(f"附件读取失败 {fp}: {e}")
        return "\n\n---\n\n".join(parts)

    def _retrieve_kb_for_chat(self, question: str) -> str:
        from src.observability import span as _span
        with _span("chat.kb_context", kind="node", in_={"q": question[:120]}) as _s:
            text = self._retrieve_kb_for_chat_impl(question)
            _s.out = {"blocks": text.count("【")}
            return text

    def _retrieve_kb_for_chat_impl(self, question: str) -> str:
        parts = []
        try:
            cols = self.config.vector_store.collections
            for col_name in (cols.writing_methodology, cols.method_papers):
                if self.vsm.collection_exists(col_name):
                    docs = self.vsm.similarity_search(col_name, question, k=3)
                    for d in docs:
                        tag = d.metadata.get("file_name", col_name)
                        parts.append(f"【知识库: {tag}】{d.page_content[:400]}")
        except Exception as e:
            logger.warning(f"会话检索失败: {e}")
        return "\n\n".join(parts[:6])

    async def _retrieve_reference_rag(self, question: str) -> str:
        """RAG-Anything 参考文献多模态证据（图/表/公式实体），库里有数据才检索"""
        if not self.config.rag_anything.enabled:
            return ""
        try:
            if not self.rag_engine.status().get("documents"):
                return ""  # 冷库跳过：避免每条聊天都触发引擎初始化
            ctx = await asyncio.wait_for(
                self.rag_engine.aquery(question, only_context=True), timeout=60)
            if ctx:
                return f"【参考文献多模态知识库（含图表/公式/表格）】\n{ctx[:1600]}"
        except Exception as e:
            logger.info(f"参考文献 RAG 检索跳过: {e}")
        return ""

    async def chat_stream(self, question: str, session_id: str,
                          attachments: List[str], selections: Optional[List[str]] = None,
                          file_drops: Optional[List[dict]] = None,
                          folders: Optional[List[str]] = None,
                          project_name: Optional[str] = None):
        """流式聊天：宪法 + 附件 + 知识库检索（LLM 调用带瞬时错误重试）"""
        session_id = scoping.owner_key(session_id)  # 会话按用户隔离
        self.conversation_manager.add_message(session_id, "user", question)
        history = self.conversation_manager.get_conversation_context(session_id)
        attachments_text = self._build_chat_context(attachments)
        kb_text = self._retrieve_kb_for_chat(question)
        ref_rag = await self._retrieve_reference_rag(question)
        if ref_rag:
            kb_text = (kb_text + "\n\n" + ref_rag) if kb_text else ref_rag
        # ---- 视觉代理：图片 → 多模态分析 → 回流主会话 ----
        vision_items = []
        _img_exts = {e.lstrip(".") for e in IMAGE_EXTS}
        for fp in attachments or []:
            if Path(fp).suffix.lower().lstrip(".") in _img_exts:
                vision_items.append({"name": Path(fp).name, "image": fp})
        for d in (file_drops or []):
            if str(d.get("content", "")).startswith("data:image"):
                vision_items.append({"name": d.get("name", "unnamed"), "image": d["content"]})
        vision_items = vision_items[: self.config.vision.max_images]
        vision_text = ""
        if vision_items:
            yield {"type": "status",
                   "content": f"🔍 视觉代理正在查看 {len(vision_items)} 张图片…"}
            import asyncio as _aio

            async def _analyze_one(vi):
                try:
                    return vi["name"], await self.vision.analyze(vi["image"], question, vi["name"])
                except Exception as ve:
                    logger.warning(f"视觉分析失败 {vi['name']}: {ve}")
                    return vi["name"], None

            vision_results = await _aio.gather(*(_analyze_one(v) for v in vision_items))
            vision_blocks = []
            for name, analysis in vision_results:
                if analysis:
                    vision_blocks.append(f"【图片视觉分析 · {name}】\n{analysis}")
                else:
                    vision_blocks.append(f"【图片 {name}】视觉代理暂时不可用，未能查看该图片。")
            vision_text = "\n\n".join(vision_blocks)
        sel_blocks = []
        for i, s in enumerate((selections or [])[:6], 1):
            quoted = "\n".join("> " + ln for ln in s[:4000].splitlines())
            sel_blocks.append(f"【选段 {i}】\n{quoted}")
        drops_text = ""
        for i, d in enumerate((file_drops or [])[:6], 1):
            content = (d.get("content") or "")[:200000]
            if content.strip():
                drops_text += f"\n【拖入文件 {i}】{d.get('name', 'unnamed')}\n{content}\n"
        folders_text = ""
        for fi, folder in enumerate((folders or [])[:4], 1):
            fdir = Path(folder)
            if not fdir.is_dir():
                continue
            entries = []
            try:
                for item in sorted(fdir.rglob("*")):
                    rel = item.relative_to(fdir).as_posix()
                    if any(part in ("__pycache__", ".pytest_cache", ".venv", "node_modules") for part in item.parts):
                        continue
                    if item.is_dir():
                        entries.append("[目录] " + rel + "/")
                    else:
                        entries.append(f"[{item.stat().st_size}B] " + rel)
                    if len(entries) >= 150:
                        entries.append("……（条目过多已截断）")
                        break
            except OSError:
                continue
            if entries:
                folders_text += (f"\n【文件夹 {fi}】{fdir.name}/（共 {len(entries)} 项，结构如下）\n"
                                 + "\n".join("  " + e for e in entries) + "\n")

        constitution = _read_prompt("constitution.md")
        # 语言规范随项目领域走（无项目时用通用规范）
        dom = None
        if project_name:
            try:
                dom = resolve_domain(self.get_profile(project_name))
            except Exception:
                dom = None
        if dom is not None:
            style = load_domain_prompt(dom, dom.style_prompt)
        else:
            style = _read_prompt("cn_academic_style.md") or _read_prompt("cn_journal_style.md")

        context_blocks = []
        # 项目研究主体（理解 Agent 产出，权威项目上下文，不向量化直接注入）
        if project_name:
            try:
                body = self.analyst.body_prompt(project_name, max_chars=9000)
            except Exception:
                body = ""
            if body:
                context_blocks.append(
                    "# 研究项目主体（项目理解 Agent 产出，回答本项目问题以此为准；"
                    "其中的脚本/结果路径为权威标注）\n" + body)
        if history:
            context_blocks.append(f"# 对话历史\n{history}")
        if attachments_text:
            context_blocks.append(f"# 注入文件\n{attachments_text}")
        if kb_text:
            context_blocks.append(f"# 知识库参考\n{kb_text}")
        if sel_blocks:
            context_blocks.append("# 引用的论文选段（用户从论文中划选，随本条消息一次性注入）\n\n" + "\n\n".join(sel_blocks))
        if drops_text:
            context_blocks.append("# 拖入的文件（随本条消息一次性注入）" + drops_text)
        if folders_text:
            context_blocks.append("# 引用的文件夹（用户拖入，结构清单如下；可据此讨论文件夹组织，文件内容需用户单独引用）" + folders_text)
        if vision_text:
            context_blocks.append("# 图片视觉分析（视觉代理已查看图片，以下为其报告）" + "\n" + vision_text)
        context = "\n\n".join(context_blocks)

        system_prompt = f"{constitution}\n\n# 语言规范\n{style}"
        # ---- 论文操控：当前项目有大纲时，注入动作协议 + 大纲快照（Agent 循环） ----
        executor = None
        parser = None
        if project_name:
            try:
                _outline = self.outline_generator.load_outline(project_name)
            except Exception:
                _outline = None
            if _outline is not None:
                from src.paper_actions import (ACTIONS_PROMPT, ActionStreamParser,
                                               PaperActionExecutor, build_outline_snapshot)
                executor = PaperActionExecutor(self, project_name)
                parser = ActionStreamParser()
                system_prompt += (ACTIONS_PROMPT + "\n# 当前论文大纲（动作 section 用方括号里的 id 或标题）\n"
                                  + build_outline_snapshot(_outline))
        # 剥离文件令牌标记（文件内容已通过附件通道注入）
        question_llm = re.sub(r"\[\[(?:文件|文件夹):[^\]]*\]\]", "", question).strip() or question
        user_prompt = (f"{context}\n\n# 当前问题\n{question_llm}" if context else question_llm)

        collected = []  # 各轮可见文本
        try:
            followup_prompt = user_prompt
            for round_no in range(3):
                round_actions = []
                sink: list = []
                async for piece in self.section_writer._stream_llm(system_prompt, followup_prompt, sink):
                    if parser is None:
                        collected.append(piece)
                        yield {"type": "chunk", "content": piece}
                        continue
                    vis, acts = parser.feed(piece)
                    if vis:
                        collected.append(vis)
                        yield {"type": "chunk", "content": vis}
                    for raw in acts:
                        from src.paper_actions import ACTION_LABELS, parse_action
                        action = parse_action(raw)
                        label = (ACTION_LABELS.get((action or {}).get("action"))
                                 or "解析动作")
                        yield {"type": "action_start", "label": label,
                               "action": action if action is not None else None}
                        if action is None:
                            result = {"ok": False, "summary": "动作 JSON 解析失败，已忽略"}
                        else:
                            from src.observability import span as _span2
                            with _span2("tool.paper_action", kind="tool",
                                        in_={k: v for k, v in action.items()
                                             if k != "action"} | {"action": action.get("action")}) as _sa:
                                result = executor.execute(action)
                                _sa.out = {"ok": bool(result.get("ok")),
                                           "summary": (result.get("summary") or "")[:120]}
                        yield {"type": "action_result",
                               "ok": bool(result.get("ok")),
                               "summary": result.get("summary", ""),
                               "affects": result.get("affects") or {},
                               "result": str(result.get("result") or "")[:400],
                               "label": label}
                        round_actions.append((action, result))
                tail = parser.flush() if parser else ""
                if tail:
                    collected.append(tail)
                    yield {"type": "chunk", "content": tail}
                if not round_actions or round_no == 2:
                    break
                # 回灌执行结果，让模型继续动作或总结
                lines = []
                for a, r in round_actions:
                    nm = (a or {}).get("action", "?")
                    row = f"- {nm}: {'✅' if r.get('ok') else '❌'} {r.get('summary', '')}"
                    if r.get("result"):
                        row += f"\n  返回内容: {str(r['result'])[:600]}"
                    lines.append(row)
                followup_prompt = ("以下是你在上一轮输出的论文动作的执行结果：\n"
                                   + "\n".join(lines)
                                   + "\n\n请继续：若还需修改可再输出动作块；若已完成，"
                                     "用简短中文向用户总结刚才做了什么（不要复述正文全文）。")
        except Exception as e:
            logger.error(f"聊天调用失败: {e}")
            # 优雅收尾：SSE 正常完成，前端显示真实错误而非 network error
            partial = "".join(collected)
            hint = "（已保留部分回答）" if partial else ""
            from src import observability as _obs2
            _obs2.log_span("chat.error", kind="error", error=repr(e)[:300],
                           out={"partial_chars": len("".join(collected))})
            yield {"type": "error",
                   "content": f"模型调用失败：{e}{hint}。瞬时故障将自动重试；若持续出现请检查网络或 API 额度。"}
            return
        full = "".join(collected)
        if full:
            self.conversation_manager.add_message(session_id, "assistant", full)
        from src import observability as _obs
        _obs.log_span("chat.done", kind="node", out={"chars": len(full)},
                      extra={"project": project_name or None})
        yield {"type": "done", "content": full,
               "session_id": session_id,
               "conversation_stats": self.conversation_manager.get_stats(session_id)}


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]+", "_", name).strip("_")


def _restore_conversations(system: PaperAgentSystem, sessions: Optional[List[dict]] = None):
    """把持久化的会话消息还原进后端对话管理器（LLM 上下文跨重启保持）。
    会话键统一加 owner 前缀，跨用户不可见"""
    if sessions is None:
        state = workspace_store.load_state()
        sessions = (state or {}).get("sessions", [])
    try:
        for s in sessions:
            sid = s.get("id")
            if not sid:
                continue
            sid = scoping.owner_key(sid)
            system.conversation_manager.clear_conversation(sid)
            for m in s.get("messages", [])[-40:]:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    system.conversation_manager.add_message(sid, role, content)
    except Exception as e:
        logger.warning(f"恢复会话历史失败（忽略）: {e}")


def _read_prompt(name: str) -> str:
    path = Path(__file__).parent / "prompts" / name
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


# ==================== 应用创建 ====================


def create_app() -> FastAPI:
    app = FastAPI(
        title="论文写作智能体 API",
        description="基于双知识库 RAG 的中文论文大纲生成与分节写作系统",
        version="3.1.0",
        dependencies=[Depends(auth.auth_dependency)],  # 全局 RBAC 鉴权：解析用户并设置 owner 上下文
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(obs.TraceAccessMiddleware)  # 全链路 trace + 访问日志
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # 静态资源禁用启发式缓存：HTML 引用的版本号已按 mtime 动态生成，内容可变时必换 URL；
    # 同 URL 也直接拉新，避免旧标签页刷新后仍命中浏览器缓存的旧样式表
    @app.middleware("http")
    async def _static_no_cache(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    system = PaperAgentSystem()
    manager = ConnectionManager()

    # 启动时恢复各用户的会话（对话上下文跨重启；键带 owner 前缀天然隔离）
    _legacy_state = scoping.BASE_DIR / "workspace" / "state.json"
    if not list((scoping.BASE_DIR / "workspace").glob("*.state.json")) and _legacy_state.exists():
        try:
            scoping.set_request_owner("")
            _restore_conversations(system, json.loads(
                _legacy_state.read_text(encoding="utf-8")).get("sessions", []))
        except Exception as _e:
            logger.warning(f"恢复 legacy 会话失败: {_e}")
    for _sf in sorted((scoping.BASE_DIR / "workspace").glob("*.state.json")):
        try:
            _owner = "" if _sf.name == "state.json" else _sf.name[:-len(".state.json")]
            _st = json.loads(_sf.read_text(encoding="utf-8"))
            scoping.set_request_owner(_owner)
            _restore_conversations(system, _st.get("sessions", []))
        except Exception as _e:
            logger.warning(f"恢复用户会话失败 {_sf.name}: {_e}")
    scoping.set_request_owner("")

    class LoginRequest(BaseModel):
        username: str
        password: str

    async def _admin_dep(request: Request) -> dict:
        """管理员路由依赖：鉴权 + RBAC 角色校验"""
        user = auth.authenticate(request)
        scoping.set_request_owner(user.get("username", ""))
        if not auth.role_has(user.get("role", ""), "user:manage"):
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user

    # ---------- 工作区 ----------
    @app.get("/api/workspace")
    async def workspace_get():
        state = workspace_store.load_state() or {}
        # 工作区根目录随用户走（存于该用户 state；多用户互不可见）
        root = state.get("workspace_root") or ""
        return {"workspace_root": root, "state": state}

    @app.put("/api/workspace")
    async def workspace_put(state: dict = Body(...)):
        """保存工作区状态；并把会话历史同步进后端对话管理器（保证 LLM 上下文跨重启）"""
        sessions = state.get("sessions", [])
        path = workspace_store.save_state(state)  # 落盘到当前用户的 state 文件
        _restore_conversations(system, sessions)
        return {"ok": True, "saved_to": path}

    @app.get("/api/fs/list")
    async def fs_list(path: str = Query("", description="目录路径；空=盘符列表/用户主目录")):
        """列出目录下的子目录（工作区文件夹选择器用）。
        Windows 下 path 为空时返回全部存在的盘符；仅返回可见目录，无权限的目录静默跳过"""
        import string as _string
        target = (path or "").strip()
        if not target:
            drives = [f"{d}:\\" for d in _string.ascii_uppercase
                      if Path(f"{d}:\\").exists()]
            if drives:
                return {"path": "", "parent": "", "is_root": True,
                        "dirs": [{"name": f"磁盘 {d}", "path": d} for d in drives]}
            target = str(Path.home())
        p = Path(target)
        if not p.exists() or not p.is_dir():
            raise HTTPException(status_code=404, detail=f"目录不存在: {target}")
        dirs = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                if child.is_dir() and not child.name.startswith((".", "$")):
                    dirs.append({"name": child.name, "path": str(child)})
        except PermissionError:
            dirs = []  # 无权限目录：返回空清单而非报错
        except OSError as e:
            raise HTTPException(status_code=400, detail=str(e))
        parent = str(p.parent) if p.parent != p else ""
        return {"path": str(p), "parent": parent, "is_root": False, "dirs": dirs}

    @app.post("/api/workspace/root")
    async def workspace_set_root(data: dict = Body(...)):
        """设置工作区根目录（项目扫描位置），立即生效并持久化"""
        root = (data.get("root") or "").strip()
        if not root:
            raise HTTPException(status_code=400, detail="root 不能为空")
        from pathlib import Path as _P
        if not _P(root).exists():
            raise HTTPException(status_code=404, detail=f"目录不存在: {root}")
        prev = workspace_store.load_state() or {}  # 当前用户的 state
        prev["workspace_root"] = root
        workspace_store.save_state(prev)
        return {"ok": True, "workspace_root": root,
                "candidates": system.indexer.list_projects(root)}

    # ---------- 系统 ----------
    @app.get("/", response_class=HTMLResponse)
    async def root():
        html = Path("templates/index.html").read_text(encoding="utf-8")
        # 静态资源缓存击穿：版本号按文件 mtime 动态生成（改版即换 URL，浏览器永不残留旧缓存）。
        # 覆盖全部本地 css/js——此前只换 app.js/styles.css，layout-v18.css 等写死 ?v=NN 的
        # 文件在个别浏览器缓存策略下会滞留旧样式
        def _bump(match: re.Match) -> str:
            path = match.group(2)
            try:
                v = int(Path(path.lstrip("/")).stat().st_mtime)
            except OSError:
                v = 0
            return f'{match.group(1)}{path}?v={v}"'
        html = re.sub(r'((?:src|href)=")(/static/(?:css|js)/[^"?]+)\?v=\d+"', _bump, html)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/api/health", response_model=HealthCheck)
    async def health():
        return HealthCheck(status="healthy", timestamp=datetime.now().isoformat(),
                           service=system.config.system.name,
                           version=system.config.system.version)

    # ---------- 设置：模型等运行配置（读 + 热更新，写入 config.yaml） ----------
    _MODEL_WHITELIST = ["glm-5.3", "glm-5.3-flash"]  # 全站模型白名单（Coding Plan 端点）

    def _zhuque_key_state() -> dict:
        from src.zhuque_client import _load_key
        k = _load_key()
        return {"configured": bool(k),
                "masked": (k[:3] + "…" + k[-4:]) if k and len(k) > 8 else "",
                "note": "腾讯朱雀 AI 检测（EdgeOne 网关），月免费 50 万 token"}

    def _write_env_key(name: str, value: str) -> None:
        """写入/更新 .env 中的 KEY=value 行（保留其他行）"""
        from pathlib import Path as _P
        env = _P("src").resolve().parents[0] / ".env"
        lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
        hit = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith(name + "="):
                lines[i] = f"{name}={value}"
                hit = True
                break
        if not hit:
            lines.append(f"{name}={value}")
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @app.get("/api/settings")
    async def get_settings():
        m, v = system.config.models, system.config.vision
        return {
            "models": {
                "llm": m.llm,                    # 主写作（大纲/正文/自查）
                "analyst_llm": m.analyst_llm,     # 项目理解 Agent
                "small_llm": m.small_llm,         # 本地小模型（意图路由/改写）
                "embedding": m.embedding,         # 只读：切换嵌入需重建知识库
                "temperature": m.temperature,
                "max_tokens": m.max_tokens,
            },
            "vision": {"model": v.model, "enabled": v.enabled},
            "options": {"llm": _MODEL_WHITELIST, "analyst_llm": _MODEL_WHITELIST,
                        "vision": _MODEL_WHITELIST},
            "zhuque": _zhuque_key_state(),
        }

    @app.post("/api/settings")
    async def update_settings(payload: Dict[str, Any] = Body(...),
                              user: dict = Depends(_admin_dep)):
        """系统级设置（模型/密钥）影响全体用户：仅管理员可改"""
        updates: Dict[str, Any] = {}
        m = payload.get("models") or {}
        md = {k: m[k] for k in ("llm", "analyst_llm", "small_llm", "temperature", "max_tokens")
              if k in m}
        for key in ("llm", "analyst_llm"):
            if md.get(key) and md[key] not in _MODEL_WHITELIST:
                raise HTTPException(400, f"{key} 仅支持：{'、'.join(_MODEL_WHITELIST)}")
        if md:
            updates["models"] = md
        v = payload.get("vision") or {}
        vd = {k: v[k] for k in ("model", "enabled") if k in v}
        if vd.get("model") and vd["model"] not in _MODEL_WHITELIST:
            raise HTTPException(400, f"视觉模型仅支持：{'、'.join(_MODEL_WHITELIST)}")
        if vd:
            updates["vision"] = vd
        zk = (payload.get("zhuque") or {}).get("api_key") or payload.get("zhuque_key")
        if zk is not None and str(zk).strip():
            _write_env_key("ZHUQUE_API_KEY", str(zk).strip())
        if not updates:
            if zk is not None and str(zk).strip():
                return {"ok": True, "applied": {"zhuque_key": "updated"},
                        "zhuque": _zhuque_key_state()}
            raise HTTPException(400, "没有可更新的字段")
        mgr = get_config_manager()
        mgr.update_config(updates)
        # 同步全系统配置引用并热重载：analyst/vision/rag 每次调用时读取 config（换引用即生效），
        # 主/小模型客户端在初始化时固化（需重初始化）
        system.config = mgr.config
        system.llm_manager.config = mgr.config
        system.llm_manager.reinit_main_llm()
        try:
            system.vision.config = mgr.config
            if getattr(system, "rag_engine", None) is not None:
                system.rag_engine.config = mgr.config
        except Exception:
            pass
        return {"ok": True, "applied": updates}

    # ---------- 认证与用户管理（RBAC） ----------
    @app.get("/api/auth/status")
    async def auth_status():
        """是否已启用多用户权限（无账号=单用户 legacy 模式）"""
        return {"enabled": auth.user_count() > 0, "users": auth.user_count()}

    @app.post("/api/auth/register")
    async def auth_register(req: LoginRequest):
        """注册账号；首个账号自动成为 admin 并接管 legacy 单用户数据"""
        try:
            user = auth.create_user(req.username, req.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        tok = auth.login(req.username, req.password)
        return {"ok": True, "token": tok["token"], "user": user,
                "migrated_legacy": user.get("role") == "admin"}

    @app.post("/api/auth/login")
    async def auth_login(req: LoginRequest):
        try:
            return {"ok": True, **auth.login(req.username, req.password)}
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))

    @app.post("/api/auth/logout")
    async def auth_logout():
        return {"ok": True}  # 无状态令牌：客户端丢弃即登出

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        user = auth.authenticate(request)
        scoping.set_request_owner(user.get("username", ""))
        return {"user": auth.public_view(user) if not user.get("legacy")
                else {"username": "", "role": "admin", "legacy": True}}

    @app.get("/api/admin/users")
    async def admin_users(user: dict = Depends(_admin_dep)):
        from src.auth import _load_users
        return {"users": [auth.public_view(u) for u in _load_users()]}

    @app.post("/api/admin/users")
    async def admin_create_user(req: LoginRequest, role: str = Query("user"),
                                user: dict = Depends(_admin_dep)):
        try:
            return {"ok": True, "user": auth.create_user(req.username, req.password, role=role)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/api/admin/users/{username}")
    async def admin_update_user(username: str, changes: dict = Body(...),
                                user: dict = Depends(_admin_dep)):
        try:
            return {"ok": True, "user": auth.update_user(username, **{
                k: v for k, v in changes.items() if k in ("role", "disabled", "password")})}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/admin/users/{username}")
    async def admin_delete_user(username: str, user: dict = Depends(_admin_dep)):
        try:
            auth.delete_user(username)
            return {"ok": True}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/info")
    async def info():
        return {
            "system": system.config.system.model_dump(),
            "models": system.llm_manager.get_model_info(),
            "kb": system.vsm.get_all_info(),
            "uptime": str(datetime.now() - system.start_time),
        }

    # ---------- 项目 ----------
    @app.get("/api/domains")
    async def domains_list():
        """论文领域包清单（前端下拉：自动检测 + 各领域）"""
        return {"domains": list_domains()}

    @app.get("/api/projects")
    async def list_projects(root: Optional[str] = Query(None, description="工作区根目录，不传用当前用户设置")):
        """扫描工作区，返回候选项目 + 已注册项目（有画像的）。
        已注册列表只扫 projects/{当前用户}/，多用户互不可见"""
        if root is None:
            root = (workspace_store.load_state() or {}).get("workspace_root") or ""
        candidates = system.indexer.list_projects(root)
        registered = []
        projects_dir = scoping.projects_dir()
        if projects_dir.exists():
            for p in sorted(projects_dir.glob("*.profile.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    registered.append({
                        "name": data.get("project_name"),
                        "path": data.get("project_path"),
                        "profile_file": str(p),
                        "methods_count": len(data.get("methods", [])),
                        "stages_count": len(data.get("stages", [])),
                        "has_outline": (projects_dir / p.name.replace(".profile.json", ".outline.json")).exists(),
                    })
                except Exception:
                    continue
        return {"candidates": candidates, "registered": registered}

    @app.post("/api/projects/register")
    async def register_project(req: RegisterProjectRequest):
        """注册项目并构建画像；domain 显式指定时写入画像（覆盖自动检测）"""
        try:
            profile = system.indexer.build_profile(req.path, req.name, domain=req.domain)
            obs.log_span("project.register", kind="node",
                         in_={"name": req.name, "path": req.path, "domain": req.domain},
                         out={"detected_domain": profile.get("domain"),
                              "methods": len(profile.get("methods", [])),
                              "figures": len(profile.get("figures", [])),
                              "key_results": len(profile.get("key_results", []))})
            return {"ok": True, "profile": profile}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"项目注册失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/projects/{project_name}/profile")
    async def get_profile(project_name: str):
        try:
            return system.get_profile(project_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ---------- 画像深化（视觉接入）：后台线程逐图分析（状态按用户隔离） ----------
    _enrich_states: dict = {}  # owner -> {"state":..., "done":..., ...}

    def _enrich_state_of(owner: str) -> dict:
        return _enrich_states.setdefault(owner, {"state": "none", "done": 0, "total": 0,
                                                 "error": "", "project": ""})

    @app.post("/api/profile/enrich")
    async def profile_enrich(project_name: str = Query(...), limit: int = Query(0)):
        """用视觉模型逐图分析项目图片，把真实内容描述写入画像 figures[].desc"""
        _enrich_state = _enrich_state_of(scoping.current_owner())
        if _enrich_state["state"] == "running":
            raise HTTPException(status_code=409, detail="画像深化正在进行中")
        try:
            profile = system.get_profile(project_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        figures = profile.get("figures") or []
        cap = limit or getattr(system.config.vision, "max_profile_images", 24)
        # 优先位图（png/jpg）且按文件主干去重——tiff 重复件与矢量源不占深化名额
        ranked, seen_stem = [], set()
        for f in figures:
            if f.get("desc"):
                continue
            stem = Path(f.get("path", "")).stem.lower()
            is_raster = f.get("path", "").lower().endswith((".png", ".jpg", ".jpeg"))
            if is_raster and stem not in seen_stem:
                seen_stem.add(stem)
                ranked.append(f)
        for f in figures:
            if not f.get("desc") and f not in ranked:
                ranked.append(f)
        targets = ranked[:cap]
        if not targets:
            return {"ok": True, "state": "done", "done": 0, "total": 0,
                    "message": "全部图片已有描述，无需深化"}

        _enrich_state.update(state="running", done=0, total=len(targets), error="",
                             project=project_name)

        def _worker(_snap=None):
            from src.observability import ThreadSnapshot
            (_snap or ThreadSnapshot.capture()).restore()  # 线程不继承 contextvars：恢复 owner+trace
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            question = ("这是研究项目生成的一张图（可能为统计图/曲线图/柱状图/热图/散点图/箱线图/"
                        "饼图/模型结构图/流程图等）。请客观描述：1)图类型；2)坐标轴、分组、图例或"
                        "各组件的连接关系；3)图上可见的关键数字（样本量、指标值、P值、百分比、"
                        "参数等，逐个列出）；4)一句话主要结论。共80~150字，直接给描述，"
                        "不要任何前缀或寒暄；看不清的数字写'图中未标注'，禁止编造。")
            for fig in targets:
                try:
                    desc = loop.run_until_complete(
                        system.vision.analyze(fig["path"], question, fig.get("name", "")))
                    desc = (desc or "").strip()[:400]
                    if desc:
                        fig["desc"] = desc
                        _enrich_state["done"] += 1
                except Exception as e:
                    logger.warning(f"图片分析失败 {fig.get('name')}: {e}")
            loop.close()
            try:
                # 分析结果合并回画像文件（保留其他字段）
                pf = scoping.projects_dir() / f"{_safe(project_name)}.profile.json"
                if pf.exists():
                    merged = json.loads(pf.read_text(encoding="utf-8"))
                    desc_map = {f["path"]: f.get("desc") for f in figures if f.get("desc")}
                    for f in merged.get("figures", []):
                        if f.get("path") in desc_map:
                            f["desc"] = desc_map[f["path"]]
                    merged["vision_enriched_at"] = datetime.now().isoformat(timespec="seconds")
                    pf.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            except Exception as e:
                logger.error(f"画像回写失败: {e}")
            _enrich_state["state"] = "done"

        import threading
        from src.observability import ThreadSnapshot
        threading.Thread(target=_worker, args=(ThreadSnapshot.capture(),),
                         daemon=True).start()
        return {"ok": True, "state": "running", "total": len(targets)}

    @app.get("/api/profile/enrich/status")
    async def profile_enrich_status():
        return dict(_enrich_state_of(scoping.current_owner()))

    # ---------- 项目理解 Agent：扫描→LLM分析→弹窗澄清→研究项目主体 ----------
    class UnderstandAnswersRequest(BaseModel):
        project_name: str
        answers: List[dict] = []

    class BodyEditRequest(BaseModel):
        project_name: str
        body: str

    @app.post("/api/project/understand/start")
    async def understand_start(req: RegisterProjectRequest):
        """启动项目理解：确定性扫描 + LLM 分析（后台线程），完成后返回待澄清问题"""
        st = system.analyst.state(req.name)
        if st.get("state") in ("analyzing", "finalizing"):
            raise HTTPException(status_code=409, detail="项目理解正在进行中")

        obs.set_thread_id(f"understand:{req.name}")
        system.analyst.submit(system.analyst.analyze(req.name, req.path))
        return {"ok": True, "state": "analyzing"}

    @app.get("/api/project/understand/status")
    async def understand_status(project_name: str = Query(...)):
        st = dict(system.analyst.state(project_name))
        # 轻量化：不回传大字段
        st.pop("inventory", None)
        st.pop("analysis", None)
        st.pop("body", None)
        return st

    @app.post("/api/project/understand/answers")
    async def understand_answers(req: UnderstandAnswersRequest):
        """提交弹窗澄清回答 → 合成《研究项目主体》（后台线程）"""
        st = system.analyst.state(req.project_name)
        if st.get("state") not in ("awaiting_answers", "analyzed", "done"):
            raise HTTPException(status_code=409,
                                detail=f"当前状态 {st.get('state')} 不能提交回答（先完成分析）")

        system.analyst.submit(
            system.analyst.finalize(req.project_name, req.answers))
        return {"ok": True, "state": "finalizing"}

    @app.get("/api/project/body")
    async def project_body_get(project_name: str = Query(...)):
        body = system.analyst.load_body(project_name)
        st = dict(system.analyst.state(project_name))
        return {"exists": body is not None, "body": body,
                "state": st.get("state"), "updated_at": st.get("finalized_at")}

    @app.put("/api/project/body")
    async def project_body_put(req: BodyEditRequest):
        p = system.analyst.save_body(req.project_name, req.body)
        system.analyst._set(req.project_name, state="done",
                            body=req.body,
                            finalized_at=datetime.now().isoformat(timespec="seconds"))
        return {"ok": True, "saved_to": str(p)}

    @app.get("/api/projects/{project_name}/tree")
    async def get_tree(project_name: str, max_depth: int = 4):
        try:
            profile = system.get_profile(project_name)
            return system.indexer.file_tree(profile["project_path"], max_depth=max_depth)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    def _assert_file_allowed(path: str) -> None:
        """文件访问边界：路径必须位于当前用户的已注册项目/参考论文/上传目录内，
        或共享只读的知识库；其余一律 403（跨用户项目与文档不可互访）"""
        owner = scoping.current_owner()
        target = Path(path).resolve()
        if not owner:  # 单用户 legacy 模式：沿用原行为
            return
        allowed = [scoping.BASE_DIR / "knowledge_base"]
        roots = scoping.user_root_dirs(owner)
        allowed += [roots["projects"], roots["reference"], roots["uploads"]]
        try:
            for pf in roots["projects"].glob("*.profile.json"):
                pp = json.loads(pf.read_text(encoding="utf-8")).get("project_path")
                if pp:
                    allowed.append(Path(pp).resolve())
        except Exception:
            pass
        for r in allowed:
            if str(target).startswith(str(r)):
                return
        raise HTTPException(status_code=403, detail="无权访问该路径（不在你的项目与文档范围内）")

    @app.get("/api/file")
    async def read_file(path: str = Query(...), max_chars: int = 20000):
        _assert_file_allowed(path)
        try:
            return system.indexer.read_file_preview(path, max_chars=max_chars)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/file/raw")
    async def read_file_raw(path: str = Query(...)):
        """原始文件服务（图片预览/下载）。允许：本人已注册项目目录 + 本人 uploads"""
        _assert_file_allowed(path)
        target = Path(path).resolve()
        uploads_root = scoping.uploads_dir().resolve()
        allowed = str(target).startswith(str(uploads_root))
        if not allowed:
            try:
                profile = system.get_profile(_current_project_hint(path))
                root = Path(profile["project_path"]).resolve()
                allowed = str(target).startswith(str(root))
            except FileNotFoundError:
                allowed = False
        if not allowed:
            raise HTTPException(status_code=403, detail="路径不在允许的目录内")
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {target}")
        return FileResponse(str(target), filename=target.name)

    def _current_project_hint(path: str) -> str:
        """根据文件路径反查所属项目（任一注册项目的目录前缀匹配）"""
        target = str(Path(path).resolve()).lower()
        projects_dir = scoping.projects_dir()
        if projects_dir.exists():
            for p in sorted(projects_dir.glob("*.profile.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if target.startswith(str(Path(data["project_path"]).resolve()).lower()):
                        return data.get("project_name")
                except Exception:
                    continue
        raise FileNotFoundError("文件不在任何已注册项目目录内")

    # ---------- 知识库 ----------
    @app.get("/api/kb/status")
    async def kb_status():
        return system.vsm.get_all_info()

    @app.post("/api/kb/build/{which}")
    async def kb_build(which: str):
        """后台构建知识库：methods | writing | all"""
        def _run():
            if which in ("methods", "all"):
                from ingestion.build_methods_kb import build as build_methods
                build_methods(rebuild=True)
            if which in ("writing", "all"):
                import ingestion.build_writing_kb as wb
                wb.collect_skills()
                wb.collect_web()
                wb.collect_bilibili()
                wb.ingest_collection(rebuild=True)

        try:
            await asyncio.to_thread(_run)
            return {"ok": True, "kb": system.vsm.get_all_info()}
        except Exception as e:
            logger.error(f"知识库构建失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ---------- RAG-Anything 多模态文献（图/表/公式） ----------
    @app.get("/api/rag/status")
    async def rag_status():
        """引擎状态：依赖就绪性、已入库文献清单、后台任务进度"""
        return system.rag_engine.status()

    @app.post("/api/rag/ingest")
    async def rag_ingest(req: RAGIngestRequest):
        """参考文献入库：指定 file=单篇同步入库；缺省=批量后台任务（进度看 /api/rag/status）"""
        engine = system.rag_engine
        if req.file:
            try:
                result = await engine.ingest_paper(req.file, force=req.force)
                return {"ok": True, **result}
            except FileNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception as e:
                logger.error(f"RAG 文献入库失败: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        started = engine.start_background_ingest(force=req.force)
        return {"ok": started, "job": dict(engine.job),
                "detail": "" if started else "已有批量任务在运行，请看 /api/rag/status"}

    @app.post("/api/rag/query")
    async def rag_query(req: RAGQueryRequest):
        """对参考文献多模态知识图谱问答（含图表/公式/表格事实）"""
        engine = system.rag_engine
        if not await engine.ingest_ready():
            raise HTTPException(
                status_code=503,
                detail="RAG-Anything 引擎不可用: " +
                       str(engine.diagnostics().get("import_error") or
                           engine.diagnostics().get("init_error") or "未初始化"))
        try:
            answer = await engine.aquery(req.question, mode=req.mode)
            return {"question": req.question, "answer": answer}
        except Exception as e:
            logger.error(f"RAG 查询失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ---------- 大纲 ----------
    @app.get("/api/outline/templates")
    async def outline_templates():
        return system.outline_generator.list_word_templates()

    @app.post("/api/outline/estimate")
    async def outline_estimate(template: str = "chinese_journal", total_words: Optional[int] = None):
        """纯字数预估（不调 LLM）"""
        templates = system.config.outline.templates
        if template not in templates:
            raise HTTPException(status_code=400, detail=f"未知模板: {template}")
        targets = compute_targets(templates[template], total_words)
        return {"template": template, "total": sum(targets.values()), "targets": targets}

    @app.post("/api/outline/generate")
    async def outline_generate(req: OutlineGenerateRequest):
        import time as _time
        _outline_t0 = _time.perf_counter()
        obs.set_thread_id(f"outline:{req.project_name}")
        if not system.llm_manager.main_llm_available():
            raise HTTPException(status_code=400,
                                detail="智谱主模型未配置：请先在 .env 设置 ZHIPU_API_KEY 并重启服务")
        try:
            profile = system.get_profile(req.project_name)
            reference_structure = None
            reference_skeleton = None
            if req.reference_paper:
                reference_structure = system.reference_parser.get_structure(req.reference_paper)
                if reference_structure is None:
                    raise HTTPException(status_code=404,
                                        detail=f"参考论文不存在或未解析: {req.reference_paper}")
                reference_skeleton = system.reference_parser.get_skeleton(req.reference_paper)
            outline = await system.outline_generator.generate_outline(
                profile, req.template, req.total_words, req.user_requirements,
                reference_structure=reference_structure,
                project_body=system.analyst.body_prompt(req.project_name),
                domain=req.domain,
                reference_skeleton=reference_skeleton,
                reference_paper_name=req.reference_paper)
            path = system.outline_generator.save_outline(req.project_name, outline)
            obs.log_span("outline.generate", kind="node",
                         in_={"project": req.project_name, "template": req.template,
                              "total_words": req.total_words},
                         out={"domain": outline.get("domain"),
                              "sections": len(outline.get("sections", [])),
                              "words": outline.get("total_target_words")},
                         duration_ms=round((_time.perf_counter() - _outline_t0) * 1000))
            return {"ok": True, "outline": outline, "saved_to": path}
        except HTTPException:
            raise
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"大纲生成失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ---------- 参考论文 ----------
    @app.get("/api/reference/list")
    async def reference_list():
        return {"papers": system.reference_parser.list_papers()}

    @app.post("/api/reference/upload")
    async def reference_upload(file: UploadFile = File(...)):
        """上传他人论文（pdf/docx/txt），自动解析章节结构（走主模型，约10-30秒）"""
        if not system.llm_manager.main_llm_available():
            raise HTTPException(status_code=400,
                                detail="智谱主模型未配置：解析论文需要主模型，请先设置 ZHIPU_API_KEY")
        allowed = {".pdf", ".docx", ".txt"}
        ext = Path(file.filename or "").suffix.lower()
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"仅支持 {', '.join(allowed)}，收到: {ext or '无扩展名'}")
        try:
            data = await file.read()
            if len(data) > 50 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="文件超过 50MB")
            structure = await system.reference_parser.parse_upload(data, file.filename)
            # 通道B 写作骨架：上传即后台提取（1-2 分钟），状态看 /api/reference/{name}/skeleton
            async def _bg_skeleton():
                try:
                    await system.skeleton_extractor.run_for(structure.get("name"),
                                                            system.reference_parser)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"写作骨架后台提取失败: {e}")
            asyncio.get_running_loop().create_task(_bg_skeleton())
            # RAG-Anything 自动入库：PDF 的图/表/公式进多模态知识图谱（后台执行不阻塞响应）
            ra_cfg = system.config.rag_anything
            if (ext == ".pdf" and ra_cfg.enabled and ra_cfg.auto_ingest_on_upload
                    and structure.get("file")):
                async def _bg_ingest():
                    try:
                        await system.rag_engine.ingest_paper(structure["file"])
                        logger.info(f"参考论文已入多模态知识库: {structure['file']}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"参考论文 RAG 入库失败（不影响结构解析）: {e}")
                asyncio.get_running_loop().create_task(_bg_ingest())
            return {"ok": True, "structure": structure,
                    "summary": {"name": structure.get("name"),
                                "title": structure.get("title"),
                                "sections_count": len(structure.get("sections", []))},
                    "format_profiled": system.reference_parser.get_format(
                        structure.get("name")) is not None,
                    "skeleton_started": True,
                    "rag_ingest_started": bool(ext == ".pdf" and ra_cfg.enabled
                                               and ra_cfg.auto_ingest_on_upload)}
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"参考论文解析失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/reference/{name}")
    async def reference_get(name: str):
        structure = system.reference_parser.get_structure(name)
        if structure is None:
            raise HTTPException(status_code=404, detail=f"参考论文不存在: {name}")
        return structure

    @app.delete("/api/reference/{name}")
    async def reference_delete(name: str):
        ok = system.reference_parser.delete_paper(name)
        if not ok:
            raise HTTPException(status_code=404, detail=f"参考论文不存在: {name}")
        return {"ok": True}

    # ---------- 模板论文双通道产物：格式档案 / 写作骨架 ----------
    @app.get("/api/reference/{name}/format")
    async def reference_format(name: str):
        """格式档案（通道A）：排版参数 + 编译后的导出样式 + 可读摘要"""
        profile = system.reference_parser.get_format(name)
        if profile is None:
            raise HTTPException(status_code=404,
                                detail=f"该论文尚无格式档案: {name}（仅 docx/pdf 支持，可重新上传）")
        style = compile_export_style(profile)
        return {"name": name, "profile": profile, "style": style,
                "summary": human_summary(profile, style)}

    @app.get("/api/reference/{name}/skeleton")
    async def reference_skeleton(name: str):
        """写作骨架（通道B）：提取状态 + 骨架内容"""
        status = system.skeleton_extractor.status(name)
        skeleton = system.reference_parser.get_skeleton(name)
        return {"name": name, "status": status, "skeleton": skeleton}

    @app.post("/api/reference/{name}/skeleton/extract")
    async def reference_skeleton_extract(name: str):
        """手动（重）提取写作骨架——后台执行，状态轮询 /api/reference/{name}/skeleton"""
        if system.reference_parser.get_structure(name) is None:
            raise HTTPException(status_code=404, detail=f"参考论文不存在: {name}")
        if system.skeleton_extractor.status(name).get("state") == "running":
            raise HTTPException(status_code=409, detail="骨架提取正在进行中")

        async def _bg():
            await system.skeleton_extractor.run_for(name, system.reference_parser)
        asyncio.get_running_loop().create_task(_bg())
        return {"ok": True, "state": "running"}

    # ---------- 一键套入模板（格式档案 → 项目级导出样式） ----------
    def _project_style_path(project_name: str) -> Path:
        return scoping.projects_dir() / f"{_safe(project_name)}.style.json"

    @app.get("/api/export/style")
    async def export_style_get(project_name: str = Query(...)):
        """查询项目当前套用的模板排版（一键套入状态）"""
        p = _project_style_path(project_name)
        if not p.exists():
            return {"applied": False}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {"applied": True, "source_name": data.get("source_name"),
                    "source_title": data.get("source_title"),
                    "style": data.get("style"), "applied_at": data.get("applied_at"),
                    "summary": data.get("summary", [])}
        except Exception as e:
            logger.warning(f"项目样式读取失败: {e}")
            return {"applied": False}

    @app.post("/api/export/style/apply")
    async def export_style_apply(req: StyleApplyRequest):
        """一键套入模板：格式档案 → 确定性编译 → 写入项目级导出样式（导出时自动生效）"""
        profile = system.reference_parser.get_format(req.reference_name)
        if profile is None:
            raise HTTPException(status_code=404,
                                detail=f"该论文尚无格式档案: {req.reference_name}")
        style = compile_export_style(profile)
        if req.keys:
            style = {k: v for k, v in style.items() if k in set(req.keys)}
        if not style:
            raise HTTPException(status_code=400, detail="格式档案无可套用项")
        summary = human_summary(profile, style)
        structure = system.reference_parser.get_structure(req.reference_name) or {}
        scoping.projects_dir().mkdir(parents=True, exist_ok=True)
        payload = {"source_name": req.reference_name,
                   "source_title": structure.get("title") or
                   structure.get("original_filename") or req.reference_name,
                   "style": style, "summary": summary,
                   "applied_at": datetime.now().isoformat(timespec="seconds")}
        _project_style_path(req.project_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"模板排版已套入 {req.project_name} <- {req.reference_name} "
                    f"({len(style)} 键)")
        return {"ok": True, "applied": True, "style": style, "summary": summary,
                "source_title": payload["source_title"]}

    @app.post("/api/export/style/reset")
    async def export_style_reset(req: StyleResetRequest):
        """忽略模板：清除项目级样式，回到系统默认排版"""
        p = _project_style_path(req.project_name)
        if p.exists():
            p.unlink()
        return {"ok": True, "applied": False}

    @app.get("/api/outline/{project_name}")
    async def outline_get(project_name: str):
        outline = system.outline_generator.load_outline(project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
        return outline

    @app.put("/api/outline/{project_name}")
    async def outline_save(project_name: str, req: OutlineSaveRequest):
        path = system.outline_generator.save_outline(project_name, req.outline)
        return {"ok": True, "saved_to": path}

    @app.delete("/api/outline/{project_name}")
    async def outline_clear(project_name: str, mode: str = Query(
            "content", pattern="^(content|full)$",
            description="content=保留大纲清空全部正文；full=连大纲一起删除")):
        """清空论文。content：章节结构/要点/字数目标保留，正文与状态复位（可重新逐节生成）；
        full：删除大纲文件（回到未生成状态）。运行中的全文任务先拒绝（409）。"""
        # 全文任务互斥：任务持有大纲内存副本，运行中清空会被回写复活
        ft = system.fulltext
        if ft.active() and ft.task.project_name == project_name:
            raise HTTPException(status_code=409,
                                detail="全文生成任务正在运行，请先取消任务再清空论文")
        outline = system.outline_generator.load_outline(project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")

        if mode == "full":
            path = scoping.projects_dir() / f"{_safe(project_name)}.outline.json"
            if path.exists():
                path.unlink()
            logger.info(f"论文已彻底清空（含大纲）: {project_name}")
            return {"ok": True, "mode": "full", "cleared": True}

        # content：递归清空正文与状态，保留结构/要点/骨架
        def _wipe(sec: dict) -> None:
            sec["content"] = ""
            sec["actual_words"] = 0
            sec["status"] = "pending"
            for key in ("pre_enhance_content", "enhance_scores", "bestof"):
                sec.pop(key, None)
            for sub in sec.get("subsections") or []:
                _wipe(sub)

        n_sections = len(outline.get("sections", []))
        n_drafts = sum(1 for s in outline.get("sections", [])
                       if (s.get("content") or "").strip())
        for sec in outline.get("sections", []):
            _wipe(sec)
        outline.pop("outline_review_notes", None)
        system.outline_generator.save_outline(project_name, outline)
        logger.info(f"论文正文已清空（保留大纲 {n_sections} 章，原成稿 {n_drafts} 章）: {project_name}")
        return {"ok": True, "mode": "content", "sections": n_sections,
                "cleared_drafts": n_drafts}

    # ---------- 分节 ----------
    @app.post("/api/sections/regenerate")
    async def section_regenerate(req: SectionRegenerateRequest):
        """单节重生成（SSE 流式）。递进门禁：正文未生成的章节，其全部前置章节
        必须已成稿——后节上下文（上一节全文/前文数字摘要/讨论回扣结果节）全部
        来自前节正文，跳章生成会产出孤立章节；已成稿节的重生成不受限。"""
        try:
            profile = system.get_profile(req.project_name)
            outline = system.outline_generator.load_outline(req.project_name)
            if outline is None:
                raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")

            secs = outline.get("sections", [])
            idx = next((i for i, s in enumerate(secs) if s.get("id") == req.section_id), None)
            if idx is None:
                raise HTTPException(status_code=404, detail=f"未找到节: {req.section_id}")
            if not (secs[idx].get("content") or "").strip():
                missing = [s.get("title") or s.get("id") for s in secs[:idx]
                           if not (s.get("content") or "").strip()]
                if missing:
                    raise HTTPException(
                        status_code=409,
                        detail=f"递进门禁：「{secs[idx].get('title')}」的前置章节尚未生成"
                               f"（{('、'.join(missing[:4]))}{'…' if len(missing) > 4 else ''}）。"
                               f"论文须环环相扣递进论述：前节正文与关键数字是本节的直接上下文，"
                               f"请按大纲顺序先生成前置章节；已成稿章节可随时重生成。")

            async def gen():
                async for ev in system.section_writer.write_section_stream(
                        profile, outline, req.section_id,
                        extra_materials=req.extra_materials,
                        user_instruction=req.instruction,
                        only_subsection=getattr(req, "subsection_id", None)):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                sec = system.section_writer._find_section(outline, req.section_id)
                obs.log_span("section.generate", kind="node",
                             in_={"project": req.project_name, "section": req.section_id,
                                  "instruction": (req.instruction or "")[:120] or None},
                             out={"title": (sec or {}).get("title"),
                                  "words": (sec or {}).get("actual_words")})
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"分节生成失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/sections/self_review")
    async def self_review(project_name: str):
        try:
            profile = system.get_profile(project_name)
            outline = system.outline_generator.load_outline(project_name)
            if outline is None:
                raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
            report = await system.section_writer.self_review(profile, outline)
            return {"ok": True, "report": report}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ---------- 图片换图 ----------
    @app.get("/api/figures/list")
    async def figures_list(project_name: str = Query(...)):
        try:
            profile = system.get_profile(project_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"figures": profile.get("figures", [])}

    def _rewrite_fig_path(outline: dict, section_id: str, old_path: str, new_path: str) -> dict:
        sec = next((s for s in outline.get("sections", [])
                    if s.get("id") == section_id or s.get("title") == section_id), None)
        if sec is None:
            raise HTTPException(status_code=404, detail=f"未找到节: {section_id}")
        content = sec.get("content") or ""
        if old_path not in content:
            # 兼容正反斜杠变体
            alt = old_path.replace("\\", "/")
            if alt in content:
                old_path = alt
            else:
                raise HTTPException(status_code=404, detail="正文中未找到该图片路径")
        sec["content"] = content.replace(old_path, new_path)
        return sec

    @app.post("/api/figures/replace")
    async def figures_replace(project_name: str = Query(...), section_id: str = Query(...),
                              old_path: str = Query(...), new_path: str = Query(...)):
        """项目内换图：正文 markdown 路径改写（new_path 必须在项目目录内）"""
        try:
            profile = system.get_profile(project_name)
            root = Path(profile["project_path"]).resolve()
            newp = Path(new_path).resolve()
            if not str(newp).startswith(str(root)):
                raise HTTPException(status_code=403, detail="新图必须位于项目目录内")
            if not newp.is_file():
                raise HTTPException(status_code=404, detail=f"新图不存在: {newp}")
            outline = system.outline_generator.load_outline(project_name)
            if outline is None:
                raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
            sec = _rewrite_fig_path(outline, section_id, old_path, str(newp))
            system.outline_generator.save_outline(project_name, outline)
            return {"ok": True, "new_path": str(newp), "section_words": len(sec.get("content") or "")}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"换图失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/figures/upload")
    async def figures_upload(file: UploadFile = File(...),
                             project_name: str = Form(...),
                             section_id: str = Form(...),
                             old_path: str = Form(...)):
        """上传新图替换：存入 uploads/figures/{project}/，并改写正文路径"""
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff"}
        ext = Path(file.filename or "").suffix.lower()
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"仅支持图片格式: {', '.join(sorted(allowed))}")
        try:
            data = await file.read()
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="图片超过 20MB")
            import time as _time
            dest_dir = scoping.uploads_dir() / "figures" / _safe(project_name)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{int(_time.time())}_{_safe(Path(file.filename).stem)}{ext}"
            dest.write_bytes(data)

            outline = system.outline_generator.load_outline(project_name)
            if outline is None:
                raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
            new_abs = str(dest.resolve())
            sec = _rewrite_fig_path(outline, section_id, old_path, new_abs)
            system.outline_generator.save_outline(project_name, outline)
            return {"ok": True, "new_path": new_abs, "url": "/api/file/raw?path=" + new_abs,
                    "section_words": len(sec.get("content") or "")}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"图片上传失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ---------- 润色 / 去AI味 ----------
    @app.get("/api/aicenter/full")
    async def aicenter_full(project_name: str = Query(...)):
        """AI 检测中心·整篇模式：全文一次送检（朱雀合并口径=网页版算法 + 本地BERT + 启发式汇总）"""
        outline = system.outline_generator.load_outline(project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
        secs = [s for s in outline.get("sections", []) if (s.get("content") or "").strip()]
        if not secs:
            raise HTTPException(status_code=400, detail="没有成稿章节")
        title = outline.get("paper_title") or project_name
        full_text = "\n".join(f"{s.get('title','')}\n{s['content'].strip()}" for s in secs)

        def _run() -> dict:
            from src.zhuque_client import zhuque_detect, zhuque_available
            from src.aigc_detector import detect_aigc
            from src.text_enhancer import detect_ai_markers
            heur_all = detect_ai_markers(full_text)
            return {
                "paper_title": title,
                "chars": len(full_text),
                "sections": len(secs),
                "zhuque": zhuque_detect(full_text) if zhuque_available() else None,
                "zhuque_available": zhuque_available(),
                "model_check": detect_aigc(full_text),
                "heuristic": {"score": heur_all.get("score"),
                              "markers": heur_all.get("markers", [])[:8]},
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

        return await asyncio.to_thread(_run)

    @app.get("/api/sections/ai_check")
    async def sections_ai_check(project_name: str = Query(...), section_id: str = Query(...)):
        """AI 味体检：启发式评分（零 LLM 即时）+ 模型级 AIGC 检测（BERT/RoBERTa 第二意见，懒加载）"""
        outline = system.outline_generator.load_outline(project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
        try:
            report = system.text_enhancer.check_section(outline, section_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"未找到节: {section_id}")
        # 模型检测放线程池（首次调用含模型下载/加载，不能阻塞事件循环）
        try:
            from src.aigc_detector import detect_aigc
            sec = next(s for s in outline.get("sections", []) if s.get("id") == section_id)
            report["model_check"] = await asyncio.to_thread(detect_aigc, sec.get("content") or "")
        except Exception as e:
            logger.warning("模型级 AIGC 检测失败（忽略）: %s", e)
            report["model_check"] = None
        # 朱雀官方 API（腾讯 EdgeOne 网关，月免费 50 万 token；无 key 时返回 None 降级）
        try:
            from src.zhuque_client import zhuque_detect, zhuque_available
            report["zhuque_available"] = zhuque_available()
            if report["zhuque_available"]:
                report["zhuque_check"] = await asyncio.to_thread(
                    zhuque_detect, sec.get("content") or "")
            else:
                report["zhuque_check"] = None
        except Exception as e:
            logger.warning("朱雀检测失败（忽略）: %s", e)
            report["zhuque_check"] = None
        return report

    # ---------- 上传文档 AI 查重（doccheck：上传 → 三引擎检测 → 9B 改写 → 自动复检） ----------
    class DocDetectRequest(BaseModel):
        doc_id: str = Field(..., description="上传接口返回的文档 id")

    class DocRewriteRequest(BaseModel):
        doc_id: str
        paras: List[int] = Field(..., description="要改写的段落下标（可多选）")

    def _public_doc(doc: dict) -> dict:
        paras = doc.get("paragraphs") or []
        return {"id": doc["id"], "filename": doc["filename"], "ext": doc.get("ext"),
                "uploaded_at": doc.get("uploaded_at"),
                "paras": len(paras), "chars": sum(len(p) for p in paras),
                "previews": [p.strip()[:110] for p in paras],
                "history": doc.get("history", [])[-8:]}

    @app.post("/api/doccheck/upload")
    async def doccheck_upload(file: UploadFile = File(...)):
        """上传待检测文档（word/docx、txt、md、pdf）：提取文本并切分段落（检测单独触发）"""
        from src import doc_check
        ext = Path(file.filename or "").suffix.lower()
        if ext not in doc_check.ALLOWED_EXTS:
            hint = "Word 老格式请另存为 .docx 后上传" if ext == ".doc" else \
                f"仅支持 {', '.join(sorted(doc_check.ALLOWED_EXTS))}"
            raise HTTPException(status_code=400,
                                detail=f"不支持的格式「{ext or '无扩展名'}」：{hint}")
        data = await file.read()
        if len(data) > doc_check.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="文件超过 20MB")
        import uuid as _uuid
        token = _uuid.uuid4().hex[:10]
        try:
            text = await asyncio.to_thread(doc_check.extract_upload, data,
                                            file.filename, token)
            doc = await asyncio.to_thread(doc_check.create_doc, file.filename, text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        obs.log_span("doccheck.upload", kind="node",
                     in_={"name": file.filename, "bytes": len(data)},
                     out={"paras": len(doc["paragraphs"]),
                          "chars": sum(len(p) for p in doc["paragraphs"])})
        return {"ok": True, "doc": _public_doc(doc),
                "humanize": await asyncio.to_thread(doc_check.humanize_status)}

    @app.get("/api/doccheck/doc/{doc_id}")
    async def doccheck_get(doc_id: str):
        from src import doc_check
        try:
            doc = doc_check.load_doc(doc_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"doc": _public_doc(doc), "report": doc.get("report"),
                "humanize": await asyncio.to_thread(doc_check.humanize_status)}

    @app.post("/api/doccheck/detect")
    async def doccheck_detect(req: DocDetectRequest):
        """三引擎 AI 查重：朱雀官方 API（合并口径）+ 本地 BERT + 启发式，附段落级评分"""
        from src import doc_check
        try:
            doc = doc_check.load_doc(req.doc_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        report = await asyncio.to_thread(doc_check.detect_doc, doc)
        doc["report"] = report
        doc_check.save_doc(doc)
        return {"ok": True, "report": report, "doc": _public_doc(doc)}

    @app.post("/api/doccheck/rewrite")
    async def doccheck_rewrite(req: DocRewriteRequest):
        """选段 9B 改写（SSE 流式）：逐段 humanize-9b 改写 → 自动复检 → 返回新报告"""
        from src import doc_check
        try:
            doc = doc_check.load_doc(req.doc_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        if not req.paras:
            raise HTTPException(status_code=400, detail="未选择要改写的段落")
        hs = await asyncio.to_thread(doc_check.humanize_status)
        if not hs.get("available"):
            raise HTTPException(status_code=409,
                                detail=f"微调 9B 模型不可用：{hs.get('hint') or '请启动 Ollama'}")

        async def gen():
            def sse(ev: dict) -> str:
                return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def _on_event(kind: str, payload: dict) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": kind, **payload})

            async def _run():
                try:
                    result = await asyncio.to_thread(
                        doc_check.rewrite_paragraphs, doc, req.paras, _on_event)
                    queue.put_nowait({"type": "_result", "result": result})
                except Exception as e:  # noqa: BLE001
                    queue.put_nowait({"type": "_error", "error": str(e)})

            task = asyncio.get_running_loop().create_task(_run())
            result = None
            while True:
                ev = await queue.get()
                if ev["type"] == "_result":
                    result = ev["result"]
                    break
                if ev["type"] == "_error":
                    yield sse({"type": "error", "content": f"改写失败：{ev['error']}"})
                    yield "data: [DONE]\n\n"
                    return
                yield sse(ev)
            del task  # 已完结；防 GC 警告
            yield sse({"type": "status", "content": "改写完成，正在自动复检（朱雀+本地双引擎）…"})
            # 复检放线程池，每 15 秒发心跳：SSE 静默超时（前端看门狗 120 秒）不再误杀慢复检
            detect_task = asyncio.get_running_loop().create_task(
                asyncio.to_thread(doc_check.detect_doc, doc))
            report = None
            while not detect_task.done():
                try:
                    report = await asyncio.wait_for(asyncio.shield(detect_task), timeout=15)
                except asyncio.TimeoutError:
                    yield sse({"type": "status",
                               "content": "复检中…（本地引擎加载/朱雀请求耗时较长，请稍候）"})
            if detect_task.exception():
                raise detect_task.exception()
            report = detect_task.result()
            doc["report"] = report
            doc_check.save_doc(doc)
            yield sse({"type": "recheck", "report": report})
            yield sse({"type": "done", "changed": result["changed"],
                       "failed": result["failed"], "doc": _public_doc(doc)})
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/doccheck/doc/{doc_id}/download")
    async def doccheck_download(doc_id: str, fmt: str = Query("auto", pattern="^(auto|txt|docx)$")):
        """下载（改写后）文档：auto=按原格式（docx→docx，其余 txt）"""
        from src import doc_check
        try:
            doc = doc_check.load_doc(doc_id)
            path = await asyncio.to_thread(doc_check.export_doc, doc, fmt)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return FileResponse(str(path), filename=f"{Path(doc['filename']).stem}_改写版{path.suffix}")

    @app.post("/api/sections/enhance")
    async def sections_enhance(req: EnhanceRequest):
        """单节润色/去AI味（SSE 流式）"""
        if not system.llm_manager.main_llm_available():
            raise HTTPException(status_code=400,
                                detail="智谱主模型未配置：请先在 .env 设置 ZHIPU_API_KEY 并重启服务")
        obs.set_thread_id(f"section:{req.project_name}:{req.section_id}")
        try:
            profile = system.get_profile(req.project_name)
            outline = system.outline_generator.load_outline(req.project_name)
            if outline is None:
                raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")

            async def gen():
                async for ev in system.text_enhancer.enhance_section_stream(
                        profile, outline, req.section_id,
                        mode=req.mode, strength=req.strength,
                        instruction=req.instruction):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}" + chr(10) * 2
                yield "data: [DONE]" + chr(10) * 2

            return StreamingResponse(gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"增强改写失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/sections/restore")
    async def sections_restore(req: RestoreRequest):
        """还原该节为首次增强前的原文快照"""
        outline = system.outline_generator.load_outline(req.project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")
        sec = next((s for s in outline.get("sections", [])
                    if s.get("id") == req.section_id or s.get("title") == req.section_id), None)
        if sec is None:
            raise HTTPException(status_code=404, detail=f"未找到节: {req.section_id}")
        snapshot = sec.get("pre_enhance_content")
        if not snapshot:
            raise HTTPException(status_code=400, detail="该节没有增强前的快照，无需还原")
        sec["content"] = snapshot
        sec["actual_words"] = count_cn_words(snapshot)
        sec.pop("enhance_scores", None)
        system.outline_generator.save_outline(req.project_name, outline)
        return {"ok": True, "section_id": req.section_id,
                "actual_words": sec["actual_words"]}

    @app.post("/api/sections/update")
    async def sections_update(req: SectionUpdateRequest):
        """保存人工在预览区编辑的单节正文（markdown 源码）"""
        outline = system.outline_generator.load_outline(req.project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")
        sec = next((s for s in outline.get("sections", [])
                    if s.get("id") == req.section_id or s.get("title") == req.section_id), None)
        if sec is None:
            raise HTTPException(status_code=404, detail=f"未找到节: {req.section_id}")
        sec["content"] = req.content
        sec["actual_words"] = count_cn_words(req.content)
        if sec.get("status") != "drafted" and req.content.strip():
            sec["status"] = "drafted"
        system.outline_generator.save_outline(req.project_name, outline)
        return {"ok": True, "section_id": req.section_id,
                "actual_words": sec["actual_words"], "status": sec.get("status")}

    @app.post("/api/outline/meta")
    async def outline_meta(req: OutlineMetaRequest):
        """修改论文标题（预览区可编辑的论文名）"""
        outline = system.outline_generator.load_outline(req.project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")
        outline["paper_title"] = req.paper_title.strip()
        system.outline_generator.save_outline(req.project_name, outline)
        return {"ok": True, "paper_title": outline["paper_title"]}

    @app.post("/api/sections/rename")
    async def sections_rename(req: SectionRenameRequest):
        """修改章节标题（目录/大纲联动）"""
        outline = system.outline_generator.load_outline(req.project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")
        sec = next((s for s in outline.get("sections", [])
                    if s.get("id") == req.section_id), None)
        if sec is None:
            raise HTTPException(status_code=404, detail=f"未找到节: {req.section_id}")
        sec["title"] = req.title.strip()
        system.outline_generator.save_outline(req.project_name, outline)
        return {"ok": True, "section_id": req.section_id, "title": sec["title"]}

    # ---------- 一键生成全文 ----------
    @app.post("/api/fulltext/start")
    async def fulltext_start(req: FullTextStartRequest):
        if not system.llm_manager.main_llm_available():
            raise HTTPException(status_code=400,
                                detail="智谱主模型未配置：请先在 .env 设置 ZHIPU_API_KEY 并重启服务")
        outline = system.outline_generator.load_outline(req.project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {req.project_name}")
        if not outline.get("sections"):
            raise HTTPException(status_code=400, detail="大纲为空，请先生成大纲")
        try:
            profile = system.get_profile(req.project_name)
            result = system.fulltext.start(req.project_name, profile, outline,
                                           force=req.force,
                                           auto_humanize_threshold=req.auto_humanize_threshold,
                                           only=req.only)
            if not result.get("ok"):
                raise HTTPException(status_code=409, detail=result.get("error"))
            return result
        except HTTPException:
            raise
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    def _assert_fulltext_owner():
        task = getattr(system.fulltext, "task", None)
        if task is not None and getattr(task, "owner", ""):
            me = scoping.current_owner()
            if task.owner != me and me:
                raise HTTPException(status_code=403, detail="任务属于其他用户，禁止查看/操作")

    @app.get("/api/fulltext/status")
    async def fulltext_status():
        _assert_fulltext_owner()
        return system.fulltext.status()

    @app.post("/api/fulltext/cancel")
    async def fulltext_cancel():
        _assert_fulltext_owner()
        result = system.fulltext.cancel()
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("error"))
        return result

    _export_lock = asyncio.Lock()  # 同一时刻仅允许一个导出（PDF 占用本机 Word COM）

    @app.get("/api/outline/export/{fmt}")
    async def export_paper(
        fmt: str,
        project_name: str = Query(...),
        body_font_px: Optional[float] = Query(None, ge=10, le=24, description="正文字号 px"),
        line_height: Optional[float] = Query(None, ge=1.0, le=3.0, description="行距倍数"),
        first_indent_em: Optional[float] = Query(None, ge=0, le=4, description="首行缩进 em，0=不缩进"),
        body_align: Optional[str] = Query(None, pattern="^(justify|left)$", description="正文对齐"),
        table_align: Optional[str] = Query(None, pattern="^(center|left)$", description="表格位置"),
        table_width: Optional[str] = Query(None, pattern="^(full|auto)$", description="表格宽度"),
        table_font_px: Optional[float] = Query(None, ge=9, le=20, description="表格字号 px"),
        cell_align: Optional[str] = Query(None, pattern="^(center|left)$", description="单元格文字对齐"),
        fig_width_pct: Optional[int] = Query(None, ge=30, le=100, description="图片宽度百分比"),
    ):
        """导出论文：fmt = md | docx | pdf（浏览器直接下载）。
        排版查询参数缺省时依次回退 config.export_style → 代码默认值"""
        if fmt not in ("md", "docx", "pdf"):
            raise HTTPException(status_code=400, detail="fmt 仅支持 md / docx / pdf")
        outline = system.outline_generator.load_outline(project_name)
        if outline is None:
            raise HTTPException(status_code=404, detail=f"项目尚无大纲: {project_name}")
        # 排版合并：config 默认 ← 项目套入的模板样式 ← 查询参数覆盖（优先级最高）
        cfg_style = system.config.export_style.model_dump()
        style_file = scoping.projects_dir() / f"{_safe(project_name)}.style.json"
        if style_file.exists():
            try:
                tpl = json.loads(style_file.read_text(encoding="utf-8"))
                merged_tpl = dict(tpl.get("style") or {})
                # 活编译：套入记录的来源档案若已升级（如新检测的前置部分），自动补入新键
                src = tpl.get("source_name")
                prof = system.reference_parser.get_format(src) if src else None
                if prof is not None:
                    for k, v in compile_export_style(prof).items():
                        if v is not None and (k not in merged_tpl or k == "front_matter"):
                            merged_tpl[k] = v
                cfg_style.update({k: v for k, v in merged_tpl.items() if v is not None})
                logger.info(f"导出套用模板排版: {tpl.get('source_name')}")
            except Exception as e:
                logger.warning(f"项目模板样式读取失败（按默认导出）: {e}")
        style = {k: v for k, v in {
            "body_font_px": body_font_px, "line_height": line_height,
            "first_indent_em": first_indent_em, "body_align": body_align,
            "table_align": table_align, "table_width": table_width,
            "table_font_px": table_font_px, "cell_align": cell_align,
            "fig_width_pct": fig_width_pct}.items() if v is not None}
        cfg_style.update(style)
        # 导出加锁 + 移出事件循环：PDF 走本机 Word COM（可达 1 分钟），
        # 并发导出会同时驱动多个 Word 实例；阻塞事件循环则令全站轮询停摆
        if _export_lock.locked():
            raise HTTPException(status_code=409, detail="已有导出任务进行中，请稍候再试")
        async with _export_lock:
            try:
                if fmt == "md":
                    path = await asyncio.to_thread(system.section_writer.export_md, outline)
                    media = "text/markdown; charset=utf-8"
                elif fmt == "pdf":
                    path = await asyncio.to_thread(
                        system.section_writer.export_pdf, outline, style=cfg_style)
                    media = "application/pdf"
                else:
                    # 默认走 python-docx 通道（字体逐 run 可控、与《最终版》规格一致）；
                    # pandoc（OMML 公式/嵌入图片更强）作为备选回退
                    def _export_docx():
                        try:
                            return system.section_writer.export_docx(outline, style=cfg_style)
                        except Exception as pe:
                            logger.warning(f"python-docx 导出失败，回退 pandoc: {pe}")
                            return system.section_writer.export_docx_pandoc(outline, style=cfg_style)
                    path = await asyncio.to_thread(_export_docx)
                    media = ("application/vnd.openxmlformats-officedocument"
                             ".wordprocessingml.document")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"导出失败: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        # 下载名带时间戳：浏览器不再为同名文件堆 (1)(2)(3)… 副本
        file = Path(path)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return FileResponse(
            str(file), media_type=media, filename=f"{file.stem}_{ts}{file.suffix}")

    # ---------- 会话聊天 ----------
    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        session_id = req.session_id or f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # ThreadId=会话ID；用户 query 作为轨迹首条 span
        obs.set_thread_id(session_id)
        obs.log_span("user.query", kind="query",
                     in_={"text": req.question[:500],
                          "attachments": len(req.attachments or []),
                          "project": req.project_name})
        # 附件/文件夹注入前做越权校验（跨用户项目与文档不可读）
        for _fp in list(req.attachments or []) + list(req.folders or [])[:4]:
            _assert_file_allowed(_fp)

        async def gen():
            async for ev in system.chat_stream(req.question, session_id, req.attachments,
                                               getattr(req, "selections", None),
                                               getattr(req, "file_drops", None),
                                               getattr(req, "folders", None),
                                               getattr(req, "project_name", None)):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # ---------- 对话管理 ----------
    @app.get("/api/conversation/{session_id}")
    async def conversation_info(session_id: str):
        return system.conversation_manager.get_stats(scoping.owner_key(session_id))

    @app.post("/api/conversation/{session_id}/clear")
    async def conversation_clear(session_id: str):
        system.conversation_manager.clear_conversation(scoping.owner_key(session_id))
        return {"session_id": session_id, "status": "cleared"}

    @app.get("/api/conversation/{session_id}/export")
    async def conversation_export(session_id: str):
        conv = system.conversation_manager.get_or_create_conversation(scoping.owner_key(session_id))
        return {"session_id": session_id, "conversation": conv.to_dict(), "status": "success"}

    # ---------- WebSocket（轻量：系统信息 + 聊天） ----------
    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # WS 无法携带 Authorization 头：改用 ?token=（多用户启用后强制校验）
        if auth.user_count() > 0:
            payload = auth.parse_token(websocket.query_params.get("token", ""))
            ws_user = auth.get_user((payload or {}).get("sub", "")) if payload else None
            if ws_user is None or ws_user.get("disabled"):
                await websocket.close(code=4401)
                return
            scoping.set_request_owner(ws_user["username"])
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                mtype = message.get("type", "")

                if mtype == "get_system_info":
                    await websocket.send_json({
                        "type": "system_info",
                        **system.llm_manager.get_model_info(),
                        "kb": system.vsm.get_all_info(),
                    })
                elif mtype == "chat":
                    question = message.get("question", "")
                    session_id = message.get("session_id", "default")
                    attachments = message.get("attachments", [])
                    await websocket.send_json({"type": "stream_start", "session_id": session_id})
                    async for ev in system.chat_stream(question, session_id, attachments):
                        if ev["type"] == "chunk":
                            await websocket.send_json({"type": "stream_chunk", "chunk": ev["content"]})
                        elif ev["type"] == "error":
                            await websocket.send_json({"type": "error", "message": ev["content"]})
                        elif ev["type"] == "done":
                            await websocket.send_json({
                                "type": "stream_end", "answer": ev["content"],
                                "session_id": session_id,
                                "conversation_stats": ev.get("conversation_stats"),
                            })
                elif mtype == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json({"type": "error", "message": f"未知消息类型: {mtype}"})
        except WebSocketDisconnect:
            manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket 错误: {e}")
            manager.disconnect(websocket)

    return app


def run_server():
    app = create_app()
    config = get_config_manager().get_config()
    uvicorn.run(app, host=config.server.host, port=config.server.port,
                log_level="info", reload=False, access_log=False)


if __name__ == "__main__":
    run_server()
