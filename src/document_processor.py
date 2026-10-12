"""
文档处理模块（论文写作智能体）
负责从目录加载知识库文档（md/txt/pdf），分块并附加元数据
"""
import logging
from pathlib import Path
from typing import List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from src.config import get_config_manager

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".md", ".txt", ".pdf", ".json"}


def _strip_yaml_frontmatter(text: str) -> str:
    """去掉 markdown 的 YAML frontmatter（避免污染检索内容）"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


def _load_pdf(path: Path) -> List[Document]:
    """用 pypdf 提取 PDF 全文"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(Document(
                    page_content=text,
                    metadata={"source": str(path), "page": i + 1},
                ))
        return pages
    except Exception as e:
        logger.error(f"PDF 解析失败 {path}: {e}")
        return []


def _load_text_like(path: Path) -> List[Document]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        raw = _strip_yaml_frontmatter(raw)
        if not raw.strip():
            return []
        return [Document(page_content=raw, metadata={"source": str(path)})]
    except Exception as e:
        logger.error(f"文本读取失败 {path}: {e}")
        return []


class DocumentProcessor:
    """知识库文档处理器：目录级加载 + 分块"""

    def __init__(self, config=None):
        if config is None:
            config = get_config_manager().get_config()
        self.config = config
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=150,
            length_function=len,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )

    def load_directory(self, directory: str, source_tag: str = "") -> List[Document]:
        """递归加载目录下所有支持的文档"""
        root = Path(directory)
        if not root.exists():
            logger.warning(f"知识库目录不存在: {root}")
            return []

        documents: List[Document] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            if path.suffix.lower() == ".pdf":
                docs = _load_pdf(path)
            else:
                docs = _load_text_like(path)
            for d in docs:
                d.metadata.setdefault("source", str(path))
                d.metadata["file_name"] = path.name
                d.metadata["method_id"] = path.stem  # 方法库文件名即注册表 method_id
                if source_tag:
                    d.metadata["kb"] = source_tag
            documents.extend(docs)

        logger.info(f"目录 {root} 加载 {len(documents)} 个文档片段")
        return documents

    def split_documents(
        self,
        documents: List[Document],
        collection_tag: str = "",
        base_metadata: Optional[dict] = None,
    ) -> List[Document]:
        """分块并写 chunk_id（带 collection 前缀保证唯一）"""
        if not documents:
            return []
        splits = self.text_splitter.split_documents(documents)
        prefix = collection_tag or "chunk"
        for i, split in enumerate(splits):
            split.metadata["chunk_id"] = f"{prefix}_{i:06d}"
            if base_metadata:
                split.metadata.update(base_metadata)
        logger.info(f"分块完成: {len(documents)} -> {len(splits)} 个块")
        return splits

    def process_directory(
        self,
        directory: str,
        collection_tag: str,
        source_tag: str = "",
        base_metadata: Optional[dict] = None,
    ) -> List[Document]:
        """加载 + 分块一步完成"""
        raw = self.load_directory(directory, source_tag=source_tag)
        return self.split_documents(raw, collection_tag=collection_tag, base_metadata=base_metadata)
