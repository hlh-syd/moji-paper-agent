"""
腾讯朱雀 AI 检测 · 官方 API 客户端（EdgeOne makers 网关）
================================================================
端点: POST https://ai-gateway.edgeone.link/v1/providers/zhuque-text/classify
鉴权: Bearer API Key（EdgeOne 控制台 → Makers → Models → API Key 创建）
额度: 每月免费 50 万 token（论文全篇 4 万字 ≈ 6~8 万 token，可测多轮）

设计约束：
  - key 从 .env 的 ZHUQUE_API_KEY 读取（用户自行填写，不入库不入仓）
  - 无 key / 网络失败 / 额度耗尽 → 返回 None 优雅降级，绝不影响主流程
  - 调用放线程池（由 web 层 to_thread 驱动），超时 30s
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ENDPOINT = "https://ai-gateway.edgeone.link/v1/providers/zhuque-text/classify"
# 单次提交字符上限（实测 4 万字全文可一次送检；网关按 token 计费）
MAX_CHARS = 60000


def _load_key() -> str | None:
    key = os.environ.get("ZHUQUE_API_KEY")
    if key:
        return key.strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for ln in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln.startswith("ZHUQUE_API_KEY") and "=" in ln:
                v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return None


def zhuque_available() -> bool:
    return bool(_load_key())


def zhuque_detect(text: str, merge: bool = True) -> dict | None:
    """朱雀文本检测。merge=True 合并口径（与朱雀网页版算法一致，推荐）；
    返回 {ai_ratio, suspect_ratio, human_ratio, confidence,
    segments:[{text,label,conf}], tokens}；不可用/失败返回 None。
    labels_ratio 语义（官方）："0"=人工占比 "1"=AI占比 "2"=疑似AI占比，均 [0,1]"""
    key = _load_key()
    if not key:
        return None
    body = text.strip()[:MAX_CHARS]
    if len(body) < 30:
        return None
    try:
        import requests
        r = requests.post(_ENDPOINT,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"text": body, "is_merge": merge},
                          timeout=30)
        if r.status_code == 401:
            logger.warning("朱雀 API Key 无效（请检查 .env 的 ZHUQUE_API_KEY）")
            return None
        if r.status_code == 429:
            logger.warning("朱雀 API 限流/额度不足")
            return None
        r.raise_for_status()
        d = r.json()
        if d.get("status") != "success":
            logger.warning("朱雀 API 返回异常: %s", str(d)[:200])
            return None
        lr = d.get("labels_ratio") or {}
        to_f = lambda x: round(float(x or 0), 4)  # noqa: E731
        return {
            "ai_ratio": to_f(lr.get("1")),
            "suspect_ratio": to_f(lr.get("2")),
            "human_ratio": to_f(lr.get("0")),
            "confidence": round(float(d.get("softmax_confidence") or 0), 4),
            "segments": [
                {"text": (s.get("text") or "")[:60],
                 "label": s.get("label"),
                 "conf": round(float(s.get("conf") or 0), 3)}
                for s in (d.get("segment_labels") or [])[:40]],
            "tokens": (d.get("makers_models_usage") or {}).get("total_tokens"),
        }
    except Exception as e:
        logger.warning("朱雀检测失败（降级忽略）: %s", e)
        return None
