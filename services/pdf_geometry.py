"""几何闭环层：从 PDF 矢量图元还原焊盘阵列的尺寸、节距与本体（零模型成本）。

为什么要有这一层（实测依据见 docs/规格书读取方案.md §7）
--------------------------------
- 文本层候选只覆盖 26.9% 的真值（定到页）/ 42.3%（全页）。模型是在这 27% 的候选上
  靠看图补到 72.5% 的；22 项失分里 pad_x/pad_y/pitch 类的口径之争占了一半。
- 参考盲读（forge eval 的 92.5% 基线）能做到，是因为它用 ``get_drawings()`` 重建了
  焊盘矩形，再用图上印的数做多路自洽标定（13.589pt↔0.50、7.338pt↔0.27、
  149.486pt↔5.50 都指向 27.178 pt/mm）。
- 这一层把那套做法固化成代码：线段 → 矩形 → 同尺寸族 → 行/列 → 节距，再用文本层的
  数字反标定 pt/mm。零模型成本、可复现、不依赖大模型通道。

输出的是**候选**而不是结论：所有量都带 pt 值与来源，给 L1 判读与 L2 确认台使用。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

TOL = 0.35        # 线段贴合容差（pt）
MIN_PT = 1.5      # 小于这个尺寸的矩形不当焊盘
MAX_PT = 260.0    # 大于这个尺寸的矩形不当焊盘
MIN_FAMILY = 4    # 同尺寸族至少这么多成员
PAD_DEV = 0.35    # 族内尺寸允许的相对偏差


# ------------------------------------------------------------
# 图元 → 线段
# ------------------------------------------------------------

def segments(page: Any, tol: float = TOL) -> Tuple[List[Tuple[float, float, float]],
                                                   List[Tuple[float, float, float]]]:
    """把页面图元拆成横竖线段。返回 (H, V)，元素为 (起, 止, 常数轴)。"""
    h: List[Tuple[float, float, float]] = []
    v: List[Tuple[float, float, float]] = []
    for d in page.get_drawings():
        for it in d.get("items") or []:
            if it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            x1, y1 = float(p1.x), float(p1.y)
            x2, y2 = float(p2.x), float(p2.y)
            if abs(x1 - x2) <= tol and abs(y1 - y2) > 1.0:
                v.append((min(y1, y2), max(y1, y2), x1))
            elif abs(y1 - y2) <= tol and abs(x1 - x2) > 1.0:
                h.append((min(x1, x2), max(x1, x2), y1))
    return h, v


# ------------------------------------------------------------
# 线段 → 矩形
# ------------------------------------------------------------

def build_rects(h: Sequence[Tuple[float, float, float]],
                v: Sequence[Tuple[float, float, float]],
                tol: float = TOL,
                max_pt: float = MAX_PT) -> List[Dict[str, float]]:
    """把「两条等长竖线 + 上下两条等跨度横线」拼成矩形。

    焊盘在 PDF 里通常不是闭合路径（实测 type_c24 第 2 页 0 个可用的 pad 尺寸
    drawing），必须从线段重建。
    """
    # 竖线按 (y0, y1) 聚成组
    spans: List[List[float]] = []       # [y0, y1, ...xs]
    for y0, y1, x in v:
        if y1 - y0 > max_pt:
            continue
        for g in spans:
            if abs(g[0] - y0) <= tol and abs(g[1] - y1) <= tol:
                g.append(x)
                break
        else:
            spans.append([y0, y1, x])

    rects: List[Dict[str, float]] = []
    for g in spans:
        y0, y1 = g[0], g[1]
        height = y1 - y0
        if height < MIN_PT or height > max_pt:
            continue
        xs = sorted(g[2:])
        for a, b in zip(xs, xs[1:]):
            width = b - a
            if width < MIN_PT or width > max_pt:
                continue
            if not _has_h(h, y0, a, b, tol) or not _has_h(h, y1, a, b, tol):
                continue
            if any(abs(r["x0"] - a) < tol and abs(r["y0"] - y0) < tol for r in rects):
                continue
            rects.append({
                "x0": a, "y0": y0, "x1": b, "y1": y1,
                "w": width, "h": height,
                "cx": (a + b) / 2, "cy": (y0 + y1) / 2,
            })
    return rects


def _has_h(segs: Sequence[Tuple[float, float, float]], y: float,
           a: float, b: float, tol: float) -> bool:
    for x0, x1, yy in segs:
        if abs(yy - y) <= tol and x0 <= a + tol and x1 >= b - tol:
            return True
    return False


# ------------------------------------------------------------
# 矩形 → 同尺寸族 → 阵列
# ------------------------------------------------------------

def _cluster(values: Sequence[float], tol: float) -> List[List[float]]:
    groups: List[List[float]] = []
    for val in sorted(values):
        if groups and val - groups[-1][-1] <= tol:
            groups[-1].append(val)
        else:
            groups.append([val])
    return groups


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def pick_array(rects: Sequence[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """从所有矩形里挑出「焊盘阵列」那一族。

    判据：同一宽度成组（同一阵列里两排焊盘的**高度可能不同**，type_c24 就是
    0.90mm 与 0.80mm 两种，所以不能按 (宽,高) 分组）、成员 ≥ 4、行内节距规则、
    行内高度一致、行数不多。先用节距规则度筛，再按成员数择优 —— 只用「行内矩形
    最多」会把表格单元格误认成焊盘（实测 xtal/tf9/zif30 都落到过表格上）。
    """
    fams: Dict[float, List[Dict[str, float]]] = defaultdict(list)
    for r in rects:
        fams[round(r["w"], 1)].append(r)

    best: Optional[Dict[str, Any]] = None
    for w, members in fams.items():
        if len(members) < MIN_FAMILY:
            continue
        if w > 120 or w < 1.0:
            continue
        ws = [m["w"] for m in members]
        if (max(ws) - min(ws)) / max(w, 1e-6) > PAD_DEV:
            continue

        h_med = _median([m["h"] for m in members])
        rows = [sum(g) / len(g) for g in _cluster([m["cy"] for m in members],
                                                  max(2.5, h_med * 0.2))]
        if len(rows) > 6:
            continue
        row_members: List[List[Dict[str, float]]] = [[] for _ in rows]
        for m in members:
            idx = min(range(len(rows)), key=lambda i: abs(rows[i] - m["cy"]))
            row_members[idx].append(m)

        # 行内：x 节距规则 + 高度一致
        pitches: List[float] = []
        dev = 0.0
        for rm in row_members:
            if not rm:
                continue
            hs = [m["h"] for m in rm]
            if (max(hs) - min(hs)) > 1.0:
                dev = 1.0
                break
            xs = sorted(m["cx"] for m in rm)
            deltas = [b - a for a, b in zip(xs, xs[1:])]
            if not deltas:
                continue
            med = _median(deltas)
            if med <= 0:
                continue
            pitch_dev = max(abs(d - med) for d in deltas) / med
            pitches.append(med)
            dev = max(dev, pitch_dev)
        if dev > 0.08 or not pitches:
            continue
        # 各行节距必须彼此一致（错位排列允许，节距不允许变）
        if (max(pitches) - min(pitches)) / max(_median(pitches), 1e-6) > 0.05:
            continue
        # 焊盘必须比节距窄：表格单元格的宽/节距≈1.00，阵列焊盘通常 ≤0.7
        # （实测 xtal/tf9/zif30 不加这条都会落到表格格子上）
        if _median(ws) >= _median(pitches) * 0.9:
            continue

        span_x = max(m["x1"] for m in members) - min(m["x0"] for m in members)
        span_y = max(m["y1"] for m in members) - min(m["y0"] for m in members)
        if span_x < 12 and span_y < 12:
            continue

        score = (len(members), -dev)
        if best is None or score > best["_score"]:
            best = {
                "_score": score,
                "members": members,
                "rows": rows,
                "row_members": row_members,
                "pitch_x_pt": _median(pitches),
                "pitch_dev": dev,
                "pad_w_pt": _median(ws),
                "pad_h_rows_pt": [_median([m["h"] for m in rm])
                                  for rm in row_members if rm],
                "span_pt": [span_x, span_y],
            }
    return best


def array_summary(arr: Dict[str, Any]) -> Dict[str, Any]:
    """把选中的阵列压成可读结论：脚数、行列、双向节距、每排焊盘高、包围盒。"""
    rows = arr["rows"]
    row_counts = [len(r) for r in arr["row_members"] if r]
    pitch_y: Optional[float] = None
    if len(rows) >= 2:
        deltas = [b - a for a, b in zip(rows, rows[1:])]
        if deltas:
            pitch_y = _median(deltas)
    members = arr["members"]
    bbox = [
        min(m["x0"] for m in members), min(m["y0"] for m in members),
        max(m["x1"] for m in members), max(m["y1"] for m in members),
    ]
    row_h = sorted({round(v, 1) for v in arr["pad_h_rows_pt"]})
    return {
        "pin_count": len(members),
        "rows": len(rows),
        "cols": int(round(_median(row_counts))) if row_counts else 0,
        "row_counts": row_counts,
        "pitch_x_pt": round(arr["pitch_x_pt"], 3),
        "pitch_y_pt": round(pitch_y, 3) if pitch_y else None,
        "pad_w_pt": round(arr["pad_w_pt"], 3),
        "pad_h_pt": round(_median(arr["pad_h_rows_pt"]), 3),
        "pad_h_rows_pt": row_h,
        "pitch_dev": round(arr["pitch_dev"], 4),
        "bbox_pt": [round(x, 2) for x in bbox],
        "span_pt": [round(x, 2) for x in arr["span_pt"]],
    }


# ------------------------------------------------------------
# 闭合图元 → 焊盘（填充路径 / 四边形 / 圆）
# ------------------------------------------------------------

def _closed_loop(items: Sequence[Any], tol: float = 0.8) -> bool:
    """判断一条路径是否首尾闭合（只看首尾点，够用）。"""
    pts: List[Tuple[float, float]] = []
    for it in items:
        if it[0] == "l":
            pts.append((float(it[1].x), float(it[1].y)))
            pts.append((float(it[2].x), float(it[2].y)))
        elif it[0] == "c":
            pts.append((float(it[1].x), float(it[1].y)))
            pts.append((float(it[4].x), float(it[4].y)))
        elif it[0] == "qu":
            q = it[1]
            pts.extend([(float(p.x), float(p.y)) for p in (q.ul, q.ur, q.lr, q.ll)])
    if len(pts) < 3:
        return False
    return abs(pts[0][0] - pts[-1][0]) <= tol and abs(pts[0][1] - pts[-1][1]) <= tol


def closed_shapes(page: Any) -> List[Dict[str, Any]]:
    """收集闭合图元作为候选焊盘。

    线段重建只能认出「四条线画的矩形」；实测 hdmi19/tf9 的焊盘是**填充路径**
    （``type f/fs``），bga496 是**圆**，另有一类是四边形（``qu``）。这里直接按
    图元的 rect 取包围盒，再按外形判矩形/圆。
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for d in page.get_drawings():
        items = d.get("items") or []
        if not items:
            continue
        kinds = {it[0] for it in items}
        if kinds <= {"re"}:
            kind = "rect"
        elif kinds <= {"qu"}:
            kind = "quad"
        elif kinds <= {"l"} and d.get("type") in ("f", "fs") and _closed_loop(items):
            kind = "fill"
        elif kinds <= {"c", "l"} and _closed_loop(items):
            kind = "curve"
        else:
            continue
        r = d.get("rect")
        if r is None:
            continue
        w, h = float(r.width), float(r.height)
        if w < MIN_PT or h < MIN_PT or w > 120 or h > 120:
            continue
        key = (round(float(r.x0), 1), round(float(r.y0), 1),
               round(w, 1), round(h, 1))
        if key in seen:
            continue
        seen.add(key)
        circle = (kind == "curve" and kinds <= {"c"}
                  and abs(w - h) <= max(0.6, 0.15 * max(w, h)))
        out.append({
            "x0": float(r.x0), "y0": float(r.y0),
            "x1": float(r.x1), "y1": float(r.y1),
            "w": w, "h": h,
            "cx": float(r.x0) + w / 2, "cy": float(r.y0) + h / 2,
            "kind": "circle" if circle else "shape",
            "src": kind,
        })
    return out


