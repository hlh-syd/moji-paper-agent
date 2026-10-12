"""
格式档案提取器（模板论文 → 形式模板，通道 A）
=================================================
从用户上传的模板论文（docx/pdf）中提取**与研究内容无关的排版参数**：
字体（中文/英文、字号）、行距、首行缩进、标题层级规格、图片尺寸规律、
公式编号样式、表格样式、页面边距、页眉页脚。

设计原则（详见设计文档）：
  - 确定性代码 + 众数统计，**LLM 不读格式**（杜绝幻觉字号）；
  - docx 后端 = styles.xml 声明 + document.xml 实测双轨，置信度 high；
  - pdf 后端 = pdfplumber 字符级统计，置信度 medium/low；
  - 产物 format_profile.json → compile_export_style() 确定性编译为导出排版键，
    白名单制：参考文献格式等学术合规项**不**跟随模板。

单位换算：twips/567=cm；EMU/360000=cm；半点/2=pt；pt/0.75=px。
"""
import json
import logging
import re
import zipfile
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
W = "{%s}" % NS["w"]

TWIPS_PER_CM = 567.0
EMU_PER_CM = 360000.0
PT_PER_CM = 28.3465

# 常见 PDF 内嵌字体名 → 中文族名（子集前缀 ABCDEF+ 已剥离后）
_PDF_FONT_CN = {
    "SimSun": "宋体", "NSimSun": "新宋体", "SimHei": "黑体", "STHeiti": "黑体",
    "SourceHanSans": "思源黑体", "KaiTi": "楷体", "STKaiti": "楷体",
    "FangSong": "仿宋", "STFangsong": "仿宋", "STSong": "华文宋体",
    "宋体": "宋体", "黑体": "黑体", "楷体": "楷体", "仿宋": "仿宋",
}
_PDF_FONT_EN = {
    "TimesNewRoman": "Times New Roman", "TimesNewRomanPSMT": "Times New Roman",
    "Times-Roman": "Times New Roman", "ArialMT": "Arial", "Arial": "Arial",
    "Calibri": "Calibri", "Cambria": "Cambria",
}

_STOP_CJK = set("的了与和在基于及其对于通过利用分析研究方法结果目的结论背景数据"
                "应用相关系统模型实验设计评估影响特征表达构建预测意义价值问题讨论"
                "材料资料临床患者标本队列样本回顾性前瞻性 prospective retrospective")


def _mode(counter: Counter, default=None):
    """众数（None 安全）"""
    return counter.most_common(1)[0][0] if counter else default


def _norm_font_name(name: str) -> str:
    """剥离 PDF 子集前缀（ABCDEA+SimSun → SimSun）并归一到通用族名"""
    if not name:
        return ""
    name = name.split("+")[-1].split("-")[0]
    if name in _PDF_FONT_CN:
        return _PDF_FONT_CN[name]
    if name in _PDF_FONT_EN:
        return _PDF_FONT_EN[name]
    if re.fullmatch(r"[\u4e00-\u9fff]+", name):
        return name
    # TimesNewRoman,Bold 之类带逗号样式的
    base = name.split(",")[0]
    return _PDF_FONT_CN.get(base) or _PDF_FONT_EN.get(base) or name


# ==================== 前置部分识别（封面/声明/目录/中英摘要页） ====================

_COVER_FIELD_RE = re.compile(
    r"(研究生姓名|研究生学号|学生姓名|导师姓名|指导教师|指导小组成员|答辩委员会主席|"
    r"培养单位|所在学院|学院|系所|学科专业|所学专业|专业名称|专业|研究方向|"
    r"入学时间|毕业时间|完成日期|论文日期|论文提交日期|学位论文完成日期|"
    r"学位授予单位|学位类别|评阅人|答辩时间|学号|密级|中图分类号)")
_DECL_RE = re.compile(r"(独创性声明|原创性声明|版权使用|使用授权|保密声明)")
_NEXT_HEAD_RE = re.compile(r"^(摘\s*要|目\s*录|ABSTRACT|Abstract|第[一二三四五六七八九十\d]+章|绪论|引言)")


def _sanitize_persons(t: str) -> str:
    """姓名类个人信息 → 下划线占位（保留标签与其他字段值：专业/学校/日期不动）"""
    t = re.sub(r"((?:学生|研究生|论文)?(?:姓名|作者)[：:]?\s*)"
               r"([\u4e00-\u9fff·]{2,4})(?=\s|_|$|＿)",
               r"\1＿＿＿", t)
    t = re.sub(r"((?:指导教师|导师)[：:]?\s*)([\u4e00-\u9fff·]{2,4})(?=\s|_|$|＿|，)",
               r"\1＿＿＿", t)
    t = re.sub(r"(专业\s+)([\u4e00-\u9fff]{2,4})(\s+在)", r"\1＿＿＿\3", t)
    return t


