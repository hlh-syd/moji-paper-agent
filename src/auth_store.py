"""
认证存储层：MySQL 用户库 + Redis 会话
====================================
- MySQL（本地 root）：库 moji，表 users（账号/密码哈希/角色/启停）+ auth_audit（登录审计）。
  首次调用 init_db() 自动建库建表；表空时注入种子账号（admin/demo_user）并迁移旧 users.json。
- Redis：服务端会话令牌（token = secrets.token_urlsafe），键 moji:session:{token} →
  用户 JSON，EX = TTL。登出/改密/禁用即删键踢下线。Redis 自身 AOF+RDB 持久化，
  重启后未过期会话仍然有效。
- 连接策略：MySQL 每次操作短连接（本地延迟可忽略，天然线程安全）；Redis 走
  redis-py 内置连接池。二者不可达统一抛 AuthStoreError，由上层转 503。
"""
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import List, Optional

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

_CONF = None            # AuthStoreConfig（register_config 注入）
_redis = None           # 惰性全局 Redis 客户端（自带连接池）
_init_lock = threading.Lock()
_initialized = False

SESSION_PREFIX = "moji:session:"


class AuthStoreError(RuntimeError):
    """MySQL/Redis 不可达或操作失败（上层转 HTTP 503）"""


def register_config(conf) -> None:
    """注入 AuthStoreConfig；随后第一次数据库操作会触发 init_db。"""
    global _CONF
    _CONF = conf


def _conf():
    global _CONF
    if _CONF is None:  # 未注入则用默认配置（config.AuthStoreConfig 默认值）
        from src.config import AuthStoreConfig
        _CONF = AuthStoreConfig()
    return _CONF


# ==================== MySQL ====================

def _connect(server: bool = False) -> pymysql.connections.Connection:
    c = _conf()
    try:
        return pymysql.connect(
            host=c.mysql_host, port=c.mysql_port, user=c.mysql_user,
            password=c.mysql_password, charset="utf8mb4", autocommit=True,
            database=None if server else c.mysql_database,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5, read_timeout=10, write_timeout=10,
        )
    except pymysql.MySQLError as e:
        raise AuthStoreError(f"MySQL 连接失败: {e}") from e


def _fetch(sql: str, args: tuple = ()) -> List[dict]:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    except pymysql.MySQLError as e:
        raise AuthStoreError(f"MySQL 查询失败: {e}") from e


def _execute(sql: str, args: tuple = ()) -> int:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.rowcount
    except pymysql.MySQLError as e:
        raise AuthStoreError(f"MySQL 写入失败: {e}") from e


# ==================== Redis ====================

def _r():
    global _redis
    if _redis is None:
        import redis as redis_lib
        try:
            _redis = redis_lib.Redis.from_url(
                _conf().redis_url, decode_responses=True, protocol=2,
                socket_connect_timeout=5, socket_timeout=5)
            _redis.ping()
        except Exception as e:
            _redis = None
            raise AuthStoreError(f"Redis 连接失败: {e}") from e
    return _redis


def _redis_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AuthStoreError:
        raise
    except Exception as e:
        global _redis
        _redis = None   # 连接可能已坏，下次重建
        raise AuthStoreError(f"Redis 操作失败: {e}") from e


# ==================== 建库 / 建表 / 种子注入 ====================

SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(32)  NOT NULL UNIQUE,
  display_name  VARCHAR(64)  DEFAULT NULL COMMENT '展示名（头像旁显示，空则用 username）',
  avatar        MEDIUMTEXT   DEFAULT NULL COMMENT '头像 data URL（前端已压缩，上限 400KB）',
  password_hash VARCHAR(128) NOT NULL COMMENT 'salt$pbkdf2-hmac-sha256',
  role          ENUM('admin','user') NOT NULL DEFAULT 'user',
  disabled      TINYINT(1)   NOT NULL DEFAULT 0,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='墨记账号（RBAC）'
