# -*- coding: utf-8 -*-
"""下载 humanize 数据集并转成训练用 jsonl（本机 hf-mirror 通道）"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

from datasets import load_dataset  # noqa: E402

DS = "XiangJinYu/Qwen3.5-9B-Humanize-Dataset"

SYSTEM_PROMPT = (
    "你是中文学术文本去AI味改写器。把用户给出的文本改写得更像自然人写作："
    "保持原意、全部数字/统计量/引用标记/术语一字不动，句式变换优先于同义词替换，"
    "不得拆散自然长句，不新增或删减事实。直接输出改写后的正文。"
)


def to_chat(rec: dict) -> dict | None:
    """数据集字段自适应：尝试常见键名组合，提取 (AI味原文, 人类改写)"""
    keys = {k.lower(): k for k in rec.keys()}
    ai_cands = [keys.get(k) for k in ("instruction", "input", "ai_text", "source", "prompt", "question", "query")]
    hu_cands = [keys.get(k) for k in ("output", "response", "human_text", "target", "answer", "chosen")]
    ai_k = next((k for k in ai_cands if k and isinstance(rec.get(k), str) and len(rec[k]) > 40), None)
    hu_k = next((k for k in hu_cands if k and isinstance(rec.get(k), str) and len(rec[k]) > 40), None)
    # chat 原生格式（messages/conversations）
    if ai_k is None and ("messages" in rec or "conversations" in rec):
        msgs = rec.get("messages") or rec.get("conversations") or []
        try:
            user = next(m["content"] for m in msgs if m.get("role") in ("user", "human"))
            asst = next(m["content"] for m in msgs if m.get("role") in ("assistant", "gpt"))
            return {"messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
                {"role": "assistant", "content": asst}]}
        except StopIteration:
            return None
    if not (ai_k and hu_k):
        return None
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": rec[ai_k]},
        {"role": "assistant", "content": rec[hu_k]}]}


def main():
    print(f"下载 {DS}（hf-mirror）…")
    ds = load_dataset(DS)
    split = "train" if "train" in ds else list(ds.keys())[0]
    rows = ds[split]
    print(f"原始 {len(rows)} 条 · 字段: {rows.column_names}")
    out, skipped = [], 0
    for rec in rows:
        c = to_chat(rec)
        if c:
            out.append(c)
        else:
            skipped += 1
    if not out and rows:
        # 兜底：打印首条帮助人工判读字段
        print("!! 未能自动识别字段，首条样例：")
        print(json.dumps(rows[0], ensure_ascii=False)[:800])
        sys.exit(1)
    # 去重（user 内容相同只留一条）
    seen, dedup = set(), []
    for c in out:
        u = c["messages"][1]["content"]
        h = hash(u)
        if h not in seen:
            seen.add(h)
            dedup.append(c)
    path = DATA / "train.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for c in dedup:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"✓ {len(dedup)} 条训练对（跳过 {skipped} 条无法解析 / 去重 {len(out)-len(dedup)}）→ {path}")


if __name__ == "__main__":
    main()
