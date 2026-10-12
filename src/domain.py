"""
论文领域包（domain profile）注册表
论文智能体是通用系统：不同研究项目（医学/计算机/工程/社科…）共用同一写作管线，
领域差异全部收敛到这里——每个领域包提供：
  - persona / reviewer      大纲编辑与审稿的 LLM 人设
  - style_prompt            语言规范提示词文件（src/prompts/ 下）
  - templates_prompt        章节叙事模板提示词文件
  - briefs_file             分节写作简报 JSON
  - kb_query                写作教学知识库检索词
  - review_addendum         自查清单附加项（如医学 TRIPOD/统计报告）
  - fact_example            beats 证据引用示例（引导 LLM 引用本领域指标）
画像无 domain 字段时由 detect_domain() 依据数据集/指标/方法类目自动判定；
用户可在注册项目或生成大纲时显式指定，显式指定优先于自动检测。
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class DomainProfile:
    key: str
    label: str                       # 前端下拉显示名
    persona: str                     # 大纲/深化阶段的编辑人设
    reviewer: str                    # 自查/择优评分的审稿人人设
    style_prompt: str                # 语言规范文件名
    style_header: str                # 写作系统提示中规范块的标题
    templates_prompt: str            # 章节叙事模板文件名
    briefs_file: str                 # 分节简报 JSON 文件名
    kb_query: str                    # 写作知识库检索词
    fact_example: str                # beats support 引用示例
    review_addendum: str = ""        # 自查附加清单文件名（空=无）
    outline_hint: str = ""           # 大纲任务里的领域补充要求


DOMAINS: Dict[str, DomainProfile] = {
    # 医学科研论文（预测模型/临床研究）：沿用既有医学提示词，行为与 v3.0 一致
    "medical": DomainProfile(
        key="medical",
        label="医学科研论文（临床/组学/预测模型）",
        persona="中文医学期刊资深编辑与论文导师",
        reviewer="严格的中文医学期刊审稿人",
        style_prompt="cn_journal_style.md",
        style_header="中文医学论文语言规范",
        templates_prompt="section_templates_medical.md",
        briefs_file="section_briefs_medical.json",
        kb_query="中文医学论著 结构 IMRAD 前言 方法 结果 讨论",
        fact_example="【C指数=0.713】",
        review_addendum="self_review_medical.md",
        outline_hint="医学论著须明确数据来源与伦理、队列/入排标准，预测模型研究覆盖 TRIPOD 要素。",
    ),
    # 计算机/工程论文（算法、系统、仿真、CV/NLP）
    "cs": DomainProfile(
        key="cs",
        label="计算机/工程论文（算法·系统·仿真）",
        persona="计算机与工程领域资深研究者兼中文期刊编辑",
        reviewer="严格的计算机/工程领域期刊审稿人",
        style_prompt="cn_academic_style.md",
        style_header="中文科技论文语言规范",
        templates_prompt="section_templates_cs.md",
        briefs_file="section_briefs_cs.json",
        kb_query="计算机论文写作 结构 引言 相关工作 方法 实验 结论",
        fact_example="【mAP=0.682】【F1=0.91】",
        outline_hint=(
            "方法章按「问题定义→总体框架→各模块设计（含公式与复杂度）→实现细节」组织；"
            "实验章须包含数据集与基线、评价指标、消融实验、对比分析与效率分析；"
            "图表中的示例（结构图/曲线图）须在文中逐一解读。"
        ),
    ),
    # 通用科研论文（默认）：其余学科走通用 IMRAD
    "generic": DomainProfile(
        key="generic",
        label="通用科研论文（默认 IMRAD）",
        persona="中文科技期刊资深编辑与论文导师",
        reviewer="严格的中文科技期刊审稿人",
        style_prompt="cn_academic_style.md",
        style_header="中文学术论文语言规范",
        templates_prompt="section_templates_generic.md",
        briefs_file="section_briefs_generic.json",
        kb_query="中文科技论文 结构 IMRAD 前言 材料与方法 结果 讨论",
        fact_example="【关键指标=数值】",
        outline_hint="方法与结果两章的小节必须一一镜像对应；所有量化结论须给出指标与口径。",
    ),
}

# 自动检测信号：命中数多者胜，均无信号 → generic
_MEDICAL_DATASET_TOKENS = {"GEO 数据集", "TCGA 队列", "CPTAC 队列", "MSK CRC 2017 队列"}
_MEDICAL_METRICS = {"C指数", "Uno C", "HR", "IBS", "中位生存", "95%CI", "P值"}
_MEDICAL_CATEGORIES = {"生存模型", "生存分析基础", "医学统计", "组学方法"}
_CS_METRICS = {"F1", "mAP", "IoU", "PSNR", "SSIM", "BLEU", "Top-1", "Top-5", "mIoU", "WER", "CER"}
_CS_DATASET_TOKENS = {"IMAGENET", "CIFAR-10", "CIFAR-100", "MNIST", "FASHION-MNIST",
                      "COCO", "VOC", "OPENIMAGE", "ADE20K", "SQUAD", "GLUE", "WIKITEXT"}
_CS_CATEGORIES = {"计算机视觉", "自然语言处理", "深度学习架构"}  # 供项目级注册表使用


def detect_domain(profile: dict) -> str:
    """依据画像中的数据集标识/关键指标/方法类目判定领域（medical/cs/generic）"""
    med = cs = 0
    for d in profile.get("datasets", []):
        label = (d.get("label") or "").strip()
        token = (d.get("dataset") or "").upper().replace("-", "").replace("_", "")
        if label in _MEDICAL_DATASET_TOKENS:
            med += 2
        if token in _CS_DATASET_TOKENS:
            cs += 2
    for r in profile.get("key_results", []):
        metric = (r.get("metric") or "").strip()
        if metric in _MEDICAL_METRICS:
            med += 1
        elif metric in _CS_METRICS:
            cs += 1
    for m in profile.get("methods", []):
        cat = (m.get("category") or "").strip()
        if cat in _MEDICAL_CATEGORIES:
            med += 1
        elif cat in _CS_CATEGORIES:
            cs += 1
    if med > cs and med >= 2:
        return "medical"
    if cs > med and cs >= 2:
        return "cs"
    return "generic"


def resolve_domain(profile: Optional[dict], override: Optional[str] = None) -> DomainProfile:
    """显式指定 > 画像 domain 字段 > 自动检测；未知值回落 generic"""
    key = (override or "").strip()
    if not key and profile:
        key = (profile.get("domain") or "").strip()
        if key not in DOMAINS:  # 画像未写或写过期值 → 现场检测
            key = detect_domain(profile) if (profile.get("datasets") or profile.get("methods")
                                             or profile.get("key_results")) else ""
    dom = DOMAINS.get(key)
    if dom is None:
        dom = DOMAINS["generic"]
        if key and key not in DOMAINS:
            logger.warning(f"未知领域 '{key}'，回落 generic")
    return dom


def load_prompt(name: str) -> str:
    """读取 prompts 目录文本；领域专属文件缺失时回落同名基础文件"""
    try:
        return (PROMPTS_DIR / name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_domain_prompt(dom: DomainProfile, base: str) -> str:
    """优先领域专属提示词，缺失回落基础文件（base 形如 section_templates.md / cn_journal_style.md）"""
    stem = base.split(".")[0]
    ext = base.split(".", 1)[1] if "." in base else "md"
    for name in (f"{stem}_{dom.key}.{ext}", base):
        text = load_prompt(name)
        if text:
            return text
    return ""


def list_domains() -> List[dict]:
    """前端下拉用：全部领域包 + 自动检测选项"""
    return [{"key": "auto", "label": "自动检测"}] + [
        {"key": d.key, "label": d.label} for d in DOMAINS.values()]
