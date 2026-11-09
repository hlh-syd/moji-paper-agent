"""
用户状态存储（MySQL）：聊天会话/消息 + 界面偏好
================================================
workspace/{owner}.state.json 的继任者：
- conversations / conversation_messages：会话元数据与消息逐条入库，按
  (owner, client_id) / (conversation_id, seq) 幂等 upsert——根治旧单文件
  「前端 800ms 防抖全量重写 + 超 30MB 静默截断每会话只留 20 条」的丢消息问题；
- user_settings：界面偏好（theme/font_size/paper_style/active_* 等全部
  非会话键）随账号漫游；rev 乐观锁计数单独成列（沿用旧 state.rev 语义）。
- 读写入口在 workspace_store 门面（load_state/save_state 签名不变），
  MySQL 不可达时由门面回退旧文件路径；本模块只管 DB，抛 AuthStoreError。
- 一次性迁移见 workspace_store.migrate_owner_file（导入成功后旧文件改名
  .migrated 留存；upsert 键天然幂等，重复导入不会产生重复会话）。
"""
import datetime as _dt
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src import auth_store
from src.auth_store import AuthStoreError

logger = logging.getLogger(__name__)

SCHEMA_SETTINGS = """
CREATE TABLE IF NOT EXISTS user_settings (
  username   VARCHAR(32) NOT NULL PRIMARY KEY,
  rev        INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '工作区乐观锁版本（沿用旧 state.rev 语义）',
  prefs      JSON        NOT NULL COMMENT '界面偏好：theme/font_size/paper_style/active_* 等全部非会话键',
  updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='用户界面偏好（随账号漫游，换浏览器不丢）'
"""

