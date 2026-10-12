"""
全链路可观测性（trace / 结构化日志 / LLM 调用观测 / Agent 执行轨迹）
====================================================================
Agent 轨迹（基础日志）落在 logs/agent_trace.jsonl，每行一个 span：
  {"ts","trace","thread","step","kind","name","in","out","duration_ms","error"}
  - kind: query(用户输入) | node(管线节点) | tool(工具调用) | llm(模型调用) | error
  - thread: 会话/任务级 ThreadId（chat=session_id，写作任务=task/project）
  - step: 同一 trace 内递增编号（contextvar，请求内顺序执行时严格递增）
一条请求的完整链路：HTTP 中间件(trace 注入) → API 层(owner 注入) → 业务层(事件打点)
→ LLM 层(callback 观测) → 后台线程/任务(trace 续接)。每条日志自动携带
[trace_id user]，定位问题时拿 trace_id 一grep 到底。

组成：
  - trace 上下文（contextvars）+ ThreadSnapshot（线程边界续接）
  - TraceFilter：把 trace_id/owner 附加到每条日志记录
  - configure_logging：RotatingFileHandler（按大小轮转）+ 控制台；text/json 两种格式
  - log_event：业务事件打点（注册/大纲/分节/导出/全文/建库…）
  - LLMCallObserver：LangChain callback，每次模型调用追加 logs/llm_calls.jsonl
    （模型/purpose/输入输出字符数/耗时/状态——成本与性能分析的数据底座）
"""
import json
import logging
import logging.handlers
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import contextvars

_trace_var = contextvars.ContextVar("trace_id", default="")
_thread_var = contextvars.ContextVar("thread_id", default="")
_step_var = contextvars.ContextVar("step_no", default=0)

LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"
_LLM_JSONL_LOCK = threading.Lock()


# ==================== trace 上下文 ====================

def new_trace_id() -> str:
    return secrets.token_hex(4)  # 8 位，单机够用且日志友好


def current_trace() -> str:
    return _trace_var.get() if _trace_var is not None else ""


def set_trace_id(tid: str) -> None:
    if _trace_var is not None:
        _trace_var.set(tid or "")


def set_thread_id(tid: str) -> None:
    """设置当前链路的 ThreadId（chat=session_id；写作任务=task_id/project）"""
    _thread_var.set(tid or "")


def current_thread() -> str:
    return _thread_var.get()


def next_step() -> int:
    """当前 trace 内递增的 Step 编号（请求内顺序执行时严格递增；
    同请求并发节点会各自基于快照计数，仅保证不重复起点）"""
    n = _step_var.get() + 1
    _step_var.set(n)
    return n


def current_step() -> int:
    return _step_var.get()


class ThreadSnapshot:
    """捕获当前请求上下文（owner/trace/thread/step），后台线程入口恢复——contextvars 不跨线程。
    用法：snap = ThreadSnapshot.capture()；线程内 snap.restore()"""

    def __init__(self, owner: str, trace: str, thread: str = "", step: int = 0):
        self.owner = owner
        self.trace = trace
        self.thread = thread
        self.step = step

    @classmethod
    def capture(cls) -> "ThreadSnapshot":
        from src import scoping
        return cls(scoping.current_owner(), current_trace(), current_thread(), current_step())

    def restore(self) -> None:
        from src import scoping
        scoping.set_request_owner(self.owner)
        set_trace_id(self.trace)
        set_thread_id(self.thread)
        _step_var.set(self.step)


# ==================== 日志格式与过滤器 ====================