def pick_circle_array(shapes: Sequence[Dict[str, Any]],
                      min_members: int = 6) -> Optional[Dict[str, Any]]:
    """同直径圆排成的规则网格（BGA / 圆形焊盘阵列）。"""
    fams: Dict[float, List[Dict[str, Any]]] = defaultdict(list)
    for s in shapes:
        if s["kind"] == "circle":
            fams[round((s["w"] + s["h"]) / 2, 1)].append(s)

    best: Optional[Dict[str, Any]] = None
    for dia, members in fams.items():
        if len(members) < min_members or dia > 60 or dia < MIN_PT:
            continue
        gap = max(2.0, dia * 0.5)
        col_x = [_median(g) for g in _cluster([m["cx"] for m in members], gap)]
        row_y = [_median(g) for g in _cluster([m["cy"] for m in members], gap)]
        if len(col_x) < 2 or len(row_y) < 2:
            continue
        # 网格必须基本填满：真阵列 496 个球不会只有零星几行
        if len(col_x) * len(row_y) > 2.5 * len(members):
            continue
        dx = [b - a for a, b in zip(col_x, col_x[1:])]
        dy = [b - a for a, b in zip(row_y, row_y[1:])]
        if not dx or not dy:
            continue
        px, py = _median(dx), _median(dy)
        if px <= dia * 1.05 or py <= dia * 1.05:
            continue
        dev = max(max(abs(d - px) for d in dx) / px,
                  max(abs(d - py) for d in dy) / py)
        if dev > 0.08:
            continue
        row_members: List[List[Dict[str, Any]]] = [[] for _ in row_y]
        for m in members:
            idx = min(range(len(row_y)), key=lambda i: abs(row_y[i] - m["cy"]))
            row_members[idx].append(m)
        span_x = max(m["x1"] for m in members) - min(m["x0"] for m in members)
        span_y = max(m["y1"] for m in members) - min(m["y0"] for m in members)
        score = (len(members), -dev)
        if best is None or score > best["_score"]:
            best = {
                "_score": score,
                "members": members,
                "rows": row_y,
                "row_members": row_members,
                "pitch_x_pt": px,
                "pitch_dev": dev,
                "pad_w_pt": dia,
                "pad_h_rows_pt": [dia],
                "span_pt": [span_x, span_y],
                "shape": "circle",
            }
    return best


