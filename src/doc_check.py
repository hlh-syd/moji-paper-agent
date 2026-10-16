# -*- coding: utf-8 -*-
"""
上传文档 AI 查重检测中心（doccheck）
====================================
用户上传 word(docx)/txt(/pdf/md) 文档 → 提取文本 → 三引擎 AI 查重检测
（朱雀官方 API + 本地 BERT + 启发式体检）→ 段落级评分 → 选中段落用本地
微调 humanize-9b（Ollama）改写 → 自动复检出新报告。

存储：uploads/doccheck/{doc_id}.json（经 scoping.uploads_dir 按用户隔离）
  {id, filename, ext, uploaded_at, paragraphs: [str],
   report: 最近一次检测, history: [{time, action, paras}]}
设计约束：
  - 检测/改写均为阻塞调用，web 层统一 asyncio.to_thread 驱动
  - 朱雀无 Key / BERT 模型缺失 / Ollama 不在线均优雅降级（字段置 None + 提示）
  - 改写只动选中段落，未选中段落原样保留（改写失败也保留原文）
"""
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from src import scoping

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {".docx", ".txt", ".md", ".pdf"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PARAS = 300              # 超长文档截断保护（检测/展示时延控制）
_MIN_REWRITE_CHARS = 100     # 与 local_humanize.rewrite 下限一致
_PARA_MODEL_CAP = 80         # 段落级 BERT 检测上限（纯 CPU，逐段推理慢）

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")


# ---------- 存储 ----------

def _doc_dir() -> Path:
    d = scoping.uploads_dir() / "doccheck"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _doc_path(doc_id: str) -> Path:
    if not _DOC_ID_RE.match(doc_id or ""):
        raise FileNotFoundError(f"非法文档 id: {doc_id}")
    return _doc_dir() / f"{doc_id}.json"


def load_doc(doc_id: str) -> dict:
    p = _doc_path(doc_id)
    if not p.exists():
        raise FileNotFoundError(f"文档不存在或已过期: {doc_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_doc(doc: dict) -> Path:
    p = _doc_path(doc["id"])
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def full_text(doc: dict) -> str:
    return "\n\n".join(doc.get("paragraphs") or [])


# ---------- 上传解析 ----------

def split_paragraphs(text: str) -> List[str]:
    """全文 → 段落列表。空行分段为主；单换行的 txt 按行聚合：
    短行（<60 字符，多为列表/单句）并入下一段，标题/表格/代码行跳过。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = re.split(r"\n{2,}", text)
    if len(blocks) <= 1:
        # 单换行文档：逐行聚合（短行向后合并，保留语义完整性）
        paras, cur = [], ""
        for ln in text.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            cur = f"{cur}{ln}" if cur else ln
            if len(cur) >= 60:
                paras.append(cur)
                cur = ""
        if cur:
            paras.append(cur)
        return paras[:MAX_PARAS]

    def _keep(p: str) -> bool:
        bare = p.lstrip("*").strip()
        if not bare:
            return False
        if bare.startswith(("#", ">", "|", "```")):   # markdown 标题/引用/表格/代码
            return True                                 # 保留结构行但不参与改写判定
        return True

    return [b.strip() for b in blocks if _keep(b)][:MAX_PARAS]


def create_doc(filename: str, text: str) -> dict:
    doc_id = f"{datetime.now().strftime('%Y%m%d')}_{''.join(uuid.uuid4().hex[:10])}"
    doc = {
        "id": doc_id,
        "filename": filename,
        "ext": Path(filename).suffix.lower(),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "paragraphs": split_paragraphs(text),
        "report": None,
        "history": [],
    }
    if not doc["paragraphs"]:
        raise ValueError("未从文档中解析出有效文本（扫描件/空文档？）")
    save_doc(doc)
    return doc


def extract_upload(data: bytes, filename: str, doc_id: str) -> str:
    """把上传字节落盘后复用 reference_parser.extract_text 提取纯文本"""
    from src.reference_parser import extract_text
    ext = Path(filename).suffix.lower()
    src = _doc_dir() / f"{doc_id}_src{ext}"
    src.write_bytes(data)
    try:
        return extract_text(src)
    finally:
        try:
            src.unlink()
        except OSError:
            pass


# ---------- 检测（三引擎 + 段落级评分） ----------

def detect_doc(doc: dict) -> dict:
    """整篇三引擎检测 + 段落级本地评分（BERT 概率 / 启发式分）。阻塞调用。"""
    from src.zhuque_client import zhuque_detect, zhuque_available
    from src.aigc_detector import detect_aigc
    from src.text_enhancer import detect_ai_markers

    full = full_text(doc)
    paras = doc.get("paragraphs") or []
    heur_all = detect_ai_markers(full)

    para_scores = []
    for i, p in enumerate(paras[:_PARA_MODEL_CAP]):
        entry = {"chars": len(p), "ai_prob": None, "heur": None,
                 "rewritable": len(p.strip()) >= _MIN_REWRITE_CHARS}
        if entry["rewritable"] and not p.lstrip("*").strip().startswith(("#", "|", "```")):
            mc = detect_aigc(p)          # <200 字 / 模型缺失自动 None
            entry["ai_prob"] = (mc or {}).get("ai_prob")
            entry["heur"] = (detect_ai_markers(p) or {}).get("score")
        para_scores.append(entry)
    # 截断范围之外的段落只给字符数（避免逐段推理拖满 CPU）
    for p in paras[_PARA_MODEL_CAP:]:
        para_scores.append({"chars": len(p), "ai_prob": None, "heur": None,
                            "rewritable": len(p.strip()) >= _MIN_REWRITE_CHARS})

    report = {
        "doc_id": doc["id"],
        "filename": doc["filename"],
        "chars": len(full),
        "paras": len(paras),
        "zhuque": zhuque_detect(full) if zhuque_available() else None,
        "zhuque_available": zhuque_available(),
        "model_check": detect_aigc(full),
        "heuristic": {"score": heur_all.get("score"),
                      "markers": heur_all.get("markers", [])[:8]},
        "para_scores": para_scores,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    return report


# ---------- 9B 改写（humanize-9b via Ollama） ----------

def humanize_status() -> dict:
    """本地微调模型可用性（不可用时前端给启动指引）"""
    try:
        from src.local_humanize import available
        ok = available()
    except Exception:
        ok = False
    return {"available": bool(ok), "model": "humanize-9b",
            "hint": "" if ok else
                    "需要本机 Ollama 在线且已导入 humanize-9b 模型"
                    "（ollama serve 后 ollama list 确认）"}


def rewrite_paragraphs(doc: dict, indexes: List[int],
                       on_event: Optional[Callable[[str, dict], None]] = None) -> dict:
    """逐段调用 9B 模型改写并落盘。on_event(kind, payload) 同步回报进度。
    返回 {changed: [...], failed: [{idx, reason}], doc}。阻塞调用（放 to_thread）。"""
    from src.local_humanize import rewrite as _rewrite

    paras = doc.get("paragraphs") or []
    changed, failed = [], []
    for idx in indexes:
        if not (0 <= idx < len(paras)):
            failed.append({"idx": idx, "reason": "段落下标越界"})
            continue
        src = paras[idx].strip()
        if len(src) < _MIN_REWRITE_CHARS:
            failed.append({"idx": idx, "reason": f"段落过短（<{_MIN_REWRITE_CHARS}字），9B 模型不处理"})
            continue
        if on_event:
            on_event("para_start", {"idx": idx, "chars": len(src)})
        t0 = time.time()
        try:
            out = _rewrite(src)
        except Exception as e:  # noqa: BLE001
            logger.warning("doccheck 段落改写异常 %d: %s", idx, e)
            out = None
        if out:
            paras[idx] = out
            changed.append(idx)
            if on_event:
                on_event("para_done", {"idx": idx, "ok": True,
                                       "before_chars": len(src), "after_chars": len(out),
                                       "secs": round(time.time() - t0, 1)})
        else:
            failed.append({"idx": idx, "reason": "模型输出未过锁字校验或调用失败，已保留原文"})
            if on_event:
                on_event("para_done", {"idx": idx, "ok": False, "secs": round(time.time() - t0, 1)})

    doc["paragraphs"] = paras
    doc.setdefault("history", []).append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "action": "humanize_rewrite",
        "paras": list(indexes),
        "changed": changed,
    })
    save_doc(doc)
    return {"changed": changed, "failed": failed}


# ---------- 改写后导出 ----------

def export_doc(doc: dict, fmt: str = "auto") -> Path:
    """导出当前（改写后）文档：docx（按段落映射标题层级）或 txt"""
    ext = doc.get("ext") or ".txt"
    if not fmt or fmt == "auto":
        fmt = "docx" if ext == ".docx" else "txt"
    if fmt not in ("txt", "docx"):
        raise ValueError(f"不支持的导出格式: {fmt}")
    out = _doc_dir() / f"{doc['id']}_rewritten.{fmt}"
    if fmt == "txt":
        out.write_text(full_text(doc), encoding="utf-8")
        return out
    from docx import Document as Docx
    d = Docx()
    for p in doc.get("paragraphs") or []:
        bare = p.strip()
        m = re.match(r"^(#{1,4})\s+(.*)$", bare)
        if m:
            d.add_heading(m.group(2), level=min(len(m.group(1)), 4))
        else:
            d.add_paragraph(bare)
    d.save(str(out))
    return out
