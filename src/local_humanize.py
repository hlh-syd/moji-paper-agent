# -*- coding: utf-8 -*-
"""本地蒸馏去AI味引擎（humanize-9b via Ollama）
训练于 18000 条「AI味→人类文」反向蒸馏对（Qwen3.5-9B QLoRA）。
设计：规则引擎第 1 轮轻扫后，若复检仍 >55 分，由本模型做句式级深改；
模型输出经锁字校验（数字/引用丢失即弃用），事实保真优先。"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MODEL = "humanize-9b"
_OLLAMA = "http://localhost:11434"
_SYSTEM = ("你是中文学术文本去AI味改写器。把用户给出的文本改写得更像自然人写作："
           "保持原意、全部数字/统计量/引用标记/术语一字不动，句式变换优先于同义词替换，"
           "不得拆散自然长句，不新增或删减事实。直接输出改写后的正文。")

_down_until = 0.0  # 熔断：Ollama 无模型/失败后 10 分钟跳过


def available() -> bool:
    import time
    if time.time() < _down_until:
        return False
    try:
        import requests
        r = requests.get(f"{_OLLAMA}/api/tags", timeout=5)
        return any(m.get("name", "").startswith(_MODEL) for m in r.json().get("models", []))
    except Exception:
        return False


def _key_tokens(s: str):
    return (re.findall(r"\d+\.\d+|\d{2,5}", s), re.findall(r"\[\d{1,3}\]", s))


def rewrite(text: str) -> Optional[str]:
    """同步调用（放 to_thread）。返回改写文本；失败/锁字校验不过返回 None。"""
    import time
    import requests
    if not text or len(text.strip()) < 100:
        return None
    pre_nums, pre_cites = _key_tokens(text)
    try:
        r = requests.post(f"{_OLLAMA}/api/chat", json={
            "model": _MODEL,
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": text}],
            "stream": False, "think": False,
            "options": {"temperature": 0.8, "num_predict": min(4096, len(text) * 2)},
        }, timeout=600)
        r.raise_for_status()
        out = (r.json().get("message") or {}).get("content") or ""
        out = out.strip()
    except Exception as e:
        logger.warning("humanize-9b 调用失败: %s", e)
        globals()["_down_until"] = time.time() + 600
        return None
    if len(out) < len(text) * 0.5:  # 明显截断
        logger.warning("humanize-9b 输出过短(%d/%d)，弃用", len(out), len(text))
        return None
    post_nums, post_cites = _key_tokens(out)
    lost = [n for n in pre_nums if n not in post_nums] + \
           [c for c in pre_cites if c not in post_cites]
    if lost:
        logger.warning("humanize-9b 输出丢失 %d 个锁定要素，弃用", len(lost))
        return None
    return out