def find_body(shapes: Sequence[Dict[str, Any]],
              big_rects: Sequence[Dict[str, float]],
              pad_bbox: Sequence[float],
              tol: float = 1.0) -> Optional[Dict[str, float]]:
    """找本体外廓：**能把焊盘阵列整个包住的最小闭合矩形**。

    本体在图上通常由四条长线画成（type_c24 的 243×249pt），也可能是一条闭合
    路径/圆角框。要求：包住焊盘包围盒、外扩不超过 4 倍（否则是页框/表格）。
    """
    px0, py0, px1, py1 = [float(v) for v in pad_bbox]
    pw, ph = px1 - px0, py1 - py0
    best: Optional[Dict[str, float]] = None
    for r in list(big_rects) + list(shapes):
        x0, y0 = float(r["x0"]), float(r["y0"])
        x1, y1 = float(r["x1"]), float(r["y1"])
        w, h = x1 - x0, y1 - y0
        if w <= pw + 0.5 or h <= ph + 0.5:
            continue
        if w > pw * 4 or h > ph * 4:
            continue
        if not (x0 <= px0 + tol and y0 <= py0 + tol
                and x1 >= px1 - tol and y1 >= py1 - tol):
            continue
        if best is None or w * h < best["w"] * best["h"]:
            best = {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "w": w, "h": h}
    return best


