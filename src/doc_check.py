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
from io import BytesIO
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

# ===== 检测报告解析：外部报告常见的 AI 标记形态 =====
_REPORT_KEYWORD_RE = re.compile(
    r"(AI\s*(?:生成|写作|疑似|率|检测|味|风险|概率|痕迹|占比)"
    r"|AIGC|人工智能生成|机器生成|疑似\s*AI|高风险)", re.I)
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# 段首标记： [AI生成] / 【AI】 / (疑似AI) / AI率 87%：
_MARK_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"[\[【(（]\s*(?:AI|AIGC|疑似\s*AI|人工智能生成|高风险)[^\]】)）]{0,10}[\]】)）]\s*"
    r"|(?:AI|AIGC)\s*(?:生成|写作|疑似|率|检测|风险|占比)\s*[:：]?\s*(?:\d{1,3}(?:\.\d+)?\s*%?)?\s*[:：]?\s*"
    r")+", re.I)


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


# ---------- 导入外部 AI 检测报告：提取标记 + 论文核验 ----------

def _report_mark(text: str) -> Optional[dict]:
    """识别段落中的 AI 标记关键词/百分比，返回标记信息（无标记返回 None）"""
    t = (text or "").strip()
    if len(t) < 20:   # 标题/标签行不参与标记（如「AI 检测报告」）
        return None
    m = _REPORT_KEYWORD_RE.search(t)
    if not m:
        return None
    pct_m = _PCT_RE.search(t)
    val = float(pct_m.group(1)) if pct_m else None
    severity = "high" if (val is not None and val >= 50) else "medium"
    reason = f"报告标记「{m.group(0)}」" + (f" {val:.0f}%" if val is not None else "")
    return {"reason": reason, "severity": severity, "keyword": m.group(0), "pct": val}


def _clean_marked_text(text: str) -> str:
    """去掉报告加在段首的标记（如「【AI生成】」「AI率 87%：」），避免把标签喂给 9B"""
    t = (text or "").strip()
    for _ in range(3):
        n = _MARK_PREFIX_RE.sub("", t, count=1).strip()
        if n == t:
            break
        t = n
    return t or (text or "").strip()


def _docx_run_marked(run) -> bool:
    """docx run 是否带高亮/警示色（只认黄/红/橙系，避免蓝色标题整篇误判）"""
    try:
        if run.font.highlight_color is not None:
            return True
    except Exception:
        pass
    try:
        col = run.font.color
        rgb = str(col.rgb).upper() if (col is not None and col.rgb is not None) else ""
        if len(rgb) == 6:
            r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
            if (r >= 170 and b <= 130) or (r >= 190 and g >= 140 and b <= 120):
                return True
    except Exception:
        pass
    return False


def _docx_report_items(data: bytes) -> List[dict]:
    """解析 docx：段落 + 高亮/彩色/底纹/关键词/表格百分比标记"""
    from docx import Document as Docx
    from docx.oxml.ns import qn

    d = Docx(BytesIO(data))
    items: List[dict] = []
    for para in d.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        heading = (para.style.name or "").startswith("Heading")
        marked = any(_docx_run_marked(r) for r in para.runs if (r.text or "").strip())
        shaded = False
        pPr = para._p.pPr
        if pPr is not None:
            try:
                shaded = pPr.find(qn("w:shd")) is not None
            except Exception:
                shaded = False
        mark = None
        if marked or shaded:
            mark = {"reason": "文档高亮/警示色标记", "severity": "high"}
        elif not heading:
            mark = _report_mark(text)
        body = _clean_marked_text(text) if mark else text
        if heading:
            body = f"## {body}"
        items.append({"text": body, "mark": mark})

    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if (c.text or "").strip()]
            if not cells:
                continue
            joined = " | ".join(cells)
            pcts = [float(x) for x in _PCT_RE.findall(joined)]
            long_cell = max(cells, key=len)
            if pcts and max(pcts) >= 50 and len(long_cell) >= 40:
                items.append({"text": long_cell,
                              "mark": {"reason": f"报告表格标注 AI {max(pcts):.0f}%",
                                       "severity": "high"}})
            elif len(joined) >= 40:
                mk = _report_mark(joined)
                body = _clean_marked_text(long_cell if len(long_cell) >= 40 else joined)
                items.append({"text": body, "mark": mk})
    return items


