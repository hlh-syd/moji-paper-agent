# -*- coding: utf-8 -*-
"""模型供应商路由

设置页三个下拉（主写作 / 项目理解 / 图片理解）共用一份候选清单；
按模型名把界面选项解析为 OpenAI 兼容客户端参数：
  - glm-*       → 智谱 Coding Plan 端点（关闭深度思考，输出干净正文）
  - deepseek-*  → DeepSeek 端点；展示名 deepseek-4.1-flash，真实 model id 取
                  config.deepseek.model_id（不同网关命名不一致时可改配置）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.config import AppConfig

# 三个模型下拉的候选（顺序即展示顺序）；新增模型在此登记并同步设置页说明
MODEL_OPTIONS = ["glm-5.3", "glm-5.3-flash", "deepseek-4.1-flash"]

DEEPSEEK_PREFIX = "deepseek"


@dataclass
class ModelRoute:
    """一次模型调用所需的连接参数"""
    provider: str                 # zhipu | deepseek
    model: str                    # 真正发给服务端的 model id
    base_url: str
    api_key: Optional[str]
    timeout: int
    extra_body: Dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def key_env(self) -> str:
        return "DEEPSEEK_API_KEY" if self.provider == "deepseek" else "ZHIPU_API_KEY"

    @property
    def label(self) -> str:
        return "DeepSeek" if self.provider == "deepseek" else "智谱"


def is_deepseek(model: str) -> bool:
    return (model or "").strip().lower().startswith(DEEPSEEK_PREFIX)


def resolve_model(model: str, config: AppConfig) -> ModelRoute:
    """界面模型名 → 供应商连接参数（未知前缀一律按智谱处理，保持旧行为）"""
    model = (model or "").strip()
    if is_deepseek(model):
        d = config.deepseek
        return ModelRoute(
            provider="deepseek",
            model=(d.model_id or "deepseek-flash").strip(),
            base_url=(d.base_url or "https://api.deepseek.com").strip(),
            api_key=d.resolve_api_key(),
            timeout=d.timeout,
            # 写作/理解/看图都用非思考模式：输出直接、速度快，且思考 token 不会挤占
            # max_tokens 导致正文为空或截断（接口实测支持该参数）
            extra_body={"thinking": {"type": "disabled"}},
        )
    z = config.zhipu
    return ModelRoute(
        provider="zhipu",
        model=model or config.models.llm,
        base_url=z.base_url,
        api_key=z.resolve_api_key(),
        timeout=z.timeout,
        extra_body={"thinking": {"type": "disabled"}},
    )
