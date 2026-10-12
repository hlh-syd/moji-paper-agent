"""
论文写作教学知识库构建脚本
三个来源：
  A) 本地已装 skills 的 references 教材（nature-writing / scientific-writing / literature-review 等）
  B) 网络公开写作指南（writing_sources.yaml 中的 web_sources）
  C) B站论文写作教学视频字幕（yt-dlp；无字幕视频自动跳过）

产出 knowledge_base/writing_methodology/{skills/,web/,bilibili/} 下 md/txt 文件，
并全部入 Chroma collection: writing_methodology_zh

用法：
  .venv/Scripts/python.exe ingestion/build_writing_kb.py [--rebuild] [--skip-web] [--skip-bilibili]
"""
import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("build_writing_kb")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "knowledge_base" / "writing_methodology"

# 本地 skills 教材目录（写作规范类）
SKILL_DIRS = [
    "nature-writing", "nature-polishing", "scientific-writing",
    "literature-review", "peer-review", "citation-management",
    "scientific-problem-selection", "scientific-visualization",
]
SKILLS_ROOT_CANDIDATES = [
    Path(r"C:\Users\he\.agents\skills"),
]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PaperAgent/3.0"}


# ---------- 来源 A：本地 skills ----------
def collect_skills():
    """拷贝写作类 skills 的 SKILL.md 与 references/*.md 到知识库"""
    dst = OUT_DIR / "skills"
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for skill_name in SKILL_DIRS:
        for root in SKILLS_ROOT_CANDIDATES:
            skill_dir = root / skill_name
            if not skill_dir.exists():
                continue
            files = [skill_dir / "SKILL.md"]
            refs = skill_dir / "references"
            if refs.exists():
                files += [p for p in refs.rglob("*.md")]
            for f in files:
                if not f.exists():
                    continue
                target = dst / f"{skill_name}__{f.name}"
                shutil.copy2(f, target)
                copied += 1
            break
    logger.info(f"[skills] 拷贝 {copied} 个教学文件")
    return copied


