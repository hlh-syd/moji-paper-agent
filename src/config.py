"""
配置管理模块（论文写作智能体）
"""
import yaml
import os
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """模型配置：主写作通道 + 本地小模型 + 嵌入（统一 GLM-5.3 系列，禁用 4.x/4V）"""
    provider: str = "zhipu"
    llm: str = "glm-5.3"
    analyst_llm: str = "glm-5.3-flash"  # 项目理解 Agent 专属模型（独立于主写作模型）
    small_llm: str = "qwen3:4b-instruct"
    embedding: str = "bge-m3"
    temperature: float = 0.4
    max_tokens: int = 16384


class ZhipuConfig(BaseModel):
    """智谱 OpenAI 兼容 API 配置"""
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    api_key_env: str = "ZHIPU_API_KEY"
    timeout: int = 300

    def resolve_api_key(self) -> Optional[str]:
        """从环境变量解析 API key；支持直接在 yaml 写 api_key（不推荐）"""
        direct = getattr(self, "api_key", None)
        if direct:
            return direct
        return os.environ.get(self.api_key_env)


class QualityConfig(BaseModel):
    """质量管线配置：择优/审稿轮次"""
    bestof_n: int = 3
    bestof_sections: List[str] = Field(default_factory=lambda: ["摘要", "前言", "讨论"])
    review_rounds: int = 2


class VisionConfig(BaseModel):
    """视觉代理（多模态图片理解）配置——GLM-5.3-Flash（5.3 系列中唯一原生多模态）"""
    enabled: bool = True
    model: str = "glm-5.3-flash"
    base_url: str = "https://open.bigmodel.cn/api/coding/paas/v4/"
    timeout: int = 120
    max_images: int = 4
    max_profile_images: int = 24   # 画像深化时分析的项目图片数上限


class OllamaConfig(BaseModel):
    """本地 Ollama 配置（小模型 + 嵌入）"""
    base_url: str = "http://localhost:11434"
    timeout: int = 120


class CollectionsConfig(BaseModel):
    """向量库 collection 名称"""
    writing_methodology: str = "writing_methodology_zh"
    method_papers: str = "method_papers"
    project_files: str = "project_files"


class VectorStoreConfig(BaseModel):
    """向量存储配置"""
    persist_directory: str = "./chroma_db"
    search_k: int = 6
    collections: CollectionsConfig = Field(default_factory=CollectionsConfig)


class RAGConfig(BaseModel):
    """RAG 相关配置"""
    enable_function_call: bool = False
    function_call_mode: str = "auto"


class RAGAnythingModelsConfig(BaseModel):
    """RAG-Anything 引擎用模型（统一 GLM-5.3 系列）"""
    llm: str = "glm-5.3"
    vision: str = "glm-5.3-flash"
    light: str = "glm-5.3-flash"


class RAGAnythingEmbeddingConfig(BaseModel):
    """RAG-Anything 嵌入配置（复用 Ollama bge-m3）"""
    dim: int = 1024
    batch_size: int = 32


class RAGAnythingConfig(BaseModel):
    """RAG-Anything 多模态文献引擎配置：MinerU 解析 + LightRAG 多模态知识图谱"""
    enabled: bool = True
    workspace: str = "./lightrag_storage/references"
    parser: str = "mineru"            # mineru | docelement
    parse_method: str = "auto"        # auto | txt | ocr
    device: str = "cuda"              # cuda | cpu
    backend: str = "pipeline"         # mineru 3.4: pipeline | vlm-engine | hybrid-engine
    model_source: str = "modelscope"  # MinerU 模型下载源: auto|huggingface|modelscope|local
    auto_ingest_on_upload: bool = True
    query_mode: str = "hybrid"        # hybrid | local | global | naive
    query_top_k: int = 12
    models: RAGAnythingModelsConfig = Field(default_factory=RAGAnythingModelsConfig)
    embedding: RAGAnythingEmbeddingConfig = Field(default_factory=RAGAnythingEmbeddingConfig)


class KnowledgeBaseConfig(BaseModel):
    """知识库路径配置"""
    writing_dir: str = "./knowledge_base/writing_methodology"
    methods_dir: str = "./knowledge_base/method_papers"
    registry_dir: str = "./ingestion"
    project_root: str = ""   # 已废弃：项目由用户在界面注册，不再绑定默认脚本目录


class WorkspaceConfig(BaseModel):
    """工作区（项目扫描位置）配置"""
    exclude_dirs: List[str] = Field(default_factory=list,
                                    description="list_projects 扫描时排除的目录名")


