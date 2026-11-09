"""
认证与 RBAC 权限（多用户隔离，MySQL + Redis 真实存储）
======================================================
- 用户存储：MySQL 库 moji（users 表：pbkdf2-hmac-sha256 加盐哈希 + 角色 + 启停；
  auth_audit 表：登录/登出/管理审计）——见 src/auth_store.py
- 会话令牌：服务端会话（token = 128bit 随机），存 Redis `moji:session:{token}`，
  EX=TTL；登出/改密/禁用即删键踢下线。Redis AOF/RDB 持久化，重启后仍有效
- RBAC：角色 → 权限集合；admin 管理用户与查看全局，user 仅访问自己的项目/文档/会话
- 单用户 legacy 模式：库中无任何账号时不拦截请求（owner=""），兼容存量数据；
  第一个注册/注入的账号自动成为 admin，并接管 legacy 数据（见 migrate_legacy）

路由侧用法（web_api）：
    app = FastAPI(dependencies=[Depends(auth_dependency)])   # 全局鉴权+设置 owner 上下文
    @app.get("/api/admin/users")  async def ...(user=Depends(require_admin))
"""
import hashlib
import hmac
import logging
import secrets
import time
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

from src import auth_store
from src.auth_store import AuthStoreError

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 7 * 24 * 3600          # 7 天（Redis EX，配置 auth_store.session_ttl_seconds）
PBKDF2_ROUNDS = 100_000

# ---------- RBAC：角色 → 权限 ----------
PERMISSIONS = {
    "project:read", "project:write",          # 本人项目/画像/大纲/正文
    "kb:read", "kb:build",                    # 共享知识库（读/重建）
    "chat:use",                               # 会话
    "reference:manage",                       # 本人参考论文
    "export:use",                             # 导出
    "user:manage",                            # 用户管理（admin 专属）
}
ROLE_PERMISSIONS: Dict[str, set] = {
    "admin": PERMISSIONS,                     # 管理员：全部权限（含用户管理）
    "user": PERMISSIONS - {"user:manage"},    # 普通用户：自己的资源 + 共享知识库
}
ROLES = list(ROLE_PERMISSIONS.keys())


def role_has(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


# ==================== 密码哈希（pbkdf2，纯标准库） ====================

def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), PBKDF2_ROUNDS).hex()
    return f"{salt}${digest}"


_hash_password = hash_password   # 旧内部名兼容


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                salt.encode("utf-8"), PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(check, digest)


# ==================== 用户（MySQL） ====================

def _guard(e: Exception) -> None:
    """存储层异常 → 503（服务可用性问题与凭据错误区分开）"""
    if isinstance(e, AuthStoreError):
        raise HTTPException(status_code=503, detail=f"认证存储不可用: {e}") from e


def _load_users() -> List[dict]:
    """兼容旧内部名：全量用户行（含密码哈希，仅内部/管理端使用）"""
    try:
        return auth_store.list_users()
    except AuthStoreError as e:
        _guard(e)
        return []


def user_count() -> int:
    try:
        return auth_store.user_count()
    except AuthStoreError as e:
        _guard(e)
        return 0


def get_user(username: str) -> Optional[dict]:
    try:
        return auth_store.get_user(username)
    except AuthStoreError as e:
        _guard(e)
        return None


