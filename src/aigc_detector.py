"""
模型级 AIGC 检测器（simple-aigc-detect 思路的中文化裁剪实现）
================================================================
与 text_enhancer.detect_ai_markers（启发式体检，零 LLM）互补的第二意见：
用 HuggingFace 序列分类模型做"模型判别"，更接近知网/维普 AIGC 检测的行为。

模型（均走 transformers pipeline，本地缓存离线可用）：
  - 中文：yuchuantian/AIGC_detector_zhv2（BERT 二分类，主用）
  - 英文：yuchuantian/AIGC_detector_env2（RoBERTa 二分类，按语言自动选择）

设计约束：
  - 懒加载：首次调用才加载（服务器启动零开销；模型缺失/网络失败只降级不报错）
  - 纯 CPU：避免与 Ollama 抢占 GPU 显存（GPU 竞争曾致嵌入服务 502）
  - 长文分块：段落→句→512 字符块，块级 AI 概率加权融合（长块权重高）
  - 结果缓存：同文重复体检（全文流水线会反复调用）直接命中
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MODELS = {
    "zh": "yuchuantian/AIGC_detector_zhv2",
    "en": "yuchuantian/AIGC_detector_env2",
}
_MAX_CHARS = 460          # BERT 512 token 上限的字符安全界（中文约 1 字 1 token）
_MIN_CHARS = 60           # 过短块不判
_CACHE_MAX = 8            # 结果 LRU 上限（正文哈希 → 分数）
_cache: dict = {}

_pipelines: dict = {}
_load_failed = False


def _load(lang: str):
    """懒加载对应语言的分类 pipeline（线程级单例；失败全局降级）。
    已缓存模型强制离线加载：部分 huggingface_hub 版本即使 local_files_only=True
    仍会做联网 etag 校验（断网时 5 轮重试 ≈ 4 分钟，曾把 SSE 复检拖死）；
    本地加载失败再回退联网（首装下载路径）"""
    global _load_failed
    if _load_failed:
        return None
    if lang not in _pipelines:
        from transformers import pipeline as hf_pipeline
        try:
            with _force_offline():
                try:
                    # 不给 pipeline 传 local_files_only：会经 **kwargs 落到每次
                    # tokenizer() 调用上（TypeError）。离线语义由 _force_offline 保证
                    _pipelines[lang] = hf_pipeline(
                        "text-classification", model=_MODELS[lang],
                        device=-1, truncation=True,
                    )
                except Exception:
                    pass  # 缓存缺失 → 出离线模式走在线下载
            if lang not in _pipelines:
                _pipelines[lang] = hf_pipeline(
                    "text-classification", model=_MODELS[lang],
                    device=-1, truncation=True,
                )
            logger.info("AIGC 检测模型已加载(%s): %s", lang, _MODELS[lang])
        except Exception as e:
            _load_failed = True
            logger.warning("AIGC 检测模型不可用（模型检测降级关闭）: %s", e)
            return None
    return _pipelines[lang]


class _force_offline:
    """加载期间临时置 huggingface_hub/transformers 为离线模式，结束后还原"""

    def __enter__(self):
        import os
        from huggingface_hub import constants as _c
        self._env = (os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE"))
        self._const = _c.HF_HUB_OFFLINE
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        _c.HF_HUB_OFFLINE = True
        return self

    def __exit__(self, *exc):
        import os
        from huggingface_hub import constants as _c
        for k, v in zip(("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"), self._env):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _c.HF_HUB_OFFLINE = self._const
        return False


def _lang_of(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    # 中文论文常夹大量数字/统计量/基因名，只要汉字体量可观且不低于英文单词量即走中文模型
    return "zh" if cjk >= 30 and cjk >= letters * 0.5 else "en"


def _chunks(text: str) -> list:
    """段落→句→定长 三级切分，去掉公式/表格/代码等噪声块"""
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    body = re.sub(r"\|.*\|", " ", body)                 # 表格行
    blocks: list = []
    for para in body.split("\n"):
        para = para.strip()
        if not para:
            continue
        bare = para.lstrip("*").strip()                 # 剥离 **加粗** 标记后再判类型
        if bare.startswith(("#", ">", "|")):
            continue
        if re.match(r"^[-*+]\s", bare):                 # 真列表项（"- " / "* "）
            continue
        blocks.append(para)
    # 段落过长的按句切
    fine: list = []
    for b in blocks:
        if len(b) <= _MAX_CHARS:
            fine.append(b)
            continue
        for s in re.split(r"(?<=[。！？；.!?])", b):
            fine.append(s.strip())
    # 合并成 ≥_MIN_CHARS 且 ≤_MAX_CHARS 的块
    out, cur = [], ""
    for s in fine:
        if not s:
            continue
        if len(cur) + len(s) <= _MAX_CHARS:
            cur += s
        else:
            if len(cur) >= _MIN_CHARS:
                out.append(cur)
            cur = s if len(s) <= _MAX_CHARS else s[:_MAX_CHARS]
    if len(cur) >= _MIN_CHARS:
        out.append(cur)
    return out[:64]  # 超长文最多取 64 块（CPU 时延控制）


def _ai_prob(pipe, chunk: str) -> Optional[float]:
    """单块 → AI 概率 [0,1]；label 方向由模型 label 名自适应"""
    try:
        r = pipe(chunk[:_MAX_CHARS])[0]
    except Exception as e:
        logger.debug("AIGC 块推理失败: %s", e)
        return None
    label = str(r.get("label", "")).lower()
    score = float(r.get("score", 0.5))
    if any(k in label for k in ("1", "ai", "fake", "machine", "label_1")) and \
       not any(k in label for k in ("0", "human", "real")):
        return score
    return 1.0 - score


def detect_aigc(text: str) -> Optional[dict]:
    """模型级 AIGC 检测。返回 {ai_prob, verdict, chunks, model}；
    模型不可用/文本过短返回 None（调用方仅显示启发式分数）"""
    if not text or len(text.strip()) < 200:
        return None
    key = hash(text[:4000]) ^ len(text)
    if key in _cache:
        return _cache[key]
    lang = _lang_of(text)
    pipe = _load(lang)
    if pipe is None:
        return None
    chunks = _chunks(text)
    if not chunks:
        return None
    probs = [p for p in (_ai_prob(pipe, c) for c in chunks) if p is not None]
    if not probs:
        return None
    # 融合：均值为主，头部块（通常含摘要性表述）略加权
    head_w = [1.25 if i < 3 else 1.0 for i in range(len(probs))]
    ai_prob = sum(p * w for p, w in zip(probs, head_w)) / sum(head_w)
    result = {
        "ai_prob": round(ai_prob, 3),
        "verdict": "ai" if ai_prob >= 0.7 else ("suspect" if ai_prob >= 0.5 else "human"),
        "chunks": len(probs),
        "model": _MODELS[lang].split("/")[-1],
    }
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = result
    return result
