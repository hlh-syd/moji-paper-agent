"""
方法原论文知识库构建脚本
流程：
  1) 读取 ingestion/method_registry.yaml
  2) 对每个方法：Crossref(DOI) 校验元数据；PubMed(eutils) 抓摘要；缺失 pmid 时用 pubmed_query 兜底检索
  3) 尽量抓 OA 全文：PMC (NCBI) / arXiv，PDF→pypdf 解析为文本
  4) 产出 knowledge_base/method_papers/{id}.json（元数据）+ {id}.txt（入库文本）
  5) 全部入 Chroma collection: method_papers

用法：
  .venv/Scripts/python.exe ingestion/build_methods_kb.py [--rebuild] [--limit N]
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen, Request

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("build_methods_kb")

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF_BASE = "https://api.crossref.org/works"
ARXIV_ABS = "https://arxiv.org/abs/"

UA = {"User-Agent": "PaperAgent/3.0 (knowledge-base builder; contact: local)"}


def http_get(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers=UA)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ncbi_params() -> dict:
    """eutils 礼貌参数"""
    params = {}
    email = os.environ.get("NCBI_EMAIL")
    api_key = os.environ.get("NCBI_API_KEY")
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


# ---------- PubMed ----------
def esearch_pmid(query: str) -> str | None:
    if not query:
        return None
    url = f"{NCBI_BASE}/esearch.fcgi?db=pubmed&term={quote(query)}&retmode=json&retmax=1"
    for k, v in ncbi_params().items():
        url += f"&{k}={quote(v)}"
    try:
        data = json.loads(http_get(url))
        ids = data.get("esearchresult", {}).get("idlist", [])
        return ids[0] if ids else None
    except Exception as e:
        logger.warning(f"esearch 失败 ({query}): {e}")
        return None


def efetch_abstract(pmid: str) -> dict | None:
    """抓取标题/摘要/PMCID"""
    url = f"{NCBI_BASE}/efetch.fcgi?db=pubmed&id={pmid}&rettype=abstract&retmode=xml"
    for k, v in ncbi_params().items():
        url += f"&{k}={quote(v)}"
    try:
        xml_text = http_get(url).decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_text)
        art = root.find(".//PubmedArticle/MedlineCitation/Article")
        if art is None:
            return None
        title = "".join((art.findtext("ArticleTitle") or "").split())
        abstract_parts = art.findall(".//AbstractText")
        abstract = "\n".join(
            "".join(node.itertext()).strip() for node in abstract_parts if "".join(node.itertext()).strip()
        )
        pmcid = None
        for aid in root.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "pmc":
                pmcid = aid.text
        return {"title": title, "abstract": abstract, "pmcid": pmcid}
    except Exception as e:
        logger.warning(f"efetch 失败 (pmid={pmid}): {e}")
        return None


# ---------- Crossref ----------
def crossref_meta(doi: str) -> dict | None:
    try:
        data = json.loads(http_get(f"{CROSSREF_BASE}/{quote(doi)}", timeout=20))
        msg = data.get("message", {})
        titles = msg.get("title") or []
        return {
            "title": titles[0] if titles else None,
            "container": (msg.get("container-title") or [None])[0],
            "year": (msg.get("issued", {}).get("date-parts") or [[None]])[0][0],
            "authors": ", ".join(
                f"{a.get('family','')} {a.get('given','')}".strip() for a in msg.get("author", [])[:6]
            ) or None,
        }
    except Exception as e:
        logger.warning(f"Crossref 查询失败 (doi={doi}): {e}")
        return None


# ---------- OA 全文 ----------
def fetch_pmc_text(pmcid: str) -> str | None:
    """抓 PMC 全文（XML → 纯文本）"""
    if not pmcid:
        return None
    pmc_num = pmcid.replace("PMC", "")
    url = f"{NCBI_BASE}/efetch.fcgi?db=pmc&id={pmc_num}&retmode=xml"
    for k, v in ncbi_params().items():
        url += f"&{k}={quote(v)}"
    try:
        xml_text = http_get(url, timeout=60).decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_text)
        chunks = []
        for p in root.iter("p"):
            text = "".join(p.itertext()).strip()
            if len(text) > 40:
                chunks.append(text)
        full = "\n\n".join(chunks)
        # PMC 限 50万字
        return full[:500000] if len(full) > 500 else None
    except Exception as e:
        logger.info(f"PMC 全文不可得 ({pmcid}): {e}")
        return None


def fetch_arxiv_text(arxiv_id: str) -> str | None:
    """抓 arXiv 摘要页文本（全文 PDF 另行解析）"""
    if not arxiv_id:
        return None
    try:
        html = http_get(ARXIV_ABS + arxiv_id, timeout=30).decode("utf-8", errors="ignore")
        m = re.search(r'<blockquote class="abstract[^"]*">\s*<span[^>]*>Abstract:</span>(.*?)</blockquote>',
                      html, re.S)
        if m:
            abstract = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            return abstract
    except Exception as e:
        logger.info(f"arXiv 页面获取失败 ({arxiv_id}): {e}")
    return None


# ---------- 主流程 ----------
def compose_text(entry: dict, abstract: str | None, oa_text: str | None) -> str:
    """组装入库文本：中文方法定位 + 引用信息 + 摘要 + (OA全文)"""
    paper = entry.get("paper") or {}
    lines = [
        f"# 方法：{entry.get('name_zh', entry['id'])} ({entry.get('name_en', '')})",
        f"类别：{entry.get('category', '')}",
        f"原始文献：{paper.get('authors', '')}。{paper.get('title', '')}。"
        f"{paper.get('journal', '')} {paper.get('year', '')}。"
        + (f"DOI: {paper['doi']}。" if paper.get("doi") else ""),
        "",
    ]
    if abstract:
        lines += ["## 原文摘要", abstract, ""]
    if oa_text:
        lines += ["## 开放获取全文（节选）", oa_text[:200000], ""]
    return "\n".join(lines)


def build(limit: int | None = None, rebuild: bool = False):
    root_dir = Path(__file__).resolve().parents[1]
    registry_path = root_dir / "ingestion" / "method_registry.yaml"
    out_dir = root_dir / "knowledge_base" / "method_papers"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or []

    if limit:
        registry = registry[:limit]

    records = []
    for i, entry in enumerate(registry, 1):
        mid = entry["id"]
        paper = entry.get("paper") or {}
        meta_extra = {}

        # 1) Crossref 校验/补全（有 DOI 且 confidence != high 时）
        doi = paper.get("doi")
        if doi and entry.get("confidence") != "high":
            cr = crossref_meta(doi)
            if cr:
                meta_extra = cr
                time.sleep(0.5)

        # 2) PubMed 摘要
        abstract, pmcid = None, None
        pubmed_query = entry.get("pubmed_query")
        if pubmed_query:
            pmid = esearch_pmid(pubmed_query)
            time.sleep(0.4)
            if pmid:
                fetched = efetch_abstract(pmid)
                if fetched:
                    abstract = fetched["abstract"]
                    pmcid = fetched["pmcid"]
                    meta_extra.setdefault("pubmed_pmid", pmid)
                    if fetched["title"] and (not paper.get("title") or entry.get("confidence") == "search"):
                        meta_extra["title_verified"] = fetched["title"]
        elif doi:
            # 用 DOI 在 PubMed 里找（部分能命中）
            pmid = esearch_pmid(doi)
            time.sleep(0.4)
            if pmid:
                fetched = efetch_abstract(pmid)
                if fetched:
                    abstract = fetched["abstract"]
                    pmcid = fetched["pmcid"]
                    meta_extra["pubmed_pmid"] = pmid

        # 3) OA 全文
        oa_text = fetch_pmc_text(pmcid) if pmcid else None
        if not oa_text:
            oa_text = fetch_arxiv_text(entry.get("arxiv_id"))
        time.sleep(0.4)

        text = compose_text(entry, abstract, oa_text)
        record = {
            "id": mid,
            "name_zh": entry.get("name_zh"),
            "name_en": entry.get("name_en"),
            "category": entry.get("category"),
            "paper": paper,
            "abstract_available": bool(abstract),
            "oa_fulltext": bool(oa_text),
            **meta_extra,
        }
        (out_dir / f"{mid}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{mid}.txt").write_text(text, encoding="utf-8")
        records.append(record)
        logger.info(
            f"[{i}/{len(registry)}] {mid}: 摘要={'√' if abstract else '×'} "
            f"OA全文={'√' if oa_text else '×'}"
        )

    ok_abstract = sum(1 for r in records if r["abstract_available"])
    ok_fulltext = sum(1 for r in records if r["oa_fulltext"])
    logger.info(f"完成：{len(records)} 条 | 摘要 {ok_abstract} | OA全文 {ok_fulltext}")

    # 4) 入库
    if rebuild:
        from src.vector_store import VectorStoreManager
        from src.document_processor import DocumentProcessor
        from src.llm_manager import LLMManager
        from src.config import get_config_manager

        config = get_config_manager().get_config()
        collection = config.vector_store.collections.method_papers
        processor = DocumentProcessor(config)
        docs = processor.process_directory(
            str(out_dir), collection_tag="method", source_tag="method_papers",
            base_metadata={"kb": "method_papers"},
        )
        # 只入 .txt（跳过 .json 元数据文件）
        docs = [d for d in docs if d.metadata.get("file_name", "").endswith(".txt")]
        llm = LLMManager(config)
        vsm = VectorStoreManager(config, llm.get_embeddings())
        vsm.reset_collection(collection)
        vsm.add_documents(collection, docs)
        logger.info(f"已入库 collection[{collection}]: {len(docs)} 块")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建方法原论文知识库")
    parser.add_argument("--rebuild", action="store_true", help="构建后重建向量库")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（调试用）")
    args = parser.parse_args()

    # 加载 .env
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    build(limit=args.limit, rebuild=args.rebuild)
