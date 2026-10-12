# 转 GGUF 并导入 Ollama

## 1. 编译/安装 llama.cpp（Windows：直接下 release）
从 https://github.com/ggerganov/llama.cpp/releases 下载 `llama-bXXXX-bin-win-cuda-x64.zip`
（本机有 CUDA；或 cpu 版也够转换用），解压后把目录加入 PATH 或记下路径 `<llamacpp>`。

## 2. 合并模型转 GGUF
```bash
python distill_humanize/merge_export.py          # 先产出 dist/humanize-qwen3-4b（fp16 完整模型）
python <llamacpp>/convert_hf_to_gguf.py dist/humanize-qwen3-4b --outfile humanize-q4-src.gguf --outtype f16
<llamacpp>/llama-quantize humanize-q4-src.gguf humanize-qwen3-4b-q4km.gguf Q4_K_M
```
（Q4_K_M ≈ 2.5GB；追求质量用 Q8_0 ≈ 4.5GB）

## 3. 导入 Ollama
`distill_humanize/Modelfile`：
```
FROM ./humanize-qwen3-4b-q4km.gguf
TEMPLATE """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
{{ .Response }}<|im_end|>
"""
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.05
PARAMETER stop "<|im_end|>"
SYSTEM "你是中文学术文本去AI味改写器。保持原意与全部数字/引用/术语，句式变换优先于同义词替换，直接输出改写后的正文。"
```
```bash
ollama create humanize-4b -f distill_humanize/Modelfile
ollama run humanize-4b "（粘贴一段AI味文本测试）"
```

## 4. 接入论文智能体
模型就位后，把模型名 `humanize-4b` 告诉 ZCode：在设置弹窗加「深度去AI引擎」选项，
闭环降AI的第 2/3 轮自动改调此模型（规则引擎做第 1 轮轻量清理，模型做深度轮）。
