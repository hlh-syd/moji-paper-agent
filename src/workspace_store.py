"""
工作区状态持久化（门面）
========================
读写签名不变（load_state/save_state），内部改为 MySQL 状态库（state_store：
conversations / conversation_messages / user_settings 三表，会话逐条入库，
根治旧单文件「全量重写 + 30MB 静默截断丢消息」）；MySQL 不可达时自动回退
旧 workspace/{owner}.state.json 文件路径（降级可用）。

旧文件 → MySQL 的一次性迁移：migrate_owner_file（服务启动时逐用户执行，
幂等；成功后原文件改名 .migrated 留存）。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ==================== 文件回退（MySQL 不可用时的降级路径） ====================

def STATE_FILE() -> Path:
    """按当前请求 owner 取状态文件（多用户隔离；legacy=workspace/state.json）"""
    from src import scoping
    return scoping.workspace_state_file()

MAX_STATE_BYTES = 30 * 1024 * 1024  # 30MB 防护


def _load_state_file() -> Optional[Dict[str, Any]]:
    sf = STATE_FILE()
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"工作区状态读取失败（忽略并继续）: {e}")
        return None


def _save_state_file(state: Dict[str, Any]) -> str:
    sf = STATE_FILE()
    sf.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1)
    if len(text.encode("utf-8")) > MAX_STATE_BYTES:
        # 超限时压缩会话消息（每会话只留最近20条）
        trimmed = dict(state)
        for s in trimmed.get("sessions", []):
            msgs = s.get("messages", [])
            s["messages"] = msgs[-20:]
        text = json.dumps(trimmed, ensure_ascii=False, indent=1)
    # 原子写：前端 800ms 防抖保存 + 并发标签页，崩溃/中断不会留下半截 JSON
    from src.atomic_io import write_text_atomic
    write_text_atomic(sf, text)
    logger.info(f"工作区状态已保存到文件（{len(text) // 1024}KB）")
    return str(sf)


# ==================== 门面：DB 优先，文件回退 ====================

def load_state() -> Optional[Dict[str, Any]]:
    """当前用户完整工作区 state（偏好 + rev + 会话/消息），形状与旧 state.json
    一致；新用户（无记录无文件）返回 None。"""
    from src.scoping import current_owner
    try:
        from src import state_store
        return state_store.load_owner_state(current_owner())
    except Exception as e:  # AuthStoreError（MySQL/Redis 不可达）→ 文件降级
        logger.warning(f"状态库不可用，回退 state.json: {e}")
        return _load_state_file()


def save_state(state: Dict[str, Any]) -> str:
    """保存当前用户工作区状态；返回落点描述（作为 PUT 响应 saved_to）。"""
    from src.scoping import current_owner
    try:
        from src import state_store
        state_store.save_owner_state(current_owner(), state)
        return f"mysql://user_settings/{current_owner() or 'legacy'}"
    except Exception as e:
        logger.warning(f"状态库不可用，回退 state.json: {e}")
        return _save_state_file(state)


# ==================== 旧文件一次性迁移 ====================

def migrate_owner_file(owner: str) -> bool:
    """workspace/{owner}.state.json → MySQL 一次性迁移（幂等）。
    成功后原文件改名 .migrated 留存，杜绝「旧文件回灌覆盖新数据」；
    MySQL 不可达时保持原文件不动，下次启动重试。"""
    from src import scoping, state_store
    from src.auth_store import AuthStoreError
    sf = (scoping.BASE_DIR / "workspace" /
          ("state.json" if not owner else f"{scoping.safe_name(owner)}.state.json"))
    if not sf.exists():
        return False
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"迁移读取失败（跳过）{sf.name}: {e}")
        return False
    try:
        state_store.save_owner_state(owner, state)
    except AuthStoreError as e:
        logger.warning(f"迁移写入失败（保持原文件，下次重试）{owner or 'legacy'}: {e}")
        return False
    try:
        sf.rename(sf.with_name(sf.name + ".migrated"))
    except Exception as e:
        logger.warning(f"迁移后改名失败（不影响已入库数据）: {e}")
    logger.info(f"用户 {owner or 'legacy'} 工作区状态已迁移到 MySQL"
                f"（会话 {len(state.get('sessions') or [])} 个）")
    return True
