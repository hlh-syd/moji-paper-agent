# -*- coding: utf-8 -*-
"""
视觉代理（VisionAgent）
作用：主会话收到图片时，调用多模态视觉模型查看图片并结合用户意图给出分析，
分析结果作为上下文回流主会话 LLM——弥补文本主模型看不到图片的问题。
- 图片来源：会话附件路径 / 资源管理器拖入的 dataURL
- 模型：GLM-5.3（原生多模态，Coding Plan 端点，与主通道统一）
- 失败降级：明确告知"视觉代理不可用"而非静默
"""
import base64
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是论文写作助手的视觉分析代理。用户会给你一张图片（通常是科研图表、流程图、"
    "结果图或截图）和一个问题。请："
    "1) 客观描述图中可见内容（图的类型、坐标轴/标签、数据模式、文字标注、结构组成）；"
    "2) 直接回答用户的问题；"
    "3) 只描述图中真实存在的内容，不得编造图中不存在的数据或结论；"
    "4) 如果图片无法辨认，如实说明。回答控制在 400 字以内。"
)


class VisionAgent:
    """多模态视觉子代理"""

    def __init__(self, config):
        self.config = config
        self._client = None
        self._client_sig = None
        self._api_model = None
        self.reload_config()

    def reload_config(self):
        """设置页热更新后刷新模型/端点（同时丢弃旧客户端）"""
        v = getattr(self.config, "vision", None)
        self.enabled = bool(v and v.enabled)
        self.model = v.model if v else "glm-5.3-flash"
        self.base_url = v.base_url if v else "https://open.bigmodel.cn/api/coding/paas/v4/"
        self.timeout = v.timeout if v else 120
        self.max_images = v.max_images if v else 4
        self._client = None
        self._client_sig = None

    def _get_client(self):
        from src.model_registry import resolve_model
        route = resolve_model(self.model, self.config)
        if not route.api_key:
            raise RuntimeError(f"{route.key_env} 未配置，视觉代理不可用")
        sig = (route.api_key, route.base_url)
        if self._client is None or self._client_sig != sig:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=route.api_key, base_url=route.base_url,
                                       timeout=self.timeout)
            self._client_sig = sig
        self._api_model = route.model
        return self._client

    @staticmethod
    def _to_data_url(image: str) -> str:
        """path 或 dataURL 统一转为 dataURL"""
        if image.startswith("data:image"):
            return image
        p = Path(image)
        if not p.is_file():
            raise FileNotFoundError(f"图片不存在: {image}")
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
                "webp": "webp", "bmp": "bmp"}.get(ext, "png")
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f"data:image/{mime};base64,{b64}"

    async def analyze(self, image: str, question: str = "", fig_name: str = "") -> str:
        """分析单张图片。image: 文件路径或 dataURL。返回分析文本。"""
        if not self.enabled:
            raise RuntimeError("视觉代理未启用")
        import time as _time
        _t0 = _time.perf_counter()
        try:
            data_url = self._to_data_url(image)
            client = self._get_client()
            user_text = question.strip() or "请描述这张图片的内容。"
            prompt = f"图片文件名：{fig_name or '（未命名）'}\n用户问题：{user_text}"
            resp = await client.chat.completions.create(
                model=self._api_model or self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ]},
                ],
                max_tokens=1024,
                temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()
        finally:
            from src import observability as _obs
            _obs.log_span("llm.vision", kind="llm", in_={"fig": fig_name or None},
                          out={"model": self.model},
                          duration_ms=round((_time.perf_counter() - _t0) * 1000))
