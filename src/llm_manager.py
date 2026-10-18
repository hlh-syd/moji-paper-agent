"""
LLM管理器（论文写作智能体）
双通道设计：
  - 主通道：智谱 OpenAI 兼容 API（GLM），负责大纲生成/正文写作等重任务
  - 辅助通道：本地 Ollama 小模型，负责意图路由/查询改写等轻任务（离线可用）
  - 嵌入：Ollama bge-m3（中文友好）
"""
import logging
from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_ollama import OllamaLLM, OllamaEmbeddings

from src.config import AppConfig, get_config_manager
from src.model_registry import resolve_model
from src.observability import attach_llm_observer

logger = logging.getLogger(__name__)


class LLMManager:
    """双通道 LLM 管理器"""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config_manager().get_config()
        self.main_llm = None       # 智谱主写作模型
        self.small_llm = None      # 本地小模型
        self.embeddings = None
        self._init_main_llm()
        self._init_small_llm()
        self._init_embeddings()

    # ---------- 主通道：智谱 / DeepSeek ----------
    def _init_main_llm(self):
        """初始化主写作模型（OpenAI 兼容接口）：按模型名自动选择供应商"""
        route = resolve_model(self.config.models.llm, self.config)
        self._main_route = route
        if not route.api_key:
            logger.warning(
                f"未配置 {route.key_env}，{route.label}主写作通道不可用（大纲/正文生成将失败）。"
                "请在设置页填写 API Key，或在 论文智能体/.env 中设置"
            )
            self.main_llm = None
            return
        try:
            kwargs = dict(
                model=route.model,
                api_key=route.api_key,
                base_url=route.base_url,
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                timeout=route.timeout,
                streaming=True,
            )
            if route.extra_body:
                kwargs["extra_body"] = route.extra_body
            self.main_llm = ChatOpenAI(**kwargs)
            attach_llm_observer(self.main_llm, "main")
            logger.info(f"{route.label}主写作模型初始化成功: {route.model}")
        except Exception as e:
            logger.error(f"{route.label}主写作模型初始化失败: {e}")
            self.main_llm = None

    def reinit_main_llm(self):
        """API key 更新后重新初始化主通道"""
        self._init_main_llm()

    def main_llm_available(self) -> bool:
        return self.main_llm is not None

    # ---------- 辅助通道：本地 Ollama ----------
    def _init_small_llm(self):
        """初始化本地小模型（意图路由/查询改写，可离线）"""
        try:
            self.small_llm = OllamaLLM(
                model=self.config.models.small_llm,
                base_url=self.config.ollama.base_url,
                temperature=0.1,
                num_predict=512,
                client_kwargs={"timeout": self.config.ollama.timeout},
            )
            attach_llm_observer(self.small_llm, "small")
            logger.info(f"本地小模型初始化成功: {self.config.models.small_llm}")
        except Exception as e:
            logger.warning(f"本地小模型初始化失败（辅助功能降级）: {e}")
            self.small_llm = None

    # ---------- 嵌入 ----------
    def _init_embeddings(self):
        try:
            self.embeddings = OllamaEmbeddings(
                model=self.config.models.embedding,
                base_url=self.config.ollama.base_url,
                keep_alive="-1",  # 常驻显存：空闲 5 分钟被逐出后重载期间会持续 502
                client_kwargs={"timeout": self.config.ollama.timeout},
            )
            logger.info(f"嵌入模型初始化成功: {self.config.models.embedding}")
        except Exception as e:
            logger.error(f"嵌入模型初始化失败: {e}")
            raise

    # ---------- 获取实例 ----------
    def get_llm(self):
        """兼容旧接口：返回主写作模型（不可用时回退本地小模型）"""
        if self.main_llm is not None:
            return self.main_llm
        if self.small_llm is not None:
            logger.warning("主模型不可用，回退本地小模型")
            return self.small_llm
        raise RuntimeError("无可用 LLM：请配置 ZHIPU_API_KEY 或启动本地 Ollama")

    def get_main_llm(self):
        if self.main_llm is None:
            route = getattr(self, "_main_route", None)
            env = route.key_env if route else "ZHIPU_API_KEY"
            raise RuntimeError(f"主写作模型未初始化：请配置 {env} 并重启服务")
        return self.main_llm

    def get_small_llm(self):
        if self.small_llm is None:
            raise RuntimeError("本地小模型未初始化：请启动 Ollama 并拉取 qwen3:4b-instruct")
        return self.small_llm

    def get_embeddings(self) -> OllamaEmbeddings:
        if self.embeddings is None:
            self._init_embeddings()
        return self.embeddings

    def get_model_info(self) -> Dict[str, Any]:
        route = getattr(self, "_main_route", None)
        return {
            "provider": route.provider if route else self.config.models.provider,
            "provider_label": route.label if route else "",
            "api_model": route.model if route else self.config.models.llm,
            "main_llm": self.config.models.llm,
            "analyst_llm": getattr(self.config.models, "analyst_llm", ""),
            "vision_llm": getattr(getattr(self.config, "vision", None), "model", ""),
            "main_llm_available": self.main_llm is not None,
            "small_llm": self.config.models.small_llm,
            "small_llm_available": self.small_llm is not None,
            "embedding_model": self.config.models.embedding,
            "temperature": self.config.models.temperature,
            "max_tokens": self.config.models.max_tokens,
            "ollama_url": self.config.ollama.base_url,
            "zhipu_base_url": self.config.zhipu.base_url,
        }