def _detect_front_matter(early: list, root=None) -> dict:
    """前置部分结构识别：封面（字段词密度）、声明页（法定文本提取）、目录、
    中文摘要页样式（标题字号/关键词行）、英文摘要页有无。
    early = 文档前部段落 [{text, sz, jc}]（docx 精确 / pdf 行近似）。"""
    fm = {"has_cover": False, "has_declaration": False, "has_toc": False,
          "has_cn_abstract": False, "has_en_abstract": False}
    if not early:
        return fm
    texts = [e["text"] for e in early]

    # ---- 封面：首个长正文段之前 ≥2 个信息字段词（长段按中文字数计，英文长题名不算）----
    def _cjk_len(s):
        return len(re.findall(r"[\u4e00-\u9fff]", s))
    first_long = next((i for i, e in enumerate(early[:45]) if _cjk_len(e["text"]) >= 60), 45)
    head_zone = early[:first_long]
    field_hits = []
    for e in head_zone:
        m = _COVER_FIELD_RE.search(e["text"])
        if m and len(e["text"]) <= 45:
            field_hits.append(m.group(1))
    if len(field_hits) >= 2:
        fm["has_cover"] = True
        fm["cover_fields"] = list(dict.fromkeys(field_hits))[:8]
        szs = [e["sz"] for e in head_zone if e["sz"] and len(e["text"]) >= 4]
        fm["cover_title_pt"] = round(max(szs), 1) if szs else 22.0
        # ---- 封面版式捕获：整页重放用（文本/字号/加粗/居中/字体/分页），姓名脱敏 ----
        layout, blank = [], 0
        title_i, title_cjk, en_i = None, -1, None
        for e in head_zone[:30]:
            t = _sanitize_persons(e["text"])
            if not t:
                blank += 1
                if layout and blank <= 2:
                    layout.append({"t": "", "sz": e.get("sz"), "b": bool(e.get("bold")),
                                   "c": e.get("jc") == "center", "e": e.get("east"),
                                   "br": False})
                continue
            blank = 0
            cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
            latin = len(re.findall(r"[A-Za-z]", t))
            layout.append({"t": t[:100], "sz": e.get("sz"), "b": bool(e.get("bold")),
                           "c": e.get("jc") == "center", "e": e.get("east"),
                           "br": bool(e.get("br"))})
            if cjk >= 8 and cjk > title_cjk and "论文" not in t[:6]:
                title_i, title_cjk = len(layout) - 1, cjk
            if latin >= 20 and cjk == 0 and en_i is None:
                en_i = len(layout) - 1
        if title_i is not None:
            # 题名可能重复出现（外封 + 内封）：所有同文本行一并标记，导出时统一替换为本论文题名
            title_text = re.sub(r"\s+", "", layout[title_i]["t"])
            for item in layout:
                if re.sub(r"\s+", "", item.get("t") or "") == title_text and item.get("t"):
                    item["T"] = "cn_title"
            layout[title_i]["T"] = "cn_title"
        if en_i is not None:
            layout[en_i]["T"] = "en_title"
        fm["cover_layout"] = layout

    # ---- 声明页：法定固定文本（属模板格式的一部分，提取以便复刻）----
    decl_idx = next((i for i, t in enumerate(texts) if _DECL_RE.search(t)), None)
    if decl_idx is not None:
        fm["has_declaration"] = True
        buf = []
        for e in early[decl_idx:decl_idx + 40]:
            if e["text"] and e["text"] != texts[decl_idx] and _NEXT_HEAD_RE.match(e["text"]):
                break
            if e["text"]:
                buf.append(e["text"])
        fm["declaration_text"] = "\n".join(buf)[:2500]

    # ---- 目录：标题或 Word TOC 域 ----
    has_toc_field = False
    if root is not None:
        has_toc_field = any("TOC" in (fs.get(W + "instr") or "")
                            for fs in root.findall(".//w:fldSimple", NS))
    toc_title = next((t for t in texts if re.fullmatch(r"目\s*录|CONTENTS|Table of Contents",
                                                       t.strip(), re.I)), None)
    fm["has_toc"] = has_toc_field or toc_title is not None
    fm["toc_field"] = has_toc_field

    # ---- 中文摘要页：标题 + 样式 + 关键词行 ----
    abs_idx = next((i for i, t in enumerate(texts) if re.fullmatch(r"摘\s*要", t.strip())), None)
    if abs_idx is not None:
        fm["has_cn_abstract"] = True
        e = early[abs_idx]
        kw = next((t for t in texts[abs_idx:abs_idx + 30] if re.match(r"^关键词[:：]", t)), None)
        fm["cn_abstract_style"] = {
            "heading": "摘  要", "heading_size_pt": e.get("sz") or 16,
            "has_keywords": kw is not None}

    # ---- 英文摘要页 ----
    en_idx = next((i for i, t in enumerate(texts) if re.fullmatch(r"ABSTRACT|Abstract", t.strip())), None)
    if en_idx is not None:
        en_kw = next((t for t in texts[en_idx:en_idx + 40]
                      if re.match(r"^Key ?words?[:：]", t, re.I)), None)
        fm["has_en_abstract"] = True
        fm["en_abstract_style"] = {"heading": "ABSTRACT", "has_keywords": en_kw is not None}
    return fm


# ==================== 主入口 ====================

