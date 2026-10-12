# Humanize-zh 本机蒸馏（RTX 4060 Laptop · 8GB）

## 目标
用开源的 1.8 万条「AI味→人类文」SFT 对，QLoRA 微调 **Qwen3-4B-Instruct**，
产出 4B 级中文去AI味模型（GGUF → Ollama），接入论文智能体作「深度去AI」引擎。
验收：改写后朱雀 AI 确证占比显著下降（原 9B 版 30%→<10%；4B 预期 10~15%），数字/引用零丢失。

## 硬件适配（4060 8GB · sm89）
- ✅ 支持 bf16 / FlashAttention2 / bitsandbytes 4bit（比 P100 友好）
- ⚠️ 显存 8GB：**QLoRA（4bit 基座）必须**；训练期间建议停 Ollama 模型（释放 ~2.2GB）
- ⚠️ 笔记本满载训练注意散热（垫高/外接散热），建议插电

## 流程（目录内脚本按序执行）
1. `prepare_data.py`   下载 XiangJinYu/Qwen3.5-9B-Humanize-Dataset → data/train.jsonl（走 hf-mirror）
2. `train_qlora.py`    QLoRA 训练（4bit 基座 + LoRA r16 + bf16 + FA2 + grad ckpt）
   - 冒烟：`python train_qlora.py --smoke`（20 条跑通显存）
   - 正式：`python train_qlora.py`（1.8 万条 × 3 epoch，4060 约 4~8 小时）
3. `merge_export.py`   合并 adapter → dist/humanize-qwen3-4b（完整 fp16 模型）
4. `to_gguf.md`        llama.cpp 转 GGUF + Ollama 导入（Modelfile 已给）
5. `test_humanize.py`  效果验证：对本仓库某节改写 → 可选朱雀 API 复检

## 训练要点（已内置）
- LoRA r=16 α=32 dropout=0.05，目标 q/k/v/o/gate/up/down 全线性层
- lr 1e-4 cosine warmup 3%，seq 1024，batch 1 × grad-accum 16（有效 16）
- 指令格式与原仓库一致：system=去AI规则摘要，user=AI味原文，assistant=人类改写
- checkpoints 每 epoch 存 adapter（每个仅几百 MB）

## 许可
humanize-zh 无 License（版权保留）：本蒸馏版仅个人研究使用，勿商用/分发。
