# -*- coding: utf-8 -*-
"""压缩摘要的「影子」状态

会话原文永不被删除；压缩只产生一个影子：摘要文本 + 覆盖到哪个下标。
投影时用「摘要 + 从 through_index 起的原文」替代原前缀——即 harness
surfaceOp=replace 的最小等价物。

影子存活的两个条件（任一不满足即作废，宁可再生一次也不冒错配风险）：
1. 当前消息列表的前缀指纹与生成摘要时一致；
2. 侧车文件可读且版本号匹配。

之所以要持久化：前端每次防抖保存都会让后端重建会话，纯内存的影子会被冲掉。
侧车放在 workspace/context/ 下，按会话 id 哈希命名，随内容指纹自动失效。
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.atomic_io import write_json_atomic

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
_SESSION_HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def _role_content(msg: Any):
    if isinstance(msg, dict):
        return str(msg.get("role") or ""), str(msg.get("content") or "")
    return (str(getattr(msg, "role", "") or ""),
            str(getattr(msg, "content", "") or ""))


def prefix_digest(messages: List[Any], through: int) -> str:
    """[0, through) 段消息的内容指纹（角色 + 全文，逐条分隔）"""
    h = hashlib.sha256()
    for msg in messages[:max(0, through)]:
        role, content = _role_content(msg)
        h.update(role.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(content.encode("utf-8", "replace"))
        h.update(b"\x01")
    return h.hexdigest()[:32]


@dataclass
class Shadow:
    through_index: int
    prefix_digest: str
    summary_json: Dict[str, str] = field(default_factory=dict)
    summary_md: str = ""
    created_at: str = ""
    source_tokens: int = 0
    model: str = ""
    source_locator: str = ""     # 被归档原文的溢写定位符（审计用）
    version: int = SIDECAR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "through_index": self.through_index,
            "prefix_digest": self.prefix_digest,
            "summary_json": self.summary_json,
            "summary_md": self.summary_md,
            "created_at": self.created_at,
            "source_tokens": self.source_tokens,
            "model": self.model,
            "source_locator": self.source_locator,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["Shadow"]:
        try:
            through = int(data.get("through_index") or 0)
            digest = str(data.get("prefix_digest") or "")
            if through <= 0 or not digest:
                return None
            return cls(
                through_index=through,
                prefix_digest=digest,
                summary_json=dict(data.get("summary_json") or {}),
                summary_md=str(data.get("summary_md") or ""),
                created_at=str(data.get("created_at") or ""),
                source_tokens=int(data.get("source_tokens") or 0),
                model=str(data.get("model") or ""),
                source_locator=str(data.get("source_locator") or ""),
                version=int(data.get("version") or SIDECAR_VERSION),
            )
        except (TypeError, ValueError):
            return None

    def describe(self) -> Dict[str, Any]:
        return {
            "through_index": self.through_index,
            "summary_chars": len(self.summary_md or ""),
            "source_tokens": self.source_tokens,
            "created_at": self.created_at,
            "model": self.model,
            "source_locator": self.source_locator,
            "version": self.version,
        }


def resolve(shadow: Optional[Shadow], messages: List[Any]) -> Optional[Shadow]:
    """影子是否仍然对得上当前消息前缀；对不上返回 None（自动作废）"""
    if shadow is None:
        return None
    if shadow.version != SIDECAR_VERSION:
        return None
    n = shadow.through_index
    if n <= 0 or n > len(messages):
        return None
    return shadow if prefix_digest(messages, n) == shadow.prefix_digest else None


# ---------- 侧车持久化 ----------
def _default_base() -> Path:
    from src import scoping
    return scoping.context_dir()


def _session_hash(session_id: str) -> str:
    return hashlib.sha1((session_id or "").encode("utf-8")).hexdigest()[:16]


def sidecar_path(session_id: str, base_dir=None) -> Path:
    base = Path(base_dir) if base_dir else _default_base()
    return base / f"{_session_hash(session_id)}.json"


def load_sidecar(session_id: str, base_dir=None) -> Optional[Shadow]:
    path = sidecar_path(session_id, base_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.info("压缩侧车读取失败（忽略）: %s", e)
        return None
    if not isinstance(data, dict) or data.get("session_id") != session_id:
        return None
    return Shadow.from_dict(data.get("shadow") or {})


def save_sidecar(session_id: str, shadow: Shadow, base_dir=None) -> None:
    path = sidecar_path(session_id, base_dir)
    payload = {
        "version": SIDECAR_VERSION,
        "session_id": session_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "shadow": shadow.to_dict(),
    }
    try:
        write_json_atomic(path, payload)
    except OSError as e:
        logger.warning("压缩侧车写入失败（忽略）: %s", e)


def clear_sidecar(session_id: str, base_dir=None) -> None:
    try:
        sidecar_path(session_id, base_dir).unlink()
    except OSError:
        pass