SCHEMA_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS conversations (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  owner       VARCHAR(32)  NOT NULL,
  client_id   VARCHAR(64)  NOT NULL COMMENT '前端会话 id（chat_xxx），幂等对齐键',
  name        VARCHAR(128) NOT NULL DEFAULT '新会话',
  project     VARCHAR(64)  DEFAULT NULL COMMENT '关联项目名（NULL=未绑定）',
  ts          BIGINT UNSIGNED DEFAULT NULL COMMENT '前端时间戳（epoch ms），展示用',
  pinned      TINYINT(1)   NOT NULL DEFAULT 0,
  pinned_at   DATETIME     DEFAULT NULL,
  archived    TINYINT(1)   NOT NULL DEFAULT 0,
  archived_at DATETIME     DEFAULT NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_owner_client (owner, client_id),
  INDEX idx_owner (owner, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='聊天会话元数据（消息在 conversation_messages，外键级联删除）'
"""

SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS conversation_messages (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  conversation_id BIGINT UNSIGNED NOT NULL,
  seq             INT UNSIGNED NOT NULL COMMENT '会话内序号（0 起）',
  role            VARCHAR(16)  NOT NULL COMMENT 'user/assistant/system/tool（放宽为 VARCHAR：未知角色不致整包保存失败）',
  content         MEDIUMTEXT   NOT NULL,
  attachments     JSON         DEFAULT NULL COMMENT '随消息附件（文件名/类型数组）',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_conv_seq (conversation_id, seq),
  CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id)
    REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='聊天消息；保存时整会话替换（DELETE+批量 INSERT 于单事务）'
"""


def init_schema() -> None:
    """幂等建表（随 auth_store.init_db / project_store.init_schema 之后调用）"""
    auth_store._execute(SCHEMA_SETTINGS)
    auth_store._execute(SCHEMA_CONVERSATIONS)
    auth_store._execute(SCHEMA_MESSAGES)


# ==================== 纯函数（单测覆盖） ====================

_PREF_SKIP = ("sessions", "rev")


def _split_state(state: Optional[dict]) -> Tuple[dict, List[dict]]:
    """前端全量 state → (偏好 dict, 会话列表)；sessions/rev 不进偏好。"""
    state = state or {}
    prefs = {k: v for k, v in state.items() if k not in _PREF_SKIP}
    sessions = state.get("sessions") or []
    return prefs, [s for s in sessions if isinstance(s, dict) and s.get("id")]


def _norm_messages(msgs: List[dict]) -> List[Tuple[int, str, str, Optional[str]]]:
    """消息数组 → (seq, role, content, attachments_json) 行元组；容错跳过非 dict 项。"""
    rows = []
    for i, m in enumerate(m for m in (msgs or []) if isinstance(m, dict)):
        att = m.get("attachments")
        rows.append((i, str(m.get("role") or "user"),
                     str(m.get("content") or ""),
                     json.dumps(att, ensure_ascii=False) if att else None))
    return rows


def _to_dt(v: Any) -> Optional[_dt.datetime]:
    """epoch ms / ISO 字符串 / datetime → datetime；解析失败返回 None（列可空）。"""
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(v / 1000)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        return _dt.datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _to_iso(v: Any) -> Optional[str]:
    d = _to_dt(v)
    return d.strftime("%Y-%m-%dT%H:%M:%S") if d else None


# ==================== 读 ====================

def load_owner_state(owner: str) -> Optional[dict]:
    """组装该用户完整工作区 state（偏好 + rev + 全部会话/消息），形状与旧
    state.json 完全一致（前端零改动）；无任何记录返回 None（新用户）。"""
    rows = auth_store._fetch(
        "SELECT rev, prefs FROM user_settings WHERE username=%s", (owner,))
    if not rows:
        return None
    try:
        prefs = json.loads(rows[0]["prefs"] or "{}")
    except ValueError:
        prefs = {}
    if not isinstance(prefs, dict):
        prefs = {}
    prefs.pop("rev", None)
    prefs.pop("sessions", None)
    state: Dict[str, Any] = dict(prefs)
    state["rev"] = int(rows[0]["rev"] or 0)

    sessions: List[dict] = []
    convs = auth_store._fetch(
        "SELECT * FROM conversations WHERE owner=%s ORDER BY id", (owner,))
    for c in convs:
        mrows = auth_store._fetch(
            "SELECT role, content, attachments FROM conversation_messages "
            "WHERE conversation_id=%s ORDER BY seq", (c["id"],))
        msgs = []
        for m in mrows:
            msg: Dict[str, Any] = {"role": m["role"], "content": m["content"]}
            if m.get("attachments"):
                try:
                    att = json.loads(m["attachments"])
                except ValueError:
                    att = None
                if att:
                    msg["attachments"] = att
            msgs.append(msg)
        sessions.append({
            "id": c["client_id"],
            "name": c["name"],
            "project": c["project"],
            "ts": c["ts"],
            "pinned": bool(c["pinned"]),
            "pinnedAt": _to_iso(c["pinned_at"]),
            "archived": bool(c["archived"]),
            "archivedAt": _to_iso(c["archived_at"]),
            "attachments": [],
            "messages": msgs,
        })
    state["sessions"] = sessions
    return state


def list_owners() -> List[str]:
    """有状态记录的用户（偏好表 ∪ 会话表），启动恢复会话用。"""
    rows = auth_store._fetch(
        "SELECT username AS o FROM user_settings "
        "UNION SELECT DISTINCT owner AS o FROM conversations WHERE owner!=''")
    return [r["o"] for r in rows]


# ==================== 写 ====================

def save_owner_state(owner: str, state: dict) -> None:
    """全量保存（幂等）：偏好 upsert + 会话按 client_id upsert + 消息整会话
    替换 + 不在本次载荷中的会话删除（用户在前端删除的会话同步移除）。
    单连接事务：任一步失败整体回滚，不留下半套状态。"""
    prefs, sessions = _split_state(state)
    rev = int((state or {}).get("rev") or 0)
    conn = auth_store._connect()
    try:
        conn.begin()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_settings (username, rev, prefs) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE rev=VALUES(rev), prefs=VALUES(prefs)",
            (owner, rev, json.dumps(prefs, ensure_ascii=False)))
        incoming: List[str] = []
        for s in sessions:
            cid = str(s.get("id")).strip()
            incoming.append(cid)
            cur.execute(
                "INSERT INTO conversations (owner, client_id, name, project, ts, "
                "pinned, pinned_at, archived, archived_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), project=VALUES(project), "
                "ts=VALUES(ts), pinned=VALUES(pinned), pinned_at=VALUES(pinned_at), "
                "archived=VALUES(archived), archived_at=VALUES(archived_at), updated_at=NOW()",
                (owner, cid, str(s.get("name") or "新会话")[:128],
                 s.get("project") or None, int(s["ts"]) if s.get("ts") else None,
                 1 if s.get("pinned") else 0, _to_dt(s.get("pinnedAt")),
                 1 if s.get("archived") else 0, _to_dt(s.get("archivedAt"))))
            cur.execute(
                "SELECT id FROM conversations WHERE owner=%s AND client_id=%s",
                (owner, cid))
            conv_id = cur.fetchone()["id"]
            cur.execute("DELETE FROM conversation_messages WHERE conversation_id=%s",
                        (conv_id,))
            rows = _norm_messages(s.get("messages") or [])
            if rows:
                cur.executemany(
                    "INSERT INTO conversation_messages "
                    "(conversation_id, seq, role, content, attachments) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    [(conv_id, seq, role, content, att) for seq, role, content, att in rows])
        if incoming:
            ph = ",".join(["%s"] * len(incoming))
            cur.execute(f"DELETE FROM conversations WHERE owner=%s "
                        f"AND client_id NOT IN ({ph})", (owner, *incoming))
        else:
            cur.execute("DELETE FROM conversations WHERE owner=%s", (owner,))
        conn.commit()
    except AuthStoreError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise AuthStoreError(f"状态保存失败: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def rename_owner(old: str, new: str) -> None:
    """改名级联：会话与偏好归属随新名（消息经 conversation_id 外键自然跟随）。
    client_id 为前端随机 id，撞 (new, client_id) 唯一键在现实中不可能，
    一旦发生由唯一键拒绝（上层转为「改名失败」）。"""
    auth_store._execute("UPDATE conversations SET owner=%s WHERE owner=%s", (new, old))
    auth_store._execute("UPDATE user_settings SET username=%s WHERE username=%s",
                        (new, old))
    logger.info(f"状态库归属已迁移: {old} → {new}")
