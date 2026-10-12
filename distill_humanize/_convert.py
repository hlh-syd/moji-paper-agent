# -*- coding: utf-8 -*-
"""v15_sft_18k.jsonl → data/train.jsonl（chat 格式 + 标点规范化）"""
import json
import re
from pathlib import Path

SYSTEM_PROMPT = ("你是中文学术文本去AI味改写器。把用户给出的文本改写得更像自然人写作："
                 "保持原意、全部数字/统计量/引用标记/术语一字不动，句式变换优先于同义词替换，"
                 "不得拆散自然长句，不新增或删减事实。直接输出改写后的正文。")

_CJK = r"[\u4e00-\u9fff\u3001\u3002\uff0c\uff1b\uff1a）」』]"


def normalize_punct(t: str) -> str:
    """CSL 人类原文用半角标点——恢复中文全角（仅中文语境），避免模型学到坏标点习惯"""
    t = re.sub(r"(" + _CJK + r")\s*,\s*", r"\1，", t)
    t = re.sub(r"(" + _CJK + r")\s*;\s*", r"\1；", t)
    t = re.sub(r"(" + _CJK + r")\s*:\s*", r"\1：", t)
    # 中文后的句点→句号；后跟数字的不动（小数/编号），英文缩写不在此模式内
    t = re.sub(r"(" + _CJK + r")\.(?!\d)", r"\1。", t)
    t = re.sub(r"\(([\u4e00-\u9fff][^)]*)\)", r"（\1）", t)
    return t


src = Path(__file__).parent / "data_raw" / "data" / "v15_sft_18k.jsonl"
out, seen = [], set()
for ln in src.read_text(encoding="utf-8").splitlines():
    d = json.loads(ln)
    user = (d.get("input") or "").strip()
    asst = normalize_punct((d.get("output") or "").strip())
    if len(user) < 40 or len(asst) < 40:
        continue
    h = hash(user)
    if h in seen:
        continue
    seen.add(h)
    out.append({"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
        {"role": "assistant", "content": asst}]})

dst = Path(__file__).parent / "data" / "train.jsonl"
dst.parent.mkdir(exist_ok=True)
with open(dst, "w", encoding="utf-8") as f:
    for c in out:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print(f"OK {len(out)} 条（去重 {18000 - len(out)}）→ {dst}")
print("标点规范化示例:", out[0]["messages"][2]["content"][:80])
