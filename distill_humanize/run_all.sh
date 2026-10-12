#!/usr/bin/env bash
# ============================================================
# humanize-4b 云 GPU 一键流水线（Linux + NVIDIA 卡）
# 环境检测 → 基座下载 → 冒烟 → 正式训练 → 合并 → GGUF量化
# 用法：解压后执行  bash run_all.sh
#   断点续跑：再次执行同一命令（基座/数据已存在会自动跳过）
#   只跑后半段（已有 adapter）：SKIP_TRAIN=1 bash run_all.sh
# ============================================================
set -e
cd "$(dirname "$0")"
export PATH=/root/miniconda3/bin:$PATH
export BASE="${BASE:-Qwen/Qwen3.5-9B}"
export TAG="${TAG:-humanize-9b}"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
export TOKENIZERS_PARALLELISM=false

echo "═══ [1/8] GPU 检测 ═══"
nvidia-smi | sed -n '1,10p' || { echo "✗ 未检测到 NVIDIA GPU"; exit 1; }

echo "═══ [2/8] 依赖安装（平台镜像若已带 torch 会自动跳过） ═══"
python -c "import torch" 2>/dev/null || \
  pip install -q torch --index-url https://download.pytorch.org/whl/cu126 || pip install -q torch
python -c "import transformers, peft, datasets, accelerate, bitsandbytes" 2>/dev/null || \
  pip install -q transformers==5.18.0 peft datasets accelerate bitsandbytes huggingface_hub
echo "torch=$(python -c 'import torch;print(torch.__version__)') cuda_ok=$(python -c 'import torch;print(torch.cuda.is_available())')"

echo "═══ [3/8] 数据检查 ═══"
[ -f data/train.jsonl ] && echo "✓ 训练数据 18000 条已就绪" || { echo "✗ 缺 data/train.jsonl"; exit 1; }

echo "═══ [4/8] 下载基座 Qwen3-4B（~8GB，已存在则跳过） ═══"
if [ ! -f base_model/config.json ]; then
  python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(__import__("os").environ.get("BASE","Qwen/Qwen3.5-9B"), local_dir="base_model", max_workers=8)
print("基座下载完成")
PY
else echo "✓ 基座已存在"; fi

echo "═══ [5/8] 冒烟测试（2 分钟） ═══"
python train_qlora.py --smoke --base ./base_model --batch 2 --ga 8

if [ "${SKIP_TRAIN:-0}" != "1" ]; then
  echo "═══ [6/8] 正式训练（4090≈3.5h / A100≈2.5h，日志 tee 到 train.log） ═══"
  python train_qlora.py --base ./base_model --epochs 2 --lr 1e-4 --batch 2 --ga 8 2>&1 | tee train.log
fi
[ -d output/run/adapter_final ] || { echo "✗ 未找到 output/run/adapter_final"; exit 1; }

echo "═══ [7/8] 合并 adapter → 完整模型（CPU，约5分钟） ═══"
python merge_export.py --base ./base_model --adapter output/run/adapter_final --out dist/$TAG-model

echo "═══ [8/8] 转 GGUF + Q4_K_M 量化 ═══"
pip install -q gguf
if [ ! -d llama.cpp ]; then git clone --depth 1 https://github.com/ggerganov/llama.cpp; fi
python llama.cpp/convert_hf_to_gguf.py dist/$TAG-model --outfile $TAG-f16.gguf --outtype f16
if [ ! -x llama.cpp/build/bin/llama-quantize ]; then
  (command -v cmake >/dev/null || (apt-get update -qq && apt-get install -y -qq cmake build-essential) || \
   (sudo apt-get update -qq && sudo apt-get install -y -qq cmake build-essential))
  cmake llama.cpp -B llama.cpp/build -DCMAKE_BUILD_TYPE=Release > /dev/null
  cmake --build llama.cpp/build --target llama-quantize -j"$(nproc)" > /dev/null
fi
./llama.cpp/build/bin/llama-quantize $TAG-f16.gguf $TAG-q4km.gguf Q4_K_M

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✅ 完成！交付文件（下载这一个即可）：      ║"
ls -lh $TAG-q4km.gguf
echo "╚══════════════════════════════════════════╝"
