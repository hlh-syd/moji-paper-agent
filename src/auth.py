"""
认证与 RBAC 权限（多用户隔离）
==============================
- 用户存储：workspace/users.json（pbkdf2-hmac-sha256 加盐哈希，零第三方依赖）
- 令牌：HMAC-SHA256 签名的 base64url(JSON payload)（类 JWT：sub/exp/role），签名密钥
  持久化 workspace/auth_secret.key（首次自动生成）
- RBAC：角色 → 权限集合；admin 管理用户与查看全局，user 仅访问自己的项目/文档/会话
- 单用户 legacy 模式：尚未创建任何账号时不拦截请求（owner=""），兼容存量数据；
  第一个注册的账号自动成为 admin，并接管 legacy 数据（见 migrate_legacy）

路由侧用法（web_api）：
    app = FastAPI(dependencies=[Depends(auth_dependency)])   # 全局鉴权+设置 owner 上下文
    @app.get("/api/admin/users")  async def ...(user=Depends(require_admin))
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"
USERS_FILE = WORKSPACE_DIR / "users.json"
SECRET_FILE = WORKSPACE_DIR / "auth_secret.key"
TOKEN_TTL_SECONDS = 7 * 24 * 3600          # 7 天
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


# ==================== 用户存储 ====================

def _load_users() -> List[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data.get("users", [])
    except Exception as e:
        logger.error(f"用户存储读取失败: {e}")
        return []


def _save_users(users: List[dict]) -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), PBKDF2_ROUNDS).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                salt.encode("utf-8"), PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(check, digest)


def user_count() -> int:
    return len(_load_users())


def get_user(username: str) -> Optional[dict]:
    for u in _load_users():
        if u.get("username") == username:
            return u
    return None


def create_user(username: str, password: str, role: Optional[str] = None) -> dict:
    """创建用户；第一个用户自动 admin（并接管 legacy 数据）；密码强度下限 6 位"""
    username = (username or "").strip()
    if not re_valid_username(username):
        raise ValueError("用户名仅允许 3-32 位字母/数字/下划线/中文")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if get_user(username):
        raise ValueError(f"用户已存在: {username}")
    users = _load_users()
    role = role if role in ROLES else ("admin" if not users else "user")
    user = {
        "username": username,
        "password": _hash_password(password),
        "role": role,
        "disabled": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    users.append(user)
    _save_users(users)
    logger.info(f"用户创建: {username} (role={role})")
    if role == "admin" and len(users) == 1:
        migrate_legacy(username)   # 首个管理员接管 legacy 单用户数据
    return public_view(user)


def re_valid_username(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[\w\u4e00-\u9fff]{3,32}", name))


def public_view(user: dict) -> dict:
    return {"username": user.get("username"), "role": user.get("role"),
            "disabled": bool(user.get("disabled")),
            "created_at": user.get("created_at")}


def update_user(username: str, **changes) -> dict:
    """管理接口：改角色/启停/重置密码；不可禁用/降级最后一个可用 admin"""
    users = _load_users()
    target = next((u for u in users if u.get("username") == username), None)
    if target is None:
        raise ValueError(f"用户不存在: {username}")
    new_role = changes.get("role", target.get("role"))
    new_disabled = bool(changes.get("disabled", target.get("disabled")))
    if new_role not in ROLES:
        raise ValueError(f"非法角色: {new_role}（可选 {ROLES}）")
    if target.get("role") == "admin" and (new_role != "admin" or new_disabled):
        active_admins = [u for u in users
                         if u.get("role") == "admin" and not u.get("disabled")
                         and u.get("username") != username]
        if not active_admins:
            raise ValueError("不能禁用或降级最后一个可用管理员")
    if "password" in changes and changes["password"]:
        if len(changes["password"]) < 6:
            raise ValueError("密码至少 6 位")
        target["password"] = _hash_password(changes["password"])
    target["role"] = new_role
    target["disabled"] = new_disabled
    _save_users(users)
    return public_view(target)


def delete_user(username: str) -> None:
    users = _load_users()
    target = next((u for u in users if u.get("username") == username), None)
    if target is None:
        raise ValueError(f"用户不存在: {username}")
    if target.get("role") == "admin":
        active_admins = [u for u in users
                         if u.get("role") == "admin" and not u.get("disabled")
                         and u.get("username") != username]
        if not active_admins:
            raise ValueError("不能删除最后一个可用管理员")
    users = [u for u in users if u.get("username") != username]
    _save_users(users)
    # 数据目录保留（不物理删除），由管理员手工清理
    logger.info(f"用户已删除（数据目录保留）: {username}")


# ==================== 令牌（HMAC 签名，类 JWT） ====================

def _secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes().strip()
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(secret)
    try:  # 尽力限制权限（Windows 忽略）
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def make_token(username: str, role: str) -> str:
    payload = {"sub": username, "role": role,
               "exp": int(time.time()) + TOKEN_TTL_SECONDS,
               "jti": secrets.token_hex(8)}
    body = _b64e(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        expect = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


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
    user = get_user(username)
    if user is None or not _verify_password(password or "", user.get("password", "")):
        raise ValueError("用户名或密码错误")
    if user.get("disabled"):
        raise ValueError("账号已禁用")
    return {"token": make_token(user["username"], user["role"]),
            "user": public_view(user)}


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
