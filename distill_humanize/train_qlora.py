# -*- coding: utf-8 -*-
"""QLoRA 训练：Qwen3-4B-Instruct × humanize 数据（RTX 4060 8GB 专用配置）

冒烟（验证显存/链路，~3 分钟）:  python train_qlora.py --smoke
正式（1.8万条 × 3 epoch，约 4~8 小时）:  python train_qlora.py
可选:  --base Qwen/Qwen3-1.7B   （显存吃紧时降级）
"""
import argparse
import math
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "train.jsonl"
OUT = HERE / "output"

import torch  # noqa: E402
from datasets import load_dataset  # noqa: E402
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # noqa: E402
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,  # noqa: E402
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

BASE = "Qwen/Qwen3-4B"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--smoke", action="store_true", help="20 条 × 8 步冒烟")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--seqlen", type=int, default=1024)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--ga", type=int, default=16)
    args = ap.parse_args()

    assert DATA.exists(), "先运行 prepare_data.py"
    run_out = OUT / ("smoke" if args.smoke else "run")
    run_out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,   # 4060(Ada) 原生支持
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base, quantization_config=bnb, device_map={"": 0},
        torch_dtype=torch.bfloat16, trust_remote_code=True,
        attn_implementation="sdpa",  # Windows 无 flash-attn 包，SDPA 内置且 4060 高效
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    ds = load_dataset("json", data_files=str(DATA))["train"]
    if args.smoke:
        ds = ds.select(range(20))

    def fmt(ex):
        msgs = ex["messages"]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        ids = tok(text, truncation=True, max_length=args.seqlen)["input_ids"]
        # 只对 assistant 段计 loss：模板化定位（Qwen chat 模板 im_end 收尾）
        labels = list(ids)
        try:
            sys_user = tok.apply_chat_template(msgs[:2], tokenize=False,
                                               add_generation_prompt=True)
            pre_len = len(tok(sys_user, truncation=True,
                              max_length=args.seqlen)["input_ids"])
            for i in range(min(pre_len, len(labels))):
                labels[i] = -100
        except Exception:
            pass
        return {"input_ids": ids, "labels": labels,
                "attention_mask": [1] * len(ids)}

    ds = ds.map(fmt, remove_columns=ds.column_names, desc="tokenize")

    targs = TrainingArguments(
        output_dir=str(run_out),
        num_train_epochs=(0.02 if args.smoke else args.epochs),
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.ga,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=max(2, math.ceil(
            len(ds) / 16) * max(1, round(args.epochs if not args.smoke else 0.02)) // 33),
        bf16=True,
        logging_steps=5,
        save_strategy=("no" if args.smoke else "epoch"),
        save_total_limit=3,
        report_to=[],
        optim="paged_adamw_8bit",
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
        print(f"✓ adapter 已保存: {run_out / 'adapter_final'}")
    print("✓ 训练完成" + ("（冒烟）" if args.smoke else ""))


if __name__ == "__main__":
    main()
