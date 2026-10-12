# -*- coding: utf-8 -*-
"""
数字守卫（Fact Guard）
生成后校验：正文提及某指标时，画像 key_results 中的基准值必须出现；
基准值缺失清单 → 自动修复一轮；防止 LLM 编造/漂移关键数字。
"""
import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# 画像关键结果的提取模式（与 project_indexer.RESULT_PATTERNS 互补，扩展覆盖面）
FACT_PATTERNS = [
    (r"C指数", r"(0\.\d{3,4})"),
    (r"Uno'?s? C", r"(0\.\d{3,4})"),
    (r"AUC", r"(0\.\d{3,4})"),
    (r"IBS", r"(0\.\d{3,4})"),
    (r"HR", r"(\d+\.\d{1,2})"),
    (r"样本量", r"n\s*[=＝]\s*(\d{2,5})"),
    (r"95%CI", r"(\d+\.\d{2,3})"),
]

_NUM_TOKEN = re.compile(r"\d+\.\d{2,4}|\d{2,5}")


def extract_facts(profile: dict) -> List[Tuple[str, str]]:
    """从画像 key_results 提取 (指标标签, 基准值) 列表"""
    facts: List[Tuple[str, str]] = []
    for r in profile.get("key_results", []):
        metric = (r.get("metric") or "").strip()
        value = str(r.get("value") or "").strip()
        if metric and value:
            facts.append((metric, value))
    return facts


def check_numbers(content: str, facts: List[Tuple[str, str]]) -> List[dict]:
    """校验一节正文：
    1) 基准值覆盖：正文提及指标标签但未出现画像基准值 → missing
    2) 疑似漂移：正文中与基准值同位数格式的近似数（不同值）出现于同一句 → suspicious
    返回诊断列表（空=通过）。"""
    issues: List[dict] = []
    if not content or not facts:
        return issues
    # 按句子切分，定位指标标签所在句
    sentences = re.split(r"(?<=[。；！？])", content)
    norm = lambda v: (v or "").replace("%", "").strip().rstrip("0").rstrip(".") if "." in (v or "").replace("%", "").strip() else (v or "").replace("%", "").strip()
    # 同指标多基准值分组（如 C指数 在多队列下有多个合法值）
    groups: dict = {}
    for label, value in facts:
        groups.setdefault(label, []).append(norm(value))
    for label, values in groups.items():
        mentioned = [s for s in sentences if label.lower() in s.lower()]
        if not mentioned:
            continue  # 本节未提及该指标 → 不强制
        # 该指标的任一合法基准值出现即通过
        all_vals = values
        if any(any(v in norm(s) or v in s for v in all_vals) for s in mentioned):
            continue
        # 全部基准值均未出现 → 检查近似漂移（同句位数相近的不同数）
        drifted = []
        for s in mentioned:
            for tok in _NUM_TOKEN.findall(s):
                for v in all_vals:
                    if len(tok) == len(v) and tok != v and tok[0] == v[0]:
                        drifted.append(tok)
        if drifted:
            issues.append({
                "type": "数字漂移",
                "label": label,
                "expected": "或".join(all_vals[:3]),
                "found": sorted(set(drifted))[:3],
            })
        else:
            issues.append({
                "type": "基准值缺失",
                "label": label,
                "expected": "或".join(all_vals[:3]),
                "hint": "正文提及该指标但未给出画像基准值",
            })
    return issues


def repair_prompt(issues: List[dict]) -> str:
    """把诊断清单转为修复指令"""
    if not issues:
        return ""
    lines = ["以下关键数字存在漂移或缺失，必须按给定基准值修正（其余内容不得改动）："]
    for it in issues:
        if it["type"] == "数字漂移":
            lines.append(f"- {it['label']}：正文写作 {it['found']}，应为 **{it['expected']}**")
        else:
            lines.append(f"- {it['label']}：补充画像基准值 **{it['expected']}**")
    return "\n".join(lines)
