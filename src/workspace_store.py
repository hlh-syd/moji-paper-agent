"""
工作区持久化存储
保存/恢复完整的用户工作状态：
  - 工作区根目录（项目扫描位置）
  - 当前选中项目
  - 全部会话（消息、附件、名称）
  - 参考论文选择、总字数设置
持久化为 workspace/state.json；服务重启后前端与后端(对话上下文)均可恢复。
生成物（项目画像/大纲JSON/参考论文解析/docx）本就落盘，见各自目录。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

def STATE_FILE() -> Path:
    """按当前请求 owner 取状态文件（多用户隔离；legacy=workspace/state.json）"""
    from src import scoping
    return scoping.workspace_state_file()
MAX_STATE_BYTES = 30 * 1024 * 1024  # 30MB 防护


def load_state() -> Optional[Dict[str, Any]]:
    sf = STATE_FILE()
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"工作区状态读取失败（忽略并继续）: {e}")
        return None


def save_state(state: Dict[str, Any]) -> str:
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
    sf.write_text(text, encoding="utf-8")
    logger.info(f"工作区状态已保存（{len(text) // 1024}KB）")
    return str(STATE_FILE)
