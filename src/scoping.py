"""
资源作用域（多用户隔离）
========================
以「请求级 owner 上下文」实现按用户隔离：FastAPI 鉴权依赖解析出当前用户后
调用 set_request_owner(username)，此后所有存储层函数通过本模块取路径/键，
业务代码签名零改动：
  - projects/{owner}/            画像/大纲/研究主体/样式/论文草稿（legacy: projects/）
  - workspace/{owner}.state.json 工作区状态（legacy: workspace/state.json）
  - reference_papers/{owner}/    参考论文（legacy: reference_papers/）
  - uploads/figures/{owner}/…    换图上传
  - 会话/内存任务键："{owner}::{key}" 前缀防跨用户碰撞

owner 为空串 = 单用户 legacy 模式（未创建任何账号时保持原路径，兼容存量数据）。

线程边界：threading.Thread 不会继承 contextvars，后台线程入口必须显式
set_request_owner(owner)（见 web_api 各 worker）；asyncio.create_task 自动继承 ✓。
"""
import contextvars
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

_owner_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_owner", default="")


def set_request_owner(username: str) -> None:
    _owner_var.set((username or "").strip())


def current_owner() -> str:
    return _owner_var.get()


def safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (name or "").strip()).strip("_") or "_"


def _scoped(base: Path, owner: str) -> Path:
    """owner 非空 → base/{safe(owner)}；空 → base 本身（legacy 单用户模式）"""
    if not owner:
        return base
    return base / safe_name(owner)


# ---------- 各资源根路径（调用方一律经由此处取路径） ----------
def projects_dir() -> Path:
    return _scoped(BASE_DIR / "projects", current_owner())


def legacy_projects_dir() -> Path:
    """跨用户枚举（仅鉴权/迁移用）：projects 根"""
    return BASE_DIR / "projects"


def workspace_state_file() -> Path:
    if not current_owner():
        return BASE_DIR / "workspace" / "state.json"
    return BASE_DIR / "workspace" / f"{safe_name(current_owner())}.state.json"


def reference_dir() -> Path:
    return _scoped(BASE_DIR / "reference_papers", current_owner())


def uploads_dir() -> Path:
    return _scoped(BASE_DIR / "uploads", current_owner())


def user_root_dirs(owner: str) -> dict:
    """指定用户（非当前上下文）的各根目录——鉴权边界/管理接口用"""
    return {
        "projects": _scoped(BASE_DIR / "projects", owner),
        "reference": _scoped(BASE_DIR / "reference_papers", owner),
        "uploads": _scoped(BASE_DIR / "uploads", owner),
    }


# ---------- 键前缀（会话/后台任务等内存态） ----------
def owner_key(key: str) -> str:
    owner = current_owner()
    return f"{owner}::{key}" if owner else key


def split_owner_key(owned: str):
    """拆出 (owner, key)；无前缀返回 ('', key)"""
    if "::" in owned:
        owner, _, key = owned.partition("::")
        return owner, key
    return "", owned