def create_report_doc(data: bytes, filename: str, token: str, paper_text: str = "") -> dict:
    """导入检测报告：保留段落结构并抽取 AI 标记（docx 高亮/警示色；文本关键词/百分比；
    朱雀 PDF 片段详情按 AIGC 值提取 AI/疑似片段）。
    paper_text 非空时，PDF 报告会先定位到当前论文的真实段落再交给 9B（回写可精确命中）。"""
    ext = Path(filename).suffix.lower()
    items = []
    if ext == ".docx":
        items = _docx_report_items(data)
    elif ext == ".pdf":
        seg_items, is_zhuque = _zhuque_pdf_items(data, paper_text)
        if is_zhuque:
            if not seg_items:
                raise ValueError("朱雀报告中未发现 AI/疑似片段（所有片段 AIGC 值均低于 0.5）")
            items = seg_items
    if not items:
        text = extract_upload(data, filename, token)
        for p in split_paragraphs(text):
            mark = _report_mark(p)
            items.append({"text": _clean_marked_text(p) if mark else p, "mark": mark})
    items = [it for it in items if (it.get("text") or "").strip()][:MAX_PARAS]
    if not items:
        raise ValueError("未从文档中解析出有效文本（扫描件/空文档？）")

    doc_id = f"{datetime.now().strftime('%Y%m%d')}_{''.join(uuid.uuid4().hex[:10])}"
    marks = [{"idx": i, **it["mark"]} for i, it in enumerate(items) if it.get("mark")]
    doc = {
        "id": doc_id,
        "filename": filename,
        "ext": ext,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "paragraphs": [it["text"].strip() for it in items],
        "marks": marks,
        "report": None,
        "history": [],
    }
    save_doc(doc)
    return doc


# ---------- 朱雀检测报告 PDF：片段详情解析 ----------

_ZHUQUE_HDR_RE = re.compile(
    r"NO\.\s*(\d+)\s*片段\s*(\d+)\s*AIGC值\s*([0-9.]+)", re.S)


def _join_pdf_lines(lines: List[str]) -> str:
    """PDF 视觉换行 → 连续文本：中文直接拼接，英文单词跨行补空格"""
    out = ""
    for ln in lines:
        ln = (ln or "").strip()
        if not ln:
            continue
        if out:
            prev, nxt = out[-1], ln[0]
            if prev != "-" and re.match(r"[A-Za-z0-9]", prev) and re.match(r"[A-Za-z0-9]", nxt):
                out += " "
        out += ln
    return out


def _keep_pdf_body_block(t: str) -> bool:
    t = (t or "").strip()
    if not t:
        return False
    if re.search(r"\.(?:png|jpe?g|svg|tif|tiff|pdf|emf)\s*$", t, re.I):
        return False                      # 图文件名/图片占位行，不是正文段落
    if len(t) < 15:
        return False                      # 表格单元格/页码/短标签
    if re.fullmatch(r"[\d\s.,%()（）\-—:：/|、]+", t):
        return False                      # 纯数值表格行
    return True


