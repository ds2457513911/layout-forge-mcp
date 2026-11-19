"""读数配额层：候选数字带坐标 + 焦点区域按像素配额渲染。

为什么要有这一层
----------------
forge 已有 render_pdf_region（按矩形渲染）与 measure_region（矢量量距），
缺的是「模型该看哪一块、该看多清」这件事的机器化：

- 900 dpi 的整页 A4 是 7442x8210 = 6100 万像素。主流视觉模型内部会把长边压到
  ~1600 px —— 我们花的渲染成本全部被丢弃，等效只剩 ~190 dpi，正好落进
  forge 文档里的「200 dpi 把 0.5 读成 0.7」翻车区间。
- 正确做法是**分辨率配额**：概览图压到 MAX_IMAGE_PX（看整体布局），
  焦点区切 2x2 让面积降到 1/4、可容纳的 dpi 翻倍（等效分辨率翻倍）。
  焦点区连 600 dpi 都做不到就**继续缩范围**，不是降 dpi 了事。

候选数字（文本层 span 的数值 + bbox）只作**线索**，不在这里判定归属：
同一个 0.5 可能是焊盘宽、也可能是公差。让模型带着位置判断「标在图上还是表格里」。

★ 纯扫描件抽不到数字不是失败：返回空表，模型直接看裁剪图。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services import pdf_service

#: 区域渲染的默认 dpi 上限（亚毫米尺寸要 900–1200 dpi 才读得准）
DEFAULT_REGION_DPI = 900

#: 单张图的长边像素上限（送给模型的图再大也会被压到 ~1600）
MAX_IMAGE_PX = 1600

#: 分块上限（2x2）。再多会让单次调用超时、注意力被摊薄
MAX_TILES = 4

#: 焦点区域的**最低** dpi。达不到就把范围缩小，而不是降 dpi
MIN_FOCUS_DPI = 600

#: 候选数字正则：带 1–3 位小数的数，以及整数（页码/年份在下面再筛）
_NUM_RE = re.compile(r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)(?![\d.])")

#: 明显不是尺寸的数字（年份）
_SKIP_PREFIX = ("20",)


def page_limits(pdf_path: Path, page: int) -> Tuple[float, float, float, float]:
    """取页面显示坐标矩形（rotation 已并入）。失败返回 A4 近似值。"""
    try:
        import pymupdf                                     # noqa: PLC0415

        with pymupdf.open(str(pdf_path)) as doc:
            if 1 <= page <= len(doc):
                r = doc[page - 1].rect
                return (r.x0, r.y0, r.x1, r.y1)
    except Exception:                                      # noqa: BLE001
        pass
    return (0.0, 0.0, 595.0, 842.0)


def dpi_for_pixels(rect: Tuple[float, float, float, float], target_px: int) -> int:
    """
    反解：让这块区域渲染后长边不超过 target_px，dpi 该取多少。

    PDF 坐标单位是 pt（1/72 inch），像素 = pt / 72 * dpi。
    """
    w = abs(rect[2] - rect[0])
    h = abs(rect[3] - rect[1])
    longest = max(w, h)
    if longest <= 0:
        return 150
    return max(72, min(int(target_px * 72.0 / longest), 1200))


def extract_numbers(
    pdf_path: Path,
    page: int,
    *,
    max_numbers: int = 400,
) -> Dict[str, Any]:
    """
    抽该页文本层里「像尺寸」的数字，带 bbox（PDF 显示坐标 pt）。

    ★ 只收不筛：0.5 可能是焊盘宽、也可能是公差，归属交给看图的模型。
    ★ 扫描件返回空表 + 一条 warning，不算失败。

    :return: {"ok", "page", "numbers": [{"text","value","bbox","src"}], "count", "warnings"}
    """
    warnings: List[str] = []
    out: List[Dict[str, Any]] = []
    try:
        import pymupdf                                     # noqa: PLC0415

        with pymupdf.open(str(pdf_path)) as doc:
            if not (1 <= page <= len(doc)):
                return {"ok": False, "page": page, "numbers": [],
                        "count": 0, "warnings": [f"页码越界：{page}（共 {len(doc)} 页）"]}
            pg = doc[page - 1]
            data = pg.get_text("dict")
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if not text:
                            continue
                        for m in _NUM_RE.finditer(text):
                            raw = m.group(1)
                            if raw.startswith(_SKIP_PREFIX) and "." not in raw and len(raw) == 4:
                                continue                       # 年份
                            try:
                                val = float(raw)
                            except ValueError:                 # pragma: no cover
                                continue
                            if val <= 0:
                                continue
                            bbox = span.get("bbox")
                            out.append({
                                "text": raw,
                                "value": val,
                                "bbox": [round(float(v), 2) for v in bbox] if bbox else None,
                                "src": "text",
                            })
                            if len(out) >= max_numbers:
                                warnings.append(f"候选数字达到上限 {max_numbers}，已截断")
                                return {"ok": True, "page": page, "numbers": out,
                                        "count": len(out), "warnings": warnings}
    except Exception as e:                                 # noqa: BLE001
        warnings.append(f"文本层抽数失败（{type(e).__name__}: {e}）；该页可能需纯读图")
        return {"ok": True, "page": page, "numbers": [], "count": 0, "warnings": warnings}
    return {"ok": True, "page": page, "numbers": out, "count": len(out), "warnings": warnings}


def cluster_rect(
    numbers: List[Dict[str, Any]],
    bounds: Tuple[float, float, float, float],
    *,
    pad: float = 0.12,
) -> Optional[Tuple[float, float, float, float]]:
    """
    从候选数字的坐标里找出「数字最密集的一块」，作为焦点区域。

    ★ 为什么可以这么找：land pattern 的尺寸标注总是成串挤在图纸上，
      而标题栏、页脚、参数表里的数字是散开的。把页面分成 12x16 格，
      取「3x3 邻域内数字最多」的那一格，再取窗口内所有数字的包围盒并外扩 12%，
      就得到标注密集区 —— 不需要理解图纸，只需要数数。

    :return: 焦点矩形；数字太少（<6 个）或全无坐标时返回 None
    """
    pts = [n for n in numbers if isinstance(n.get("bbox"), (list, tuple)) and len(n["bbox"]) == 4]
    if len(pts) < 6:
        return None
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None

    gw, gh = 12, 16
    cells: Dict[Tuple[int, int], int] = {}
    for n in pts:
        b = n["bbox"]
        cx = min(gw - 1, max(0, int(((b[0] + b[2]) / 2.0 - x0) / w * gw)))
        cy = min(gh - 1, max(0, int(((b[1] + b[3]) / 2.0 - y0) / h * gh)))
        cells[(cx, cy)] = cells.get((cx, cy), 0) + 1

    best: Optional[Tuple[int, int]] = None
    best_cnt = 0
    for (cx, cy) in cells:
        cnt = 0
        for r in range(max(0, cx - 1), min(gw, cx + 2)):
            for s in range(max(0, cy - 1), min(gh, cy + 2)):
                cnt += cells.get((r, s), 0)
        if cnt > best_cnt:
            best_cnt, best = cnt, (cx, cy)
    if best is None:
        return None

    bx, by = best
    lx = x0 + max(0, bx - 1) / gw * w
    rx = x0 + min(gw, bx + 2) / gw * w
    ty = y0 + max(0, by - 1) / gh * h
    byy = y0 + min(gh, by + 2) / gh * h

    inside = [n["bbox"] for n in pts
              if lx <= (n["bbox"][0] + n["bbox"][2]) / 2.0 <= rx
              and ty <= (n["bbox"][1] + n["bbox"][3]) / 2.0 <= byy]
    if not inside:
        return None
    fx0 = min(b[0] for b in inside)
    fy0 = min(b[1] for b in inside)
    fx1 = max(b[2] for b in inside)
    fy1 = max(b[3] for b in inside)
    mx = (fx1 - fx0) * pad
    my = (fy1 - fy0) * pad
    return (max(x0, fx0 - mx), max(y0, fy0 - my), min(x1, fx1 + mx), min(y1, fy1 + my))


def split_rect(rect: Tuple[float, float, float, float]) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """把矩形切成 2x2 四块，返回 [(后缀, 矩形)]。"""
    x0, y0, x1, y1 = rect
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return [
        ("ul", (x0, y0, cx, cy)),
        ("ur", (cx, y0, x1, cy)),
        ("ll", (x0, cy, cx, y1)),
        ("lr", (cx, cy, x1, y1)),
    ]


def render_focus_regions(
    pdf_path: Path,
    page: int,
    out_root: Path,
    *,
    dpi: int = DEFAULT_REGION_DPI,
    max_numbers: int = 400,
) -> Dict[str, Any]:
    """
    一次给出「该看哪几块、各自多清」：概览图 + 数字密集焦点区（必要时 2x2 切块）。

    ★ dpi 由像素配额反解，不是硬给：概览图长边压到 MAX_IMAGE_PX，
      焦点区强制 >= MIN_FOCUS_DPI；达不到就切块，切块仍达不到会写 warning
      提示人工复核（亚毫米尺寸）。

    :param out_root: 输出根目录（调用方传 config.RENDER_OUTPUT_ROOT）
    :return: {"ok", "page", "rect", "numbers": [...], "regions": [
                 {"id","page","rect","dpi","image_path","size"}],
              "measure": {...}, "warnings": [...]}
              regions[0] 恒为 overview；id 以 focus 开头的是焦点块。
    """
    warnings: List[str] = []
    x0, y0, x1, y1 = page_limits(pdf_path, page)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return {"ok": False, "page": page, "error": "页面尺寸异常", "warnings": warnings}
    # 去掉上 12%（标题栏）与下 10%（页脚 / 图号）
    rect = (x0, y0 + h * 0.12, x1, y1 - h * 0.10)

    nums = extract_numbers(pdf_path, page, max_numbers=max_numbers)
    warnings.extend(nums.get("warnings") or [])

    regions: List[Dict[str, Any]] = []

    def _add(rid: str, r: Tuple[float, float, float, float], want_dpi: int) -> None:
        res = pdf_service.render_region(pdf_path, page, r, out_root, dpi=want_dpi)
        if res.get("ok"):
            regions.append({
                "id": rid,
                "page": page,
                "rect": res.get("rect"),
                "dpi": res.get("dpi"),
                "image_path": res.get("image_path"),
                "size": res.get("size"),
            })
        else:
            warnings.append(f"{rid} 渲染失败：{res.get('error')}")

    # ---- 概览：长边不超 MAX_IMAGE_PX ----
    _add("overview", rect, min(dpi, dpi_for_pixels(rect, MAX_IMAGE_PX)))

    # ---- 焦点区：数字密集处 ----
    focus = cluster_rect(nums.get("numbers") or [], rect)
    if focus is None:
        warnings.append("候选数字不足以定位焦点区，只有概览图；读值需人工核对")
    else:
        max_dpi = min(int(dpi), 1200)
        need_dpi = max(MIN_FOCUS_DPI, dpi_for_pixels(focus, MAX_IMAGE_PX))
        pieces = [("focus", focus)]
        if need_dpi < MIN_FOCUS_DPI:
            pieces = [(f"focus_{sfx}", r) for sfx, r in split_rect(focus)]
            if dpi_for_pixels(pieces[0][1], MAX_IMAGE_PX) < MIN_FOCUS_DPI:
                warnings.append(
                    f"焦点区切 2x2 后仍达不到 {MIN_FOCUS_DPI} dpi，已取上限 "
                    f"{min(max_dpi, dpi_for_pixels(focus, MAX_IMAGE_PX))} dpi；"
                    f"亚毫米尺寸建议人工复核")
        for rid, frect in pieces[:MAX_TILES]:
            fdpi = min(max_dpi, max(MIN_FOCUS_DPI, dpi_for_pixels(frect, MAX_IMAGE_PX)))
            _add(rid, frect, fdpi)

    meas = pdf_service.measure_region(pdf_path, page, rect)
    if not meas.get("ok"):
        warnings.append(f"矢量量距失败（不影响读图）：{meas.get('error')}")
        meas = {"ok": False, "unit": "pt", "boxes": [], "n_in_rect": 0}

    return {
        "ok": True,
        "page": page,
        "rect": [round(v, 2) for v in rect],
        "numbers": nums.get("numbers") or [],
        "regions": regions,
        "measure": {
            "ok": meas.get("ok"),
            "unit": meas.get("unit", "pt"),
            "n_in_rect": meas.get("n_in_rect", 0),
            "boxes": (meas.get("boxes") or [])[:200],
            "note": meas.get("note", ""),
        },
        "warnings": warnings,
    }