"""

SCHEMA_AUDIT = """
CREATE TABLE IF NOT EXISTS auth_audit (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  username   VARCHAR(32) NOT NULL,
  action     VARCHAR(16) NOT NULL COMMENT 'register/login/logout/deny/update/delete',
  detail     VARCHAR(255) DEFAULT '',
  created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_username_time (username, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='认证审计日志'
"""


def _ensure_columns() -> None:
    """老库升级：users 表补 display_name / avatar 两列（幂等，MySQL 无 ADD COLUMN IF NOT EXISTS）"""
    rows = _fetch(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='users'", (_conf().mysql_database,))
    have = {r["COLUMN_NAME"] for r in rows}
    if "display_name" not in have:
        _execute("ALTER TABLE users ADD COLUMN display_name VARCHAR(64) DEFAULT NULL "
                 "COMMENT '展示名（头像旁显示，空则用 username）' AFTER username")
        logger.info("users 表已补列: display_name")
    if "avatar" not in have:
        _execute("ALTER TABLE users ADD COLUMN avatar MEDIUMTEXT DEFAULT NULL "
                 "COMMENT '头像 data URL（前端已压缩，上限 400KB）' AFTER display_name")
        logger.info("users 表已补列: avatar")


def init_db(force: bool = False) -> None:
    """建库建表 + 种子注入 + 旧 users.json 迁移。幂等；进程内只跑一次（force 除外）。"""
    global _initialized
    with _init_lock:
        if _initialized and not force:
            return
        c = _conf()
        with _connect(server=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{c.mysql_database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cur.execute(f"USE `{c.mysql_database}`")
            cur.execute(SCHEMA_USERS)
            cur.execute(SCHEMA_AUDIT)
        _ensure_columns()
        count = user_count()
        migrated = _migrate_users_json()
        if migrated:
            count = user_count()
        if count == 0:
            from src import auth as _auth  # 哈希函数在 auth 层，避免循环：此处延迟导入
            _execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)",
                (c.seed_admin, _auth.hash_password(c.seed_admin_password), "admin"))
            _execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)",
                (c.seed_user, _auth.hash_password(c.seed_user_password), "user"))
            logger.info(f"种子账号已注入: {c.seed_admin}/admin, {c.seed_user}/user")
            # 种子管理员接管单用户 legacy 数据（projects/workspace 等）
            try:
                _auth.migrate_legacy(c.seed_admin)
            except Exception as e:
                logger.warning(f"legacy 迁移跳过: {e}")
        _initialized = True
        logger.info(f"认证存储就绪: MySQL 库 {c.mysql_database}（{user_count()} 个账号）"
                    f" + Redis 会话（{_r().dbsize()} 键）")


def _migrate_users_json() -> int:
    """旧 workspace/users.json 一次性导入 MySQL（按用户名去重）。"""
    legacy = Path(__file__).resolve().parents[1] / "workspace" / "users.json"
    if not legacy.exists():
        return 0
    try:
        users = json.loads(legacy.read_text(encoding="utf-8")).get("users", [])
    except Exception as e:
        logger.warning(f"users.json 解析失败，跳过迁移: {e}")
        return 0
    moved = 0
    for u in users:
        if get_user(u.get("username", "")):
            continue
        try:
            _execute(
                "INSERT INTO users (username, password_hash, role, disabled, created_at) "
                "VALUES (%s,%s,%s,%s,COALESCE(%s,NOW()))",
                (u["username"], u["password"], u.get("role", "user"),
                 1 if u.get("disabled") else 0, u.get("created_at")))
            moved += 1
        except Exception as e:
            logger.warning(f"迁移用户 {u.get('username')} 失败: {e}")
    if moved:
        legacy.rename(legacy.with_suffix(".json.migrated"))
        logger.info(f"users.json → MySQL 迁移完成（{moved} 个账号，原文件改名留存）")
    return moved


# ==================== 用户 CRUD ====================

def user_count() -> int:
    rows = _fetch("SELECT COUNT(*) AS n FROM users")
    return int(rows[0]["n"]) if rows else 0


def list_users() -> List[dict]:
    rows = _fetch("SELECT id, username, display_name, avatar, password_hash AS password, role, disabled, "
                  "created_at, updated_at FROM users ORDER BY id")
    return [_normalize(r) for r in rows]


def get_user(username: str) -> Optional[dict]:
    rows = _fetch("SELECT id, username, display_name, avatar, password_hash AS password, role, disabled, "
                  "created_at FROM users WHERE username=%s", (username,))
    return _normalize(rows[0]) if rows else None


def insert_user(username: str, password_hash: str, role: str) -> None:
    _execute("INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)",
             (username, password_hash, role))
    audit(username, "register", f"role={role}")


def update_user_row(username: str, *, role: Optional[str] = None,
                    disabled: Optional[bool] = None,
                    password_hash: Optional[str] = None) -> None:
    sets, args = ["updated_at=NOW()"], []
    if role is not None:
        sets.append("role=%s"); args.append(role)
    if disabled is not None:
        sets.append("disabled=%s"); args.append(1 if disabled else 0)
    if password_hash is not None:
        sets.append("password_hash=%s"); args.append(password_hash)
    args.append(username)
    _execute(f"UPDATE users SET {', '.join(sets)} WHERE username=%s", tuple(args))
    audit(username, "update", f"role={role} disabled={disabled} password={'***' if password_hash else ''}")


def delete_user_row(username: str) -> None:
    _execute("DELETE FROM users WHERE username=%s", (username,))
    audit(username, "delete", "")
    revoke_user_sessions(username)


def update_profile(username: str, *, display_name: Optional[str] = None,
                   avatar: Optional[str] = None) -> None:
    """用户自助修改资料（头像）。传空串表示清空该字段；不踢会话。"""
    sets, args = ["updated_at=NOW()"], []
    if avatar is not None:
        sets.append("avatar=%s"); args.append(avatar or None)
    if len(sets) == 1:
        return
    args.append(username)
    _execute(f"UPDATE users SET {', '.join(sets)} WHERE username=%s", tuple(args))
    audit(username, "profile", f"avatar={'set' if avatar else ''}")


def rename_user(old: str, new: str) -> None:
    """改名：users.username 唯一键 + 用户项目记录一并更新（新名须先查重）。
    会话与数据目录由上层 auth.rename_username 级联处理。"""
    _execute("UPDATE users SET username=%s, updated_at=NOW() WHERE username=%s", (new, old))
    _execute("UPDATE user_projects SET username=%s WHERE username=%s", (new, old))
    audit(old, "rename", f"{old} → {new}")


def active_admin_count(exclude: str = "") -> int:
    rows = _fetch(
        "SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0 AND username!=%s",
        (exclude,))
    return int(rows[0]["n"]) if rows else 0


def _normalize(row: dict) -> dict:
    """DATETIME → ISO 字符串；与旧 users.json 字段形状对齐（auth.public_view 无感切换）。"""
    out = dict(row)
    for k in ("created_at", "updated_at"):
        v = out.get(k)
        out[k] = v.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(v, "strftime") else (v or "")
    for k in ("display_name", "avatar"):
        if k in out and out[k] is None:
            out[k] = ""
    out["disabled"] = bool(out.get("disabled"))
    return out


# ==================== 会话（Redis） ====================

def create_session(username: str, role: str) -> dict:
    """登录成功 → 生成服务端会话 token（Redis 持久化，EX 兜底过期）"""
    token = secrets.token_urlsafe(32)
    payload = {"sub": username, "role": role,
               "iat": int(time.time()), "exp": int(time.time()) + _conf().session_ttl_seconds}
    ttl = _conf().session_ttl_seconds
    _redis_call(_r().set, SESSION_PREFIX + token, json.dumps(payload, ensure_ascii=False), ex=ttl)
    audit(username, "login", f"session={token[:8]}…")
    return {"token": token, "payload": payload}


def get_session(token: str) -> Optional[dict]:
    raw = _redis_call(_r().get, SESSION_PREFIX + token)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if payload.get("exp", 0) < time.time():
        revoke_session(token)
        return None
    return payload


def revoke_session(token: str) -> None:
    if token:
        _redis_call(_r().delete, SESSION_PREFIX + token)


def revoke_session_by_user(token: str, username: str) -> None:
    """登出专用：仅当会话属于该用户时删除（幂等）"""
    payload = get_session(token)
    if payload and payload.get("sub") == username:
        revoke_session(token)
        audit(username, "logout", f"session={token[:8]}…")


def revoke_user_sessions(username: str) -> int:
    """改密/禁用/删除用户 → 踢掉其全部在线会话"""
    r = _r()
    killed = 0
    for key in _redis_call(r.scan_iter, SESSION_PREFIX + "*", 200):
        raw = _redis_call(r.get, key)
        try:
            if raw and json.loads(raw).get("sub") == username:
                _redis_call(r.delete, key)
                killed += 1
        except ValueError:
            continue
    if killed:
        logger.info(f"用户 {username} 的 {killed} 个会话已吊销")
    return killed


def session_stats() -> dict:
    """在线会话统计（管理/诊断用）"""
    r = _r()
    total = 0
    users: dict = {}
    for key in _redis_call(r.scan_iter, SESSION_PREFIX + "*", 200):
        raw = _redis_call(r.get, key)
        try:
            sub = json.loads(raw).get("sub", "?") if raw else "?"
        except ValueError:
            sub = "?"
        total += 1
        users[sub] = users.get(sub, 0) + 1
    return {"sessions": total, "by_user": users}


# ==================== 审计 ====================

def audit(username: str, action: str, detail: str = "") -> None:
    """尽力写入审计表；失败只记日志不影响主流程"""
    try:
        _execute("INSERT INTO auth_audit (username, action, detail) VALUES (%s,%s,%s)",
                 (username, action, detail[:255]))
    except AuthStoreError as e:
        logger.warning(f"审计写入失败: {e}")
