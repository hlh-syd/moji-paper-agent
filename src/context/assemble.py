# -*- coding: utf-8 -*-
"""上下文装配引擎

两条互相独立的预算阶梯：

1. **注入块**（项目主体/附件/知识库/选段/拖入文件/文件夹/视觉报告）
   per-block 裁剪 → 全局等比收缩 → 按优先级丢弃 → 追加省略说明。全程零 LLM。

2. **对话历史**
   Tier0 原文 → Tier1 早期消息确定性裁剪 → Tier2 压缩子代理产出 8 段摘要
   → Tier3 收缩滑动窗口 → 硬裁兜底。

两条阶梯都满足同一组不变量：
- 原文永不被删除（裁剪只作用于投影）；
- 任何异常都回退到旧行为；
- 每一次决策都记进 stats，可经 /api/context/stats 查看。
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.context import shadow as shadow_mod
from src.context.compact import SUMMARY_PREAMBLE, render_summary_md
from src.context.prune import prune_message_content, prune_text
from src.context.spill import SpillStore
from src.context.tokens import (Budget, estimate_messages_tokens,
                                estimate_tokens, resolve_budget,
                                resolve_model_window)

logger = logging.getLogger(__name__)

MIN_BLOCK_TOKENS = 400          # 任何注入块收缩后的下限
_ROLE_NAMES = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}


def _role_content(msg: Any) -> Tuple[str, str]:
    if isinstance(msg, dict):
        return str(msg.get("role") or ""), str(msg.get("content") or "")
    return (str(getattr(msg, "role", "") or ""),
            str(getattr(msg, "content", "") or ""))


@dataclass
class ContextBlock:
    """一个待注入的上下文块"""
    key: str
    title: str                 # 注入时的标题（含 #）
    text: str
    priority: int = 2          # 0=不可删，数字越大越先丢
    budget: Optional[int] = None
    prunable: bool = True
    spill: bool = True


@dataclass
class BlockStat:
    key: str
    title: str
    priority: int
    tokens_before: int = 0
    tokens_after: int = 0
    pruned: bool = False
    dropped: bool = False
    prunable: bool = True
    omitted_chars: int = 0
    locator: Optional[str] = None
    text: str = ""
    original: str = ""          # 原文，二次收缩时从这里重裁（避免嵌套省略标记）
    source_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after, "pruned": self.pruned,
            "dropped": self.dropped, "omitted_chars": self.omitted_chars,
            "source_chars": self.source_chars, "locator": self.locator,
        }


@dataclass
class AssembleStats:
    dry_run: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    limit: int = 0
    blocks: List[BlockStat] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "limit": self.limit,
            "blocks": [b.to_dict() for b in self.blocks],
            "dropped": list(self.dropped),
        }


@dataclass
class HistoryStats:
    mode: str = "empty"        # empty|verbatim|pruned|compacted|truncated
    messages: int = 0
    kept: int = 0              # 最近窗口保留的原文条数
    shadowed: int = 0          # 被摘要覆盖的条数
    tokens: int = 0
    budget: int = 0
    compacted: bool = False
    locator: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode, "messages": self.messages, "kept": self.kept,
            "shadowed": self.shadowed, "tokens": self.tokens,
            "budget": self.budget, "compacted": self.compacted,
            "locator": self.locator, "note": self.note,
        }


def _render_plain(messages: Iterable[Any]) -> str:
    lines = []
    for msg in messages:
        role, content = _role_content(msg)
        lines.append(f"{_ROLE_NAMES.get(role, role or '消息')}: {content}")
    return "\n\n".join(lines)


class ContextEngine:
    def __init__(self, config: Any, *,
                 messages_provider: Callable[[str], List[Any]],
                 summarize_fn: Optional[Callable[[str, str], Any]] = None,
                 spill_store: Optional[SpillStore] = None,
                 model_name: Optional[str] = None,
                 shadow_base_dir=None):
        self.config = config
        self._messages = messages_provider
        self._summarize_fn = summarize_fn
        self.model_name = model_name
        self.spill = spill_store
        self.shadow_base_dir = shadow_base_dir
        self._locks: Dict[str, asyncio.Lock] = {}
        self._throttle: Dict[str, Tuple[float, int]] = {}
        self._last_assemble: Dict[str, Dict[str, Any]] = {}
        self._max_throttle_entries = 500

    # ---------- 预算 ----------
    @property
    def cfg(self):
        src = self.config
        if callable(src):
            src = src()
        return src.context

    def budget(self, model: Optional[str] = None) -> Budget:
        cfg = self.cfg
        window = resolve_model_window(
            model or self.model_name,
            default_window=int(getattr(cfg, "context_window", 128000)),
            overrides=dict(getattr(cfg, "model_windows", {}) or {}))
        return resolve_budget(
            window,
            int(getattr(cfg, "reserve_output_tokens", 16384)),
            int(getattr(cfg, "headroom_tokens", 8192)),
            float(getattr(cfg, "compact_threshold_ratio", 0.8)))

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _block_budget(self, block: ContextBlock) -> int:
        if block.budget:
            return int(block.budget)
        budgets = dict(getattr(self.cfg, "budgets", {}) or {})
        return int(budgets.get(block.key) or 2000)

    # ---------- 注入块阶梯 ----------
    def assemble_blocks(self, blocks: List[ContextBlock], *,
                        soft_limit: Optional[int] = None) -> Tuple[str, AssembleStats]:
        cfg = self.cfg
        dry_run = bool(getattr(cfg, "dry_run", False))
        limit = int(soft_limit or 0) or self.budget().soft_limit

        if not bool(getattr(cfg, "enabled", True)):
            text = "\n\n".join(f"{b.title}\n{b.text}" for b in blocks if b.text)
            st = AssembleStats(dry_run=False, tokens_before=estimate_tokens(text),
                               tokens_after=estimate_tokens(text), limit=limit)
            return text, st

        stats: List[BlockStat] = []
        for block in blocks:
            if not block.text:
                continue
            before = estimate_tokens(block.text)
            st = BlockStat(key=block.key, title=block.title,
                           priority=block.priority, tokens_before=before,
                           tokens_after=before, text=block.text,
                           original=block.text, prunable=block.prunable,
                           source_chars=len(block.text))
            budget = self._block_budget(block)
            if block.prunable and before > budget:
                try:
                    result = prune_text(
                        block.text, budget,
                        spill=self.spill if block.spill else None,
                        spill_namespace=f"ctx_{block.key}")
                    if result.pruned:
                        st.text = result.text
                        st.tokens_after = result.tokens_after
                        st.pruned = True
                        st.omitted_chars = result.omitted_chars
                        st.locator = result.locator
                except Exception as e:  # noqa: BLE001
                    logger.warning("注入块 %s 裁剪失败（保留原文）: %s", block.key, e)
            stats.append(st)

        out = AssembleStats(dry_run=dry_run,
                            tokens_before=sum(s.tokens_before for s in stats),
                            limit=limit, blocks=stats)
        total = sum(s.tokens_after for s in stats)

        # 全局等比收缩（只从原文重新裁，避免出现嵌套省略标记）
        if total > limit:
            total = self._shrink_blocks(stats, total, limit)

        # 仍超：按优先级丢弃（priority=0 永不丢）
        if total > limit:
            total = self._drop_blocks(stats, total, limit, out)

        out.tokens_after = total
        if dry_run:
            text = "\n\n".join(f"{b.title}\n{b.text}" for b in blocks if b.text)
            return text, out

        pieces = [f"{s.title}\n{s.text}" for s in stats if not s.dropped]
        text = "\n\n".join(pieces)
        if out.dropped:
            text += "\n\n" + self._omission_notice(stats)
        return text, out

    def _shrink_blocks(self, stats: List[BlockStat], total: int, limit: int) -> int:
        over = total - limit
        for prio in (3, 2, 1, 0):
            group = [s for s in stats
                     if s.priority == prio and not s.dropped and s.prunable
                     and s.tokens_after > MIN_BLOCK_TOKENS]
            capacity = sum(s.tokens_after - MIN_BLOCK_TOKENS for s in group)
            if capacity <= 0:
                continue
            cut = min(over, capacity)
            for s in group:
                share = (s.tokens_after - MIN_BLOCK_TOKENS) * cut / capacity
                target = max(MIN_BLOCK_TOKENS, int(s.tokens_after - share))
                try:
                    result = prune_text(s.original or s.text, target,
                                        spill=self.spill,
                                        spill_namespace=f"ctx_{s.key}")
                    if result.pruned:
                        s.text = result.text
                        s.tokens_after = result.tokens_after
                        s.pruned = True
                        s.omitted_chars = result.omitted_chars
                        s.locator = s.locator or result.locator
                except Exception as e:  # noqa: BLE001
                    logger.warning("注入块 %s 二次收缩失败: %s", s.key, e)
            over -= cut
            if over <= 0:
                break
        return sum(s.tokens_after for s in stats if not s.dropped)

    def _drop_blocks(self, stats: List[BlockStat], total: int, limit: int,
                     out: AssembleStats) -> int:
        for prio in (3, 2, 1):
            candidates = sorted(
                [s for s in stats if s.priority == prio and not s.dropped],
                key=lambda s: -s.tokens_after)
            for s in candidates:
                if total <= limit:
                    return total
                s.dropped = True
                total -= s.tokens_after
                out.dropped.append(s.key)
            if total <= limit:
                return total
        return total

    def _omission_notice(self, stats: List[BlockStat]) -> str:
        items = []
        for s in stats:
            if not s.dropped:
                continue
            name = s.title.lstrip("# ").split("（")[0].strip() or s.key
            seg = f"{name}（原文 {s.source_chars} 字"
            if s.locator:
                seg += f"，已存档 {s.locator}"
            seg += "）"
            items.append(seg)
        return ("【因上下文长度限制，以下内容未注入：" + "；".join(items)
                + "。如需其中细节，请用户明确指定范围后单独引用。】")

    # ---------- 历史阶梯 ----------
    def _history_messages(self, session_key: str, pending_text: str) -> List[Any]:
        try:
            messages = list(self._messages(session_key) or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("读取会话消息失败（按空历史处理）: %s", e)
            return []
        if pending_text and messages:
            role, content = _role_content(messages[-1])
            if role == "user" and content == pending_text:
                messages = messages[:-1]
        return messages

    def _load_shadow(self, session_key: str, messages: List[Any]):
        try:
            return shadow_mod.resolve(
                shadow_mod.load_sidecar(session_key, self.shadow_base_dir),
                messages)
        except Exception as e:  # noqa: BLE001
            logger.warning("压缩影子读取失败（按无影子处理）: %s", e)
            return None

    def has_shadow(self, session_key: str) -> bool:
        """该会话是否已有可用压缩影子（聊天前置提示用）"""
        return self._load_shadow(
            session_key, self._history_messages(session_key, "")) is not None

    @staticmethod
    def _render_one(msg: Any, cap: int) -> Tuple[str, int]:
        role, content = _role_content(msg)
        name = _ROLE_NAMES.get(role, role or "消息")
        if cap > 0:
            content = prune_message_content(content, cap).text
        line = f"{name}: {content}"
        return line, estimate_tokens(line) + 4

    def _render_trimmed(self, messages: List[Any], keep: int,
                        budget: int) -> Tuple[str, int, int]:
        """滑窗 + 逐条裁剪 + 必要时丢最旧；返回 (文本, 保留条数, 丢弃条数)。

        确定性兜底路径：不调 LLM，但保证结果一定落在预算内。
        最近窗口的单条消息也可能巨大（例如刚生成的整节草稿）——超预算时
        按预算封顶，避免一条长文挤掉全部早期上下文。
        """
        n = len(messages)
        if n == 0:
            return "", 0, 0
        keep = max(1, min(keep, n))
        tail = messages[n - keep:]
        older = messages[:n - keep]

        tail_tokens = estimate_messages_tokens(tail)
        tail_cap = 0
        if tail_tokens + 40 > budget:
            # 超预算：每条近消息封顶，留出早期消息的份额
            tail_cap = max(400, budget // max(1, keep * 2))
        tail_lines, tail_cost = [], 0
        for msg in tail:
            line, cost = self._render_one(msg, tail_cap)
            tail_lines.append(line)
            tail_cost += cost
        tail_block = "最近对话记录：\n" + "\n\n".join(tail_lines)

        if not older:
            return tail_block, keep, 0
        used = tail_cost + 40
        if used >= budget:
            return (f"（更早的 {len(older)} 条对话因长度限制未保留）\n\n{tail_block}",
                    keep, len(older))
        remaining = budget - used
        cap = max(40, remaining // max(1, len(older)))
        picked: List[str] = []
        for msg in reversed(older):
            line, cost = self._render_one(msg, cap)
            if cost > remaining:
                break
            picked.append(line)
            remaining -= cost
        picked.reverse()
        dropped = len(older) - len(picked)
        parts: List[str] = []
        if dropped:
            parts.append(f"（更早的 {dropped} 条对话因长度限制未保留）")
        if picked:
            parts.append("早期对话（已压缩）：\n" + "\n\n".join(picked))
        parts.append(tail_block)
        return "\n\n".join(parts), keep, dropped

    async def build_history_block(self, session_key: str, *,
                                  pending_text: str = "",
                                  model: Optional[str] = None,
                                  allow_compact: bool = True
                                  ) -> Tuple[str, HistoryStats]:
        cfg = self.cfg
        c = cfg.compaction
        budget = int(getattr(cfg, "history_budget_tokens", 6000))
        messages = self._history_messages(session_key, pending_text)
        if not messages:
            return "", HistoryStats(mode="empty", budget=budget)

        full_tokens = estimate_messages_tokens(messages)
        if bool(getattr(cfg, "dry_run", False)):
            # 灰度：完全保持旧的 3×100 格式化，只汇报"如果开启会怎样"
            would = (full_tokens > budget
                     and bool(getattr(c, "enabled", True))
                     and full_tokens >= int(getattr(c, "min_history_tokens", 4000)))
            lines = ["最近对话记录："]
            for msg in messages[-3:]:
                role, content = _role_content(msg)
                name = _ROLE_NAMES.get(role, role or "消息")
                lines.append(f"{name}: {content[:100]}{'...' if len(content) > 100 else ''}")
            return "\n".join(lines), HistoryStats(
                mode="dry-run", messages=len(messages), kept=min(3, len(messages)),
                tokens=full_tokens, budget=budget,
                note="would-compact" if would else "would-fit")

        if full_tokens <= budget:
            text = _render_plain(messages)
            return text, HistoryStats(mode="verbatim", messages=len(messages),
                                      kept=len(messages),
                                      tokens=estimate_tokens(text), budget=budget)

        keep_recent = max(2, int(getattr(c, "keep_recent_messages", 12)))

        # 1) 已有影子：优先复用（不产生任何模型调用）。
        #    但若影子之后又积累了超出滑窗的早期消息，渲染时只能把最新窗口
        #    压成摘录——保真度倒挂；此时应先尝试滚动合并（步骤 2），
        #    压缩不可用再退回这个渲染结果。
        shadow = self._load_shadow(session_key, messages)
        shadow_render = None
        if shadow:
            text, sh, kept = self._render_with_shadow(
                messages, shadow, keep_recent, budget)
            if estimate_tokens(text) <= budget:
                shadow_render = (text, sh, kept)
                can_compact = (allow_compact
                               and bool(getattr(c, "enabled", True))
                               and full_tokens >= int(getattr(c, "min_history_tokens", 4000)))
                if (len(messages) - shadow.through_index <= keep_recent
                        or not can_compact):
                    return text, HistoryStats(mode="compacted", messages=len(messages),
                                              kept=kept, shadowed=sh,
                                              tokens=estimate_tokens(text),
                                              budget=budget, compacted=True,
                                              locator=shadow.source_locator or None)

        # 2) 压缩子代理：把滑动窗口之外的历史归档成 8 段摘要（滚动合并）
        if (allow_compact and bool(getattr(c, "enabled", True))
                and full_tokens >= int(getattr(c, "min_history_tokens", 4000))):
            created = await self._ensure_shadow(session_key, messages, keep_recent)
            if created:
                shadow = created
                text, sh, kept = self._render_with_shadow(
                    messages, shadow, keep_recent, budget)
                if estimate_tokens(text) <= budget:
                    return text, HistoryStats(
                        mode="compacted", messages=len(messages),
                        kept=kept, shadowed=sh, tokens=estimate_tokens(text),
                        budget=budget, compacted=True,
                        locator=shadow.source_locator or None)

        # 2.5) 压缩不可用/失败：用此前的影子渲染结果兜底
        if shadow_render:
            text, sh, kept = shadow_render
            return text, HistoryStats(mode="compacted", messages=len(messages),
                                      kept=kept, shadowed=sh,
                                      tokens=estimate_tokens(text),
                                      budget=budget, compacted=True,
                                      locator=(shadow.source_locator
                                               if shadow else None) or None)

        # 3) 确定性兜底：滑窗 + 逐条裁剪（压缩不可用/失败时）
        text, kept, dropped = self._render_trimmed(messages, keep_recent, budget)
        tokens = estimate_tokens(text)
        if tokens <= budget:
            return text, HistoryStats(
                mode="pruned" if dropped == 0 else "truncated",
                messages=len(messages), kept=kept, tokens=tokens,
                budget=budget, compacted=bool(shadow),
                note=f"dropped {dropped}" if dropped else "")

        # 4) 逐级收缩滑动窗口
        for keep in (8, 4, 2):
            if keep >= keep_recent:
                continue
            text, kept, dropped = self._render_trimmed(messages, keep, budget)
            tokens = estimate_tokens(text)
            if tokens <= budget:
                return text, HistoryStats(mode="truncated", messages=len(messages),
                                          kept=kept, tokens=tokens,
                                          budget=budget, compacted=bool(shadow),
                                          note=f"dropped {dropped}" if dropped else "")

        # 5) 兜底硬裁（保留更多头部：历史块不是工具输出）
        result = prune_text(text, budget, head_ratio=0.7, snap_window=200)
        return result.text, HistoryStats(mode="truncated", messages=len(messages),
                                         kept=min(keep_recent, len(messages)),
                                         tokens=result.tokens_after,
                                         budget=budget, compacted=bool(shadow),
                                         note="hard-prune")

    def _render_with_shadow(self, messages: List[Any], shadow,
                            keep: int, budget: int) -> Tuple[str, int, int]:
        """摘要 + 影子之后的原文（尾部窗口仍受 budget 约束）"""
        through = min(shadow.through_index, len(messages))
        summary_md = shadow.summary_md or SUMMARY_PREAMBLE
        tail = messages[through:]
        if not tail:
            return summary_md, through, through
        remaining = max(400, budget - estimate_tokens(summary_md) - 40)
        tail_text, kept, _ = self._render_trimmed(tail, keep, remaining)
        text = summary_md + "\n\n" + tail_text
        return text, through, kept

    async def _ensure_shadow(self, session_key: str, messages: List[Any],
                             keep_recent: int, *, force: bool = False):
        c = self.cfg.compaction
        target_start = max(0, len(messages) - keep_recent)
        if target_start <= 0:
            return None
        async with self._lock(session_key):
            current = self._load_shadow(session_key, messages)
            if current and current.through_index >= target_start:
                return current
            if self._summarize_fn is None:
                return current

            now = time.monotonic()
            last_ts, _ = self._throttle.get(session_key, (0.0, 0))
            min_gap = float(getattr(c, "min_interval_seconds", 60.0))
            if not force and (now - last_ts) < min_gap:
                return current

            start = current.through_index if current else 0
            archived = messages[start:target_start]
            if not archived:
                return current
            archived_text = _render_plain(archived)
            locator = None
            if self.spill is not None:
                ref = self.spill.save_text(archived_text, namespace="history")
                locator = getattr(ref, "locator", None)

            try:
                # 滚动摘要：只把上一版的 8 段结构交给子代理，不传注入用的前言
                prev = (json.dumps(current.summary_json, ensure_ascii=False)
                        if current and current.summary_json else "")
                summary = await self._summarize_fn(archived_text, prev)
            except Exception as e:  # noqa: BLE001
                logger.warning("压缩子代理异常（本次跳过压缩）: %s", e)
                summary = None
            self._throttle[session_key] = (time.monotonic(),
                                           estimate_messages_tokens(messages))
            if len(self._throttle) > self._max_throttle_entries:
                self._throttle.clear()

            if not summary:
                return current

            md = render_summary_md(summary, int(getattr(c, "summary_max_chars", 1400)))
            created = shadow_mod.Shadow(
                through_index=target_start,
                prefix_digest=shadow_mod.prefix_digest(messages, target_start),
                summary_json=summary,
                summary_md=md,
                created_at=datetime.now().isoformat(timespec="seconds"),
                source_tokens=estimate_messages_tokens(messages[:target_start]),
                model=self.model_name or "",
                source_locator=locator or "")
            try:
                if bool(getattr(c, "persist_shadow", True)):
                    shadow_mod.save_sidecar(session_key, created,
                                            self.shadow_base_dir)
            except Exception as e:  # noqa: BLE001
                logger.warning("压缩影子落盘失败（仅内存生效）: %s", e)
            logger.info("会话 %s 完成压缩：%d 条消息 → %d 字摘要",
                        session_key[:24], target_start, len(created.summary_md))
            return created

    # ---------- 手动压缩 / 诊断 ----------
    async def compact_now(self, session_key: str) -> Dict[str, Any]:
        cfg = self.cfg
        if not bool(getattr(cfg.compaction, "enabled", True)):
            return {"ok": False, "reason": "压缩未启用"}
        messages = self._history_messages(session_key, "")
        if not messages:
            return {"ok": False, "reason": "会话为空"}
        keep = max(2, int(getattr(cfg.compaction, "keep_recent_messages", 12)))
        shadow = await self._ensure_shadow(session_key, messages, keep, force=True)
        if not shadow:
            return {"ok": False, "reason": "压缩未产生结果（模型不可用或输出不合规）",
                    "messages": len(messages)}
        return {"ok": True, "messages": len(messages),
                "shadow": shadow.describe()}

    def record_assemble(self, session_key: str, stats: AssembleStats) -> None:
        self._last_assemble[session_key] = stats.to_dict()
        if len(self._last_assemble) > 200:
            self._last_assemble.clear()

    def history_stats(self, session_key: str, *,
                      model: Optional[str] = None) -> Dict[str, Any]:
        """同步版历史诊断（不触发压缩）"""
        cfg = self.cfg
        budget = int(getattr(cfg, "history_budget_tokens", 6000))
        messages = self._history_messages(session_key, "")
        shadow = self._load_shadow(session_key, messages)
        keep = max(2, int(getattr(cfg.compaction, "keep_recent_messages", 12)))
        if not messages:
            mode, tokens, kept, through = "empty", 0, 0, 0
        elif estimate_messages_tokens(messages) <= budget:
            text = _render_plain(messages)
            mode, tokens, kept, through = ("verbatim", estimate_tokens(text),
                                           len(messages), 0)
        elif shadow:
            text, through, kept = self._render_with_shadow(
                messages, shadow, keep, budget)
            tokens = estimate_tokens(text)
            mode = "compacted" if tokens <= budget else "over-budget"
        else:
            text, kept, _ = self._render_trimmed(messages, keep, budget)
            tokens, through, mode = estimate_tokens(text), 0, "over-budget"
        b = self.budget(model)
        return {
            "mode": mode, "messages": len(messages), "kept": kept,
            "shadowed": through, "tokens": tokens, "budget": budget,
            "window": b.window, "soft_limit": b.soft_limit,
            "hard_limit": b.hard_limit,
            "shadow": shadow.describe() if shadow else None,
            "spill": self.spill.stats() if self.spill else None,
            "last_assemble": self._last_assemble.get(session_key),
        }
