# -*- coding: utf-8 -*-
"""合并 LoRA adapter → 完整 fp16 模型（CPU 内存执行，8GB 显存机器友好）"""
import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

HERE = Path(__file__).resolve().parent

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="Qwen/Qwen3-4B")
ap.add_argument("--adapter", default=str(HERE / "output" / "run" / "adapter_final"))
ap.add_argument("--out", default=str(HERE / "dist" / "humanize-qwen3-4b"))
args = ap.parse_args()

print("加载基座（CPU fp16）…")
model = AutoModelForCausalLM.from_pretrained(
    args.base, torch_dtype=torch.float16, trust_remote_code=True, device_map="cpu")
tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)

print(f"合并 adapter: {args.adapter}")
model = PeftModel.from_pretrained(model, args.adapter)
model = model.merge_and_unload()

out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
model.save_pretrained(out, safe_serialization=True)
tok.save_pretrained(out)
print(f"✓ 完整模型已导出: {out}")
print("下一步：按 to_gguf.md 转 GGUF 并导入 Ollama")