# ------------------------------------------------------------
# 文本层数字 → pt/mm 标定
# ------------------------------------------------------------

def _nearest(vals: Sequence[float], target: float, rel: float) -> Optional[float]:
    best: Optional[float] = None
    for v in vals:
        if abs(v - target) <= rel * max(abs(target), 1e-6):
            if best is None or abs(v - target) < abs(best - target):
                best = v
    return best


def calibrate(distances: Dict[str, float], numbers: Sequence[float]) -> Optional[Dict[str, Any]]:
    """用图上印的数字反标定 pt/mm。

    做法：假设所有几何距离 d 都满足 ``mm = d / s``，枚举 (d, 图上某个数) 组合出 s，
    再检查同一个 s 能否让**至少两个不同来源**的距离都落回图上印的数字（相对 2%）。
    三个来源同时命中即视为可信（参考盲读当时就是三路自洽）。
    """
    # numbers 既接受 [0.50, 0.27, ...]，也接受 [{"value": 0.50}, ...]
    # （extract_page_numbers 的返回就是后者，直接传进来不该报错）
    pool_vals = []
    for v in numbers:
        if isinstance(v, dict):
            v = v.get("value")
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.05 <= f <= 60:
            pool_vals.append(round(f, 3))
    pool = sorted(set(pool_vals))
    if not pool or not distances:
        return None

    items = [(k, d) for k, d in distances.items() if d and d > 2.0]
    if not items:
        return None

    best: Optional[Dict[str, Any]] = None
    for tag, d in items:
        for v in pool:
            s = d / v
            if not (1.2 <= s <= 60):
                continue
            hits: List[Tuple[str, float]] = []
            for tag2, d2 in items:
                mapped = d2 / s
                hit = _nearest(pool, mapped, 0.02)
                if hit is not None:
                    hits.append((tag2, round(hit, 3)))
            tags = {t for t, _ in hits}
            if len(tags) < 2:
                continue
            score = (len(tags), len(hits), -abs(s - 27.0))
            if best is None or score > best["_score"]:
                best = {
                    "_score": score,
                    "pt_per_mm": s,
                    "evidence": [{"from": t, "pt": round(dict(items)[t], 3),
                                  "mm": v2} for t, v2 in hits],
                    "anchor": {"tag": tag, "mm": v},
                }
    if best is None:
        return None
    best.pop("_score", None)
    best["pt_per_mm"] = round(best["pt_per_mm"], 3)
    return best


