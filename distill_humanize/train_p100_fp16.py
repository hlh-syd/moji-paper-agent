# -*- coding: utf-8 -*-
"""P100（Pascal · 16GB）专用：fp16 全精度 LoRA（该架构不支持 bitsandbytes 4bit）
用法与 train_qlora.py 一致：先 --smoke 冒烟，再正式跑。
python train_p100_fp16.py --base Qwen/Qwen3-4B
"""
import argparse
import math
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "train.jsonl"
OUT = HERE / "output_p100"

import torch  # noqa: E402
from datasets import load_dataset  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import (AutoModelForCausalLM, AutoTokenizer,  # noqa: E402
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen3-4B")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--seqlen", type=int, default=1024)
    args = ap.parse_args()

    assert DATA.exists(), "缺少 data/train.jsonl（用迁移包里的）"
    run_out = OUT / ("smoke" if args.smoke else "run")
    run_out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    # P100 关键差异：fp16（无 bf16）、无 4bit 量化、sdpa attention
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.float16, device_map={"": 0},
        trust_remote_code=True, attn_implementation="sdpa")
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    ds = load_dataset("json", data_files=str(DATA))["train"]
    if args.smoke:
        ds = ds.select(range(20))

    def fmt(ex):
        msgs = ex["messages"]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        ids = tok(text, truncation=True, max_length=args.seqlen)["input_ids"]
        labels = list(ids)
        try:
            pre = tok.apply_chat_template(msgs[:2], tokenize=False,
                                          add_generation_prompt=True)
            n = len(tok(pre, truncation=True, max_length=args.seqlen)["input_ids"])
            for i in range(min(n, len(labels))):
                labels[i] = -100
        except Exception:
            pass
        return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids)}

    ds = ds.map(fmt, remove_columns=ds.column_names, desc="tokenize")

    targs = TrainingArguments(
        output_dir=str(run_out),
        num_train_epochs=(0.02 if args.smoke else args.epochs),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=max(2, math.ceil(len(ds) / 16) * 3 // 100),
        fp16=True,                      # Pascal 只支持 fp16
        logging_steps=5,
        save_strategy=("no" if args.smoke else "epoch"),
        save_total_limit=3,
        report_to=[],
        optim="adagrad",                # paged_adamw_8bit 依赖 bnb，P100 用原生
        max_grad_norm=1.0,
        dataloader_num_workers=0,
        gradient_checkpointing=True,
        seed=42,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=ds,
                      data_collator=DataCollatorForSeq2Seq(tok, model=model,
                                                           padding=True,
                                                           label_pad_token_id=-100))
    trainer.train()
    if not args.smoke:
        model.save_pretrained(run_out / "adapter_final")
        tok.save_pretrained(run_out / "adapter_final")
        print(f"OK adapter: {run_out / 'adapter_final'}")
    print("完成" + ("（冒烟）" if args.smoke else ""))


if __name__ == "__main__":
    main()
