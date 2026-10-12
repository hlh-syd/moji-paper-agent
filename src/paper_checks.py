# -*- coding: utf-8 -*-
"""
论文质量诊断器（P3）
程序化检查：数字漂移 / 术语变体 / 跨节重复 / [待补充] 密度
输出诊断清单 → 供 LLM 定向修订
"""
import re
from difflib import SequenceMatcher
from typing import Dict, List

from src import fact_guard

# 术语变体对（同义但期刊要求统一；可按需扩充）
TERM_VARIANTS = [
    ("患者", "病人"),
    ("标志物", "标记物"),
    ("多组学", "多组学"),  # 占位保持结构
    ("肿瘤", "肿瘤"),
]
# 过滤占位（相同词不算变体）
TERM_VARIANTS = [(a, b) for a, b in TERM_VARIANTS if a != b]

SHINGLE = 20  # 跨节重复检测的字符 shingle 长度


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？])", text or "") if len(s.strip()) >= 8]


def _norm_text(s: str) -> str:
    return re.sub(r"[\s#*>|`]", "", s or "")


def run_checks(content: str, outline: dict, profile: dict,
               prev_sections: List[dict], section_id: str = "") -> Dict:
    """对一节正文跑全部程序化诊断。返回 {issues: [...], counts}"""
    issues: List[dict] = []

    # ---- 1. 数字漂移（复用 fact_guard）----
    facts = fact_guard.extract_facts(profile)
    num_issues = fact_guard.check_numbers(content, facts)
    for it in num_issues:
        issues.append({"type": "数字问题", "detail": f"{it['label']}：期望 {it.get('expected')}（{it['type']}）",
                       "fix_hint": fact_guard.repair_prompt([it])})

    # ---- 2. 术语变体（同一概念两种写法并存）----
    variants_found = []
    normed = _norm_text(content)
    for a, b in TERM_VARIANTS:
        if (a in normed) != (b in normed):
            continue  # 只出现一个写法没问题
        # 两种都出现 → 变体冲突
        variants_found.append(f"{a} / {b}")
    if variants_found:
        issues.append({"type": "术语不统一",
                       "detail": "同一概念混用写法：" + "、".join(variants_found),
                       "fix_hint": "全文统一为其中一种写法（保留首次定义）"})

    # ---- 3. 跨节重复（与前文 shingle 重叠）----
    dup = []
    own_norm = _norm_text(content)
    own_shingles = {own_norm[i:i + SHINGLE] for i in range(0, max(len(own_norm) - SHINGLE, 1), 10)}
    for prev in prev_sections:
        prev_norm = _norm_text(prev.get("content") or "")
        if len(prev_norm) < SHINGLE:
            continue
        prev_shingles = {prev_norm[i:i + SHINGLE]
                         for i in range(0, len(prev_norm) - SHINGLE, 10)}
        overlap = own_shingles & prev_shingles
        if overlap:
            sample = sorted(overlap)[0][:SHINGLE]
            dup.append(f"与「{prev.get('title', '?')}」重复：…{sample}…")
    if dup:
        issues.append({"type": "跨节重复",
                       "detail": "；".join(dup[:3]),
                       "fix_hint": "改写重复段落为引用前文（如'如前所述'），或删除重复表述"})

    # ---- 4. 占位/假设清单硬门（宪法禁止出现，>0 即 FAIL）----
    ph = len(re.findall(r"\[待补充", content))
    ph += len(re.findall(r"^#{1,4}[^\n]*(?:假设与缺失输入|缺失输入清单|假设清单)", content, re.M))
    if ph > 0:
        issues.append({"type": "占位内容",
                       "detail": f"[待补充]/假设清单共 {ph} 处（论文正文禁止出现）",
                       "fix_hint": "把占位句改写为不依赖该数字的定性表述；删除假设/缺失输入清单小节"})

    # ---- 5. AI 味句子定位（句级采样）----
    from src.text_enhancer import detect_ai_markers
    report = detect_ai_markers(content)
    if report["score"] >= 40:
        issues.append({"type": "AI味偏重",
                       "detail": f"体检 {report['score']}/100，"
                                 + "；".join(f"{m['type']}×{m['count']}" for m in report.get("markers", [])[:3]),
                       "fix_hint": "消除套话、打乱句长节奏、砍三联排比"})

    # ---- 6. 独立成段的图片缺题注 ----
    bad_imgs = re.findall(r"!\[\s*\]\(([^)]+)\)", content)
    if bad_imgs:
        issues.append({"type": "图片缺题注",
                       "detail": f"{len(bad_imgs)} 张图片 alt 为空",
                       "fix_hint": "补充 ![图x-x 题注] 形式的题注"})

    counts = {"issues": len(issues),
              "placeholders": ph,
              "ai_score": report.get("score", 0)}
    return {"issues": issues, "counts": counts}