def re_valid_username(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[\w\u4e00-\u9fff]{3,32}", name))


def public_view(user: dict) -> dict:
    return {"username": user.get("username"), "role": user.get("role"),
            "display_name": user.get("display_name") or "",
            "avatar": user.get("avatar") or "",
            "disabled": bool(user.get("disabled")),
            "created_at": user.get("created_at")}


def create_user(username: str, password: str, role: Optional[str] = None) -> dict:
    """创建用户（写 MySQL）；库空时首个用户自动 admin（并接管 legacy 数据）；密码强度下限 6 位"""
    try:
        username = (username or "").strip()
        if not re_valid_username(username):
            raise ValueError("用户名仅允许 3-32 位字母/数字/下划线/中文")
        if len(password or "") < 6:
            raise ValueError("密码至少 6 位")
        if auth_store.get_user(username):
            raise ValueError(f"用户已存在: {username}")
        role = role if role in ROLES else ("admin" if auth_store.user_count() == 0 else "user")
        auth_store.insert_user(username, hash_password(password), role)
        logger.info(f"用户创建: {username} (role={role})")
        if role == "admin" and auth_store.user_count() == 1:
            migrate_legacy(username)   # 首个管理员接管 legacy 单用户数据
        return public_view(auth_store.get_user(username))
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("创建失败：认证存储不可用")


def update_user(username: str, **changes) -> dict:
    """管理接口：改角色/启停/重置密码；不可禁用/降级最后一个可用 admin；写后踢下线"""
    try:
        target = auth_store.get_user(username)
        if target is None:
            raise ValueError(f"用户不存在: {username}")
        new_role = changes.get("role", target.get("role"))
        new_disabled = bool(changes.get("disabled", target.get("disabled")))
        if new_role not in ROLES:
            raise ValueError(f"非法角色: {new_role}（可选 {ROLES}）")
        if target.get("role") == "admin" and (new_role != "admin" or new_disabled):
            if auth_store.active_admin_count(exclude=username) == 0:
                raise ValueError("不能禁用或降级最后一个可用管理员")
        password_hash = None
        if changes.get("password"):
            if len(changes["password"]) < 6:
                raise ValueError("密码至少 6 位")
            password_hash = hash_password(changes["password"])
        auth_store.update_user_row(username, role=new_role if "role" in changes else None,
                                   disabled=new_disabled if "disabled" in changes else None,
                                   password_hash=password_hash)
        # 角色/启停/密码变化 → 该用户全部会话失效，强制重新登录
        auth_store.revoke_user_sessions(username)
        return public_view(auth_store.get_user(username))
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("更新失败：认证存储不可用")


def update_profile(username: str, *, avatar: Optional[str] = None) -> dict:
    """用户自助修改资料：头像（base64 data URL，≤400KB）。传空串表示清空。"""
    try:
        if auth_store.get_user(username) is None:
            raise ValueError(f"用户不存在: {username}")
        if avatar:
            if not avatar.startswith("data:image/"):
                raise ValueError("头像格式无效（需 data:image/... base64）")
            if len(avatar) > 400_000:
                raise ValueError("头像过大（上限 400KB）")
        auth_store.update_profile(username, avatar=avatar)
        return public_view(auth_store.get_user(username))
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("资料更新失败：认证存储不可用")


def rename_username(old: str, new: str) -> None:
    """改用户名（登录名本身）：MySQL 行 + 项目记录 + 数据目录级联 + 全部会话踢下线。
    改名后该用户所有已登录客户端必须重新登录。"""
    from src.scoping import BASE_DIR, safe_name
    from pathlib import Path as _P
    try:
        new = (new or "").strip()
        if new == old:
            return
        if not re_valid_username(new):
            raise ValueError("用户名仅允许 3-32 位字母/数字/下划线/中文")
        if auth_store.get_user(new):
            raise ValueError(f"用户名已被占用: {new}")
        if auth_store.get_user(old) is None:
            raise ValueError(f"用户不存在: {old}")
        auth_store.rename_user(old, new)
        # 数据目录级联（safe_name 后同名的目录才会随账号走）
        old_safe, new_safe = safe_name(old), safe_name(new)
        if old_safe != new_safe:
            for base in ("projects", "reference_papers", "uploads"):
                src, dst = BASE_DIR / base / old_safe, BASE_DIR / base / new_safe
                if src.exists() and not dst.exists():
                    src.rename(dst)
            old_state = BASE_DIR / "workspace" / f"{old_safe}.state.json"
            new_state = BASE_DIR / "workspace" / f"{new_safe}.state.json"
            if old_state.exists() and not new_state.exists():
                old_state.rename(new_state)
        # 内存对话上下文键前缀迁移（old:: → new::），否则改名后对话记忆失联
        try:
            from src.conversation_manager import get_conversation_manager
            get_conversation_manager().rename_owner_prefix(old, new)
        except Exception as e:
            logger.warning(f"对话上下文前缀迁移跳过: {e}")
        # Redis 项目列表缓存：旧 key 立即失效（新名下次查询自动回源 MySQL）
        try:
            from src import project_store
            project_store._cache_drop(old)
            project_store._cache_drop(new)
        except Exception:
            pass
        # 旧会话的 payload.sub 还是旧名，全部吊销 → 客户端重新登录
        auth_store.revoke_user_sessions(old)
        logger.info(f"用户改名: {old} → {new}（数据目录与会话已级联）")
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("改名失败：认证存储不可用")


def delete_user(username: str) -> None:
    try:
        target = auth_store.get_user(username)
        if target is None:
            raise ValueError(f"用户不存在: {username}")
        if target.get("role") == "admin" and auth_store.active_admin_count(exclude=username) == 0:
            raise ValueError("不能删除最后一个可用管理员")
        auth_store.delete_user_row(username)   # 内部同时踢会话 + 审计
        logger.info(f"用户已删除（数据目录保留）: {username}")
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("删除失败：认证存储不可用")


# ==================== 会话令牌（Redis） ====================

def make_token(username: str, role: str) -> str:
    """登录后签发服务端会话 token（Redis 持久化）"""
    try:
        return auth_store.create_session(username, role)["token"]
    except AuthStoreError as e:
        _guard(e)
        raise


def parse_token(token: str) -> Optional[dict]:
    """token → 会话 payload {sub, role, iat, exp}；无效/过期/被踢 → None"""
    try:
        return auth_store.get_session(token)
    except AuthStoreError as e:
        _guard(e)
        return None


def revoke_token(token: str) -> None:
    """登出：吊销该会话（存储层按归属幂等处理）"""
    try:
        payload = auth_store.get_session(token)
        if payload:
            auth_store.revoke_session_by_user(token, payload.get("sub", ""))
    except AuthStoreError as e:
        _guard(e)


# ==================== FastAPI 集成 ====================

# 无需鉴权的路径（登录页/静态资源/健康检查/认证本身）
PUBLIC_PATHS = {"/", "/api/health", "/api/auth/status", "/api/auth/login",
                "/api/auth/register", "/api/auth/logout",
                "/api/ws"}  # WS 在端点内用 ?token= 鉴权


def _token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # WebSocket / 文件直链场景：浏览器无法自定义 header，允许 ?token= 查询参数
    return request.query_params.get("token", "")


def authenticate(request: Request) -> Optional[dict]:
    """校验请求 → 返回用户 dict；单用户 legacy 模式（无账号）返回 {'username':'', 'role':'admin'}"""
    if user_count() == 0:
        return {"username": "", "role": "admin", "legacy": True}
    token = _token_from_request(request)
    payload = parse_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或令牌失效")
    user = get_user(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if user.get("disabled"):
        raise HTTPException(status_code=403, detail="账号已禁用")
    return user


async def auth_dependency(request: Request) -> dict:
    """全局依赖：鉴权 + 把 owner 写入请求上下文（scoping.current_owner 生效于整条调用链）"""
    if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
        return {"username": "", "role": "anonymous"}
    user = authenticate(request)
    from src import scoping
    scoping.set_request_owner(user.get("username", ""))
    return user


async def require_admin(user: dict = None, request: Request = None) -> dict:
    """管理员路由依赖：角色 RBAC 校验"""
    if request is not None and user is None:
        user = authenticate(request)
    if not user or not role_has(user.get("role", ""), "user:manage"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def login(username: str, password: str) -> dict:
    try:
        user = auth_store.get_user(username)
        if user is None or not _verify_password(password or "", user.get("password", "")):
            auth_store.audit(username or "?", "deny", "bad credentials")
            raise ValueError("用户名或密码错误")
        if user.get("disabled"):
            auth_store.audit(username, "deny", "disabled")
            raise ValueError("账号已禁用")
        session = auth_store.create_session(user["username"], user["role"])
        return {"token": session["token"], "user": public_view(user)}
    except AuthStoreError as e:
        _guard(e)
        raise ValueError("登录失败：认证存储不可用")


# ==================== legacy 数据迁移 ====================

def migrate_legacy(username: str) -> None:
    """首个管理员注册时接管单用户时代的存量数据：projects/ workspace/state.json
    reference_papers/ uploads/ → 各自 {dir}/{username}/ 下。幂等（目标已存在则跳过）。"""
    from src import scoping
    roots = scoping.user_root_dirs(username)
    base = scoping.BASE_DIR
    moves: List[tuple] = []

    proj_root = base / "projects"
    if proj_root.exists():
        dest = roots["projects"]
        dest.mkdir(parents=True, exist_ok=True)
        for p in proj_root.iterdir():
            if p.is_file():
                moves.append((p, dest / p.name))

    ws_state = base / "workspace" / "state.json"
    dest_state = base / "workspace" / f"{scoping.safe_name(username)}.state.json"
    if ws_state.exists() and not dest_state.exists():
        moves.append((ws_state, dest_state))

    for name in ("reference_papers", "uploads"):
        src_root = base / name
        if src_root.exists():
            dest = roots[name.replace("reference_papers", "reference")]
            dest.mkdir(parents=True, exist_ok=True)
            for p in src_root.iterdir():
                if p.is_file():
                    moves.append((p, dest / p.name))

    moved = 0
    for src, dst in moves:
        if dst.exists():
            continue
        try:
            src.rename(dst)
            moved += 1
        except OSError as e:
            logger.warning(f"legacy 迁移跳过 {src}: {e}")
    if moved:
        logger.info(f"legacy 数据迁移完成：{moved} 个文件归属用户 {username}")
