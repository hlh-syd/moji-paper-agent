# -*- coding: utf-8 -*-
"""确定性头尾裁剪（零 LLM）

移植 harness 的 spill/retention 思路：超预算的文本保留「头 + 尾」，中间以
省略标记替代，并可把全文交给 spill 存储留档。三条护栏：

1. 切点吸附到最近的换行，避免把句子劈成两半；
2. 切点不落在 ``` / $$ / \\[ \\] 围栏内部，避免切碎代码或公式；
3. 任何异常都回退为「原样返回」，绝不因为裁剪把内容弄丢。
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.context.tokens import chars_for_tokens, estimate_tokens

logger = logging.getLogger(__name__)

PRUNE_MARKER_TMPL = "\n\n……【此处省略 {omitted} 字】……\n\n"

Interval = Tuple[int, int]


@dataclass
class PruneResult:
    text: str
    omitted_chars: int = 0
    pruned: bool = False
    locator: Optional[str] = None
    tokens_before: int = 0
    tokens_after: int = 0


def _pair_same(text: str, marker: str) -> List[Interval]:
    spans: List[Interval] = []
    pos, n = 0, len(marker)
    while True:
        a = text.find(marker, pos)
        if a < 0:
            break
        b = text.find(marker, a + n)
        if b < 0:
            spans.append((a, len(text)))
            break
        spans.append((a, b + n))
        pos = b + n
    return spans


def _pair_diff(text: str, open_m: str, close_m: str) -> List[Interval]:
    spans: List[Interval] = []
    pos = 0
    while True:
        a = text.find(open_m, pos)
        if a < 0:
            break
        b = text.find(close_m, a + len(open_m))
        if b < 0:
            spans.append((a, len(text)))
            break
        spans.append((a, b + len(close_m)))
        pos = b + len(close_m)
    return spans


def fence_intervals(text: str, *, protect: bool = True) -> List[Interval]:
    """代码块/公式块的字符区间（已排序合并）；protect=False 返回空表"""
    if not protect or not text:
        return []
    spans = (_pair_same(text, "```")
             + _pair_same(text, "$$")
             + _pair_diff(text, "\\[", "\\]"))
    if not spans:
        return []
    spans.sort()
    merged: List[Interval] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _containing(intervals: List[Interval], pos: int) -> Optional[Interval]:
    """pos 严格落在哪个围栏内部（返回该围栏区间）"""
    for s, e in intervals:
        if s < pos < e:
            return (s, e)
        if s > pos:
            break
    return None


def _inside(intervals: List[Interval], pos: int) -> bool:
    return _containing(intervals, pos) is not None


def _adjust_head(pos: int, intervals: List[Interval], target: int) -> int:
    """头部切点落在围栏内时的外移规则：优先切在围栏之前"""
    hit = _containing(intervals, pos)
    if not hit:
        return pos
    s, e = hit
    if s >= target * 0.25:
        return s               # 围栏前还留有足够头部
    if e <= target * 1.25:
        return e               # 围栏几乎占满头部，索性整段保留
    return pos                 # 两头都不划算：硬切，交给长度护栏


def _adjust_tail(pos: int, intervals: List[Interval], target: int,
                 text_len: int) -> int:
    """尾部起点落在围栏内时的外移规则：优先从围栏之后开始"""
    hit = _containing(intervals, pos)
    if not hit:
        return pos
    s, e = hit
    if text_len - e >= target * 0.25:
        return e               # 围栏之后仍有足够尾部
    return s                   # 否则整段围栏留在尾部


def _prev_safe_newline(text: str, pos: int, intervals: List[Interval],
                       window: int) -> Optional[int]:
    """pos 之前最近的换行（返回换行后一位），跳过围栏内部的换行"""
    lo = max(0, pos - max(0, window))
    idx = text.rfind("\n", lo, pos)
    while idx != -1:
        if not _inside(intervals, idx):
            return idx + 1
        idx = text.rfind("\n", lo, idx)
    return None


def _next_safe_newline(text: str, pos: int, intervals: List[Interval],
                       window: int) -> Optional[int]:
    """pos 之后最近的换行（返回换行后一位），跳过围栏内部的换行"""
    hi = min(len(text), pos + max(0, window))
    idx = text.find("\n", pos, hi)
    while idx != -1:
        if not _inside(intervals, idx):
            return idx + 1
        idx = text.find("\n", idx + 1, hi)
    return None


def prune_text(text: str, max_tokens: int, *, head_ratio: float = 0.5,
               snap_window: int = 120, protect_fences: bool = True,
               marker_tmpl: str = PRUNE_MARKER_TMPL,
               spill=None, spill_namespace: str = "chat",
               min_omitted_chars: int = 80) -> PruneResult:
    """把 text 裁到约 max_tokens 以内，保留头尾并插入省略标记。

    未超预算、无法有效缩短、或内部异常时一律原样返回（pruned=False）。
    """
    tokens_before = estimate_tokens(text)
    if not text or tokens_before <= max_tokens:
        return PruneResult(text=text, tokens_before=tokens_before,
                           tokens_after=tokens_before)

    locator: Optional[str] = None
    if spill is not None:
        try:
            ref = spill.save_text(text, namespace=spill_namespace)
            locator = getattr(ref, "locator", None)
        except Exception as e:  # noqa: BLE001 - 留档失败绝不影响裁剪
            logger.warning("裁剪留档失败（忽略）: %s", e)

    try:
        marker_probe = marker_tmpl.format(omitted=len(text))
        body_tokens = max_tokens - estimate_tokens(marker_probe)
        if body_tokens <= 0:
            return PruneResult(text=text, locator=locator,
                               tokens_before=tokens_before,
                               tokens_after=tokens_before)
        body_chars = chars_for_tokens(text, body_tokens)
        head_target = max(0, int(body_chars * max(0.05, min(0.95, head_ratio))))
        tail_target = max(0, body_chars - head_target)

        intervals = fence_intervals(text, protect=protect_fences)

        head_end = _adjust_head(head_target, intervals, head_target)
        snapped = _prev_safe_newline(text, head_end, intervals, snap_window)
        if snapped is not None:
            head_end = snapped

        tail_start = _adjust_tail(len(text) - tail_target, intervals,
                                  tail_target, len(text))
        snapped_tail = _next_safe_newline(text, tail_start, intervals, snap_window)
        if snapped_tail is not None:
            tail_start = snapped_tail

        if head_end < 0:
            head_end = 0
        if tail_start > len(text):
            tail_start = len(text)
        if tail_start <= head_end:
            return PruneResult(text=text, locator=locator,
                               tokens_before=tokens_before,
                               tokens_after=tokens_before)

        omitted = tail_start - head_end
        if omitted < min_omitted_chars:
            return PruneResult(text=text, locator=locator,
                               tokens_before=tokens_before,
                               tokens_after=tokens_before)

        composed = text[:head_end] + marker_tmpl.format(omitted=omitted) + text[tail_start:]
        if len(composed) >= len(text):
            return PruneResult(text=text, locator=locator,
                               tokens_before=tokens_before,
                               tokens_after=tokens_before)

        tokens_after = estimate_tokens(composed)
        return PruneResult(text=composed, omitted_chars=omitted, pruned=True,
                           locator=locator, tokens_before=tokens_before,
                           tokens_after=tokens_after)
    except Exception as e:  # noqa: BLE001 - 裁剪是优化，失败就回退原文
        logger.warning("文本裁剪异常，回退原文: %s", e)
        return PruneResult(text=text, locator=locator,
                           tokens_before=tokens_before,
                           tokens_after=tokens_before)


def prune_message_content(content: str, cap_tokens: int) -> PruneResult:
    """早期消息的确定性上限：头尾各半，失败回退原文"""
    return prune_text(content, cap_tokens, head_ratio=0.5, snap_window=60)
