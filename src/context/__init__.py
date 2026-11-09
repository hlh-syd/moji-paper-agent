# -*- coding: utf-8 -*-
"""上下文预算与减压（从 deepseek-harness 移植的四个模式）

- tokens  : 字符密度 token 估算 + 预算解析（对齐 compactThreshold/headroom）
- prune   : 确定性头尾裁剪（围栏保护、段落吸附）
- spill   : 内容寻址溢写存储（全文留档，行内只留头尾）
- shadow  : 压缩摘要影子（前缀指纹 + 侧车持久化，对应 surfaceOp=replace）
- compact : 压缩子代理（8 段结构化输出，两阶段提交）
- assemble: ContextEngine —— 注入块阶梯 + 历史阶梯

模块只依赖标准库与项目内 config/atomic_io，可脱离 FastAPI 单测。
"""
from src.context.assemble import (AssembleStats, ContextBlock, ContextEngine,
                                  HistoryStats)
from src.context.prune import PRUNE_MARKER_TMPL, PruneResult, prune_text
from src.context.spill import SpillRef, SpillStore
from src.context.tokens import (Budget, estimate_tokens, resolve_budget,
                                resolve_model_window)

__all__ = [
    "AssembleStats", "ContextBlock", "ContextEngine", "HistoryStats",
    "PRUNE_MARKER_TMPL", "PruneResult", "prune_text",
    "SpillRef", "SpillStore",
    "Budget", "estimate_tokens", "resolve_budget", "resolve_model_window",
]
