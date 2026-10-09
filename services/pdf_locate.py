# -*- coding: utf-8 -*-
"""
job_find_land_pattern_page.py —— 在 PDF 里定位含 land pattern 关键词的页

提供函数:
    find_land_pattern_page(pdf_path: Path) -> dict
    返回 {
        "page": int | None,      # 人眼 1-based 页码；None 表示未找到
        "matched": [关键词...],
        "total_pages": int,
        "method": "primary" | "fallback" | "no_match",
    }

独立运行:
    python job_find_land_pattern_page.py "C:\\path\\to\\folder"   # 扫描单个文件夹
    python job_find_land_pattern_page.py                          # 扫描 DEFAULT_FOOTPRINT_ROOT
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("请先安装 pdfplumber:  pip install pdfplumber", file=sys.stderr)
    raise

# 默认目录（仅在无参数独立运行时使用）
DEFAULT_FOOTPRINT_ROOT = Path(r"C:\Users\ds245\Documents\工作文档_蓝晨\Layout\footprint")
OUTPUT_JSON = Path(r"C:\Users\ds245\Documents\Project_Layout\land_pattern_pages.json")

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


def _score_page(text: str) -> tuple[int, list[str]]:
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


def find_land_pattern_page(pdf_path: Path) -> dict:
    """
    扫描 PDF，返回最佳匹配页。
    匹配失败（无任何关键词）返回 {"page": None, "method": "no_match", ...}。
    """
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total = len(pdf.pages)
            page_texts = []
            for i in range(total):
                try:
                    t = pdf.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                page_texts.append(t)
    except Exception as e:
        return {
            "page": None, "matched": [], "total_pages": 0,
            "method": "open_failed", "error": str(e),
        }

    # 第一轮：强关键词
    candidates = []
    for i, text in enumerate(page_texts):
        score, hits = _score_page(text)
        if score > 0:
            candidates.append((score, i, hits))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        best_score, best_idx, best_hits = candidates[0]
        return {
            "page": best_idx + 1,
            "matched": best_hits,
            "score": best_score,
            "total_pages": total,
            "method": "primary",
        }

    # 第二轮：弱关键词兜底
    fallback = []
    for i, text in enumerate(page_texts):
        upper = text.upper()
        hits = [kw for kw in FALLBACK_KEYWORDS if kw.upper() in upper]
        if hits:
            fallback.append((len(hits), i, hits))
    if fallback:
        fallback.sort(key=lambda x: (-x[0], x[1]))
        n_hits, idx, hits = fallback[0]
        return {
            "page": idx + 1,
            "matched": hits,
            "score": n_hits,
            "total_pages": total,
            "method": "fallback",
        }

    return {
        "page": None, "matched": [], "score": 0,
        "total_pages": total, "method": "no_match",
    }


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
                print(f"  ✅ [{folder.name}] 第 {page} 页  [{r.get('method')}]")
            else:
                print(f"  ❌ [{folder.name}] 未找到匹配页")
        except Exception as e:
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