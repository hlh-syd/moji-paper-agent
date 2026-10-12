# -*- coding: utf-8 -*-
"""
RAG-Anything 多模态文献引擎
=============================
基于 HKUDS/RAG-Anything + LightRAG，弥补参考文献 PDF 的图表/公式/表格盲区：
  MinerU 解析 PDF（图/表/公式/文本分块）→ VLM(GLM-5.3) 看图写描述、表格转语义文本、
  LaTeX 解析 → LightRAG 多模态知识图谱（文本实体+多模态实体混合索引）→ 模态感知检索。

模型统一约束：本引擎内 LLM/视觉全部走 GLM-5.3 / GLM-5.3-Flash（Coding Plan 端点），
嵌入复用本地 Ollama bge-m3（1024 维），与主系统共用一套 .env 配置。

设计要点：
  - 懒初始化：首次入库/查询时才构建 LightRAG + RAGAnything（导入失败不阻塞主服务）
  - manifest.json 去重：同文件（大小+mtime 一致）不重复解析入库
  - 全异步接口；同步上下文请用 asyncio.to_thread + query_blocking
"""
import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.config import AppConfig, get_config_manager

logger = logging.getLogger(__name__)

_RETRYABLE = ("timeout", "timed out", "connection", "rate", "429", "502", "503", "504",
              "overloaded", "temporarily", "1113")


def _is_transient(err: Exception) -> bool:
    text = str(err).lower()
    return any(k in text for k in _RETRYABLE)


async def _chat_with_retry(client, *, model: str, messages: list, max_tokens: int,
                           temperature: float, timeout: int, retries: int = 3) -> str:
    """带瞬时错误重试的 GLM 调用（thinking 关闭，输出干净文本）"""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body={"thinking": {"type": "disabled"}},
                timeout=timeout,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt >= retries or not _is_transient(e):
                raise
            await asyncio.sleep(2 ** attempt * 1.5)
    raise last  # pragma: no cover


# ==================== GLM-5.3 系列模型适配（LightRAG/RAG-Anything 接口） ====================

def make_llm_model_func(model: str, api_key: str, base_url: str, timeout: int = 300):
    """构造 LightRAG 兼容的 llm_model_func：async (prompt, system_prompt=None,
    history_messages=[], keyword_extraction=False, **kwargs) -> str"""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    async def glm_llm_model_func(
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[List[dict]] = None,
        keyword_extraction: bool = False,
        **kwargs: Any,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages += (history_messages or [])
        messages.append({"role": "user", "content": prompt})
        # 关键词抽取走低温短输出；正文生成稍高温度
        temp = 0.1 if keyword_extraction else 0.4
        max_tokens = 2048 if keyword_extraction else 8192
        return await _chat_with_retry(
            client, model=model, messages=messages,
            max_tokens=max_tokens, temperature=temp, timeout=timeout)

    glm_llm_model_func.model_name = model  # type: ignore[attr-defined]
    return glm_llm_model_func


def make_vision_model_func(model: str, api_key: str, base_url: str, timeout: int = 300):
    """构造 RAG-Anything 兼容的 vision_model_func：
    async (image_path: str, prompt: str, system_prompt: str = None) -> str
    GLM-5.3-Flash 原生多模态（OpenAI image_url 格式），替代已停用的 GLM-4.5V 链路。
    注意 glm-5.3 为纯文本，视觉必须走 glm-5.3-flash。"""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    _MIME = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".bmp": "bmp",
             ".gif": "gif", ".webp": "webp", ".tiff": "tiff", ".tif": "tiff"}

    async def glm_vision_model_func(image_path: str = "", prompt: str = "",
                                    system_prompt: Optional[str] = None,
                                    messages: Optional[list] = None) -> str:
        # 调用风格 B（查询期 VLM 增强）：raganything 直接传组装好的 OpenAI messages
        # （其中 image_url 已替换为 base64 dataURL），image_path/prompt 为空
        if messages is not None:
            out = await _chat_with_retry(
                client, model=model, messages=messages,
                max_tokens=4096, temperature=0.2, timeout=timeout)
            if not out:  # flash 偶发空回复，重试一次
                out = await _chat_with_retry(
                    client, model=model, messages=messages,
                    max_tokens=4096, temperature=0.4, timeout=timeout)
            return out
        # 调用风格 A（入库期图表分析）：image_path + prompt
        p = Path(image_path)
        if not p.is_file():
            raise FileNotFoundError(f"图片不存在: {image_path}")
        mime = _MIME.get(p.suffix.lower(), "png")
        b64 = base64.b64encode(p.read_bytes()).decode()
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        })
        out = await _chat_with_retry(
            client, model=model, messages=msgs,
            max_tokens=2048, temperature=0.2, timeout=timeout)
        if not out:  # flash 偶发空回复，重试一次（提高温度）
            out = await _chat_with_retry(
                client, model=model, messages=msgs,
                max_tokens=2048, temperature=0.4, timeout=timeout)
        return out

    glm_vision_model_func.model_name = model  # type: ignore[attr-defined]
    return glm_vision_model_func


