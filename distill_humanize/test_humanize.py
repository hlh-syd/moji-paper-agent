# -*- coding: utf-8 -*-
"""蒸馏效果验证：对样例/论文节做去AI味改写，可选朱雀 API 前后对比"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

AI_SAMPLE = ("本文旨在深入探讨人工智能技术在教育领域的重要应用与价值。首先，人工智能技术具有重要的作用，"
             "它为教育改革提供了有力支撑。其次，值得注意的是，个性化学习系统能够根据学生的学习情况进行智能分析，"
             "不难发现，这种技术具有重要意义。此外，智能评估体系进一步提升了教学效率。综上所述，"
             "人工智能技术为教育现代化提供了坚实的技术基础，总而言之，其发展前景十分广阔，值得深入研究。")


def load_model(src: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(src, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        src, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True)
    model.eval()
    return model, tok


def rewrite(model, tok, text: str) -> str:
    msgs = [{"role": "system", "content": "你是中文学术文本去AI味改写器。保持原意、全部数字/引用/术语一字不动，"
                                          "句式变换优先于同义词替换。直接输出改写后的正文。"},
            {"role": "user", "content": text}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    import torch
    ids = tok(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=1024, do_sample=True,
                             temperature=0.7, top_p=0.9, repetition_penalty=1.05)
    return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def zhuque(text: str):
    sys.path.insert(0, str(ROOT))
    from src.zhuque_client import zhuque_detect
    return zhuque_detect(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(HERE / "dist" / "humanize-qwen3-4b"))
    ap.add_argument("--section", default=None, help="论文节标题（如 摘要），省略用内置AI样文")
    ap.add_argument("--zhuque", action="store_true", help="改写前后朱雀复检")
    args = ap.parse_args()

    text = AI_SAMPLE
    if args.section:
        import json
        d = json.load(open(ROOT / "projects" / "script.outline.json", encoding="utf-8"))
        text = next((s["content"] for s in d["sections"] if s["title"] == args.section), text)[:3000]

    print(f"载入模型: {args.model}")
    model, tok = load_model(args.model)
    out = rewrite(model, tok, text)
    print("\n【改写结果】\n" + out[:800])

    if args.zhuque:
        b, a = zhuque(text), zhuque(out)
        if b and a:
            print(f"\n【朱雀对比】改写前 AI {round(b['ai_ratio']*100)}% → 改写后 AI {round(a['ai_ratio']*100)}%"
                  f"（疑似 {round(a['suspect_ratio']*100)}%）")


if __name__ == "__main__":
    main()