def profile_document(path) -> dict:
    """提取格式档案。任何失败都返回带 notes 的最小档案（不抛异常，不阻塞上传）。"""
    path = Path(path)
    ext = path.suffix.lower()
    profile = {"source": {"file": path.name, "backend": ext.lstrip(".") or "none",
                          "confidence": "none"},
               "notes": []}
    try:
        if ext == ".docx":
            profile = _profile_docx(path)
        elif ext == ".pdf":
            profile = _profile_pdf(path)
        else:
            profile["notes"].append(f"暂不支持 {ext} 的格式提取（仅 docx/pdf）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"格式档案提取失败 {path.name}: {e}")
        profile["notes"].append(f"提取失败：{e}")
    profile.setdefault("source", {})["file"] = path.name
    return profile


# ==================== docx 后端 ====================

def _style_chain(styles: dict, sid, depth=0):
    """样式自身属性（含 basedOn 一层解析）"""
    if sid not in styles or depth > 2:
        return {}
    s = dict(styles[sid])
    base = s.get("based_on")
    if base and base in styles:
        merged = _style_chain(styles, base, depth + 1)
        merged.update({k: v for k, v in s.items() if v is not None})
        return merged
    return s


def _read_styles(zf) -> dict:
    from lxml import etree
    root = etree.fromstring(zf.read("word/styles.xml"))
    out = {}
    for st in root.findall("w:style", NS):
        sid = st.get(W + "styleId") or ""
        name_el = st.find("w:name", NS)
        name = name_el.get(W + "val") if name_el is not None else sid
        info = {"name": name or sid, "based_on": None}
        bo = st.find("w:basedOn", NS)
        if bo is not None:
            info["based_on"] = bo.get(W + "val")
        rpr = st.find("w:rPr", NS)
        if rpr is not None:
            sz = rpr.find("w:sz", NS)
            info["sz"] = int(sz.get(W + "val")) / 2 if sz is not None else None
            rf = rpr.find("w:rFonts", NS)
            if rf is not None:
                info["east"] = rf.get(W + "eastAsia")
                info["ascii"] = rf.get(W + "ascii")
            info["bold"] = rpr.find("w:b", NS) is not None
        ppr = st.find("w:pPr", NS)
        if ppr is not None:
            sp = ppr.find("w:spacing", NS)
            if sp is not None:
                info["line"] = sp.get(W + "line")
                info["line_rule"] = sp.get(W + "lineRule")
                info["before"] = sp.get(W + "before")
                info["after"] = sp.get(W + "after")
            jc = ppr.find("w:jc", NS)
            info["jc"] = jc.get(W + "val") if jc is not None else None
            ol = ppr.find("w:outlineLvl", NS)
            info["outline"] = int(ol.get(W + "val")) if ol is not None else None
        out[sid] = info
    return out


def _profile_docx(path: Path) -> dict:
    from lxml import etree

    with zipfile.ZipFile(path) as zf:
        doc_root = etree.fromstring(zf.read("word/document.xml"))
        styles = _read_styles(zf)
        header_parts = [n for n in zf.namelist()
                        if re.fullmatch(r"word/header\d+\.xml", n)]
        footer_parts = [n for n in zf.namelist()
                        if re.fullmatch(r"word/footer\d+\.xml", n)]
        header_xmls = [zf.read(n) for n in header_parts[:4]]
        footer_xmls = [zf.read(n) for n in footer_parts[:4]]

    body = doc_root.find("w:body", NS)
    paras = body.findall("w:p", NS) if body is not None else []

    # ---- 段落级实测 ----
    body_sz, body_east, body_ascii = Counter(), Counter(), Counter()
    line_rules, indents = Counter(), Counter()
    heading_infos, caption_infos = [], []
    early = []  # 前部段落元数据（前置部分识别用）
    for p in paras:
        text = "".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()
        ppr = p.find("w:pPr", NS)
        sid = None
        if ppr is not None:
            ps = ppr.find("w:pStyle", NS)
            sid = ps.get(W + "val") if ps is not None else None
        stl = _style_chain(styles, sid) if sid else {}
        style_name = (stl.get("name") or "").lower()

        # 直接格式（优先）→ 样式声明（兜底）
        first_run = p.find("w:r", NS)
        sz = east = ascii_f = None
        bold = None
        rpr_src = first_run.find("w:rPr", NS) if first_run is not None else None
        if rpr_src is not None:
            szel = rpr_src.find("w:sz", NS)
            sz = int(szel.get(W + "val")) / 2 if szel is not None else None
            rf = rpr_src.find("w:rFonts", NS)
            if rf is not None:
                east = rf.get(W + "eastAsia")
                ascii_f = rf.get(W + "ascii")
            bold = rpr_src.find("w:b", NS) is not None
        sz = sz if sz is not None else stl.get("sz")
        east = east or stl.get("east")
        ascii_f = ascii_f or stl.get("ascii")
        bold = bold if bold is not None else stl.get("bold")
        jc = None
        if ppr is not None:
            jcel = ppr.find("w:jc", NS)
            jc = jcel.get(W + "val") if jcel is not None else None
        jc = jc or stl.get("jc")
        if len(early) < 220:  # 前置部分只可能出现在文档前部
            br = any(b.get(W + "type") == "page" for b in p.findall(".//w:br", NS))
            early.append({"text": text, "sz": sz, "jc": jc, "bold": bold,
                          "east": east, "br": br})

        is_heading = bool(re.search(r"heading|标题", style_name)) or \
            (stl.get("outline") is not None)
        is_caption = ("题注" in style_name or "caption" in style_name or
                      re.match(r"^(图|表)\s*[\d一二三四五六七八九十]", text))

        if ppr is not None:
            sp = ppr.find("w:spacing", NS)
            if sp is not None and sp.get(W + "line"):
                rule = sp.get(W + "lineRule") or "auto"
                line_rules[(sp.get(W + "line"), rule)] += 1
            ind = ppr.find("w:ind", NS)
            if ind is not None:
                flc = ind.get(W + "firstLineChars")
                if flc is not None:
                    indents[round(int(flc) / 100.0, 1)] += 1
                elif ind.get(W + "firstLine"):
                    indents["raw"] += 1

        if is_caption and text:
            caption_infos.append({"sz": sz or stl.get("sz"), "east": east or stl.get("east"),
                                  "bold": bold, "jc": jc, "text": text[:24]})
        elif is_heading and text:
            before = after = None
            if ppr is not None:
                sp = ppr.find("w:spacing", NS)
                if sp is not None:
                    before = sp.get(W + "before")
                    after = sp.get(W + "after")
            heading_infos.append({"sz": sz, "east": east, "bold": bold, "jc": jc,
                                  "before": before, "after": after, "text": text[:24]})
        elif len(text) >= 40:  # 正文-like 段
            if sz:
                body_sz[round(sz, 1)] += 1
            if east:
                body_east[east] += 1
            if ascii_f:
                body_ascii[ascii_f] += 1

    body_pt = _mode(body_sz, 12.0)
    body_east_f = _mode(body_east, "宋体")
    body_ascii_f = _mode(body_ascii, "Times New Roman")

    # 行距：line/240（auto 规则）
    line_h = 1.5
    if line_rules:
        (line, rule), _ = line_rules.most_common(1)[0]
        try:
            if (rule or "auto") == "auto":
                line_h = max(1.0, min(3.0, round(int(line) / 240.0, 2)))
            else:  # exact/atLeast：按正文字号折倍数
                line_h = max(1.0, min(3.0, round((int(line) / 20.0) / body_pt, 2)))
        except (TypeError, ValueError):
            pass
    first_indent = _mode(indents, 2.0)
    if first_indent == "raw":  # 无 firstLineChars：twips/字号宽折算
        first_indent = 2.0

    # 标题分档：按字号聚类取前三档
    h_sizes = sorted({round(h["sz"], 1) for h in heading_infos if h["sz"]}, reverse=True)
    h_sizes = [s for s in h_sizes if s > body_pt * 1.05][:3]
    h1_sz = h_sizes[0] if len(h_sizes) >= 1 else 16
    h2_sz = h_sizes[1] if len(h_sizes) >= 2 else 14
    h3_sz = h_sizes[2] if len(h_sizes) >= 3 else 12

    def _head_stat(target_sz):
        grp = [h for h in heading_infos if h["sz"] and abs(h["sz"] - target_sz) < 0.3]
        if not grp:
            return {}
        jc = _mode(Counter(h["jc"] for h in grp if h["jc"]))
        east = _mode(Counter(h["east"] for h in grp if h["east"]))
        bold = _mode(Counter(bool(h["bold"]) for h in grp), True)
        before = _mode(Counter(h["before"] for h in grp if h["before"]))
        after = _mode(Counter(h["after"] for h in grp if h["after"]))
        out = {"size_pt": target_sz, "bold": bold, "align": {"center": "center"}.get(jc, "left"),
               "cn": _norm_font_name(east or "黑体")}
        if before:
            out["space_before_pt"] = round(int(before) / 20.0, 1)
        if after:
            out["space_after_pt"] = round(int(after) / 20.0, 1)
        return out

    h1 = _head_stat(h1_sz) or {"size_pt": h1_sz, "cn": "黑体", "bold": True, "align": "center"}
    h2 = _head_stat(h2_sz) or {"size_pt": h2_sz, "cn": "黑体", "bold": True, "align": "left"}
    h3 = _head_stat(h3_sz) or {"size_pt": h3_sz, "cn": "黑体", "bold": True, "align": "left"}

    # 题注
    cap_sz = _mode(Counter(round(c["sz"], 1) for c in caption_infos if c["sz"]), 10.5)
    cap_east = _mode(Counter(c["east"] for c in caption_infos if c["east"]), "黑体")
    cap_jc = _mode(Counter(c["jc"] for c in caption_infos if c["jc"]), "center")

    # ---- 页面（最后一个 sectPr = 正文节）----
    sect = doc_root.findall(".//w:sectPr", NS)
    page = {}
    pgnum_fmt = None
    if sect:
        sp = sect[-1]
        pgsz = sp.find("w:pgSz", NS)
        pgmar = sp.find("w:pgMar", NS)
        if pgsz is not None:
            try:
                page["size_mm"] = [round(int(pgsz.get(W + "w")) / TWIPS_PER_CM * 10),
                                   round(int(pgsz.get(W + "h")) / TWIPS_PER_CM * 10)]
            except (TypeError, ValueError):
                pass
        if pgmar is not None:
            m = {}
            for key, attr in (("top", "top"), ("bottom", "bottom"),
                              ("left", "left"), ("right", "right")):
                try:
                    # twips → mm（×10 与 schema 的 margins_mm 对齐）
                    m[key] = round(int(pgmar.get(W + attr)) / TWIPS_PER_CM * 10, 1)
                except (TypeError, ValueError):
                    pass
            page["margins_mm"] = m
        pgn = sp.find("w:pgNumType", NS)
        if pgn is not None:
            pgnum_fmt = pgn.get(W + "fmt")
    pw_cm = (page.get("size_mm") or [21.0, 29.7])[0] / 10.0
    margins = page.get("margins_mm") or {"top": 254, "bottom": 254, "left": 318, "right": 318}
    text_block = round(pw_cm - margins.get("left", 30) / 10.0 - margins.get("right", 25) / 10.0, 1)
    page["text_block_cm"] = text_block

    # ---- 图片 ----
    fig_pcts, fig_count = [], 0
    for ext_el in doc_root.findall(".//wp:extent", NS):
        try:
            cx = int(ext_el.get("cx", "0"))
            cy = int(ext_el.get("cy", "0"))
        except ValueError:
            continue
        if cx <= 0 or cy <= 0:
            continue
        fig_count += 1
        w_cm = cx / EMU_PER_CM
        h_cm = cy / EMU_PER_CM
        fig_pcts.append((round(w_cm / text_block * 100 / 5) * 5,
                         w_cm, h_cm, h_cm / max(w_cm, 0.1)))
    figures = {"count": fig_count}
    if fig_pcts:
        horiz = [p for p in fig_pcts if p[3] < 0.85]
        vert = [p for p in fig_pcts if p[3] >= 1.25]
        if horiz:
            figures["mode_pct_of_text_block"] = _mode(Counter(p[0] for p in horiz))
        if vert:
            figures["vertical_height_cap_cm"] = round(max(p[2] for p in vert), 1)

    # ---- 公式 ----
    omaths = doc_root.findall(".//m:oMath", NS)
    numbered = 0
    for om in omaths:
        par = om.getparent()
        while par is not None and not par.tag.endswith("}p"):
            par = par.getparent()
        if par is None:
            continue
        ptext = "".join(t.text or "" for t in par.findall(".//w:t", NS))
        if re.search(r"[（(]\s*\d+[-–.]\d+\s*[)）]", ptext):
            numbered += 1
    equations = {"count": len(omaths), "font_size_pt": body_pt}
    if omaths:
        equations["numbered"] = numbered >= max(1, len(omaths) * 0.5)
        equations["number_position"] = "right_tab"

    # ---- 表格 ----
    tbls = doc_root.findall(".//w:tbl", NS)
    three_line = 0
    tbl_szs = Counter()
    for tb in tbls[:30]:
        borders = tb.find("w:tblPr/w:tblBorders", NS)
        if borders is not None:
            iv = borders.find("w:insideV", NS)
            ih = borders.find("w:insideH", NS)
            if (iv is None or (iv.get(W + "val") in ("none", "nil"))) and \
               (ih is None or (ih.get(W + "val") in ("none", "nil"))):
                three_line += 1
        for szel in tb.findall(".//w:sz", NS)[:40]:
            try:
                tbl_szs[round(int(szel.get(W + "val")) / 2, 1)] += 1
            except (TypeError, ValueError):
                pass
    tables = {"count": len(tbls)}
    if tbls:
        tables["style"] = "three_line" if three_line >= len(tbls) * 0.5 else "grid"
        tables["header_bold"] = True
    if tbl_szs:
        tables["size_pt"] = _mode(tbl_szs, 10.5)

    # ---- 页眉页脚 ----
    hf = {}
    if header_xmls:
        from lxml import etree as _et
        hdr = _et.fromstring(header_xmls[0])
        htext = "".join(t.text or "" for t in hdr.findall(".//w:t", NS)).strip()
        if htext:
            hf["header_text"] = htext[:30]
        hf["header_rule"] = any(
            b.get(W + "val") not in (None, "none", "nil")
            for hx in header_xmls
            for b in _et.fromstring(hx).findall(".//w:pBdr/w:bottom", NS))
    if footer_xmls:
        from lxml import etree as _et
        has_page = False
        for fx in footer_xmls:
            froot = _et.fromstring(fx)
            for it in froot.findall(".//w:instrText", NS):
                if "PAGE" in (it.text or ""):
                    has_page = True
                    break
            if not has_page:  # fldSimple 形式的页码字段
                for fs in froot.findall(".//w:fldSimple", NS):
                    if "PAGE" in (fs.get(W + "instr") or ""):
                        has_page = True
                        break
            if has_page:
                break
        if has_page:
            hf["page_number"] = {"position": "footer_center",
                                 "front": "roman" if pgnum_fmt and "oman" in pgnum_fmt else "arabic",
                                 "body": "arabic"}

    # 置信度：正文实测样本量决定
    n_body = sum(body_sz.values())
    confidence = "high" if n_body >= 20 else ("medium" if n_body >= 5 else "low")
    notes = []
    if n_body < 5:
        notes.append("正文段实测样本不足，字号为推断值")

    return {
        "source": {"file": path.name, "backend": "docx", "confidence": confidence},
        "page": page,
        "fonts": {
            "body": {"cn": _norm_font_name(body_east_f), "en": _norm_font_name(body_ascii_f),
                     "size_pt": body_pt},
            "h1": h1, "h2": h2, "h3": h3,
            "caption": {"cn": _norm_font_name(cap_east), "size_pt": cap_sz,
                        "align": cap_jc, "bold": True},
        },
        "paragraph": {"line_spacing": line_h,
                      "first_indent_chars": first_indent if isinstance(first_indent, (int, float)) else 2.0,
                      "align": "justify"},
        "figures": figures,
        "equations": equations,
        "tables": tables,
        "headers_footers": hf,
        "front_matter": _detect_front_matter(early, doc_root),
        "notes": notes,
    }


# ==================== pdf 后端 ====================

def _profile_pdf(path: Path) -> dict:
    import pdfplumber

    body_sz, cn_fonts, en_fonts = Counter(), Counter(), Counter()
    heading_szs = Counter()
    line_deltas = Counter()
    indents_chars = Counter()
    lefts, fig_pcts = Counter(), []
    header_texts = Counter()
    footer_page = False
    text_width_pt = None
    page_texts = []  # 前置部分识别用（前 8 页全文）

    with pdfplumber.open(str(path)) as pdf:
        n_pages = len(pdf.pages)
        for page in pdf.pages[: min(n_pages, 60)]:
            chars = page.chars
            if not chars:
                continue
            # 行聚类（同一 top±1）
            lines = {}
            for c in chars:
                key = round(c["top"])
                lines.setdefault(key, []).append(c)
            line_objs = []
            for key in sorted(lines):
                cs = sorted(lines[key], key=lambda c: c["x0"])
                text = "".join(c["text"] for c in cs).strip()
                if not text:
                    continue
                line_objs.append({
                    "text": text, "x0": min(c["x0"] for c in cs),
                    "x1": max(c["x1"] for c in cs),
                    "top": key, "size": max(c["size"] for c in cs),
                    "fonts": [c["fontname"] for c in cs]})
            if not line_objs:
                continue
            if len(page_texts) < 8:
                page_texts.append("\n".join(l["text"] for l in line_objs))
            lefts[round(min(l["x0"] for l in line_objs), 1)] += 1
            # 正文行（长度足够且非页眉页脚）
            core = [l for l in line_objs
                    if 70 < l["top"] < page.height - 60 and len(l["text"]) >= 10]
            for l in core:
                for f in l["fonts"]:
                    if any("\u4e00" <= ch <= "\u9fff" for ch in l["text"]):
                        cn_fonts[_norm_font_name(f)] += 1
                    elif re.search(r"[A-Za-z]", l["text"]):
                        en_fonts[_norm_font_name(f)] += 1
                if len(l["text"]) >= 25:
                    body_sz[round(l["size"], 1)] += 1
                if l["size"] >= 13:
                    heading_szs[round(l["size"], 1)] += 1
            # 行距（正文行基线差）
            core_sorted = sorted(core, key=lambda l: l["top"])
            for a, b in zip(core_sorted, core_sorted[1:]):
                d = b["top"] - a["top"]
                if 8 < d < 40:
                    line_deltas[round(d)] += 1
            if core_sorted:
                left_pt = min(l["x0"] for l in core_sorted)
                right_pt = max(l["x1"] for l in core_sorted)
                text_width_pt = max(text_width_pt or 0, right_pt - left_pt)
                base_sz = _mode(body_sz, 12.0)
                for l in core_sorted:
                    d = l["x0"] - left_pt
                    # 只认 0.5~4.2 字符的偏移为首行缩进（双栏右栏等大偏移排除）
                    if base_sz * 0.5 < d <= base_sz * 4.2:
                        indents_chars[round(d / base_sz * 2) / 2] += 1
            # 页眉（顶部区域文本，去掉纯页码）
            for l in line_objs:
                if l["top"] < 60 and len(l["text"]) >= 4 and not re.fullmatch(r"[\dIVXivx\-–— ]+", l["text"]):
                    header_texts[l["text"][:30]] += 1
                if l["top"] > page.height - 50 and re.search(r"\d|IVX", l["text"]):
                    footer_page = True
            # 图片占比
            tw = text_width_pt or (page.width * 0.85)
            for im in page.images:
                w = im["x1"] - im["x0"]
                if w > 50:
                    fig_pcts.append((round(w / tw * 100 / 5) * 5,
                                     im["y1"] - im["y0"], (im["y1"] - im["y0"]) / max(w, 1)))

    body_pt = _mode(body_sz, 12.0)
    cn_font = _mode(cn_fonts, "宋体")
    en_font = _mode(en_fonts, "Times New Roman")

    # 行距倍数
    line_h = 1.5
    if line_deltas and body_pt:
        delta = _mode(line_deltas)
        line_h = max(1.0, min(3.0, round(delta / body_pt * 2) / 2))
    # 首行缩进（字符）；无有效样本时置 None（导出沿用系统默认）
    first_indent = _mode(indents_chars)
    try:
        first_indent = float(first_indent) if first_indent is not None else None
    except (TypeError, ValueError):
        first_indent = None

    # 标题字号分档
    h_sizes = sorted((s for s, c in heading_szs.items() if c >= 2 and s > body_pt * 1.1),
                     reverse=True)[:3]
    h1_sz = h_sizes[0] if len(h_sizes) >= 1 else 16
    h2_sz = h_sizes[1] if len(h_sizes) >= 2 else 14
    h3_sz = h_sizes[2] if len(h_sizes) >= 3 else 12

    figures = {"count": len(fig_pcts)}
    if fig_pcts:
        horiz = [p for p in fig_pcts if p[2] < 0.85]
        vert = [p for p in fig_pcts if p[2] >= 1.25]
        if horiz:
            figures["mode_pct_of_text_block"] = _mode(Counter(p[0] for p in horiz))
        if vert:
            figures["vertical_height_cap_cm"] = round(max(p[1] for p in vert) / PT_PER_CM, 1)

    n_chars = sum(body_sz.values())
    confidence = "medium" if n_chars >= 50 else "low"

    # 前置部分（文本近似）：把前部页的行折算成 early 段元数据
    early = []
    for ptext in page_texts:
        for ln in ptext.splitlines():
            ln = ln.strip()
            if ln:
                early.append({"text": ln, "sz": body_pt, "jc": None})
    front_matter = _detect_front_matter(early[:220], None)

    return {
        "source": {"file": path.name, "backend": "pdf", "confidence": confidence},
        "page": {"text_block_cm": round((text_width_pt or 468) / PT_PER_CM, 1)},
        "fonts": {
            "body": {"cn": cn_font, "en": en_font, "size_pt": body_pt},
            "h1": {"size_pt": h1_sz, "cn": "黑体", "bold": True, "align": "center"},
            "h2": {"size_pt": h2_sz, "cn": "黑体", "bold": True, "align": "left"},
            "h3": {"size_pt": h3_sz, "cn": "黑体", "bold": True, "align": "left"},
            "caption": {"cn": "黑体", "size_pt": round(body_pt * 0.875, 1),
                        "align": "center", "bold": True},
        },
        "paragraph": {"line_spacing": line_h,
                      "first_indent_chars": round(first_indent, 1) if first_indent else None,
                      "align": "justify"},
        "figures": figures,
        "equations": {"note": "PDF 字符流无法可靠识别公式，公式规格沿用系统默认"},
        "tables": {"note": "PDF 表格边框不可靠，表格样式沿用系统默认"},
        "headers_footers": {
            "header_text": _mode(header_texts) or None,
            "page_number": {"position": "footer_center", "body": "arabic"} if footer_page else None,
        },
        "front_matter": front_matter,
        "notes": [f"PDF 统计近似（{n_pages} 页，正文行 {n_chars}），建议提供 docx 版模板以获得精确档案"],
    }


# ==================== 编译：格式档案 → 导出排版键 ====================

# 可安全写入导出样式的通用字体名（未知子集字体名如 AdvP49811 不套用，回退系统默认）
_SAFE_FONTS = {"Times New Roman", "Arial", "Calibri", "Cambria", "Georgia",
               "宋体", "黑体", "楷体", "仿宋", "新宋体", "华文宋体", "思源黑体"}


def compile_export_style(profile: dict) -> dict:
    """确定性映射（白名单制）。产物键与 SectionWriter.DEFAULT_EXPORT_STYLE 对齐。"""
    style = {}
    f = (profile or {}).get("fonts") or {}
    para = (profile or {}).get("paragraph") or {}
    figs = (profile or {}).get("figures") or {}
    eqs = (profile or {}).get("equations") or {}
    tbls = (profile or {}).get("tables") or {}
    page = (profile or {}).get("page") or {}
    hf = (profile or {}).get("headers_footers") or {}

    body = f.get("body") or {}
    if body.get("size_pt"):
        style["body_font_px"] = max(10, min(24, round(float(body["size_pt"]) / 0.75)))
    if body.get("cn") and body["cn"] in _SAFE_FONTS:
        style["font_cn_body"] = body["cn"]
    if body.get("en") and body["en"] in _SAFE_FONTS:
        style["font_en"] = body["en"]
    for lvl in (1, 2, 3):
        h = f.get(f"h{lvl}") or {}
        if h.get("size_pt"):
            style[f"h{lvl}_pt"] = round(float(h["size_pt"]), 1)
        if h.get("cn"):
            style[f"h{lvl}_font_cn"] = h["cn"]
            if lvl == 1:
                style["font_cn_head"] = h["cn"]  # 全局标题字体（导出端主键）
        if h.get("align") == "center" and lvl == 1:
            style["h1_center"] = True
    cap = f.get("caption") or {}
    if cap.get("size_pt"):
        style["caption_pt"] = round(float(cap["size_pt"]), 1)
    if cap.get("cn"):
        style["caption_font_cn"] = cap["cn"]

    if para.get("line_spacing"):
        style["line_height"] = max(1.0, min(3.0, float(para["line_spacing"])))
    if para.get("first_indent_chars") is not None and \
            0 <= float(para["first_indent_chars"]) <= 4:
        style["first_indent_em"] = float(para["first_indent_chars"])
    if para.get("align"):
        style["body_align"] = "left" if para["align"] == "left" else "justify"

    if tbls.get("size_pt"):
        style["table_font_px"] = max(9, min(20, round(float(tbls["size_pt"]) / 0.75)))
    if figs.get("mode_pct_of_text_block"):
        style["fig_width_pct"] = max(30, min(100, int(figs["mode_pct_of_text_block"])))
    if eqs.get("font_size_pt"):
        style["eq_font_pt"] = round(float(eqs["font_size_pt"]), 1)

    m = page.get("margins_mm") or {}
    if m.get("top"):
        style["margin_top_cm"] = m["top"] / 10.0
    if m.get("bottom"):
        style["margin_bottom_cm"] = m["bottom"] / 10.0
    if m.get("left"):
        style["margin_left_cm"] = m["left"] / 10.0
    if m.get("right"):
        style["margin_right_cm"] = m["right"] / 10.0

    if hf.get("header_text"):
        style["header_text"] = str(hf["header_text"])[:40]
    if hf.get("header_rule"):
        style["header_rule"] = True
    pn = hf.get("page_number")
    if pn and pn.get("position"):
        style["footer_page_number"] = True

    # ---- 前置部分（封面/声明/目录/中英摘要）白名单传递 ----
    fm_raw = (profile or {}).get("front_matter") or {}
    fm = {}
    for k in ("has_cover", "cover_fields", "cover_title_pt", "cover_layout",
              "has_declaration", "declaration_text", "has_toc", "toc_field",
              "has_cn_abstract", "cn_abstract_style", "has_en_abstract",
              "en_abstract_style"):
        if fm_raw.get(k):
            fm[k] = fm_raw[k]
    if fm.get("cover_title_pt"):  # 艺术字/异常大字号钳制到合理封面区间
        fm["cover_title_pt"] = min(36.0, max(16.0, float(fm["cover_title_pt"])))
    if fm:
        fm["front_page_number"] = (pn or {}).get("front") or "roman"
        style["front_matter"] = fm
    return style


def human_summary(profile: dict, style: dict = None) -> list:
    """预览面板用的人类可读摘要行"""
    style = style if style is not None else compile_export_style(profile)
    f = (profile or {}).get("fonts") or {}
    para = (profile or {}).get("paragraph") or {}
    page = (profile or {}).get("page") or {}
    figs = (profile or {}).get("figures") or {}
    tbls = (profile or {}).get("tables") or {}
    eqs = (profile or {}).get("equations") or {}
    hf = (profile or {}).get("headers_footers") or {}
    lines = []
    body = f.get("body") or {}
    if body:
        lines.append(f"正文：{body.get('cn', '?')}（中）+ {body.get('en', '?')}（西文）"
                     f" {body.get('size_pt', '?')}pt")
    for lvl in (1, 2, 3):
        h = f.get(f"h{lvl}") or {}
        if h.get("size_pt"):
            align = "居中" if h.get("align") == "center" else "左对齐"
            lines.append(f"{lvl} 级标题：{h.get('cn', '?')} {h['size_pt']}pt {align}")
    if para:
        lines.append(f"行距 {para.get('line_spacing', '?')} 倍 · "
                     f"首行缩进 {para.get('first_indent_chars', '?')} 字符 · "
                     f"{'两端对齐' if para.get('align') != 'left' else '左对齐'}")
    if tbls.get("style"):
        lines.append(f"表格：{'三线表' if tbls['style'] == 'three_line' else '全框线'}"
                     + (f" · 表内 {tbls.get('size_pt')}pt" if tbls.get("size_pt") else ""))
    if figs.get("count"):
        extra = f"共 {figs['count']} 图"
        if figs.get("mode_pct_of_text_block"):
            extra += f" · 横图常态占版心 {figs['mode_pct_of_text_block']}%"
        if figs.get("vertical_height_cap_cm"):
            extra += f" · 竖图最高 {figs['vertical_height_cap_cm']}cm"
        lines.append(extra)
    if eqs.get("count"):
        lines.append(f"公式 {eqs['count']} 处"
                     + (" · 编号右顶格" if eqs.get("numbered") else " · 未见统一编号"))
    if page.get("text_block_cm"):
        m = page.get("margins_mm") or {}
        if m:
            lines.append(f"版心 {page['text_block_cm']}cm · 页边距 上{m.get('top', '?')}/"
                         f"下{m.get('bottom', '?')}/左{m.get('left', '?')}/右{m.get('right', '?')} mm")
    if hf.get("header_text"):
        lines.append(f"页眉「{hf['header_text']}」" + ("（带横线）" if hf.get("header_rule") else ""))
    if hf.get("page_number"):
        pn = hf["page_number"]
        lines.append(f"页码：页脚居中（前置 {pn.get('front', 'arabic')} / 正文 "
                     f"{pn.get('body', 'arabic')}）")
    fm = (profile or {}).get("front_matter") or {}
    if fm.get("has_cover"):
        fields = "、".join((fm.get("cover_fields") or [])[:5])
        ctp = min(36.0, max(16.0, float(fm.get("cover_title_pt") or 22.0)))
        lines.append(f"封面页（标题 {ctp:g}pt；字段：{fields or '按模板'}）")
    if fm.get("has_declaration"):
        lines.append("声明页（法定文本已提取，导出复刻）")
    if fm.get("has_toc"):
        lines.append("目录页（" + ("Word 目录域" if fm.get("toc_field") else "标题式目录") + "）")
    if fm.get("has_cn_abstract"):
        cas = fm.get("cn_abstract_style") or {}
        lines.append(f"中文摘要页（标题 {cas.get('heading_size_pt', '?')}pt"
                     + (" · 含关键词行" if cas.get("has_keywords") else "") + "）")
    if fm.get("has_en_abstract"):
        eas = fm.get("en_abstract_style") or {}
        lines.append("英文摘要页 ABSTRACT"
                     + (" · 含 Key words 行" if eas.get("has_keywords") else "") + "（缺失时导出占位页提醒）")
    for n in (profile or {}).get("notes", [])[:3]:
        lines.append(f"· {n}")
    return lines


def save_profile(name: str, profile: dict, ref_dir: Path) -> Path:
    ref_dir.mkdir(parents=True, exist_ok=True)
    p = ref_dir / f"{name}.format.json"
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
