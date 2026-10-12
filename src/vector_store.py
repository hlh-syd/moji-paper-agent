"""
向量存储管理器（多 collection 版）
一个持久化目录下管理多个 collection：
  - writing_methodology_zh  论文写作教学知识库
  - method_papers           研究方法原论文知识库
  - project_files           项目脚本/报告检索库
"""
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from src.config import get_config_manager
from src.observability import span as _span

logger = logging.getLogger(__name__)


class VectorStoreManager:
    """多 collection 向量存储管理器"""

    def __init__(self, config=None, embeddings: Optional[Embeddings] = None):
        if config is None:
            config = get_config_manager().get_config()
        self.config = config
        self.vs_config = config.vector_store
        self.embeddings = embeddings
        self._stores: Dict[str, Chroma] = {}

    def _ensure_embeddings(self):
        if self.embeddings is None:
            from src.llm_manager import LLMManager
            self.embeddings = LLMManager(self.config).get_embeddings()

    def _persist_dir(self) -> Path:
        p = Path(self.vs_config.persist_directory)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_store(self, collection_name: str) -> Chroma:
        """获取（或创建）指定 collection 的存储句柄；失效句柄自动重建"""
        if collection_name in self._stores:
            try:
                self._stores[collection_name]._collection.count()
                return self._stores[collection_name]
            except Exception:
                # collection 被外部（如重建脚本）删除，丢弃失效句柄
                logger.info(f"collection[{collection_name}] 句柄已失效，重建句柄")
                self._stores.pop(collection_name, None)
        self._ensure_embeddings()
        store = Chroma(
            persist_directory=str(self._persist_dir()),
            embedding_function=self.embeddings,
            collection_name=collection_name,
        )
        self._stores[collection_name] = store
        return store

    def add_documents(self, collection_name: str, documents: List[Document],
                      batch_size: int = 32) -> int:
        """向指定 collection 追加文档；分批嵌入避免大请求挂起"""
        if not documents:
            return 0
        store = self.get_store(collection_name)
        total = 0
        for start in range(0, len(documents), batch_size):
            batch = documents[start:start + batch_size]
            ids = [
                str(doc.metadata.get("chunk_id") or f"{collection_name}_{start + i}")
                for i, doc in enumerate(batch)
            ]
            store.add_documents(batch, ids=ids)
            total += len(batch)
            logger.info(f"collection[{collection_name}] 入库进度 {total}/{len(documents)}")
        logger.info(f"collection[{collection_name}] 新增 {total} 个文档块")
        return total

    def reset_collection(self, collection_name: str):
        """删除并重建指定 collection（用于全量重建知识库）"""
        try:
            store = self.get_store(collection_name)
            store.delete_collection()
        except Exception as e:
            logger.warning(f"重置 collection[{collection_name}] 时异常（可能不存在）: {e}")
        self._stores.pop(collection_name, None)
        self.get_store(collection_name)
        logger.info(f"collection[{collection_name}] 已重置")

    def collection_exists(self, collection_name: str) -> bool:
        """判断 collection 是否已有数据"""
        try:
            store = self.get_store(collection_name)
            return store._collection.count() > 0
        except Exception:
            return False

    def get_retriever(self, collection_name: str, search_k: Optional[int] = None):
        """获取指定 collection 的检索器"""
        store = self.get_store(collection_name)
        if search_k is None:
            search_k = self.vs_config.search_k
        return store.as_retriever(search_type="similarity", search_kwargs={"k": search_k})

    def similarity_search(self, collection_name: str, query: str, k: Optional[int] = None) -> List[Document]:
        """直接相似检索（带轨迹日志：入参/命中/耗时）；嵌入故障降级返回空——检索是增强信号，
        空结果仅降低证据丰富度，绝不能阻断写作主流程或拖死服务"""
        with _span("vector.similarity_search", kind="tool",
                   in_={"collection": collection_name, "q": query[:120], "k": k}) as _s:
            docs = self._similarity_search_impl(collection_name, query, k)
            _s.out = {"hits": len(docs),
                      "sources": [d.metadata.get("file_name") for d in docs[:3]]}
            return docs

    def _similarity_search_impl(self, collection_name: str, query: str, k: Optional[int] = None) -> List[Document]:
        import time as _time
        if _time.time() < getattr(self, "_embed_down_until", 0.0):
            return []  # 熔断中：嵌入服务故障，跳过检索不再重复等待
        try:
            store = self.get_store(collection_name)
            if k is None:
                k = self.vs_config.search_k
            return store.similarity_search(query, k=k)
        except Exception as e:
            # 熔断 10 分钟：一次失败即跳过后续全部检索（多查询×长超时的叠加等待是生成变慢的主因）
            self._embed_down_until = _time.time() + 600
            logger.warning("相似检索降级并熔断 10 分钟（%s）: %s", collection_name, e)
            return []

    def get_by_method_ids(self, collection_name: str, method_ids: List[str],
                          limit: int = 60) -> List[Document]:
        """按 method_id 元数据精确取块（方法名->原论文的确定性检索，免向量）"""
        if not method_ids:
            return []
        try:
            store = self.get_store(collection_name)
            res = store._collection.get(
                where={"method_id": {"$in": method_ids}},
                include=["documents", "metadatas"],
                limit=limit,
            )
            docs = []
            for text, meta in zip(res.get("documents") or [], res.get("metadatas") or []):
                docs.append(Document(page_content=text, metadata=meta or {}))
            from src.observability import log_span
            log_span("vector.get_by_method_ids", kind="tool",
                     in_={"collection": collection_name, "ids": method_ids[:10], "limit": limit},
                     out={"hits": len(docs)})
            return docs
        except Exception as e:
            logger.warning(f"按 method_id 取块失败: {e}")
            return []

    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        """获取 collection 统计信息"""
        try:
            store = self.get_store(collection_name)
            count = store._collection.count()
            return {
                "collection_name": collection_name,
                "document_count": count,
                "persist_directory": str(self._persist_dir()),
            }
        except Exception as e:
            logger.error(f"获取 collection[{collection_name}] 信息失败: {e}")
            return {"collection_name": collection_name, "document_count": 0, "error": str(e)}

    def get_all_info(self) -> Dict[str, Any]:
        """全部 collection 的状态（供前端知识库面板）"""
        cols = self.vs_config.collections.model_dump()
        return {name: self.get_collection_info(col) for name, col in cols.items()}