class OutlineSectionRatio(BaseModel):
    """大纲字数模板（单模板）：sections=比例，caps=封顶（超出重分配）"""
    name: str = "中文论文"
    total_words: int = 30000
    sections: Dict[str, float] = Field(default_factory=dict)
    caps: Dict[str, int] = Field(default_factory=dict)


class OutlineConfig(BaseModel):
    """大纲生成配置：中文期刊字数模板集合"""
    templates: Dict[str, OutlineSectionRatio] = Field(default_factory=dict)


class SystemConfig(BaseModel):
    """系统基本信息"""
    name: str = "墨记 论文智能体"
    version: str = "3.1.0"
    mode: str = "enhanced"
    workspace_root: str = ""   # 空=未设置，首次使用在界面选择根目录；选择后持久化
    default_domain: str = "auto"  # auto|medical|cs|generic（注册项目时的默认领域）


class ServerConfig(BaseModel):
    """服务器运行配置"""
    host: str = "0.0.0.0"
    port: int = 8000
    enable_websocket: bool = True


class ToolsConfig(BaseModel):
    """工具插件配置"""
    enabled_categories: List[str] = Field(default_factory=lambda: ["paper"])
    disabled_tools: List[str] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    """日志配置（全链路：trace_id/user 自动附加，按大小轮转，LLM 调用观测）"""
    level: str = "INFO"
    file: str = "logs/paper_agent.log"
    format: str = "json"          # text | json（json 便于接 ELK/Loki）
    rotate_mb: int = 10           # 单文件上限，超出轮转
    backup_count: int = 5         # 轮转保留份数
    llm_calls: bool = True        # LLM 调用观测（logs/llm_calls.jsonl）


class ConversationConfig(BaseModel):
    """对话相关配置"""
    max_messages: int = 50
    summary_min_messages: int = 8
    summary_interval: int = 5
    enable_auto_summary: bool = True


class ExportStyleConfig(BaseModel):
    """导出排版默认值（网页预览的排版设置会以同名查询参数覆盖此处）"""
    body_font_px: float = 16        # 正文字号 px（docx 按 pt=px*0.75 折算 → 12pt 小四）
    line_height: float = 1.5        # 行距倍数
    first_indent_em: float = 2.0    # 首行缩进 em（0=不缩进）
    body_align: str = "justify"     # 正文对齐 justify | left
    table_align: str = "center"     # 表格位置 center | left
    table_width: str = "full"       # 表格宽度 full=撑满页宽 | auto=适应内容
    table_font_px: float = 14       # 表格字号 px（→10.5pt 五号）
    cell_align: str = "center"      # 单元格文字 center | left
    fig_width_pct: int = 100        # 图片宽度百分比（100%→5.6英寸）


class AppConfig(BaseModel):
    """应用整体配置结构"""
    system: SystemConfig = Field(default_factory=SystemConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    zhipu: ZhipuConfig = Field(default_factory=ZhipuConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    rag_anything: RAGAnythingConfig = Field(default_factory=RAGAnythingConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    outline: OutlineConfig = Field(default_factory=OutlineConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    export_style: ExportStyleConfig = Field(default_factory=ExportStyleConfig)


class ConfigManager:
    """配置管理器，用于加载、保存和更新应用程序配置"""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "config.yaml"
        self.config = self.load_config()

    def load_config(self) -> AppConfig:
        """加载并解析 YAML 配置文件；失败则返回默认配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f) or {}
                return AppConfig(**config_data)
            except Exception as e:
                print(f"加载配置文件失败，使用默认配置: {e}")
        return AppConfig()

    def get_config(self) -> AppConfig:
        return self.config

    def save_config(self, config: Optional[AppConfig] = None):
        if config is None:
            config = self.config
        with open(self.config_path, 'w', encoding='utf-8') as f:
            config_dict = config.model_dump()
            yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, indent=2)

    def update_config(self, updates: Dict[str, Any]):
        config_dict = self.config.model_dump()
        self._update_dict(config_dict, updates)
        self.config = AppConfig(**config_dict)
        self.save_config()

    def _update_dict(self, target: Dict[str, Any], updates: Dict[str, Any]):
        for key, value in updates.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._update_dict(target[key], value)
            else:
                target[key] = value


_config_manager = None


def get_config_manager(config_path: Optional[str] = None) -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_path)
    return _config_manager
