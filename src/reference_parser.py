"""
参考论文解析器
解析他人发表论文（PDF/DOCX/TXT），用主模型提取：
  - 章节结构（标题层级、每节内容概要、近似字数占比）
  - 使用的方法清单（方法节出现的方法名）
  - 叙事逻辑（前言如何引出缺口、结果如何组织、讨论如何收束）
供大纲生成时"借鉴组织方式（禁止照搬内容）"。

产物：reference_papers/{safe}.pdf 原件 + {safe}.structure.json 结构 + {safe}.text.txt 全文
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import AppConfig, get_config_manager

logger = logging.getLogger(__name__)

def _ref_dir() -> Path:
    """参考论文目录（按当前请求 owner 隔离；legacy=reference_papers/）"""
    from src import scoping
    return scoping.reference_dir()


def re_safe(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:80] or "paper"


# ---------- 文本提取 ----------
def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if ext == ".docx":
        from docx import Document as Docx
        doc = Docx(str(path))
        parts = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            if para.style.name.startswith("Heading"):
                lvl = "".join(ch for ch in para.style.name if ch.isdigit()) or "2"
                parts.append(f"{'#' * min(int(lvl) + 1, 4)} {t}")
            else:
                parts.append(t)
        for table in doc.tables:  # 表格内容一并提取（方法/基线信息常在表中）
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    # 其余按文本
    return path.read_text(encoding="utf-8", errors="ignore")


# ---------- 无 LLM 的兜底结构识别 ----------
HEADING_RE = re.compile(
    r"^(?:(\d+(?:\.\d+)*)[.\s、]+|[（(]?([一二三四五六七八九十]+)[)）、．.]+)?\s*"
    r"((?:摘要|背景|目的|方法|材料与方法|资料与方法|结果|结论|讨论|前言|引言|"
    r"Introduction|Background|Methods|Materials and Methods|Results|Discussion|Conclusion|"
    r"Patients|Study Design|Statistical Analysis|Statistics|Related Work|Evaluation|Limitations)"
    r"[^\n]{0,40})$",
    re.IGNORECASE,
)


def rule_based_structure(text: str) -> dict:
    """无 LLM 时的粗结构：按标题正则切分"""
    sections = []
    current = {"title": "开头", "level": 1, "summary": "", "approx_words": 0}
    for line in text.splitlines():
        line = line.strip()
        m = HEADING_RE.match(line)
        if m and (m.group(1) or m.group(2) or line.isupper() or len(line) < 40):
            if current["approx_words"] > 0 or current["title"] != "开头":
                sections.append(current)
            current = {"title": m.group(3).strip(), "level": 2 if (m.group(1) or "").count(".") else 1,
                       "summary": "", "approx_words": 0}
        else:
            current["summary"] += line[:200]
            current["approx_words"] += len(line)
    if current["approx_words"] > 0:
        sections.append(current)
    return {"title": "", "sections": sections[:40], "methods_used": [],
            "narrative_logic": "（规则切分，未经模型归纳）"}


class ReferenceParser:
    """参考论文解析器"""

    def __init__(self, llm_manager, config: Optional[AppConfig] = None):
        self.llm_manager = llm_manager
        self.config = config or get_config_manager().get_config()
        _ref_dir().mkdir(parents=True, exist_ok=True)

    # ---------- 主入口 ----------
    async def parse_upload(self, file_bytes: bytes, filename: str) -> dict:
        """保存上传文件 → 提取文本 → LLM 解析结构 → 持久化"""
        safe = re_safe(Path(filename).stem)
        ext = Path(filename).suffix.lower() or ".txt"
        raw_path = _ref_dir() / f"{safe}{ext}"
        raw_path.write_bytes(file_bytes)

        text = extract_text(raw_path)
        if len(text.strip()) < 300:
            raise ValueError(f"无法从 {filename} 提取到有效文本（可能为扫描版PDF）")
        (_ref_dir() / f"{safe}.text.txt").write_text(text, encoding="utf-8")

        structure = await self._analyze_structure(text)
        structure.update({
            "name": safe,
            "original_filename": filename,
            "file": str(raw_path),
            "text_chars": len(text),
            "parsed_at": datetime.now().isoformat(),
        })
        (_ref_dir() / f"{safe}.structure.json").write_text(
            json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
        # 通道A 格式档案：确定性提取（秒级、零 LLM），失败不影响结构解析
        format_profile = None
        try:
            from src.format_profiler import profile_document, save_profile
            format_profile = profile_document(raw_path)
            save_profile(safe, format_profile, _ref_dir())
            logger.info(f"格式档案提取完成: {filename} "
                        f"(backend={format_profile['source']['backend']}, "
                        f"confidence={format_profile['source']['confidence']})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"格式档案提取失败（不影响结构解析）: {e}")
        logger.info(f"参考论文解析完成: {filename} -> {safe} ({len(structure.get('sections', []))} 节)")
        return structure

    # ---------- LLM 结构分析 ----------
    async def _analyze_structure(self, text: str) -> dict:
        from langchain_core.messages import HumanMessage

        # 摘要+正文按比例抽样，控制在 45k 字符内
        sample = self._sample_text(text, 45000)

        prompt = """你是医学期刊编辑。分析下面这篇论文的**结构与写法**（不是内容本身），输出 JSON（不要代码块包裹）：
{
  "title": "论文标题",
  "paper_type": "论著/方法学/综述 等",
  "sections": [
    {"title": "章节标题", "level": 层级数字1或2, "summary": "该节写了什么（60字内）",
     "key_methods": ["该节出现的方法名"],
     "approx_words": 该节近似字数}
  ],
  "methods_used": ["全文用到的主要方法/模型/检验，按出现顺序"],
  "narrative_logic": {
    "intro_gap": "前言如何从背景推进到研究缺口（80字内）",
    "results_organization": "结果按什么逻辑组织（如:队列描述→主结果→对比→亚组→外部验证）",
    "discussion_flow": "讨论的推进顺序与局限性处理方式（80字内）"
  }
}
注意：sections 覆盖摘要到结论全部章节；只描述结构，不要复述研究发现。