class TraceFilter(logging.Filter):
    """每条日志附加 trace_id / owner（取自请求上下文；无上下文时占位 -）"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace = current_trace() or "-"
        record.thread = current_thread() or "-"
        try:
            from src import scoping
            record.user = scoping.current_owner() or "-"
        except Exception:
            record.user = "-"
        return True


TEXT_FMT = "%(asctime)s %(levelname)-7s [%(trace)s] [%(user)s] %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志（接 ELK/Loki 免解析）；text 模式下不启用"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "trace": getattr(record, "trace", "-"),
            "thread": getattr(record, "thread", "-"),
            "user": getattr(record, "user", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ==================== 初始化 ====================

# 高噪声三方库降级（否则 trace 日志被 httpx 重试刷屏）
QUIET_LOGGERS = ["httpx", "httpcore", "urllib3", "chromadb", "sentence_transformers",
                 "matplotlib", "PIL", "numba"]


def configure_logging(cfg=None) -> None:
    """统一日志初始化：轮转文件 + 控制台 + trace 过滤。幂等（重复调用先清 root handlers）"""
    level = getattr(logging, str(getattr(cfg, "level", "INFO")).upper(), logging.INFO)
    log_file = getattr(cfg, "file", "") or "logs/paper_agent.log"
    rotate_mb = int(getattr(cfg, "rotate_mb", 10) or 10)
    backup_count = int(getattr(cfg, "backup_count", 5) or 5)
    as_json = str(getattr(cfg, "format", "text")).lower() == "json"

    path = Path(log_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    formatter = JsonFormatter() if as_json else logging.Formatter(TEXT_FMT)
    filt = TraceFilter()

    fh = logging.handlers.RotatingFileHandler(path, maxBytes=rotate_mb * 1024 * 1024,
                                              backupCount=backup_count, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.addFilter(filt)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    sh.addFilter(filt)
    root.addHandler(fh)
    root.addHandler(sh)

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"日志系统就绪: {path} (rotate {rotate_mb}MB x{backup_count}, "
                f"format={'json' if as_json else 'text'})")


# ==================== 业务事件打点 ====================

_EVENT_LOGGER = logging.getLogger("paper_agent.event")


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """业务事件打点：EVENT <name> {json kv}。trace/user 由过滤器附加。
    例：log_event("outline.generate", project="x", sections=7, duration_ms=1234)"""
    payload = json.dumps({k: v for k, v in fields.items() if v is not None},
                         ensure_ascii=False, default=str)
    _EVENT_LOGGER.log(level, f"EVENT {event} {payload}")


# ==================== Agent 执行轨迹（基础日志：span 流） ====================

class _JsonlWriter:
    """线程安全 JSONL 追加（超限滚动为 .1）"""
    _locks: dict = {}

    def __init__(self, path: Path, rotate_mb: int = 20):
        self.path = path
        self.rotate_mb = rotate_mb
        key = str(path)
        if key not in _JsonlWriter._locks:
            _JsonlWriter._locks[key] = threading.Lock()
        self.lock = _JsonlWriter._locks[key]

    def append(self, row: dict) -> None:
        try:
            with self.lock:
                if self.path.exists() and \
                        self.path.stat().st_size > self.rotate_mb * 1024 * 1024:
                    bak = self.path.with_suffix(self.path.suffix + ".1")
                    if bak.exists():
                        bak.unlink()
                    self.path.rename(bak)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # 观测失败绝不影响主流程
            logger.debug(f"jsonl 记录失败 {self.path.name}: {e}")


TRACE_JSONL = _JsonlWriter(LOGS_DIR / "agent_trace.jsonl", rotate_mb=20)


def _clip(value, limit: int = 2000):
    """入参/出参安全序列化：JSON 优先（保结构），失败降级 str；超长截断"""
    if value is None:
        return None
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = str(value)
    return s if len(s) <= limit else s[:limit] + f"...(截断,共{len(s)}字符)"


def log_span(name: str, kind: str = "node", step=None, in_=None, out=None,
             duration_ms=None, error=None, extra: dict | None = None) -> dict:
    """记录一个已完成的 span。kind: query|node|tool|llm|error
    基础字段：ts/trace/thread/user/step/kind/name + in/out/duration_ms/error"""
    from src import scoping
    row = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "trace": current_trace() or "-",
        "thread": current_thread() or "-",
        "user": scoping.current_owner() or "-",
        "step": step if step is not None else next_step(),
        "kind": kind,
        "name": name,
    }
    if in_ is not None:
        row["in"] = _clip(in_)
    if out is not None:
        row["out"] = _clip(out)
    if duration_ms is not None:
        row["duration_ms"] = duration_ms
    if error:
        row["error"] = str(error)[:500]
    if extra:
        row.update({k: v for k, v in extra.items() if v is not None})
    TRACE_JSONL.append(row)
    # 主日志同步一行（grep trace_id 时能看到节点流；完整 in/out 在 jsonl）
    brief = {k: row.get(k) for k in ("kind", "step", "duration_ms") if k in row}
    if error:
        logger.warning(f"SPAN {name} 失败: {str(error)[:120]} | {brief}")
    else:
        logger.info(f"SPAN {name} {json.dumps(brief, ensure_ascii=False, default=str)}")
    return row


class span:
    """上下文管理器版：自动计时/编号/异常捕获。
    with span("kb.retrieve", kind="tool", in_={"q": q, "k": k}) as s:
        docs = search(q, k)
        s.out = {"hits": len(docs)}"""

    def __init__(self, name: str, kind: str = "node", in_=None):
        self.name = name
        self.kind = kind
        self.in_ = in_
        self.step = next_step()
        self.t0 = time.perf_counter()
        self.out = None
        self.error = None

    def __enter__(self) -> "span":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            self.error = repr(exc_val)
        log_span(self.name, kind=self.kind, step=self.step, in_=self.in_,
                 out=self.out, duration_ms=round((time.perf_counter() - self.t0) * 1000),
                 error=self.error)
        return False  # 不吞异常


# ==================== LLM 调用观测 ====================

class LLMCallObserver:
    """LangChain callback：每次 LLM 调用 → logs/llm_calls.jsonl
    按实例绑定 purpose（main/small/analyst），模型调用起止计时，输出字符统计。
    挂法：ChatOpenAI(..., callbacks=[LLMCallObserver("main").handler])"""

    def __init__(self, purpose: str, jsonl_path: Optional[Path] = None,
                 rotate_mb: int = 20):
        from langchain_core.callbacks import BaseCallbackHandler
        self.purpose = purpose
        self.jsonl_path = jsonl_path or (LOGS_DIR / "llm_calls.jsonl")
        self.rotate_mb = rotate_mb
        self._starts: dict = {}   # run_id -> t0（回调可能并发，dict 赋值原子）
        self._lock = threading.Lock()
        cls = BaseCallbackHandler

        # 动态构造 handler 子类（闭包绑定 self），避免模块级继承受 langchain 缺失影响
        observer = self

        class _Handler(cls):
            def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
                observer._starts[run_id] = time.perf_counter()

            def on_llm_start(self, serialized, prompts, *, run_id, **kw):
                observer._starts[run_id] = time.perf_counter()

            def on_llm_end(self, response, *, run_id, **kw):
                t0 = observer._starts.pop(run_id, None)
                if t0 is None:
                    return
                out_text = ""
                try:
                    generations = getattr(response, "generations", []) or []
                    flat = [g for batch in generations for g in batch]
                    out_text = "".join(str(getattr(g, "text", "")) for g in flat)
                except Exception:
                    pass
                in_chars = 0
                try:  # chat 场景 messages 在 start 才可见，这里退化为不记或从 serialized 无从取
                    pass
                except Exception:
                    pass
                observer._append(status="ok", duration_ms=round((time.perf_counter() - t0) * 1000),
                                 out_chars=len(out_text))

            def on_llm_error(self, error, *, run_id, **kw):
                t0 = observer._starts.pop(run_id, None)
                observer._append(status="error", duration_ms=round((time.perf_counter() - t0) * 1000)
                                 if t0 else None, error=str(error)[:200])

        self.handler = _Handler()

    def _append(self, status: str, duration_ms=None, out_chars=None, error=None) -> None:
        from src import scoping
        row = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "trace": current_trace() or "-",
            "thread": current_thread() or "-",
            "user": scoping.current_owner() or "-",
            "purpose": self.purpose,
            "status": status,
        }
        if duration_ms is not None:
            row["duration_ms"] = duration_ms
        if out_chars is not None:
            row["out_chars"] = out_chars
        if error:
            row["error"] = str(error)[:500]
        _JsonlWriter(self.jsonl_path, self.rotate_mb).append(row)
        # 同步进 Agent 轨迹流（kind=llm）：与节点/工具同一条时间线检索
        log_span(f"llm.{self.purpose}", kind="llm",
                 out={"status": status, "out_chars": out_chars},
                 duration_ms=duration_ms, error=error,
                 extra={"purpose": self.purpose, "status": status})
        return
        try:
            with _LLM_JSONL_LOCK:
                if (self.jsonl_path.exists()
                        and self.jsonl_path.stat().st_size > self.rotate_mb * 1024 * 1024):
                    bak = self.jsonl_path.with_suffix(".jsonl.1")
                    if bak.exists():
                        bak.unlink()
                    self.jsonl_path.rename(bak)
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:  # 观测失败绝不影响主流程
            logger.debug(f"llm_calls 记录失败: {e}")

    def wrap_vision_call(self, fn, *args, **kwargs):
        """非 langchain 客户端（如 vision 的 AsyncOpenAI）手动包裹计时"""
        raise NotImplementedError  # vision 侧直接用 log_event，见 vision_agent


def attach_llm_observer(llm, purpose: str):
    """给已构造的 langchain LLM 实例挂观测 callback（返回原实例，便于链式使用）"""
    try:
        obs = LLMCallObserver(purpose)
        existing = list(getattr(llm, "callbacks", None) or [])
        llm.callbacks = existing + [obs.handler]
        return llm
    except Exception as e:
        logger.debug(f"LLM 观测挂载失败({purpose}): {e}")
        return llm


# ==================== HTTP 访问中间件（纯 ASGI，contextvar 同帧传播） ====================

class TraceAccessMiddleware:
    """每请求：生成/继承 trace_id → 注入上下文与响应头 X-Request-ID；
    结束记访问日志（方法/路径/状态/耗时，trace/user 由过滤器附加）。
    用纯 ASGI 而非 BaseHTTPMiddleware：后者跨 task 边界会断掉 contextvar 传播。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        import time as _time
        raw = {k.decode("latin-1"): v.decode("latin-1")
               for k, v in (scope.get("headers") or [])}
        tid = raw.get("x-request-id") or new_trace_id()
        token = _trace_var.set(tid) if _trace_var is not None else None
        t0 = _time.perf_counter()
        status = 500

        async def _send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", tid.encode("latin-1")))
            await send(message)

        path = scope.get("path", "")
        method = scope.get("method", "?")
        try:
            await self.app(scope, receive, _send)
        except Exception as e:
            logging.getLogger("paper_agent.access").error(
                f"ACCESS {method} {path} -> 500 未捕获异常: {e!r}")
            raise
        finally:
            dur = round((_time.perf_counter() - t0) * 1000)
            lvl = logging.DEBUG if path.startswith(("/static", "/api/health")) else logging.INFO
            logging.getLogger("paper_agent.access").log(
                lvl, f"ACCESS {method} {path} -> {status} {dur}ms")
            if token is not None:
                _trace_var.reset(token)