def _split_chunks(text: str, limit: int = 900) -> List[str]:
    """长片段按句子切成 ≤limit 字的改写块；过短尾块并入上一块"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])\s*|\n+", text) if p.strip()]
    chunks: List[str] = []
    cur = ""
    for p in parts:
        while len(p) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:limit])
            p = p[limit:]
        if len(cur) + len(p) <= limit:
            cur += p
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    if len(chunks) >= 2 and len(chunks[-1]) < _MIN_REWRITE_CHARS \
            and len(chunks[-2]) + len(chunks[-1]) <= int(limit * 1.15):
        chunks[-2] += chunks[-1]
        chunks.pop()
    return [c for c in chunks if c.strip()]


def _zhuque_pdf_segments(data: bytes):
    """解析朱雀 PDF：返回 (segments, is_zhuque)
    segments=[{seg, aigc, text}]（仅 AIGC≥0.5 的 AI/疑似片段）。
    is_zhuque=False 表示不是朱雀片段报告，调用方回退通用文本解析。"""
    try:
        import pymupdf
    except Exception:
        return [], False
    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return [], False

    blocks = []
    for pi in range(len(pdf)):
        page = pdf[pi]
        for b in page.get_text("blocks"):
            x0, y0, x1, y1, txt, bno, btype = b
            if btype != 0 or not (txt or "").strip():
                continue
            blocks.append({"page": pi, "y": y0, "x": x0,
                           "text": (txt or "").strip()})
    blocks.sort(key=lambda b: (b["page"], round(b["y"], 1), b["x"]))

    heads = []
    i = 0
    while i < len(blocks):
        m = _ZHUQUE_HDR_RE.search(blocks[i]["text"])
        span = 1
        if not m and blocks[i]["text"].lstrip().startswith("NO.") and i + 1 < len(blocks):
            m = _ZHUQUE_HDR_RE.search(blocks[i]["text"] + " " + blocks[i + 1]["text"])
            span = 2
        if m:
            heads.append({"i": i, "span": span, "seg": int(m.group(2)),
                          "aigc": float(m.group(3))})
            i += span
        else:
            i += 1
    if not heads:
        pdf.close()
        return [], False

    segs = []
    for k, h in enumerate(heads):
        start = h["i"] + h["span"]
        end = heads[k + 1]["i"] if k + 1 < len(heads) else len(blocks)
        lines = []
        for b in blocks[start:end]:
            if not _keep_pdf_body_block(b["text"]):
                continue
            lines.extend([ln for ln in b["text"].split("\n") if ln.strip()])
        text = _join_pdf_lines(lines)
        if h["aigc"] >= 0.5 and text:
            segs.append({"seg": h["seg"], "aigc": h["aigc"], "text": text})
    pdf.close()
    return segs, True


def _paper_paragraphs(paper_text: str) -> List[str]:
    """当前论文正文 → 可改写段落（跳过标题/表格/图片/代码行）"""
    out = []
    for p in re.split(r"\n{2,}", paper_text or ""):
        p = p.strip()
        bare = p.lstrip("*").strip()
        if len(bare) < 50:
            continue
        if bare.startswith(("#", "|", "```", "!", "<")):
            continue
        out.append(p)
    return out


def _paper_aligned_items(segments: List[dict], paper_text: str) -> List[dict]:
    """用报告片段定位当前论文中被标记的局部文本：
    论文段落先按 400 字切块，再与报告片段做 4-gram 重合匹配（块内 ≥50% 命中即标记），
    命中块使用论文原文（连续块合并 ≤900 字），保证 9B 改写后回写能精确替换。"""
    paras = _paper_paragraphs(paper_text)
    if not paras:
        return []
    cands = []
    for pi, p in enumerate(paras):
        for ci, c in enumerate(_split_chunks(p, 400)):
            if len(c) < 50:
                continue
            sh = _shingles(_norm_text(c))
            if sh:
                cands.append({"pi": pi, "ci": ci, "text": c, "sh": sh})

    best = {}
    for seg in segments:
        seg_sh = _shingles(_norm_text(seg["text"]))
        if not seg_sh:
            continue
        for c in cands:
            frac = len(c["sh"] & seg_sh) / max(1, len(c["sh"]))
            if frac >= 0.5:
                key = (c["pi"], c["ci"])
                old = best.get(key)
                if old is None or seg["aigc"] > old["aigc"]:
                    best[key] = seg

    by_para = {}
    for c in cands:
        seg = best.get((c["pi"], c["ci"]))
        if seg:
            by_para.setdefault(c["pi"], []).append((c, seg))

    groups = []
    for pi in sorted(by_para):
        cur = None
        prev_ci = None
        for c, seg in by_para[pi]:
            if (cur is not None and prev_ci is not None and c["ci"] == prev_ci + 1
                    and cur["seg"] == seg["seg"]
                    and len(cur["text"]) + len(c["text"]) <= 900):
                cur["text"] += c["text"]
            else:
                cur = {"text": c["text"], "seg": seg["seg"], "aigc": seg["aigc"]}
                groups.append(cur)
            prev_ci = c["ci"]

    items = []
    for g in groups:
        aigc = g["aigc"]
        severity = "high" if aigc >= 0.99 else "medium"
        label = "AI特征" if severity == "high" else "疑似AI"
        mark = {"reason": f"朱雀报告 片段{g['seg']} AIGC值 {aigc:.4f}（{label}）",
                "severity": severity, "aigc": aigc,
                "segment": g["seg"], "source": "zhuque_pdf"}
        for chunk in _split_chunks(g["text"]):
            items.append({"text": chunk, "mark": dict(mark)})
    return items


def _zhuque_pdf_items(data: bytes, paper_text: str = ""):
    """朱雀 PDF → items；paper_text 可用时优先按当前论文真实段落对齐"""
    segments, is_zhuque = _zhuque_pdf_segments(data)
    if not is_zhuque:
        return [], False
    if paper_text:
        aligned = _paper_aligned_items(segments, paper_text)
        if aligned:
            return aligned, True
    items = []
    for seg in segments:
        severity = "high" if seg["aigc"] >= 0.99 else "medium"
        label = "AI特征" if severity == "high" else "疑似AI"
        mark = {"reason": f"朱雀报告 片段{seg['seg']} AIGC值 {seg['aigc']:.4f}（{label}）",
                "severity": severity, "aigc": seg["aigc"],
                "segment": seg["seg"], "source": "zhuque_pdf"}
        for chunk in _split_chunks(seg["text"]):
            items.append({"text": chunk, "mark": dict(mark)})
    return items, True


def _norm_text(s: str) -> str:
    """核验用归一化：只保留中英文/数字，去空白与标点"""
    out = []
    for ch in (s or ""):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            out.append(ch.lower())
    return "".join(out)


def _shingles(s: str, n: int = 4) -> set:
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _is_report_noise(p: str) -> bool:
    """报告元信息行（标题/分数/百分比/短标签）不参与核验"""
    t = (p or "").strip()
    if len(t) < 20:
        return True
    if re.fullmatch(r"[\d\s.,%()（）\-—:：/|、]+", t):
        return True
    if _REPORT_KEYWORD_RE.search(t) and len(t) < 80:
        return True
    return False


def verify_against_paper(doc: dict, paper_text: str, paper_title: str = "") -> dict:
    """导入文档 vs 当前任务论文：段落级 4-gram 重合度（报告标签行不计入）"""
    paras = [p for p in (doc.get("paragraphs") or []) if not _is_report_noise(p)]
    imported_chars = sum(len(p) for p in paras)
    paper_norm = _norm_text(paper_text)
    if not paper_norm:
        return {"ok": None, "score": 0.0,
                "reason": "当前任务尚无成稿论文，跳过核验（无法确认文档归属）",
                "paper_title": paper_title, "current_chars": 0,
                "imported_chars": imported_chars,
                "matched_paras": 0, "total_paras": len(paras)}

    paper_sh = _shingles(paper_norm)
    total = matched = 0.0
    matched_paras = 0
    for p in paras:
        np = _norm_text(p)
        if len(np) < 20:
            continue
        total += len(np)
        if np in paper_norm:
            matched += len(np)
            matched_paras += 1
            continue
        sh = _shingles(np)
        frac = len(sh & paper_sh) / max(1, len(sh))
        if frac >= 0.45:
            matched += len(np) * frac
            if frac >= 0.7:
                matched_paras += 1
    score = (matched / total) if total else 0.0
    if paper_title:
        title_norm = _norm_text(paper_title)
        if title_norm and title_norm in _norm_text("".join(paras)):
            score = min(1.0, score + 0.05)
    ok = score >= 0.55
    reason = (f"核验通过：与当前论文《{paper_title or '未命名'}》重合度 {score:.0%}"
              if ok else
              f"核验未通过：与当前任务论文重合度仅 {score:.0%}，可能不是同一篇论文")
    return {"ok": ok, "score": round(score, 4), "reason": reason,
            "paper_title": paper_title, "current_chars": len(paper_norm),
            "imported_chars": imported_chars,
            "matched_paras": matched_paras, "total_paras": len(paras)}


def _norm_with_map(text: str):
    """归一化文本 + 每个归一化字符对应的原文下标（用于回写定位）"""
    chars, idx = [], []
    for i, ch in enumerate(text or ""):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            chars.append(ch.lower())
            idx.append(i)
    return "".join(chars), idx


def replace_in_text(content: str, before: str, after: str):
    """在当前论文正文中定位改写前段落并替换为改写后文本。返回 (新文本, 是否命中)"""
    nb, _ = _norm_with_map(before)
    if len(nb) < 20:
        return content, False
    nc, mp = _norm_with_map(content)
    pos = nc.find(nb)
    if pos < 0:
        return content, False
    start = mp[pos]
    end = mp[pos + len(nb) - 1] + 1
    # 归一化忽略标点，替换范围要吸收段首/段尾标点，否则会残留「。。」「“ ”」
    _open = "“\"'‘（(【[《<「『"
    _close = "”\"'’）)】]》>」』。，、；：！？.,;:!?…—-"
    while start > 0 and content[start - 1] not in "\n" and content[start - 1] in _open + " \t":
        start -= 1
    while end < len(content) and content[end] not in "\n" and content[end] in _close + " \t":
        end += 1
    return content[:start] + after + content[end:], True


def latest_changes(doc: dict) -> List[dict]:
    """最近一次 9B 改写记录（含 before/after，供回写当前论文）"""
    for h in reversed(doc.get("history") or []):
        if h.get("action") == "humanize_rewrite" and h.get("changes"):
            return h["changes"]
    return []


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
    changed, failed, changes = [], [], []
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
            changes.append({"idx": idx, "before": src, "after": out})
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
        "changes": changes,
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