# 论文全文（抽样）
""" + sample

        llm = self.llm_manager.get_main_llm()
        try:
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            content = content.strip()
            m = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
            if m:
                content = m.group(1)
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end + 1]
            return json.loads(content)
        except Exception as e:
            logger.warning(f"LLM 结构分析失败，退回规则切分: {e}")
            return rule_based_structure(text)

    @staticmethod
    def _sample_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        head = text[: int(limit * 0.55)]
        mid = text[len(text) // 2: len(text) // 2 + int(limit * 0.2)]
        tail = text[-int(limit * 0.25):]
        return f"{head}\n\n……[中段]……\n\n{mid}\n\n……[末段]……\n\n{tail}"

    # ---------- 存取 ----------
    def list_papers(self) -> list:
        out = []
        for p in sorted(_ref_dir().glob("*.structure.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "name": data.get("name", p.stem.replace(".structure", "")),
                    "original_filename": data.get("original_filename"),
                    "title": data.get("title", ""),
                    "paper_type": data.get("paper_type", ""),
                    "sections_count": len(data.get("sections", [])),
                    "methods_used": data.get("methods_used", [])[:8],
                    "parsed_at": data.get("parsed_at"),
                    "has_format": (_ref_dir() / f"{data.get('name', '')}.format.json").exists(),
                    "has_skeleton": (_ref_dir() / f"{data.get('name', '')}.skeleton.json").exists(),
                })
            except Exception:
                continue
        return out

    def get_structure(self, name: str) -> Optional[dict]:
        p = _ref_dir() / f"{re_safe(name)}.structure.json"
        if not p.exists():
            # 容错：直接按给定名找
            p = _ref_dir() / f"{name}.structure.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ---------- 通道A/B 产物存取 ----------
    @staticmethod
    def _resolve(name: str, suffix: str) -> Optional[Path]:
        for cand in (_ref_dir() / f"{re_safe(name)}{suffix}", _ref_dir() / f"{name}{suffix}"):
            if cand.exists():
                return cand
        return None

    def get_format(self, name: str) -> Optional[dict]:
        p = self._resolve(name, ".format.json")
        return json.loads(p.read_text(encoding="utf-8")) if p else None

    def get_skeleton(self, name: str) -> Optional[dict]:
        p = self._resolve(name, ".skeleton.json")
        return json.loads(p.read_text(encoding="utf-8")) if p else None

    def save_skeleton(self, name: str, skeleton: dict) -> Path:
        safe = skeleton.get("name") if isinstance(skeleton, dict) else None
        path = _ref_dir() / f"{re_safe(safe or name)}.skeleton.json"
        path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    def delete_paper(self, name: str) -> bool:
        safe = re_safe(name)
        deleted = False
        for suffix in (".structure.json", ".text.txt", ".format.json", ".skeleton.json"):
            p = _ref_dir() / f"{safe}{suffix}"
            if p.exists():
                p.unlink()
                deleted = True
        for p in _ref_dir().glob(f"{safe}.*"):
            if p.suffix.lower() in {".pdf", ".docx", ".txt"} and p.stem == safe:
                p.unlink()
                deleted = True
        return deleted

    # ---------- 供大纲生成使用的压缩文本 ----------
    @staticmethod
    def compress_structure(structure: dict, max_chars: int = 6000) -> str:
        """把参考论文结构压成注入 prompt 的文本"""
        lines = [f"# 参考论文：{structure.get('title', structure.get('original_filename', ''))}",
                 f"类型：{structure.get('paper_type', '')}", "", "## 章节组织"]
        for s in structure.get("sections", []):
            indent = "  " * max(0, int(s.get("level", 1)) - 1)
            methods = f"（方法: {'、'.join(s.get('key_methods', [])[:5])}）" if s.get("key_methods") else ""
            lines.append(f"- {indent}{s.get('title', '')} ≈{s.get('approx_words', '?')}字 {methods}: {s.get('summary', '')[:80]}")
        methods = structure.get("methods_used", [])
        if methods:
            lines.append("\n## 全文方法清单（按出现顺序）")
            lines.append("、".join(methods[:20]))
        logic = structure.get("narrative_logic") or {}
        if isinstance(logic, dict) and logic:
            lines.append("\n## 叙事逻辑")
            lines.append(f"- 前言推进: {logic.get('intro_gap', '')}")
            lines.append(f"- 结果组织: {logic.get('results_organization', '')}")
            lines.append(f"- 讨论收束: {logic.get('discussion_flow', '')}")
        return "\n".join(lines)[:max_chars]
