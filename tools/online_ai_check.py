#!/usr/bin/env python3
"""
在线 AI 检测脚本（免费·合规双模式）
================================================================
背景：主流免费检测站（腾讯朱雀 / ZeroGPT / GPTZero）均为网页产品，
无公开免费 API，直接逆向接口需绕过验证码/指纹防护（违反服务条款）——不做。

本脚本提供两条合规路线：

  模式 A · 浏览器辅助向导（默认，零配置）
      自动读取论文各节 → 逐节复制到剪贴板 → 打开朱雀检测页 →
      你粘贴、点检测、把分数输入回来 → 脚本汇总成报告。
      （正常使用官方网页，不绕过任何防护）

  模式 B · 云端模型检测（--hf-token，自动）
      HuggingFace Inference API 免费层（注册即得 token，
      https://huggingface.co/settings/tokens），调云端 AIGC 检测模型自动出分。

  模式 C · 本地引擎（--local）
      直接用项目内置 BERT/RoBERTa 双语检测器（离线、已缓存）。

用法：
  python tools/online_ai_check.py                # 模式 A（朱雀向导）
  python tools/online_ai_check.py --local        # 模式 C（本地）
  python tools/online_ai_check.py --hf-token hf_xxx --local    # A/B/C 全开
  python tools/online_ai_check.py --project script --min-chars 200
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def _proj_file(project: str, suffix: str) -> Path:
    return ROOT / "projects" / f"{project}{suffix}"

ZHUQUE_URL = "https://matrix.tencent.com/ai/detect/"
ZEROGPT_URL = "https://www.zerogpt.com/"
HF_ROUTER = "https://router.huggingface.co/hf-inference/models/{model}"
HF_MODELS = {
    "zh": "yuchuantian/AIGC_detector_zhv2",
    "en": "yuchuantian/AIGC_detector_env2",
}
# 检测站对单次提交的字符上限（保守值，超长自动截断并提示）
WEB_CHAR_LIMIT = 3000


def set_clipboard(text: str) -> None:
    """Windows 剪贴板（powershell 免第三方依赖）"""
    try:
        p = subprocess.Popen(
            ["powershell", "-NoProfile", "-command",
             "$Input | Set-Clipboard"], stdin=subprocess.PIPE)
        p.communicate(text.encode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  ⚠ 剪贴板写入失败（手动复制下方文本框）: {e}")


def open_url(url: str) -> None:
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        print(f"  请手动打开: {url}")


def load_sections(project: str, min_chars: int) -> list:
    path = _proj_file(project, ".outline.json")
    if not path.exists():
        sys.exit(f"未找到大纲: {path}")
    d = json.loads(path.read_text(encoding="utf-8"))
    secs = []
    for s in d.get("sections", []):
        c = (s.get("content") or "").strip()
        if len(c) >= min_chars:
            secs.append({"id": s["id"], "title": s["title"], "content": c})
    return secs


# ---------------- 模式 A：浏览器辅助向导 ----------------
def run_assist(secs: list) -> list:
    print("\n═══ 模式 A · 朱雀浏览器辅助向导 ═══")
    print("流程：每节自动复制到剪贴板并打开朱雀 → 你粘贴检测 → 输入 AI 占比回车")
    print(f"检测页：{ZHUQUE_URL}\n")
    results = []
    for i, s in enumerate(secs, 1):
        text = s["content"][:WEB_CHAR_LIMIT]
        truncated = len(s["content"]) > WEB_CHAR_LIMIT
        print(f"[{i}/{len(secs)}] {s['title']}（{len(s['content'])} 字"
              f"{'，已截取前 3000 字送检' if truncated else ''}）")
        set_clipboard(text)
        if i == 1:
            open_url(ZHUQUE_URL)
        while True:
            ans = input("  粘贴检测后，输入 AI 占比数字（如 12 表示 12%，s=跳过 r=重开网页）：").strip()
            if ans == "s":
                results.append({**s, "zhuque_pct": None}); break
            if ans == "r":
                open_url(ZHUQUE_URL); continue
            try:
                pct = float(ans)
                results.append({**s, "zhuque_pct": pct}); break
            except ValueError:
                print("  请输入数字（如 12 或 12.5）")
        time.sleep(0.3)
    return results


# ---------------- 模式 B：HuggingFace 云端 ----------------
def hf_detect(text: str, lang: str, token: str) -> dict | None:
    model = HF_MODELS[lang]
    req = urllib.request.Request(
        HF_ROUTER.format(model=model),
        data=json.dumps({"inputs": text[:2000]}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)
        # [{label, score}] 形式
        item = out[0] if isinstance(out, list) and out else out
        label = str(item.get("label", "")).lower()
        score = float(item.get("score", 0.5))
        is_ai = any(k in label for k in ("1", "ai", "fake", "machine")) and \
            not any(k in label for k in ("0", "human", "real"))
        return {"ai_prob": round(score if is_ai else 1 - score, 3), "model": model.split("/")[-1]}
    except Exception as e:
        print(f"  ⚠ HF 云端检测失败: {e}")
        return None


def run_hf(secs: list, token: str) -> list:
    print("\n═══ 模式 B · HuggingFace 云端检测 ═══")
    results = []
    for i, s in enumerate(secs, 1):
        import re
        cjk = len(re.findall(r"[\u4e00-\u9fff]", s["content"]))
        letters = len(re.findall(r"[A-Za-z]", s["content"]))
        lang = "zh" if cjk >= 30 and cjk >= letters * 0.5 else "en"
        r = hf_detect(s["content"], lang, token)
        print(f"[{i}/{len(secs)}] {s['title']:<12} → "
              f"{r and f'{round(r['ai_prob']*100)}%（{r['model']}）'}")
        results.append({**s, "hf": r})
        time.sleep(2)  # 免费层限速礼貌间隔
    return results


# ---------------- 模式 Z：朱雀官方 API（EdgeOne 网关，全自动） ----------------
def run_zhuque(secs: list) -> list:
    print("\n═══ 模式 Z · 朱雀官方 API（.env 的 ZHUQUE_API_KEY，月免费 50 万 token） ═══")
    sys.path.insert(0, str(ROOT))
    from src.zhuque_client import zhuque_detect, zhuque_available
    if not zhuque_available():
        print("  ✗ 未配置 ZHUQUE_API_KEY（在 论文智能体/.env 中添加后重跑）")
        return []
    results = []
    for i, s in enumerate(secs, 1):
        r = zhuque_detect(s["content"])
        if r:
            print(f"[{i}/{len(secs)}] {s['title']:<12} → AI {round(r['ai_ratio']*100)}%"
                  f" · 疑似 {round(r['suspect_ratio']*100)}%"
                  f" · 人工 {round(r['human_ratio']*100)}%（{r['tokens']} tokens）")
        else:
            print(f"[{i}/{len(secs)}] {s['title']:<12} → 检测失败（额度/网络）")
        results.append({**s, "zhuque": r})
        time.sleep(1)
    return results


# ---------------- 模式 C：本地引擎 ----------------
def run_local(secs: list) -> list:
    print("\n═══ 模式 C · 本地 BERT/RoBERTa 引擎 ═══")
    sys.path.insert(0, str(ROOT))
    from src.aigc_detector import detect_aigc
    results = []
    for i, s in enumerate(secs, 1):
        r = detect_aigc(s["content"])
        print(f"[{i}/{len(secs)}] {s['title']:<12} → "
              f"{r and f'{round(r['ai_prob']*100)}% {r['verdict']}（{r['model']} {r['chunks']}块）'}")
        results.append({**s, "local": r})
    return results


def merge(title_maps: list) -> list:
    """按 (id,title) 合并各模式结果"""
    final = {}
    for m in title_maps:
        for item in m:
            key = item["id"]
            final.setdefault(key, {"id": item["id"], "title": item["title"],
                                   "words": len(item["content"])})
            for k in ("zhuque_pct", "hf", "local", "zhuque"):
                if item.get(k) is not None:
                    final[key][k] = item[k]
    return list(final.values())


def main():
    ap = argparse.ArgumentParser(description="免费在线 AI 检测（合规双模式+本地引擎）")
    ap.add_argument("--project", default="script")
    ap.add_argument("--min-chars", type=int, default=200)
    ap.add_argument("--assist", action="store_true", help="模式 A：朱雀浏览器向导（默认开）")
    ap.add_argument("--hf-token", default=None, help="模式 B：HuggingFace 免费token")
    ap.add_argument("--zhuque", action="store_true", help="模式 Z：朱雀官方API（需 .env 配 ZHUQUE_API_KEY）")
    ap.add_argument("--local", action="store_true", help="模式 C：本地引擎")
    args = ap.parse_args()

    secs = load_sections(args.project, args.min_chars)
    if not secs:
        sys.exit("没有可检测的成稿章节")
    print(f"载入 {len(secs)} 节成稿（项目 {args.project}）")

    runs = []
    if args.zhuque:
        runs.append(run_zhuque(secs))
    if args.hf_token:
        runs.append(run_hf(secs, args.hf_token))
    if args.local:
        runs.append(run_local(secs))
    if not runs or args.assist:
        runs.append(run_assist(secs))

    merged = merge(runs)

    print("\n═══════ 汇总报告 ═══════")
    print(f"{'章节':<12}{'字数':>7}   朱雀API   HF云端   本地")
    print("-" * 62)
    for m in merged:
        zq = (f"{m['zhuque_pct']:.0f}%" if m.get("zhuque_pct") is not None
              else (f"{round(m['zhuque']['ai_ratio']*100)}%+{round(m['zhuque']['suspect_ratio']*100)}%"
                    if m.get("zhuque") else "-"))
        hf = f"{round(m['hf']['ai_prob']*100)}%" if m.get("hf") else "-"
        lc = f"{round(m['local']['ai_prob']*100)}%" if m.get("local") else "-"
        print(f"{m['title']:<12}{m['words']:>7}   {zq:>5}  {hf:>6}   {lc:>4}")
    out = _proj_file(args.project, ".ai_online_report.json")
    Path(out).write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print("-" * 62)
    print(f"已保存: {out}")
    print("参考：朱雀 ≤30% 一般安全；本地/HF ≥70% 判 AI、50-70% 疑似")


if __name__ == "__main__":
    main()
