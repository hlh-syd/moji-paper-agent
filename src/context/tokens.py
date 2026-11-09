# -*- coding: utf-8 -*-
"""上下文 token 估算

项目不引入 tiktoken：按字符密度做启发式估算，并**有意高估约 20%**，
使预算判断偏保守（宁可早一点压缩，也不要撑爆窗口被服务端拒绝）。

- 中日韩字符 ≈ 1.3 字/token（含全角标点）
- 其余字符 ≈ 4 字/token（英文/代码的经验值）
- 每条消息另有固定开销（role 包裹、分隔符）

估算只用于「预算」，不用于计费；精确用量以 provider 返回的 usage 为准。
长文本用三段抽样估算 CJK 占比，避免逐字符扫描大文件。
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

CJK_PER_TOKEN = 1.3
ASCII_PER_TOKEN = 4.0
MESSAGE_OVERHEAD = 6

# 双模型窗口的保守默认值；可按模型名在 config.context.model_windows 覆盖
DEFAULT_CONTEXT_WINDOW = 128000

_SAMPLE_HEAD = 1500
_SAMPLE_MID = 1500
_SAMPLE_TAIL = 1500
_SAMPLE_MAX = 6000


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    if 0x20000 <= cp <= 0x2FA1F:      # 扩展 B 及以后
        return True
    return (
        0x2E80 <= cp <= 0x2EFF or     # 部首
        0x3000 <= cp <= 0x303F or     # 中日韩标点
        0x3040 <= cp <= 0x30FF or     # 假名（顺带覆盖）
        0x3400 <= cp <= 0x4DBF or     # 扩展 A
        0x4E00 <= cp <= 0x9FFF or     # 基本汉字
        0xF900 <= cp <= 0xFAFF or     # 兼容汉字
        0xFE30 <= cp <= 0xFE4F or     # 兼容形式
        0xFF00 <= cp <= 0xFFEF        # 全角字符
    )


def cjk_ratio(text: str) -> float:
    """CJK 字符占比（0~1）。超长文本取头/中/尾三段抽样，结果确定性可测。"""
    if not text:
        return 0.0
    n = len(text)
    if n <= _SAMPLE_MAX:
        sample = text
    else:
        mid = n // 2
        sample = (text[:_SAMPLE_HEAD]
                  + text[max(0, mid - _SAMPLE_MID // 2):mid + _SAMPLE_MID // 2]
                  + text[-_SAMPLE_TAIL:])
    if not sample:
        return 0.0
    hits = sum(1 for ch in sample if _is_cjk(ch))
    return hits / len(sample)


def count_cjk(text: str) -> int:
    """精确 CJK 字符数（只在测试/诊断用，长文本请用 cjk_ratio）"""
    return sum(1 for ch in (text or "") if _is_cjk(ch))


def estimate_tokens(text: str, *, cjk_per_token: float = CJK_PER_TOKEN,
                    ascii_per_token: float = ASCII_PER_TOKEN) -> int:
    """估算一段文本的 token 数；空文本为 0。"""
    if not text:
        return 0
    n = len(text)
    ratio = cjk_ratio(text)
    tokens = (n * ratio / max(0.1, cjk_per_token)
              + n * (1.0 - ratio) / max(0.1, ascii_per_token))
    return max(1, int(math.ceil(tokens)))


def _role_content(msg: Any):
    """兼容 dict / 带 role+content 的对象（ConversationMessage）"""
    if isinstance(msg, dict):
        return str(msg.get("role") or ""), str(msg.get("content") or "")
    return (str(getattr(msg, "role", "") or ""),
            str(getattr(msg, "content", "") or ""))


def estimate_message_tokens(msg: Any) -> int:
    role, content = _role_content(msg)
    return MESSAGE_OVERHEAD + estimate_tokens(role) + estimate_tokens(content)


def estimate_messages_tokens(messages: Iterable[Any]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


@dataclass(frozen=True)
class Budget:
    """一个模型的上下文预算（全部是 token）"""
    window: int
    hard_limit: int          # 窗口 - 输出预留 - 安全余量
    soft_limit: int          # 触发压缩/收缩的水位
    reserve_output: int
    headroom: int

    def pressure(self, tokens: int) -> float:
        return (tokens / self.window) if self.window > 0 else 0.0


def resolve_budget(window: int, reserve_output: int, headroom: int,
                   ratio: float = 0.8) -> Budget:
    """对齐 harness 的 resolveCompactSpec：
    soft = min(window*ratio, window - 输出预留 - headroom)，hard 为后者。"""
    window = max(2048, int(window or DEFAULT_CONTEXT_WINDOW))
    reserve_output = max(0, int(reserve_output or 0))
    headroom = max(0, int(headroom or 0))
    hard = max(1024, window - reserve_output - headroom)
    soft = min(int(window * max(0.1, min(0.95, ratio))), hard)
    return Budget(window=window, hard_limit=hard, soft_limit=soft,
                  reserve_output=reserve_output, headroom=headroom)


def chars_for_tokens(text: str, max_tokens: int) -> int:
    """按文本自身密度把 token 预算换算成字符预算（向下取整，偏保守）"""
    if not text or max_tokens <= 0:
        return 0
    est = estimate_tokens(text)
    if est <= 0:
        return 0
    return max(0, int(len(text) * max_tokens / est))


def truncate_to_tokens(text: str, max_tokens: int, *, suffix: str = "") -> str:
    """硬截断到 token 预算内（给摘要子代理的输入封顶用，不做头尾保留）"""
    if not text:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    limit = max(0, chars_for_tokens(text, max_tokens) - len(suffix))
    return text[:limit] + suffix


def resolve_model_window(model_name: Optional[str], *,
                         default_window: int = DEFAULT_CONTEXT_WINDOW,
                         overrides: Optional[Dict[str, int]] = None) -> int:
    """按界面模型名取窗口；未登记一律用默认值（宁大勿小，靠 headroom 兜底）"""
    if overrides and model_name:
        hit = overrides.get(model_name)
        if hit:
            try:
                return max(2048, int(hit))
            except (TypeError, ValueError):
                pass
    return max(2048, int(default_window or DEFAULT_CONTEXT_WINDOW))
