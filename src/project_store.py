"""
用户项目存储：MySQL user_projects 表（权威）+ Redis 缓存（读穿透）
================================================================
- 注册项目时把「项目名称 + 目录地址」写到当前用户名下；
- 列表读取先查 Redis（moji:projects:{username}，EX=5min），未命中回源 MySQL
  并回填；注册/删除后立即失效缓存，保证跨设备/多标签一致；
- Redis 不可用时静默降级为直查 MySQL（项目列表不应因缓存故障不可用）。
"""
import json
import logging
from typing import List

from src import auth_store
from src.auth_store import AuthStoreError

logger = logging.getLogger(__name__)

CACHE_PREFIX = "moji:projects:"
CACHE_TTL = 300

SCHEMA_PROJECTS = """
CREATE TABLE IF NOT EXISTS user_projects (
  id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  username   VARCHAR(32)  NOT NULL,
  name       VARCHAR(64)  NOT NULL,
  path       VARCHAR(512) NOT NULL COMMENT '项目目录绝对地址',
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_user_name (username, name),
  INDEX idx_user (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='用户注册的研究项目（目录地址 + 名称）'
"""


def _ensure_columns() -> None:
    """老库升级：user_projects 补 domain / status / last_opened_at 三列
    （幂等，MySQL 无 ADD COLUMN IF NOT EXISTS）"""
    rows = auth_store._fetch(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='user_projects'",
        (auth_store._conf().mysql_database,))
    have = {r["COLUMN_NAME"] for r in rows}
    if "domain" not in have:
        auth_store._execute("ALTER TABLE user_projects ADD COLUMN domain VARCHAR(32) "
                            "NOT NULL DEFAULT 'auto' COMMENT '研究领域（auto=未指定）' AFTER path")
        logger.info("user_projects 表已补列: domain")
    if "status" not in have:
        auth_store._execute("ALTER TABLE user_projects ADD COLUMN status "
                            "ENUM('active','archived') NOT NULL DEFAULT 'active' "
                            "COMMENT '项目状态' AFTER domain")
        logger.info("user_projects 表已补列: status")
    if "last_opened_at" not in have:
        auth_store._execute("ALTER TABLE user_projects ADD COLUMN last_opened_at "
                            "DATETIME DEFAULT NULL COMMENT '最近进入时间' AFTER status")
        logger.info("user_projects 表已补列: last_opened_at")


def init_schema() -> None:
    """幂等建表 + 老库补列（随 auth_store.init_db 之后调用）"""
    auth_store._execute(SCHEMA_PROJECTS)
    _ensure_columns()


def _cache_get(username: str):
    """命中返回 list；未命中/缓存故障返回 None"""
    try:
        raw = auth_store._redis_call(auth_store._r().get, CACHE_PREFIX + username)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except ValueError:
        return None


def _cache_set(username: str, items: List[dict]) -> None:
    try:
        auth_store._redis_call(auth_store._r().set, CACHE_PREFIX + username,
                               json.dumps(items, ensure_ascii=False), ex=CACHE_TTL)
    except Exception:
        pass  # 缓存写失败不影响主流程


def _cache_drop(username: str) -> None:
    try:
        auth_store._redis_call(auth_store._r().delete, CACHE_PREFIX + username)
    except Exception:
        pass


def list_projects(username: str) -> List[dict]:
    """当前用户的已注册项目（MySQL 权威，Redis cache-aside）"""
    cached = _cache_get(username)
    if cached is not None:
        return cached

    def _iso(v):
        return v.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(v, "strftime") else str(v or "")

    rows = auth_store._fetch(
        "SELECT name, path, domain, status, created_at, last_opened_at "
        "FROM user_projects WHERE username=%s ORDER BY created_at, id", (username,))
    items = [{"name": r["name"], "path": r["path"],
              "domain": r.get("domain") or "auto",
              "status": r.get("status") or "active",
              "created_at": _iso(r.get("created_at")),
              "last_opened_at": _iso(r.get("last_opened_at"))}
             for r in rows]
    _cache_set(username, items)
    return items


def touch_project(username: str, name: str) -> None:
    """进入项目时刷新 last_opened_at（最近使用排序；记录不存在则忽略）"""
    auth_store._execute(
        "UPDATE user_projects SET last_opened_at=NOW() WHERE username=%s AND name=%s",
        (username, name))


def save_project(username: str, name: str, path: str) -> dict:
    """注册/更新项目（同名覆盖目录地址），并失效缓存"""
    auth_store._execute(
        "INSERT INTO user_projects (username, name, path) VALUES (%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE path=VALUES(path), updated_at=NOW()",
        (username, name, path))
    _cache_drop(username)
    auth_store.audit(username, "project", f"register {name} → {path}"[:250])
    return {"name": name, "path": path}


def delete_project(username: str, name: str) -> None:
    """移除项目记录（画像/大纲文件保留），并失效缓存"""
    auth_store._execute("DELETE FROM user_projects WHERE username=%s AND name=%s",
                        (username, name))
    _cache_drop(username)
    auth_store.audit(username, "project", f"delete {name}"[:250])


def get_project(username: str, name: str) -> dict | None:
    for item in list_projects(username):
        if item.get("name") == name:
            return item
    return None
