# -*- coding: utf-8 -*-
"""
services/pdf_locate.py —— 在 PDF 里定位含 land pattern 的页（文本证据 + 结构证据）

提供函数:
    find_land_pattern_page(pdf_path: Path, *, with_stats: bool = True) -> dict

返回 {
    # ---- 兼容旧键 ----
    "page": int | None,          # 人眼 1-based 页码；None = 不能确定（★ 不再瞎猜）
    "matched": [关键词...],      # 判定所依据的关键词
    "score": int,                # 强关键词权重（0 = 没命中强关键词）
    "total_pages": int,
    "method": str,               # primary | fallback | drawing_only | thin_drawing
                                 # | ambiguous | single_page | scan_only | no_match | open_failed
    # ---- 2026-10-14 新增：把「不确定」显式说出来 ----
    "confidence": str,           # high | medium | low | none
    "needs_visual": bool,        # True = 读数值前必须渲染看一眼（结构推定 / 弱关键词 / 并列）
    "reason": str,               # 人话：为什么给这个结论
    "candidates": [ {page, score, fb_hits, dim_signals, text_chars, drawings, images, reason} ],
    "page_stats": [ {page, text_chars, drawings, images, fb_hits, dim_signals} ],
}

为什么要有 2026-10-14 这一版
---------------------------
旧版**只看文本**，图纸页没有文本层就完全看不见，于是：
  - `fpc0510` 真实 land pattern 在第 1 页（0 字符 / 4564 图元），旧版却按弱关键词
    返回第 3 页（材料表）→ **错页，且看不出来**；
  - `hdmi19` / `tf9` / `zif30` / `lydpj` 直接 `no_match`，丢了本可指出的图纸页；
  - `bga496` 第 11/12 页信号**完全相同**（t881/862、d668/668、fb3/3、dim25/25），
    旧版按页码先后取第 11 页，静默漏掉本案例真正对应的第 12 页（Package Code CH）。
新版把「文本证据」与「结构证据（矢量图元密度）」分开统计，并遵守两条纪律：
  1. **弱关键词不得覆盖无文本层图纸页** —— 存在纯图纸页时只报候选，不硬指一页；
  2. **并列就是并列** —— 首位证据不严格领先时 `page=None` + `candidates` 全列出，
     交给渲染/人眼裁定，绝不按页码先后偷偷选一个。
数值仍然由本函数算、语义仍由 AI 判断：这里只提供「哪几页值得看」与「凭什么」。

独立运行:
    python -m services.pdf_locate "C:\\path\\to\\folder"   # 扫单个文件夹
    python -m services.pdf_locate                          # 扫 DEFAULT_FOOTPRINT_ROOT
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("请先安装 pdfplumber:  pip install pdfplumber", file=sys.stderr)
    raise

# 默认目录（仅在无参数独立运行时使用）
DEFAULT_FOOTPRINT_ROOT = Path(r"C:\Users\ds245\Documents\Work_Bmorn\Layout\Data\footprint")
OUTPUT_JSON = Path(r"C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp\Tools\log\land_pattern_pages.json")

PRIMARY_KEYWORDS = [
    "LAND PATTERN", "LANDPATTERN",
    "RECOMMENDED PAD", "RECOMMENDED LAND", "RECOMMENDED PCB",
    "PAD LAYOUT", "FOOTPRINT",
    "焊盘图", "焊盘尺寸", "推荐焊盘", "推荐布局", "封装尺寸",
]
FALLBACK_KEYWORDS = ["PAD", "焊盘", "推荐", "尺寸", "DIMENSION", "SOLDER"]

KEYWORD_WEIGHT = {
    "LAND PATTERN": 100, "LANDPATTERN": 100,
    "RECOMMENDED PAD": 90, "RECOMMENDED LAND": 90,
    "PAD LAYOUT": 80, "RECOMMENDED PCB": 80,
    "焊盘图": 85, "推荐焊盘": 85, "推荐布局": 80,
    "焊盘尺寸": 70, "封装尺寸": 60, "FOOTPRINT": 50,
}

# ---------- 结构信号（2026-10-14） ----------
DRAWING_RICH_MIN = 1000      # 矢量图元数 ≥ 此值 = 图纸页
THIN_TEXT_MAX = 300          # 文本字符数 ≤ 此值 = 文本层稀薄（标注少/纯矢量）
DRAWING_LEAD_MIN = 0.25      # 图元数领先幅度 ≥ 此比例才算「更密」
DIM_SIGNAL_RE = re.compile(r"(?:±|\bmm\b|\bMAX\b|\bMIN\b|\bTYP\b|\d+\.\d{1,3})")
STATS_PAGE_LIMIT = 60        # 逐页统计的页数上限（大 PDF 的成本闸门）
MAX_CANDIDATES = 5


def _score_page(text: str) -> tuple[int, list[str]]:
    """强关键词打分：返回 (最高权重, 命中词)。"""
    if not text:
        return 0, []
    upper = text.upper()
    hits = []
    best = 0
    for kw in PRIMARY_KEYWORDS:
        if kw.upper() in upper:
            hits.append(kw)
            best = max(best, KEYWORD_WEIGHT.get(kw, 10))
    return best, hits


def _fallback_hits(text: str) -> list[str]:
    if not text:
        return []
    upper = text.upper()
    return [kw for kw in FALLBACK_KEYWORDS if kw.upper() in upper]


def _page_record(index: int, text: str, *, counters: dict | None) -> dict:
    """单页证据。counters=None 时不做矢量统计（快路径）。"""
    text = text or ""
    rec = {
        "page": index + 1,
        "text_chars": len(text.strip()),
        "fb_hits": _fallback_hits(text),
        "dim_signals": len(DIM_SIGNAL_RE.findall(text)),
        "drawings": None,
        "images": None,
    }
    if counters is not None:
        rec.update(counters)
    rec["_text"] = text          # 内部用，_public() 会剥掉
    return rec


def analyze_pdf(pdf_path: Path, *, with_stats: bool = True,
                stats_page_limit: int = STATS_PAGE_LIMIT) -> dict:
    """
    打开 PDF，逐页取「文本 + 结构」证据（不判定，只取证）。

    :param pdf_path: PDF 路径
    :param with_stats: True 时统计每页矢量图元/位图数（慢一些，但图纸页靠它才看得见）
    :param stats_page_limit: 逐页统计的页数上限；超出部分只取文本
    :return: {"ok": bool, "error": str|None, "total_pages": int, "pages": [record...]}
    """
    pages: list[dict] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total = len(pdf.pages)
            for i in range(total):
                try:
                    text = pdf.pages[i].extract_text() or ""
                except Exception:                        # noqa: BLE001
                    text = ""
                counters = None
                if with_stats and i < stats_page_limit:
                    counters = {}
                    for attr in ("curves", "lines", "rects", "images"):
                        try:
                            counters[attr] = len(getattr(pdf.pages[i], attr) or [])
                        except Exception:                # noqa: BLE001
                            counters[attr] = -1
                    counters = {
                        "drawings": max(0, counters["curves"]) + max(0, counters["lines"])
                                    + max(0, counters["rects"]),
                        "images": counters["images"],
                    }
                pages.append(_page_record(i, text, counters=counters))
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": str(e), "total_pages": 0, "pages": []}
    return {"ok": True, "error": None, "total_pages": total, "pages": pages}


def _public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _is_sheet(rec: dict) -> bool:
    """无文本层的纯图纸页：0 字符 + 图元密度高。"""
    return (rec["text_chars"] == 0
            and (rec["drawings"] or 0) >= DRAWING_RICH_MIN)


def _is_thin(rec: dict) -> bool:
    """文本层稀薄但图元密度高的页（图纸页带少量标注）。"""
    return (rec["text_chars"] <= THIN_TEXT_MAX
            and (rec["drawings"] or 0) >= DRAWING_RICH_MIN)


def _cand_reason(rec: dict, tag: str = "") -> str:
    """候选页的一句话画像（不带 tag 时只给信号本身）。"""
    bits = [f"t{rec['text_chars']}"]
    if rec.get("drawings") is not None:
        bits.append(f"图元{rec['drawings']}")
    bits.append(f"尺寸信号{rec['dim_signals']}")
    if rec["fb_hits"]:
        bits.append("弱关键词" + "/".join(rec["fb_hits"]))
    body = "，".join(bits)
    return f"{tag}（{body}）" if tag else body


def _stats_digest(pages: list[dict], limit: int = 12) -> list[dict]:
    """
    给模型的逐页证据**摘要**（不是全量）：只保留有信息量的页（有文本 / 图纸页 /
    有弱关键词），按图元降序取前 limit 条 —— 避免几十页的 PDF 把返回值撑爆。
    """
    scored = [p for p in pages
              if p["text_chars"] > 0
              or (p["drawings"] or 0) >= DRAWING_RICH_MIN
              or p["fb_hits"]]
    scored.sort(key=lambda r: (-(r["drawings"] or 0), r["page"]))
    return [_public(p) for p in scored[:limit]]


def _decide(pages: list[dict], total: int) -> dict:
    """纯函数：把逐页证据变成结论（便于离线复算与单测）。"""
    stats = _stats_digest(pages)
    base = {"total_pages": total, "page_stats": stats, "candidates": []}

    if total == 0:
        return {**base, "page": None, "matched": [], "score": 0,
                "method": "no_match", "confidence": "none", "needs_visual": True,
                "reason": "PDF 没有页。"}

    # --- 单页 PDF：没有定位问题，直接给这一页 ---
    if total == 1:
        return {**base, "page": 1, "matched": [], "score": pages[0]["score"],
                "method": "single_page", "confidence": "high", "needs_visual": False,
                "reason": "PDF 只有 1 页，land pattern 只可能在这一页。",
                "candidates": [_public(pages[0])]}

    # --- 第 1 路：强关键词 ---
    scored = [(p["score"], p) for p in pages if p["score"] > 0]
    if scored:
        best = max(s for s, _ in scored)
        leaders = [p for s, p in scored if s == best]
        if len(leaders) == 1:
            p = leaders[0]
            conf = "high" if best >= 60 else "medium"
            return {**base, "page": p["page"], "matched": p["hits"], "score": best,
                    "method": "primary", "confidence": conf,
                    "needs_visual": conf != "high",
                    "reason": (f"第 {p['page']} 页命中强关键词 {p['hits']}（权重 {best}）"
                               + ("" if conf == "high" else "；权重偏低，读数值前先渲染确认")),
                    "candidates": [_public(p)]}
        return {**base, "page": None, "matched": [], "score": best,
                "method": "ambiguous", "confidence": "low", "needs_visual": True,
                "reason": (f"强关键词在多页并列（第 "
                           f"{'、'.join(str(x['page']) for x in sorted(leaders, key=lambda r: r['page']))} "
                           f"页同为权重 {best}）→ 不猜，逐页渲染或做拼版确认。"),
                "candidates": [_public(x) for x in sorted(leaders, key=lambda r: r["page"])]}

    sheets = [p for p in pages if _is_sheet(p)]
    thin = [p for p in pages if _is_thin(p)]
    fb_pages = [p for p in pages if p["fb_hits"]]

    # --- 第 2 路：无文本层图纸页 ---
    if sheets:
        cands = sorted(sheets, key=lambda r: r["page"])
        if len(cands) == 1:
            p = cands[0]
            others = [_cand_reason(x, "弱关键词页") for x in fb_pages if x is not p]
            return {**base, "page": p["page"], "matched": [], "score": 0,
                    "method": "drawing_only", "confidence": "medium", "needs_visual": True,
                    "reason": (f"第 {p['page']} 页无文本层但是图纸页（图元 {p['drawings']} 条）"
                               f"；全文没有强关键词，只能靠结构推定 → 读数值前必须渲染确认"
                               + (f"。另有 {'；'.join(others)}" if others else "")),
                    "candidates": [_public(x) for x in (cands + fb_pages)[:MAX_CANDIDATES]]}
        return {**base, "page": None, "matched": [], "score": 0,
                "method": "ambiguous", "confidence": "low", "needs_visual": True,
                "reason": (f"有 {len(cands)} 页无文本层的图纸页（第 "
                           f"{'、'.join(str(x['page']) for x in cands)} 页）"
                           f"，且全文没有强关键词 → 弱关键词不足以指认其中一页，"
                           f"**绝不按页码先后瞎选**；做一次缩略图拼版即可定位。"),
                "candidates": [_public(x) for x in (cands + fb_pages)[:MAX_CANDIDATES]]}

    # --- 第 3 路：文本层稀薄的图纸页 + 弱关键词页一起排名 ---
    pool = []
    for p in pages:
        if _is_thin(p):
            pool.append((p, _cand_reason(p, "稀薄文本图纸页")))
        elif p["fb_hits"]:
            pool.append((p, _cand_reason(p, "弱关键词页")))
    if pool:
        pool.sort(key=lambda t: (-len(t[0]["fb_hits"]), -t[0]["dim_signals"],
                                 -(t[0]["drawings"] or 0), t[0]["page"]))
        lead, lead_why = pool[0]
        second = pool[1] if len(pool) > 1 else None
        decisive = True
        why_tie = ""
        if second is not None:
            s = second[0]
            if len(lead["fb_hits"]) > len(s["fb_hits"]):
                decisive = True
            elif lead["dim_signals"] > s["dim_signals"]:
                decisive = True
            elif (lead["drawings"] or 0) > (s["drawings"] or 0) * (1 + DRAWING_LEAD_MIN):
                decisive = True
            else:
                decisive = False
                why_tie = (f"第 {lead['page']} 页与第 {s['page']} 页证据相当"
                           f"（弱关键词 {len(lead['fb_hits'])}:{len(s['fb_hits'])}、"
                           f"尺寸信号 {lead['dim_signals']}:{s['dim_signals']}、"
                           f"图元 {lead['drawings']}:{s['drawings']}）")
        if decisive:
            method = "fallback" if lead["fb_hits"] else "thin_drawing"
            return {**base, "page": lead["page"], "matched": lead["fb_hits"], "score": 0,
                    "method": method, "confidence": "low", "needs_visual": True,
                    "reason": (f"无强关键词；{lead_why} 在候选中领先"
                               f" → 结构/弱关键词推定，读数值前必须渲染确认。"),
                    "candidates": [_public(x) for x, _ in pool[:MAX_CANDIDATES]]}
        return {**base, "page": None, "matched": [], "score": 0,
                "method": "ambiguous", "confidence": "low", "needs_visual": True,
                "reason": f"无强关键词，且 {why_tie} → 不猜，逐页渲染或做拼版确认。",
                "candidates": [_public(x) for x, _ in pool[:MAX_CANDIDATES]]}

    # --- 第 4 路：纯位图扫描件（无文本、无矢量） ---
    if all(p["text_chars"] == 0 and not (p["drawings"] or 0) for p in pages):
        return {**base, "page": None, "matched": [], "score": 0,
                "method": "scan_only", "confidence": "none", "needs_visual": True,
                "reason": (f"整份 PDF {total} 页都是位图扫描件（文本 0 字符、矢量 0 条），"
                           f"任何文本方案都无解 → 必须走缩略图拼版人工定位。"),
                "candidates": []}

    return {**base, "page": None, "matched": [], "score": 0,
            "method": "no_match", "confidence": "none", "needs_visual": True,
            "reason": "没有命中任何强关键词，也没有可用的结构证据。",
            "candidates": []}


def find_land_pattern_page(pdf_path: Path, *, with_stats: bool = True) -> dict:
    """
    扫描 PDF，返回最佳匹配页 **或明确指出「不能确定」**。

    ★ 2026-10-14：不再从弱关键词里硬挑一页。返回的 `page=None` 有三类含义，
      看 `method` 区分：`ambiguous`（多页并列，看 candidates）、
      `scan_only`（纯扫描件，做拼版）、`no_match`（真没证据）。
      `needs_visual=True` 时，读数值前必须先渲染确认。

    :param pdf_path: PDF 路径
    :param with_stats: False = 只做文本匹配（快，但看不见无文本层图纸页）
    :return: 见模块 docstring
    """
    analyzed = analyze_pdf(pdf_path, with_stats=with_stats)
    if not analyzed["ok"]:
        return {"page": None, "matched": [], "score": 0, "total_pages": 0,
                "method": "open_failed", "confidence": "none", "needs_visual": True,
                "reason": f"PDF 打不开：{analyzed['error']}", "candidates": [],
                "page_stats": []}

    pages = analyzed["pages"]
    for p in pages:                                       # 强关键词证据在此补齐
        p["score"], p["hits"] = _score_page(p["_text"])
    return _decide(pages, analyzed["total_pages"])


def _find_first_pdf(folder: Path) -> Path | None:
    pdfs = sorted(
        p for p in folder.glob("*.pdf")
        if p.is_file() and not p.name.startswith("~$")
    )
    return pdfs[0] if pdfs else None


def _scan_folder(folder: Path) -> dict:
    pdf = _find_first_pdf(folder)
    if pdf is None:
        return {"folder": folder.name, "pdf": None, "error": "没有 PDF"}
    info = find_land_pattern_page(pdf)
    return {
        "folder": folder.name,
        "pdf": str(pdf),
        "page": info.get("page"),
        "matched": info.get("matched", []),
        "score": info.get("score", 0),
        "total_pages": info.get("total_pages"),
        "method": info.get("method"),
        "confidence": info.get("confidence"),
        "needs_visual": info.get("needs_visual"),
        "reason": info.get("reason"),
        "candidates": [c.get("page") for c in info.get("candidates", [])],
    }


def main():
    # 有参数：扫描单个文件夹
    if len(sys.argv) >= 2:
        target = Path(sys.argv[1])
        if not target.is_dir():
            print(f"目录不存在: {target}", file=sys.stderr)
            sys.exit(1)
        result = _scan_folder(target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 无参数：扫描 DEFAULT_FOOTPRINT_ROOT 下所有子文件夹
    if not DEFAULT_FOOTPRINT_ROOT.is_dir():
        print(f"目录不存在: {DEFAULT_FOOTPRINT_ROOT}", file=sys.stderr)
        sys.exit(1)

    subfolders = sorted(p for p in DEFAULT_FOOTPRINT_ROOT.iterdir() if p.is_dir())
    print(f"发现 {len(subfolders)} 个封装子文件夹")

    results = {}
    for folder in subfolders:
        try:
            r = _scan_folder(folder)
            results[folder.name] = r
            page = r.get("page")
            if page:
                mark = "✅" if r.get("confidence") == "high" else "🟡"
                print(f"  {mark} [{folder.name}] 第 {page} 页  [{r.get('method')}/"
                      f"{r.get('confidence')}]  候选 {r.get('candidates')}")
            else:
                print(f"  ❓ [{folder.name}] 不能确定（{r.get('method')}）  候选 "
                      f"{r.get('candidates')} —— {r.get('reason')}")
        except Exception as e:                            # noqa: BLE001
            results[folder.name] = {"folder": folder.name, "error": str(e)}
            print(f"  ❌ [{folder.name}] {e}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ 已写出 {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
