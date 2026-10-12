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

    # ---------- 主通道：智谱 ----------
    def _init_main_llm(self):
        """初始化智谱主写作模型（OpenAI 兼容接口）"""
        api_key = self.config.zhipu.resolve_api_key()
        if not api_key:
            logger.warning(
                "未配置 ZHIPU_API_KEY，主写作通道不可用（大纲/正文生成将失败）。"
                "请在 论文智能体/.env 中设置 ZHIPU_API_KEY=..."
            )
            self.main_llm = None
            return
        try:
            self.main_llm = ChatOpenAI(
                model=self.config.models.llm,
                api_key=api_key,
                base_url=self.config.zhipu.base_url,
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                timeout=self.config.zhipu.timeout,
                streaming=True,
                # GLM-4.x 系列关闭深度思考，输出干净正文、速度更快
                extra_body={"thinking": {"type": "disabled"}},
            )
            attach_llm_observer(self.main_llm, "main")
            logger.info(f"智谱主写作模型初始化成功: {self.config.models.llm}")
        except Exception as e:
            logger.error(f"智谱主写作模型初始化失败: {e}")
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
            raise RuntimeError("智谱主写作模型未初始化：请设置 ZHIPU_API_KEY 并重启服务")
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
        return {
            "provider": self.config.models.provider,
            "main_llm": self.config.models.llm,
            "main_llm_available": self.main_llm is not None,
            "small_llm": self.config.models.small_llm,
            "small_llm_available": self.small_llm is not None,
            "embedding_model": self.config.models.embedding,
            "temperature": self.config.models.temperature,
            "max_tokens": self.config.models.max_tokens,
            "ollama_url": self.config.ollama.base_url,
            "zhipu_base_url": self.config.zhipu.base_url,
        }
