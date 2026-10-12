"""
项目画像提取器
扫描任意研究项目目录（用户在界面上注册），结合方法注册表，
产出结构化 project_profile.json：研究阶段/方法清单(带文件证据)/数据集/关键结果数字/领域。
这是大纲生成器的输入。方法注册表优先用项目内 <项目>/method_registry.yaml（或
.paper-agent/methods.yaml），否则用全局 ingestion/method_registry.yaml——两个都缺时
方法匹配跳过（项目理解 Agent 仍可补齐）。
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from src.config import get_config_manager
from src.domain import detect_domain
from src import scoping

logger = logging.getLogger(__name__)

# 报告/文档类文件（画像的证据来源）——含结果类文本（csv/json/log/yaml）
DOC_EXTS = {".md", ".txt", ".csv", ".json", ".log", ".yaml", ".yml", ".tex"}
# 结果表直接提取的格式
TABLE_EXTS = {".csv", ".json"}
CODE_EXTS = {".py", ".ipynb", ".R"}
# 图片格式（可预览）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".webp", ".tif", ".tiff", ".ico"}
# 二进制格式（不可文本预览）
BINARY_EXTS = {".pkl", ".pickle", ".h5", ".hdf5", ".npy", ".npz", ".parquet",
               ".zip", ".gz", ".tar", ".7z", ".rar", ".exe", ".dll", ".rds",
               ".rda", ".sqlite", ".db", ".bin", ".pdf"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "legacy", ".venv", "node_modules", ".git"}
# 画像索引时跳过的重目录（图片/原始数据目录可能巨大），但文件树仍显示；
# results/output 不再跳过——它们是关键结果数字与结果表的来源
INDEX_SKIP_DIRS = SKIP_DIRS | {"figures", "loco_data", "final_model"}
SKIP_FILES: set = set()
# 项目级方法注册表的候选位置（先命中先用）
PROJECT_REGISTRY_NAMES = ("method_registry.yaml", ".paper-agent/methods.yaml", "methods.yaml")
# 结果数字提取模式：医学生存分析 + 机器学习/工程通用指标（多领域并存，互不干扰）
RESULT_PATTERNS = [
    # ---- 医学/生存分析 ----
    (r"[Cc](?:-?index|指数|统计量)[^\d\n]{0,20}?(0\.\d{2,4})", "C指数"),
    (r"Uno['’]?s?\s*[Cc][^\d\n]{0,20}?(0\.\d{2,4})", "Uno C"),
    (r"(?:HR|风险比)[^\d\n]{0,20}?(\d+\.\d{2,3})", "HR"),
    (r"C\s*=\s*(0\.\d{3,4})", "C指数"),
    (r"(?:IBS|ibs|integrated[^_\n]{0,12}Brier)[^\d\n]{0,15}?(0\.\d{2,4})", "IBS"),
    (r"(?:中位生存|median\s*OS|mOS)[^\d\n]{0,15}?(\d+(?:\.\d+)?)", "中位生存"),
    (r"95%CI[^\d\n]{0,6}(\d+\.\d{2,3})", "95%CI"),
    # ---- 机器学习/工程通用 ----
    (r"(?:AUC|auc)[^\d\n]{0,15}?(0\.\d{2,4})", "AUC"),
    (r"(?:R2|R²|决定系数)[^\d\n]{0,15}?(0\.\d{2,4})", "R²"),
    (r"(?:MSE|RMSE|MAE)[^\d\n]{0,15}?(0\.\d{2,5})", "误差"),
    (r"(?:时间依赖[^_\n]{0,12}(?:AUC|C))[^\d\n]{0,15}?(0\.\d{2,4})", "时间依赖AUC"),
    (r"(?:准确率|accuracy|Accuracy)[^\d\n]{0,15}?(0?\.\d{2,4})", "准确率"),
    (r"(?:F1[-_ ]?(?:score|分数)?|F1值)[^\d\n]{0,12}?(\d?\.\d{2,4})", "F1"),
    (r"(?:精确率|precision|Precision|查准率)[^\d\n]{0,12}?(0?\.\d{2,4})", "精确率"),
    (r"(?:召回率|recall|Recall|查全率)[^\d\n]{0,12}?(0?\.\d{2,4})", "召回率"),
    (r"(?:m?IoU|MIoU|平均交并比)[^\d\n]{0,12}?(0?\.\d{2,4})", "IoU"),
    (r"(?:mAP|MAP\b|mean\s*average\s*precision|平均精度均值)[^\d\n]{0,12}?(0?\.\d{2,4})", "mAP"),
    (r"(?:PSNR)[^\d\n]{0,12}?(\d{1,2}\.\d{1,3})", "PSNR"),
    (r"(?:SSIM)[^\d\n]{0,12}?(0?\.\d{2,4})", "SSIM"),
    (r"(?:BLEU)[^\d\n]{0,12}?(\d?\.\d{2,4})", "BLEU"),
    (r"Top[-_ ]?[15][^\d\n]{0,12}?(0?\.\d{2,4}|\d{2}\.\d{1,2})", "Top-k准确率"),
    (r"样本量[^\d\n]{0,10}n?\s*[=＝]?\s*(\d{2,5})", "样本量"),
    (r"P\s*[<＜=]\s*(0\.\d+)", "P值"),
]

# 单文件读取上限（结果目录里可能有超大 csv/log）
RESULT_FILE_MAX_BYTES = 2_000_000


class ProjectIndexer:
    """研究项目画像提取器"""

    def __init__(self, config=None):
        if config is None:
            config = get_config_manager().get_config()
        self.config = config
        self._registry = None

    # ---------- 方法注册表 ----------
    @property
    def registry(self) -> List[dict]:
        """全局方法注册表（ingestion/method_registry.yaml）；缺失时返回空（项目可带自己的注册表）"""
        if self._registry is None:
            reg_path = Path(self.config.knowledge_base.registry_dir) / "method_registry.yaml"
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    self._registry = yaml.safe_load(f) or []
            except FileNotFoundError:
                logger.info(f"未找到全局方法注册表 {reg_path}，方法匹配将跳过"
                            "（可在项目目录放 method_registry.yaml 启用）")
                self._registry = []
            except Exception as e:
                logger.warning(f"全局方法注册表读取失败（按空表处理）: {e}")
                self._registry = []
        return self._registry

    def _registry_for(self, root: Path) -> List[dict]:
        """项目级注册表优先：<项目>/method_registry.yaml 或 .paper-agent/methods.yaml"""
        for rel in PROJECT_REGISTRY_NAMES:
            p = root / rel
            if p.is_file():
                try:
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
                    logger.info(f"项目级方法注册表: {p}（{len(data)} 项）")
                    return data
                except Exception as e:
                    logger.warning(f"项目级注册表读取失败，回落全局: {p} {e}")
        return self.registry

    # ---------- 主入口 ----------
    def build_profile(self, project_path: str, project_name: Optional[str] = None,
                      domain: Optional[str] = None) -> dict:
        """扫描项目目录并生成画像 JSON；domain 显式指定时写入画像，缺省自动检测"""
        root = Path(project_path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"项目目录不存在: {root}")

        name = project_name or root.name
        logger.info(f"开始构建项目画像: {name} ({root})")

        code_files = self._collect_files(root, CODE_EXTS)
        doc_files = self._collect_files(root, DOC_EXTS)

        profile = {
            "project_name": name,
            "project_path": str(root),
            "stages": self._extract_stages(root),
            "methods": self._match_methods(code_files, root),
            "datasets": self._match_datasets(code_files + doc_files),
            "key_results": self._extract_results(doc_files),
            "result_tables": self._extract_result_tables(root),
            "figures": self._collect_figures(root),
            "documents": [str(p) for p in doc_files],
            "code_files_count": len(code_files),
            "doc_files_count": len(doc_files),
        }
        profile["domain"] = (domain or "").strip() or detect_domain(profile)

        out_path = scoping.projects_dir() / f"{self._safe_name(name)}.profile.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)  # projects/{owner}/ 自动创建
        # 保留视觉深化成果：重建画像时把旧 figures[].desc 按路径搬回（否则重索引会
        # 丢掉视觉模型逐图分析的结果）
        if out_path.exists():
            try:
                old = json.loads(out_path.read_text(encoding="utf-8"))
                old_desc = {f.get("path"): f.get("desc")
                            for f in old.get("figures", []) if f.get("desc")}
                if old_desc:
                    for f in profile.get("figures", []):
                        if f.get("path") in old_desc:
                            f["desc"] = old_desc[f["path"]]
                    profile["vision_enriched_at"] = old.get("vision_enriched_at")
            except Exception as e:
                logger.warning(f"旧画像 desc 迁移失败（忽略）: {e}")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        profile["profile_file"] = str(out_path)
        logger.info(f"项目画像已保存: {out_path}")
        return profile

    # ---------- 内部实现 ----------
    def _collect_files(self, root: Path, exts: set) -> List[Path]:
        files = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            if any(part in INDEX_SKIP_DIRS for part in p.parts):
                continue
            if p.name in SKIP_FILES:
                continue
            files.append(p)
        return files

    def _extract_stages(self, root: Path) -> List[dict]:
        """按顶层子目录识别流水线阶段（目录名即阶段名，附 README 摘要）"""
        stages = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in SKIP_DIRS:
                continue
            readme = None
            for cand in ("README.md", "readme.md", "README.MD", "研究流程设计.md", "研究方案.md"):
                r = child / cand
                if r.exists():
                    readme = r
                    break
            if readme is None:  # 大小写不敏感兜底（Readme.md / readme.txt 等）
                for r in sorted(child.glob("*")):
                    if r.is_file() and r.stem.lower() == "readme":
                        readme = r
                        break
            summary = ""
            if readme:
                try:
                    text = readme.read_text(encoding="utf-8", errors="ignore")
                    # 取前几行非标题正文作为阶段摘要（结果目录的 README 常含关键数字）
                    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
                    summary = " ".join(lines)[:1500]
                except Exception:
                    pass
            py_count = len(list(child.rglob("*.py")))
            stages.append({
                "stage_dir": child.name,
                "summary": summary,
                "python_scripts": py_count,
            })
        return stages

    def _collect_figures(self, root: Path) -> List[dict]:
        """图片注册表：扫描项目内全部图片（含 results/figures 目录），供论文引用"""
        figs = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                continue
            if any(part in SKIP_DIRS for part in p.parts):  # 基础跳过（保留 results/figures）
                continue
            try:
                if p.stat().st_size < 3000:  # 忽略小图标
                    continue
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            figs.append({"path": str(p), "rel": rel, "name": p.name})
            if len(figs) >= 200:
                break
        return figs

    def _match_methods(self, code_files: List[Path], root: Optional[Path] = None) -> List[dict]:
        """用注册表 code_patterns 在代码中匹配方法，记录文件证据（注册表空则跳过）"""
        registry = self._registry_for(root) if root else self.registry
        matched: Dict[str, dict] = {}
        compiled = []
        for m in registry:
            pats = []
            for pat in m.get("code_patterns", []):
                try:
                    compiled.append((m["id"], re.compile(re.escape(pat))))
                    pats.append(pat)
                except re.error:
                    continue

        for path in code_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = str(path)
            lines = text.splitlines()
            for method_id, pattern in compiled:
                hit_line = None
                for i, line in enumerate(lines, 1):
                    if pattern.search(line):
                        hit_line = i
                        break
                if hit_line is None:
                    continue
                entry = matched.setdefault(method_id, {
                    "method_id": method_id,
                    "evidence": [],
                })
                if len(entry["evidence"]) < 5:
                    entry["evidence"].append({"file": rel, "line": hit_line})

        # 补充方法名称信息
        reg_by_id = {m["id"]: m for m in registry}
        methods = []
        for mid, info in matched.items():
            meta = reg_by_id.get(mid, {})
            methods.append({
                "method_id": mid,
                "name_zh": meta.get("name_zh", mid),
                "name_en": meta.get("name_en", ""),
                "category": meta.get("category", ""),
                "paper_title": (meta.get("paper") or {}).get("title"),
                "paper_year": (meta.get("paper") or {}).get("year"),
                "paper_doi": (meta.get("paper") or {}).get("doi"),
                "evidence": info["evidence"],
            })
        methods.sort(key=lambda x: (x["category"], x["method_id"]))
        return methods

    def _match_datasets(self, files: List[Path]) -> List[dict]:
        """识别数据集标识：生物医学队列（GSExxx/TCGA/CPTAC）+ 常见公开数据集（CV/NLP/表格）"""
        found: Dict[str, dict] = {}
        ds_patterns = [
            # 生物医学队列
            (r"GSE\d{4,6}", "GEO 数据集"),
            (r"TCGA[-_ ]?(COAD|READ|COADREAD)?", "TCGA 队列"),
            (r"CPTAC", "CPTAC 队列"),
            (r"MSK[-_ ]?CRC[-_ ]?2017", "MSK CRC 2017 队列"),
            # 常见公开数据集（CV/NLP/通用）
            (r"\bImageNet\b", "ImageNet 数据集"),
            (r"\bCIFAR[- ]?(?:10|100)\b", "CIFAR 数据集"),
            (r"\bMNIST\b", "MNIST 数据集"),
            (r"\bFashion[- ]?MNIST\b", "Fashion-MNIST 数据集"),
            (r"\bCOCO\b", "COCO 数据集"),
            (r"\b(Pascal\s*)?VOC20\d\d\b", "VOC 数据集"),
            (r"\bADE20K\b", "ADE20K 数据集"),
            (r"\b(SQuAD|GLUE|SuperGLUE)\b", "NLP 基准数据集"),
            (r"\bWikiText\b", "WikiText 语料"),
        ]
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat, label in ds_patterns:
                for m in re.finditer(pat, text):
                    token = m.group(0).upper().replace("_", "-").replace(" ", "").strip("-")
                    if token.startswith("TCGA"):
                        token = "TCGA-COADREAD"
                    entry = found.setdefault(token, {"dataset": token, "label": label, "files": []})
                    if len(entry["files"]) < 3 and str(path) not in entry["files"]:
                        entry["files"].append(str(path))
        return list(found.values())

    def _extract_results(self, doc_files: List[Path]) -> List[dict]:
        """从报告/结果文档提取关键结果数字，每条附行级上下文与来源目录（队列/模型归属线索）"""
        results: Dict[str, dict] = {}
        total = 0
        for path in doc_files:
            try:
                if path.stat().st_size > RESULT_FILE_MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                continue
            # csv/log 行多且重复，只扫前 300 行防止同值刷屏
            lines = text.splitlines()
            if path.suffix.lower() in (".csv", ".log"):
                lines = lines[:300]
            dir_hint = path.parent.name
            file_hits = 0
            for pat, label in RESULT_PATTERNS:
                for i, ln in enumerate(lines):
                    if file_hits >= 40:
                        break
                    m = re.search(pat, ln)
                    if not m:
                        continue
                    value = m.group(1)
                    key = f"{label}={value}"
                    context = ln.strip()[:160]
                    entry = results.setdefault(key, {
                        "metric": label, "value": value, "context": context, "sources": []})
                    if len(entry["sources"]) < 3:
                        entry["sources"].append({"file": path.name, "dir": dir_hint})
                    file_hits += 1
                    total += 1
                    if total >= 400:
                        logger.info("key_results 达到 400 条上限，提前结束扫描")
                        return list(results.values())
        return list(results.values())

    def _extract_result_tables(self, root: Path, max_files: int = 60) -> List[dict]:
        """提取结果表（csv/json 前若干行）——正文表格与数字的直接证据源"""
        tables: List[dict] = []
        for p in sorted(root.rglob("*")):
            if len(tables) >= max_files:
                break
            if not p.is_file() or p.suffix.lower() not in TABLE_EXTS:
                continue
            if any(part in INDEX_SKIP_DIRS for part in p.parts):
                continue
            try:
                if p.stat().st_size > RESULT_FILE_MAX_BYTES or p.stat().st_size < 50:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                continue
            rel = p.relative_to(root).as_posix()
            if p.suffix.lower() == ".csv":
                rows = [ln.rstrip() for ln in text.splitlines() if ln.strip()][:30]
                if len(rows) < 2:
                    continue
                ncols = max((ln.count(",") + 1) for ln in rows)
                if ncols < 2:
                    continue
                tables.append({"file": rel, "dir": p.parent.name, "format": "csv",
                               "cols": ncols, "rows": len(rows), "preview": "\n".join(rows[:12])[:2500]})
            else:  # json
                s = text.strip()
                if not (s.startswith("{") or s.startswith("[")):
                    continue
                try:
                    obj = json.loads(s)
                except ValueError:
                    continue
                # 只收"小型结果型" json：解析后序列化 ≤3000 字
                dump = json.dumps(obj, ensure_ascii=False)
                if len(dump) > 3000:
                    continue
                tables.append({"file": rel, "dir": p.parent.name, "format": "json",
                               "cols": "-", "rows": "-", "preview": dump[:2500]})
        return tables

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^\w\-]+", "_", name).strip("_")

    def list_projects(self, root: Optional[str] = None) -> List[dict]:
        """列出工作区根目录下可注册的研究项目目录（含脚本/文档子目录）。
        排除目录可由 config.yaml 的 workspace.exclude_dirs 配置（默认只排除常见非项目目录）"""
        raw = root if root and str(root).strip() else self.config.system.workspace_root
        if not str(raw).strip():
            return []  # 工作区未设置：等待用户在界面选择根目录
        ws = Path(raw).resolve()
        projects = []
        if not ws.exists():
            return projects
        exclude = set(self.config.workspace.exclude_dirs) | \
            {"__pycache__", "node_modules", ".venv", ".git"}
        for child in sorted(ws.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            if child.name in exclude:
                continue
            py_count = len([p for p in child.rglob("*.py") if not any(x in p.parts for x in SKIP_DIRS)])
            md_count = len([p for p in child.rglob("*.md") if not any(x in p.parts for x in SKIP_DIRS)])
            if py_count + md_count == 0:
                continue
            projects.append({
                "name": child.name,
                "path": str(child),
                "python_scripts": py_count,
                "markdown_docs": md_count,
            })
        return projects

    def file_tree(self, project_path: str, max_depth: int = 4) -> dict:
        """返回项目目录树（供前端左栏文件选择）；显示所有格式的文件"""
        root = Path(project_path).resolve()

        def build(node: Path, depth: int) -> dict:
            tree = {
                "name": node.name,
                "path": str(node),
                "type": "dir" if node.is_dir() else "file",
                "is_image": node.suffix.lower() in IMAGE_EXTS,
                "children": [],
            }
            if node.is_dir() and depth < max_depth:
                try:
                    for child in sorted(node.iterdir()):
                        if child.name in SKIP_DIRS or child.name.startswith("."):
                            continue
                        tree["children"].append(build(child, depth + 1))
                except PermissionError:
                    pass
            return tree

        return build(root, 0)

    def read_file_preview(self, file_path: str, max_chars: int = 20000) -> dict:
        """读取文件内容（带轨迹日志）；区分文本/图片/二进制，超长截断"""
        from src.observability import span as _span
        with _span("file.read_preview", kind="tool",
                   in_={"path": file_path, "max_chars": max_chars}) as _s:
            result = self._read_file_preview_impl(file_path, max_chars)
            _s.out = {"type": result.get("type"), "chars": len(result.get("content") or "")}
            return result

    def _read_file_preview_impl(self, file_path: str, max_chars: int = 20000) -> dict:
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        ext = path.suffix.lower()

        base = {"path": str(path), "name": path.name, "file_type": ext.lstrip(".")}
        if ext in IMAGE_EXTS:
            return {**base, "type": "image", "truncated": False, "content": ""}
        if ext in BINARY_EXTS and ext != ".pdf":
            return {**base, "type": "binary", "truncated": False,
                    "content": f"[二进制文件，无法以文本预览]"}
        if ext == ".pdf":
            from src.document_processor import _load_pdf
            docs = _load_pdf(path)
            content = "\n\n".join(d.page_content for d in docs)[:max_chars]
        else:
            # 其余一律按文本尝试（含未知扩展名，如 .log/.csv/.gmt/.json 等）
            content = path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        return {**base, "type": "text", "truncated": len(content) >= max_chars, "content": content}


def get_project_indexer(config=None) -> ProjectIndexer:
    return ProjectIndexer(config)