def make_embedding_func(model: str, ollama_base_url: str, batch_size: int = 32):
    """构造 LightRAG 兼容 embedding_func：async (texts: List[str]) -> np.ndarray
    直连 Ollama /api/embed（bge-m3，1024 维），与 Chroma 知识库共用同一嵌入模型。
    必须返回 numpy 数组：LightRAG 的 EmbeddingFunc 校验用 result.size。"""
    import numpy as np
    url = f"{ollama_base_url.rstrip('/')}/api/embed"

    async def bge_m3_embedding_func(texts: List[str]) -> "np.ndarray":
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        texts = [t if t.strip() else " " for t in texts]
        out: List[List[float]] = []
        # trust_env=False：绕过系统代理（本地 Ollama 不该走代理，否则 502/超时）
        async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
            for start in range(0, len(texts), batch_size):
                batch = texts[start:start + batch_size]
                resp = await client.post(url, json={"model": model, "input": batch})
                resp.raise_for_status()
                out.extend(resp.json()["embeddings"])
        return np.asarray(out, dtype=np.float32)

    return bge_m3_embedding_func


# ==================== 引擎主体 ====================

class RAGAnythingEngine:
    """RAG-Anything + LightRAG 封装（参考文献 PDF 多模态入库与检索）"""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config_manager().get_config()
        ra = self.config.rag_anything
        self.enabled = ra.enabled
        self.workspace = Path(ra.workspace)
        self.manifest_path = self.workspace / "manifest.json"
        self._rag = None            # RAGAnything 实例
        self._lightrag = None       # LightRAG 实例
        self._init_lock = asyncio.Lock()
        self._ingest_lock = asyncio.Lock()
        self._import_error: Optional[str] = None
        self._init_error: Optional[str] = None
        # 后台任务状态（供前端轮询）
        self.job = {"running": False, "current": "", "done": [], "failed": [],
                    "started_at": None, "finished_at": None}

    # ---------- 可用性 ----------
    def available(self) -> bool:
        """依赖已装且初始化成功"""
        return self._rag is not None

    def diagnostics(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "ready": self.available(),
                "import_error": self._import_error, "init_error": self._init_error}

    def _api_key(self) -> Optional[str]:
        return self.config.zhipu.resolve_api_key()

    # ---------- 懒初始化 ----------
    async def _ensure_ready(self) -> bool:
        if self._rag is not None:
            return True
        if not self.enabled:
            self._init_error = "rag_anything.enabled=false"
            return False
        async with self._init_lock:
            if self._rag is not None:
                return True
            try:
                self._rag, self._lightrag = await self._build()
                logger.info("RAG-Anything 引擎初始化成功: %s", self.workspace)
                return True
            except ImportError as e:
                self._import_error = str(e)
                logger.warning("RAG-Anything 依赖未安装（功能停用）: %s", e)
                return False
            except Exception as e:  # noqa: BLE001
                self._init_error = f"{type(e).__name__}: {e}"
                logger.error("RAG-Anything 引擎初始化失败: %s", e, exc_info=True)
                return False

    async def _build(self):
        """构建 LightRAG + RAGAnything 实例（GLM-5.3 系列 + bge-m3）"""
        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc
        from raganything import RAGAnything
        from raganything.config import RAGAnythingConfig

        ra = self.config.rag_anything
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("ZHIPU_API_KEY 未配置，RAG-Anything 引擎不可用")
        self.workspace.mkdir(parents=True, exist_ok=True)
        # MinerU 模型下载源（国内默认 modelscope；不覆盖用户已设值）
        import os
        import sys
        if ra.model_source:
            os.environ.setdefault("MINERU_MODEL_SOURCE", ra.model_source)
        # 本地回环不走系统代理：mineru 客户端要探测 127.0.0.1 的本地 API 健康状态，
        # 系统代理会劫持 localhost 请求导致"Timed out waiting for local mineru-api"
        for _var in ("NO_PROXY", "no_proxy"):
            _addrs = [a.strip() for a in os.environ.get(_var, "").split(",") if a.strip()]
            for _host in ("localhost", "127.0.0.1"):
                if _host not in _addrs:
                    _addrs.append(_host)
            os.environ[_var] = ",".join(_addrs)
        # raganything 以子进程调用 mineru CLI：确保 venv Scripts 目录在 PATH
        # （用 python.exe 直接启动服务时 .venv/Scripts 不在 PATH，会导致误报"未安装"）
        scripts_dir = str(Path(sys.executable).resolve().parent)
        if scripts_dir.lower() not in [p.lower() for p in os.environ.get("PATH", "").split(os.pathsep) if p]:
            os.environ["PATH"] = scripts_dir + os.pathsep + os.environ.get("PATH", "")

        llm_func = make_llm_model_func(
            ra.models.llm, api_key, self.config.zhipu.base_url,
            timeout=self.config.zhipu.timeout)
        vision_func = make_vision_model_func(
            ra.models.vision, api_key, self.config.zhipu.base_url,
            timeout=self.config.zhipu.timeout)
        embed_raw = make_embedding_func(
            self.config.models.embedding, self.config.ollama.base_url,
            batch_size=ra.embedding.batch_size)
        embedding_func = EmbeddingFunc(
            embedding_dim=ra.embedding.dim, func=embed_raw)

        lightrag = LightRAG(
            working_dir=str(self.workspace),
            llm_model_func=llm_func,
            embedding_func=embedding_func,
        )
        ra_config = RAGAnythingConfig(
            working_dir=str(self.workspace),
            parser=ra.parser,                 # mineru | docelement
            parse_method=ra.parse_method,     # auto | txt | ocr
            parser_output_dir=str(self.workspace / "parsed"),
        )
        rag = RAGAnything(
            lightrag=lightrag,
            llm_model_func=llm_func,
            vision_model_func=vision_func,
            embedding_func=embedding_func,
            config=ra_config,
        )
        # 初始化存储（json KV + nano 向量默认后端，无需外部服务）
        try:
            await lightrag.initialize_storages()
        except Exception as e:  # noqa: BLE001
            logger.warning("initialize_storages 异常（默认存储可能自动就绪）: %s", e)
        return rag, lightrag

    # ---------- manifest ----------
    def _load_manifest(self) -> Dict[str, Any]:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_manifest(self, manifest: Dict[str, Any]):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _file_key(path: Path) -> Optional[str]:
        try:
            stat = path.stat()
            return f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}"
        except OSError:
            return None

    # ---------- 入库 ----------
    async def ingest_paper(self, path_str: str, force: bool = False) -> Dict[str, Any]:
        """解析并入库单个文献 PDF（幂等：manifest 去重）"""
        path = Path(path_str)
        if not path.is_file():
            raise FileNotFoundError(f"文献不存在: {path}")
        if not await self._ensure_ready():
            raise RuntimeError("RAG-Anything 引擎不可用: " +
                               (self._import_error or self._init_error or "未初始化"))
        key = self._file_key(path)
        manifest = self._load_manifest()
        if not force and manifest.get(key, {}).get("status") == "ok":
            return {"skipped": True, "file": str(path), "reason": "已入库（未变化）"}

        async with self._ingest_lock:
            ra = self.config.rag_anything
            out_dir = self.workspace / "parsed" / path.stem
            t0 = time.time()
            # file_path/output_dir/parse_method 为命名参数；
            # device/backend 等经 **kwargs 透传给 MinerU CLI（见 process_document_complete 文档）
            kwargs: Dict[str, Any] = dict(
                file_path=str(path),
                output_dir=str(out_dir),
                parse_method=ra.parse_method,
                device=ra.device,
                backend=ra.backend,
            )
            if force:
                # 重跑多模态处理（PDF 解析有 parse_cache 缓存不会重复跑）；
                # 换视觉模型/存储后端后用这个刷新图表实体
                kwargs["force_multimodal_reprocess"] = True
            result = await self._rag.process_document_complete(**kwargs)

            record = manifest.get(key) or {}
            record.update({
                "file": str(path), "status": "ok",
                "ingested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "seconds": round(time.time() - t0, 1),
                "parse_output": str(out_dir),
            })
            if isinstance(result, dict):
                record["result_keys"] = list(result.keys())[:8]
            manifest[key] = record
            # 清理同文件旧键（重命名/时间戳变化后遗留）
            for k in [k for k, v in manifest.items()
                      if k != key and v.get("file") == str(path)]:
                manifest.pop(k)
            self._save_manifest(manifest)
            logger.info("文献入库完成: %s (%.1fs)", path.name, time.time() - t0)
            return {"skipped": False, "file": str(path),
                    "seconds": record["seconds"], "output": str(out_dir)}

    def _ensure_sync(self) -> bool:
        """同步上下文检查（不发网络请求）——若未初始化返回 False"""
        return self._rag is not None

    async def ingest_ready(self) -> bool:
        return await self._ensure_ready()

    async def ingest_reference_dir(self, force: bool = False) -> Dict[str, Any]:
        """批量入库 reference_papers/ 下全部 PDF"""
        ref_dir = Path(__file__).resolve().parents[1] / "reference_papers"
        pdfs = sorted(ref_dir.glob("*.pdf"))
        if not pdfs:
            return {"total": 0, "ingested": 0, "failed": 0}
        if not await self._ensure_ready():
            raise RuntimeError("RAG-Anything 引擎不可用: " +
                               (self._import_error or self._init_error or "未初始化"))
        ok = fail = 0
        errors = []
        for pdf in pdfs:
            self.job["current"] = pdf.name
            try:
                await self.ingest_paper(str(pdf), force=force)
                ok += 1
                self.job["done"].append(pdf.name)
            except Exception as e:  # noqa: BLE001
                fail += 1
                errors.append({"file": pdf.name, "error": str(e)})
                self.job["failed"].append(pdf.name)
                logger.error("文献入库失败 %s: %s", pdf.name, e)
        return {"total": len(pdfs), "ingested": ok, "failed": fail, "errors": errors}

    def start_background_ingest(self, force: bool = False) -> bool:
        """启动后台批量入库任务（不阻塞请求；状态见 self.job / status()）"""
        if self.job["running"]:
            return False

        async def _run():
            self.job.update(running=True, current="", done=[], failed=[],
                            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                            finished_at=None, error=None)
            try:
                await self.ingest_reference_dir(force=force)
            except Exception as e:  # noqa: BLE001
                self.job["error"] = str(e)
                logger.error("后台批量入库失败: %s", e, exc_info=True)
            finally:
                self.job["running"] = False
                self.job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run())
        except RuntimeError:
            asyncio.run(self.ingest_reference_dir(force=force))
        return True

    # ---------- 检索 ----------
    async def aquery(self, question: str, mode: Optional[str] = None,
                     top_k: Optional[int] = None,
                     only_context: bool = False) -> str:
        """多模态知识图谱检索问答（hybrid 默认）。
        only_context=True 只返回检索到的上下文（图/表/公式实体描述+原文块），
        不做 LLM 答案合成——供写作链路注入用，省一次模型调用。"""
        if not await self._ensure_ready():
            raise RuntimeError("RAG-Anything 引擎不可用")
        ra = self.config.rag_anything
        mode = mode or ra.query_mode
        top_k = top_k or ra.query_top_k
        param = None
        try:
            from lightrag import QueryParam
            param = QueryParam(mode=mode, top_k=top_k,
                               only_need_context=only_context)
        except ImportError:
            pass
        if only_context:
            # 直接走 LightRAG：多模态描述已作为文本实体入图谱，无需 RAGAnything 层
            try:
                if param is not None:
                    return await self._lightrag.aquery(question, param=param)
                return await self._lightrag.aquery(question, mode=mode, top_k=top_k)
            except TypeError:
                return await self._lightrag.aquery(question, mode=mode)
        # 完整问答走 RAGAnything（检索命中图片时可 VLM 增强）；失败退回 LightRAG 文本问答
        try:
            return await self._rag.aquery(question, mode=mode, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            logger.warning("RAGAnything VLM 增强查询失败，退回 LightRAG 文本查询: %s", e)
            if param is not None:
                return await self._lightrag.aquery(question, param=param)
            return await self._lightrag.aquery(question, mode=mode)

    def query_blocking(self, question: str, mode: Optional[str] = None) -> str:
        """同步阻塞查询（仅供工作线程调用：asyncio.to_thread(engine.query_blocking, q)）"""
        return asyncio.run(self.aquery(question, mode=mode))

    async def retrieve_evidence(self, topic: str, max_chars: int = 4000) -> str:
        """供写作链路调用的证据检索：只取检索上下文，失败/无数据返回空串（优雅降级）"""
        if not self.enabled or not self._ensure_sync():
            return ""
        try:
            answer = await asyncio.wait_for(
                self.aquery(topic, only_context=True), timeout=90)
            return answer[:max_chars] if answer else ""
        except Exception as e:  # noqa: BLE001
            logger.warning("参考文献多模态检索失败（降级跳过）: %s", e)
            return ""

    # ---------- 状态 ----------
    def status(self) -> Dict[str, Any]:
        manifest = self._load_manifest()
        files = [v for v in manifest.values() if v.get("status") == "ok"]
        st = {
            "enabled": self.enabled,
            "ready": self.available(),
            "import_error": self._import_error,
            "init_error": self._init_error,
            "workspace": str(self.workspace),
            "models": {
                "llm": self.config.rag_anything.models.llm,
                "vision": self.config.rag_anything.models.vision,
                "embedding": f"{self.config.models.embedding} "
                             f"(dim={self.config.rag_anything.embedding.dim})",
            },
            "documents": [
                {"file": Path(v.get("file", "")).name,
                 "ingested_at": v.get("ingested_at"),
                 "seconds": v.get("seconds")}
                for v in sorted(files, key=lambda x: x.get("ingested_at", ""))],
            "job": dict(self.job),
        }
        return st