# ------------------------------------------------------------
# 对外入口
# ------------------------------------------------------------

def analyze_page(pdf_path: Path | str,
                 page_no: int,
                 numbers: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """分析一页，返回焊盘阵列的几何结论与候选尺寸。

    :param pdf_path: PDF 路径
    :param page_no: 1-based 页码
    :param numbers: 该页文本层抽到的数字（用于标定 pt/mm）；可为空
    :return: ``{ok, page, scale, pads, candidates, ratios, warnings}``，不抛异常
    """
    import pymupdf  # noqa: PLC0415

    out: Dict[str, Any] = {
        "ok": False, "page": page_no, "scale": None, "pads": None,
        "candidates": [], "ratios": {}, "warnings": [],
    }
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"PDF 打不开：{type(e).__name__}: {e}")
        return out
    try:
        if not (1 <= page_no <= doc.page_count):
            out["warnings"].append(f"页码越界：{page_no}/{doc.page_count}")
            return out
        page = doc[page_no - 1]
        h, v = segments(page)
        shapes = closed_shapes(page)
        out["segments"] = {"h": len(h), "v": len(v)}
        out["shape_count"] = len(shapes)
        if len(v) < 8 and not shapes:
            out["warnings"].append(
                f"竖线只有 {len(v)} 条、闭合图元 {len(shapes)} 个，该页可能是位图扫描（无矢量可重建）"
            )
            return out
        rects = build_rects(h, v)
        out["rect_count"] = len(rects)
        arr = pick_array(rects)
        arr_c = pick_circle_array(shapes)
        if arr_c and (arr is None or len(arr_c["members"]) > len(arr["members"])):
            arr = arr_c
        if arr is None:
            out["warnings"].append(
                f"未找到规则的同尺寸族（矩形 {len(rects)} 个、闭合图元 {len(shapes)} 个）"
            )
            return out
        pads = array_summary(arr)
        if arr.get("shape") == "circle":
            pads["pad_shape"] = "circle"
        out["pads"] = pads

        big = build_rects(h, v, max_pt=700.0)
        body = find_body(shapes, big, pads["bbox_pt"])
        out["body"] = body

        distances = {
            "pitch_x": pads["pitch_x_pt"],
            "pad_w": pads["pad_w_pt"],
            "pad_h": pads["pad_h_pt"],
        }
        if pads["pitch_y_pt"]:
            distances["pitch_y"] = pads["pitch_y_pt"]
        scale = calibrate(distances, numbers or [])
        out["scale"] = scale

        # 无量纲比值：即使标定失败，也能让模型用图上印的 pitch 反推 pad/body
        out["ratios"] = {
            "pad_h_over_pitch_x": round(pads["pad_h_pt"] / pads["pitch_x_pt"], 4),
            "pad_w_over_pitch_x": round(pads["pad_w_pt"] / pads["pitch_x_pt"], 4),
        }
        if pads["pitch_y_pt"]:
            out["ratios"]["pitch_y_over_pitch_x"] = round(
                pads["pitch_y_pt"] / pads["pitch_x_pt"], 4)

        if scale:
            s = scale["pt_per_mm"]
            cands = [
                ("pitch_x", pads["pitch_x_pt"], "geom.pitch_x", ""),
                ("pad_x", pads["pad_w_pt"], "geom.pad_x", ""),
            ]
            for h_pt in pads["pad_h_rows_pt"]:
                cands.append(("pad_y", h_pt, "geom.pad_y",
                              f"行高 {h_pt:.2f}pt"))
            if pads["pitch_y_pt"]:
                cands.append(("pitch_y", pads["pitch_y_pt"], "geom.pitch_y", ""))
            if body:
                nb = f"本体外廓 {body['w']:.2f}×{body['h']:.2f}pt"
                cands.append(("body_x", body["w"], "geom.body_x", nb))
                cands.append(("body_y", body["h"], "geom.body_y", nb))
            out["candidates"] = [
                {"field": f, "value": round(pt / s, 4), "pt": round(pt, 3),
                 "unit": "mm", "method": m, "note": note,
                 "bbox_pt": pads["bbox_pt"]}
                for f, pt, m, note in cands
            ]
        else:
            out["warnings"].append(
                "几何已重建但 pt/mm 标定失败（图上数字对不上），只给 pt 与比值"
            )
        out["ok"] = True
        return out
    finally:
        doc.close()


def _main(argv: List[str]) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    if len(argv) < 2:
        print("用法：python -m services.pdf_geometry <pdf> [page]")
        return 2
    pdf = Path(argv[1])
    page = int(argv[2]) if len(argv) > 2 else 1
    result = analyze_page(pdf, page)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