# ---------- 来源 B：公开网页 ----------
def _html_to_text(html: str) -> str:
    """bs4 提取正文文本；失败时退化为正则去标签"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        lines = [ln.strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)
    except ImportError:
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
        text = re.sub(r"<[^>]+>", "\n", text)
        lines = [ln.strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _save_web_source(source_id: str, title: str, text: str, url: str, tags):
    header = f"# {title}\n来源: {url}\n标签: {', '.join(tags)}\n\n"
    out = OUT_DIR / "web" / f"{source_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + text[:300000], encoding="utf-8")


def collect_web():
    """抓取 writing_sources.yaml 中的网页/PDF 来源"""
    from urllib.request import Request, urlopen

    with open(ROOT / "ingestion" / "writing_sources.yaml", "r", encoding="utf-8") as f:
        sources = yaml.safe_load(f) or {}

    ok, fail = 0, 0
    for src in sources.get("web_sources", []):
        sid, url, kind = src["id"], src["url"], src.get("kind", "html")
        optional = src.get("optional", False)
        try:
            req = Request(url, headers=UA)
            raw = urlopen(req, timeout=30).read()
            if kind == "pdf" or url.lower().endswith(".pdf"):
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(raw))
                text = "\n".join((p.extract_text() or "") for p in reader.pages)
            else:
                text = _html_to_text(raw.decode("utf-8", errors="ignore"))
            if len(text.strip()) < 200:
                raise ValueError(f"正文过短({len(text)}字符)，可能被反爬")
            _save_web_source(sid, src["title"], text, url, src.get("tags", []))
            ok += 1
            logger.info(f"[web] {sid}: {len(text)} 字符")
        except Exception as e:
            fail += 1
            level = logger.warning if not optional else logger.info
            level(f"[web] {sid} 抓取失败({url}): {e}")
    logger.info(f"[web] 成功 {ok} / 失败 {fail}")
    return ok


# ---------- 来源 C：B站字幕 ----------
def collect_bilibili() -> int:
    """yt-dlp 搜索 B站教学视频并下载字幕（CC/AI 字幕）"""
    with open(ROOT / "ingestion" / "writing_sources.yaml", "r", encoding="utf-8") as f:
        conf = (yaml.safe_load(f) or {}).get("bilibili_search", {})

    keywords = conf.get("keywords", ["论文写作 教程"])
    max_videos = int(conf.get("max_videos_per_keyword", 4))
    cookie = conf.get("cookie_from_browser")

    out_dir = OUT_DIR / "bilibili"
    out_dir.mkdir(parents=True, exist_ok=True)
    got = 0

    for kw in keywords:
        try:
            query = f"bilisearch{max_videos}:{kw}"
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(view_count)s",
                query,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="ignore")
            entries = [ln.split("\t") for ln in res.stdout.strip().splitlines() if ln.count("\t") >= 1]
            entries = [e for e in entries if len(e) >= 2]
            logger.info(f"[bilibili] 关键词「{kw}」搜到 {len(entries)} 个视频")
        except Exception as e:
            logger.warning(f"[bilibili] 搜索失败 ({kw}): {e}")
            continue

        for vid_id, title, *_ in entries[:max_videos]:
            vid_id = vid_id.strip()
            url = f"https://www.bilibili.com/video/{vid_id}" if not vid_id.startswith("http") else vid_id
            base = [
                sys.executable, "-m", "yt_dlp",
                "--skip-download",
                "--write-subs", "--write-auto-subs",
                "--sub-langs", ".*",            # B站字幕 lang 名不统一，全拉再挑中文
                "--sub-format", "json3/srt/vtt/best",
                "-o", str(out_dir / "%(id)s.%(ext)s"),
            ]
            if cookie:
                base += ["--cookies-from-browser", cookie]
            try:
                subprocess.run(base + [url], capture_output=True, text=True, timeout=180,
                               encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning(f"[bilibili] 字幕下载异常 ({title}): {e}")
                continue

            sub_file = _pick_best_subtitle(out_dir, vid_id)
            if not sub_file:
                logger.info(f"[bilibili] 无可用字幕，跳过: {title}")
                _clean_partial(out_dir, vid_id)
                continue
            text = _subtitle_to_text(sub_file)
            if len(text) < 500:
                logger.info(f"[bilibili] 字幕过短({len(text)}字符)，跳过: {title}")
                _clean_partial(out_dir, vid_id)
                continue
            header = (f"# B站教学视频字幕：{title}\n来源: {url}\n"
                      f"说明: 视频字幕由平台生成，仅供参考学习\n\n")
            (out_dir / f"{vid_id}.md").write_text(header + text, encoding="utf-8")
            _clean_partial(out_dir, vid_id, keep_md=True)
            got += 1
            logger.info(f"[bilibili] 已收录字幕: {title} ({len(text)} 字符)")

    logger.info(f"[bilibili] 共收录 {got} 个视频字幕")
    return got


def _pick_best_subtitle(out_dir: Path, vid_id: str) -> Path | None:
    """优先中文，其次任意语言"""
    subs = sorted(out_dir.glob(f"{vid_id}*.vtt")) + sorted(out_dir.glob(f"{vid_id}*.srt")) \
        + sorted(out_dir.glob(f"{vid_id}*.json3"))
    if not subs:
        return None
    zh = [s for s in subs if re.search(r"zh|ai-zh|chinese", s.name, re.I)]
    return (zh or subs)[0]


def _subtitle_to_text(path: Path) -> str:
    """把 vtt/srt/json3 字幕转纯文本（去时间轴/去重）"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".json3":
        try:
            data = json.loads(raw)
            events = data.get("events", [])
            parts = []
            for ev in events:
                for seg in ev.get("segs", []):
                    t = seg.get("utf8", "")
                    if t.strip():
                        parts.append(t)
            return "".join(parts)
        except Exception:
            return ""
    lines = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")) or "-->" in line \
                or re.fullmatch(r"[\d:.,\s]+", line):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return "\n".join(lines)


def _clean_partial(out_dir: Path, vid_id: str, keep_md: bool = False):
    """清理字幕中间文件"""
    for f in out_dir.glob(f"{vid_id}*"):
        if keep_md and f.suffix == ".md":
            continue
        if f.suffix in {".vtt", ".srt", ".json3"} or ".part" in f.name:
            try:
                f.unlink()
            except OSError:
                pass


# ---------- 入库 ----------
def ingest_collection(rebuild: bool):
    from src.vector_store import VectorStoreManager
    from src.document_processor import DocumentProcessor
    from src.llm_manager import LLMManager
    from src.config import get_config_manager

    config = get_config_manager().get_config()
    collection = config.vector_store.collections.writing_methodology
    processor = DocumentProcessor(config)
    docs = processor.process_directory(
        str(OUT_DIR), collection_tag="writing", source_tag="writing_methodology",
        base_metadata={"kb": "writing_methodology_zh"},
    )
    llm = LLMManager(config)
    vsm = VectorStoreManager(config, llm.get_embeddings())
    if rebuild:
        vsm.reset_collection(collection)
    vsm.add_documents(collection, docs)
    logger.info(f"已入库 collection[{collection}]: {len(docs)} 块")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建论文写作教学知识库")
    parser.add_argument("--rebuild", action="store_true", help="入库前清空并重建 collection")
    parser.add_argument("--skip-web", action="store_true", help="跳过网页抓取")
    parser.add_argument("--skip-bilibili", action="store_true", help="跳过B站字幕")
    parser.add_argument("--skip-skills", action="store_true", help="跳过本地skills教材")
    args = parser.parse_args()

    if not args.skip_skills:
        collect_skills()
    if not args.skip_web:
        collect_web()
    if not args.skip_bilibili:
        collect_bilibili()
    ingest_collection(rebuild=args.rebuild)
