# -*- coding: utf-8 -*-
"""
rule_checker.py —— 基于 Excel 规范的数值规则检查 (v9)

v9 改动（本次）：
  - 2.1 焊盘尺寸一致性：value 改为 dict，含 padstack 命名值 vs 实测值
  - 2.3 钢网等大：value 改为 dict，含钢网尺寸 vs 焊盘尺寸
  - 6.3 改名"Assembly 元素数"，明确它由 len(asm) 判定，
        1 脚标识的语义存在性交给 AI 检查
  - 6.5 改名"Silkscreen 复杂 path 数"，同上原因
  **判定逻辑、阈值、status 计算全部未变**，只附加/改名。

v8 已含（保留）：
  - `_item()` 的 expected / source 字段

v7 修复（保留）：
  - 5.2 Place_Bound 外扩量：reference 改用 Assembly 器件本体外框

v6 已含（保留）：
  - 元件类型识别 _classify_component
  - 3.3 焊盘不重叠（bbox 真实几何重叠）
  - 5.2 / 3.4 按元件类型应用不同规则
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


DEFAULT_RULES: Dict[str, Any] = {
    "naming": {
        "prefix": "NB_",
        "prefix_case_sensitive": True,
        "no_dot": True,
    },
    "pad": {
        "size_tolerance_mm": 0.05,
        "min_spacing_mm": 0.20,
        "soldermask_expand_mm": 0.05,
        "pastemask_equal_pad": True,
    },
    "origin": {
        "center_tolerance_mm": 0.05,        # IC / chip 类
        "connector_tolerance_mm": 20.0,      # 连接器（允许大幅偏移）
    },
    "place_bound": {
        "ic_expand_mm": 0.35,
        "chip_expand_mm": 0.15,
        "connector_expand_mm": 0.85,
        "expand_tolerance_mm": 0.05,
        # 宽松判定用范围（找不到类型时兜底）
        "min_expand_mm": 0.05,
        "max_expand_mm": 3.0,
    },
    "silkscreen": {
        "line_width_min_mm": 0.10,
        "to_pad_clearance_mm": 0.20,
    },
    "assembly": {
        "size_tolerance_mm": 0.05,
    },
}


# ============================================================
# 元件类型识别
# ============================================================
CONNECTOR_KEYWORDS = [
    "con", "conn", "connector", "hdmi", "usb", "typec", "type-c",
    "fpc", "btb", "wtb", "dsub", "rj45", "sata", "sd", "tf",
    "sim", "earphone", "jack", "socket", "header", "plug", "receptacle",
]

IC_KEYWORDS = [
    "qfn", "qfp", "lqfp", "tqfp", "sop", "soic", "ssop", "tssop",
    "bga", "csp", "wlcsp", "dip", "sot", "to-", "ic", "chip",
    "xtal", "osc", "oscillator", "crystal", "resonator", "inductor",
    "mos", "mosfet", "bjt", "diode", "regulator", "ldo", "opamp",
]

CHIP_KEYWORDS = [
    "r", "c", "l", "resistor", "capacitor", "0402", "0201", "0603",
    "0805", "1206", "sod", "chip_r", "chip_c",
]


def _classify_component(name: str) -> str:
    """
    从 symbol_name 判断元件类型。

    返回: "connector" | "ic" | "chip" | "unknown"
    """
    low = name.lower()
    # 优先匹配连接器（关键词更专属）
    for kw in CONNECTOR_KEYWORDS:
        if kw in low:
            return "connector"
    # 再匹配 IC
    for kw in IC_KEYWORDS:
        if kw in low:
            return "ic"
    # 最后匹配 chip
    for kw in CHIP_KEYWORDS:
        # 短关键词做单词边界检查
        if len(kw) <= 2:
            if re.search(rf"(^|_){re.escape(kw)}($|_)", low):
                return "chip"
        else:
            if kw in low:
                return "chip"
    return "unknown"


# ============================================================
# 几何工具
# ============================================================
def _bbox_min_distance(bbox1, bbox2) -> float:
    if not bbox1 or not bbox2:
        return float("inf")
    if bbox1[1][0] < bbox2[0][0]:
        dx = bbox2[0][0] - bbox1[1][0]
    elif bbox2[1][0] < bbox1[0][0]:
        dx = bbox1[0][0] - bbox2[1][0]
    else:
        dx = 0.0
    if bbox1[1][1] < bbox2[0][1]:
        dy = bbox2[0][1] - bbox1[1][1]
    elif bbox2[1][1] < bbox1[0][1]:
        dy = bbox1[0][1] - bbox2[1][1]
    else:
        dy = 0.0
    return math.hypot(dx, dy)


def _bbox_overlap_area(bbox1, bbox2) -> float:
    if not bbox1 or not bbox2:
        return 0.0
    x_overlap = min(bbox1[1][0], bbox2[1][0]) - max(bbox1[0][0], bbox2[0][0])
    y_overlap = min(bbox1[1][1], bbox2[1][1]) - max(bbox1[0][1], bbox2[0][1])
    if x_overlap > 0 and y_overlap > 0:
        return x_overlap * y_overlap
    return 0.0


def _bbox_center(bbox) -> Optional[List[float]]:
    if not bbox:
        return None
    return [(bbox[0][0] + bbox[1][0]) / 2, (bbox[0][1] + bbox[1][1]) / 2]


def _bbox_size(bbox) -> Optional[List[float]]:
    if not bbox:
        return None
    return [abs(bbox[1][0] - bbox[0][0]), abs(bbox[1][1] - bbox[0][1])]


def _bbox_area(bbox) -> float:
    if not bbox:
        return 0.0
    return abs(bbox[1][0] - bbox[0][0]) * abs(bbox[1][1] - bbox[0][1])


def _round(v, n=4):
    if v is None:
        return None
    return round(v, n)


def _fmt_dim(w: float, h: float) -> str:
    """把 width/height 转成 "1.4×1.2" 这样的字符串（用于报告显示）"""
    return f"{_round(w, 3)}×{_round(h, 3)}"


def _merge_bboxes(bboxes: List[List[List[float]]]) -> Optional[List[List[float]]]:
    valid = [b for b in bboxes if b and len(b) == 2]
    if not valid:
        return None
    min_x = min(b[0][0] for b in valid)
    min_y = min(b[0][1] for b in valid)
    max_x = max(b[1][0] for b in valid)
    max_y = max(b[1][1] for b in valid)
    return [[min_x, min_y], [max_x, max_y]]


def _layer_combined_bbox(elements: List[Dict]) -> Optional[List[List[float]]]:
    bboxes = [e.get("bbox") for e in elements if e.get("bbox")]
    return _merge_bboxes(bboxes)


def _pins_combined_bbox(pins: List[Dict]) -> Optional[List[List[float]]]:
    bboxes = [p.get("bbox") for p in pins if p.get("bbox")]
    return _merge_bboxes(bboxes)


def _point_in_bbox(x: float, y: float, bbox, margin: float = 0.0) -> bool:
    if not bbox:
        return False
    return (
        bbox[0][0] - margin <= x <= bbox[1][0] + margin and
        bbox[0][1] - margin <= y <= bbox[1][1] + margin
    )


def _segments_cross_bbox(element: Dict, bbox, margin: float = 0.0) -> bool:
    if not bbox:
        return False
    for seg in element.get("segments", []):
        if len(seg) < 2:
            continue
        if _point_in_bbox(seg[0][0], seg[0][1], bbox, margin):
            return True
        if _point_in_bbox(seg[1][0], seg[1][1], bbox, margin):
            return True
    return False


def _bbox_contains(outer, inner) -> bool:
    if not outer or not inner:
        return False
    return (
        outer[0][0] <= inner[0][0] and
        outer[0][1] <= inner[0][1] and
        outer[1][0] >= inner[1][0] and
        outer[1][1] >= inner[1][1]
    )


def _find_largest_bbox_element(elements: List[Dict]) -> Optional[Dict]:
    """找 bbox 面积最大的元素"""
    if not elements:
        return None
    best = None
    best_area = 0.0
    for el in elements:
        area = _bbox_area(el.get("bbox"))
        if area > best_area:
            best_area = area
            best = el
    return best


def _find_device_outline(assembly_elements: List[Dict]) -> Optional[Dict]:
    """
    从 Assembly 层元素里找"器件本体外框"。

    判定标准：
      1. 优先找 n_segs == 4 的元素（闭合矩形）
      2. 在符合条件的元素里取 bbox 面积最大的
      3. 如果没有任何 n_segs == 4 的元素，退化为找面积最大的

    为什么这么判：
      - 器件外框通常是用矩形（4 段 path）画的
      - Assembly 层还有 1 脚标识、极性字符等小元素，面积远小于外框
      - 用"n_segs == 4 + 面积最大"双条件比"绝对面积阈值"更稳
        （SOD-323 之类的小器件面积小，用阈值会误判）
    """
    if not assembly_elements:
        return None

    # 优先找 4 段矩形
    rects = [e for e in assembly_elements if e.get("n_segs") == 4]
    if rects:
        return max(rects, key=lambda e: _bbox_area(e.get("bbox")))

    # 退化为面积最大
    return _find_largest_bbox_element(assembly_elements)


# ============================================================
# 结果构造
# ============================================================
def _item(
    id_: str,
    name: str,
    status: str,
    value=None,
    rule=None,
    detail="",
    expected=None,
    source=None,
) -> Dict:
    """
    构造一个检查项。

    :param expected: 结构化"要求"值（字符串或 None）。
                     报告渲染层直接搬运到"要求"列，AI 不再从 rule 自由文本猜。
    :param source:   判据来源，取值 "default" | "datasheet" | "self" | None。
                     NA 项或纯测量项填 None。
    """
    return {
        "id": id_,
        "name": name,
        "status": status,
        "value": value,
        "rule": rule,
        "detail": detail,
        "expected": expected,
        "source": source,
    }


# ============================================================
# 一、命名
# ============================================================
def check_naming(data: Dict, rules: Dict) -> List[Dict]:
    items = []
    name = data.get("symbol_name", "")
    cfg = rules.get("naming", {})

    prefix = cfg.get("prefix", "NB_")
    case_sensitive = cfg.get("prefix_case_sensitive", True)

    if case_sensitive:
        has_prefix = name.startswith(prefix)
    else:
        has_prefix = name.upper().startswith(prefix.upper())

    if has_prefix:
        detail = ""
    else:
        if name.upper().startswith(prefix.upper()):
            actual_prefix = name[:len(prefix)]
            detail = (
                f"前缀大小写不符：实际 '{actual_prefix}'，"
                f"规范要求 '{prefix}'（笔电专用标识）"
            )
        else:
            detail = (
                f"当前名字 '{name}' 不以 '{prefix}' 开头"
                f"（规范要求 {prefix} 前缀，笔电专用标识）"
            )

    items.append(_item(
        "1.1", "封装名前缀",
        "PASS" if has_prefix else "WARN",
        value=name,
        rule=f"以 {prefix} 开头",
        detail=detail,
        expected=f"前缀 = {prefix}",
        source="default",
    ))

    if cfg.get("no_dot", True):
        has_dot = "." in name
        items.append(_item(
            "1.2", "小数点用 d 代替",
            "FAIL" if has_dot else "PASS",
            value=name,
            rule="名字中不能出现 '.'",
            detail="发现小数点，规范要求用字母 d 代替" if has_dot else "",
            expected="不含 '.'",
            source="default",
        ))

    nums = re.findall(r"\d+", name)
    items.append(_item(
        "1.3", "包含引脚数（启发式）",
        "PASS" if nums else "WARN",
        value=nums[:3],
        rule="名字中应含数字（引脚数/尺寸）",
        detail="" if nums else "未从名字中提取到任何数字",
        expected="含数字",
        source="default",
    ))

    return items


# ============================================================
# 二、焊盘
# ============================================================
def _get_etch_pad(pin: Dict) -> Optional[Dict]:
    for pad in pin.get("pads", []):
        if pad.get("layer") == "ETCH/TOP" and pad.get("size"):
            w, h = pad["size"]
            if w > 0 and h > 0:
                return pad
    return None


def _get_pad_by_layer(pin: Dict, layer: str) -> Optional[Dict]:
    for pad in pin.get("pads", []):
        if pad.get("layer") == layer:
            return pad
    return None


def check_pad_size(data: Dict, theoretical: Optional[Dict], rules: Dict) -> List[Dict]:
    items = []
    cfg = rules.get("pad", {})
    tol = cfg.get("size_tolerance_mm", 0.05)

    pins = data.get("pins", [])
    if not pins:
        return [_item(
            "2.1", "焊盘尺寸", "NA",
            detail="无 pin 数据",
            expected=None, source=None,
        )]

    # ---------- 2.1 焊盘尺寸与命名一致性 ----------
    # 注意：extracta 数据源给的是 padstack 原始定义（未旋转），
    #       SkillBridge 给的是 pin 实例旋转后的实际尺寸。
    #       pin 旋转 90°/270° 时，宽高顺序会互换，两者都算正确。
    #       所以判定时允许宽高互换。
    all_match = True
    mismatch_detail = []
    sample_named = None
    sample_actual = None
    sample_pin = None

    for pin in pins:
        etch = _get_etch_pad(pin)
        if etch is None:
            continue
        pad_name = pin.get("name", "")
        m = re.match(r"r(\d+)d(\d+)x(\d+)d(\d+)", pad_name.lower())
        if not m:
            continue
        try:
            w_named = float(f"{m.group(1)}.{m.group(2)}")
            h_named = float(f"{m.group(3)}.{m.group(4)}")
        except Exception:
            continue
        w_actual, h_actual = etch["size"]

        if sample_named is None:
            sample_named = _fmt_dim(w_named, h_named)
            sample_actual = _fmt_dim(w_actual, h_actual)
            sample_pin = pin.get("number")

        # 允许宽高互换（应对 pin 旋转 90°/270° 的场景）
        match_normal = (
            abs(w_actual - w_named) <= tol and
            abs(h_actual - h_named) <= tol
        )
        match_swapped = (
            abs(w_actual - h_named) <= tol and
            abs(h_actual - w_named) <= tol
        )
        if not (match_normal or match_swapped):
            all_match = False
            mismatch_detail.append(
                f"pin{pin['number']}: 命名 {w_named}×{h_named}, 实际 {_round(w_actual)}×{_round(h_actual)}"
            )

    v21 = {
        "sample_pin": sample_pin,
        "padstack": sample_named,
        "actual": sample_actual,
    } if sample_named is not None else None

    items.append(_item(
        "2.1", "焊盘尺寸与命名一致性",
        "PASS" if all_match else "FAIL",
        value=v21 if v21 else f"容差 ±{tol}mm",
        rule="焊盘实际尺寸必须与 padstack 命名匹配（允许宽高互换）",
        detail=" | ".join(mismatch_detail[:3]),
        expected=f"= padstack 命名值 ±{tol}",
        source="self",
    ))

    # ---------- 2.2 阻焊开窗外扩 ----------
    sm_expand_rule = cfg.get("soldermask_expand_mm", 0.05)
    sm_results = []
    for pin in pins:
        etch = _get_etch_pad(pin)
        sm = _get_pad_by_layer(pin, "PIN/SOLDERMASK_TOP")
        if not etch or not sm or not sm.get("size"):
            continue
        ew, eh = etch["size"]
        sw, sh = sm["size"]
        if ew == 0 or eh == 0:
            continue
        sm_results.append(((sw - ew) / 2, (sh - eh) / 2))

    if sm_results:
        ok = all(
            abs(ex - sm_expand_rule) <= 0.02 and abs(ey - sm_expand_rule) <= 0.02
            for ex, ey in sm_results
        )
        sample_ex, sample_ey = sm_results[0]
        items.append(_item(
            "2.2", "阻焊开窗外扩",
            "PASS" if ok else "WARN",
            value={"单边x": _round(sample_ex), "单边y": _round(sample_ey)},
            rule=f"单边外扩 {sm_expand_rule}mm（±0.02）",
            detail="",
            expected=f"单边 {sm_expand_rule} ± 0.02",
            source="default",
        ))
    else:
        items.append(_item(
            "2.2", "阻焊开窗外扩", "NA",
            detail="无 SOLDERMASK_TOP 数据",
            expected=None, source=None,
        ))

    # ---------- 2.3 钢网与焊盘等大 ----------
    if cfg.get("pastemask_equal_pad", True):
        pt_ok = True
        pt_detail = []
        sample_pad_dim = None
        sample_paste_dim = None

        for pin in pins:
            etch = _get_etch_pad(pin)
            pt = _get_pad_by_layer(pin, "PIN/PASTEMASK_TOP")
            if not etch or not pt or not pt.get("size"):
                continue
            ew, eh = etch["size"]
            pw, ph = pt["size"]
            if sample_pad_dim is None:
                sample_pad_dim = _fmt_dim(ew, eh)
                sample_paste_dim = _fmt_dim(pw, ph)
            if abs(pw - ew) > 0.02 or abs(ph - eh) > 0.02:
                pt_ok = False
                pt_detail.append(
                    f"pin{pin['number']}: 焊盘 {_round(ew)}×{_round(eh)}, 钢网 {_round(pw)}×{_round(ph)}"
                )

        v23 = {
            "pad": sample_pad_dim,
            "paste": sample_paste_dim,
        } if sample_pad_dim is not None else None

        items.append(_item(
            "2.3", "钢网与焊盘等大",
            "PASS" if pt_ok else "WARN",
            value=v23 if v23 else ("等大" if pt_ok else "不等大"),
            rule="Chip 元件钢网开窗与焊盘等大",
            detail=" | ".join(pt_detail[:3]),
            expected="钢网 = 焊盘",
            source="self",
        ))
    else:
        items.append(_item(
            "2.3", "钢网与焊盘等大", "NA",
            detail="规则关闭",
            expected=None, source=None,
        ))

    return items
    

# ============================================================
# 三、间距与原点
# ============================================================
def check_pitch(data: Dict, theoretical: Optional[Dict], rules: Dict) -> List[Dict]:
    items = []
    pins = data.get("pins", [])
    if len(pins) < 2:
        return [_item(
            "3.1", "Pin pitch", "NA",
            detail="pin 数不足",
            expected=None, source=None,
        )]

    xs = sorted(set(round(p["xy"][0], 4) for p in pins if p.get("xy")))
    ys = sorted(set(round(p["xy"][1], 4) for p in pins if p.get("xy")))

    def min_gap(vals):
        if len(vals) < 2:
            return None
        return min(round(vals[i + 1] - vals[i], 4) for i in range(len(vals) - 1))

    pitch_x = min_gap(xs)
    pitch_y = min_gap(ys)

    # 3.1 纯测量项：仅报告 pitch 值，无判据
    items.append(_item(
        "3.1", "Pin pitch",
        "PASS" if pitch_x or pitch_y else "NA",
        value={"pitch_x": pitch_x, "pitch_y": pitch_y},
        rule="相邻 pin 中心间距",
        detail="",
        expected=None,
        source=None,
    ))

    if theoretical:
        theo = theoretical.get("theoretical_land_params") or theoretical
        theo_sx = theo.get("spacing_x")
        theo_sy = theo.get("spacing_y")
        if theo_sx is not None and pitch_x is not None:
            ok_x = abs(pitch_x - theo_sx) <= 0.05
            items.append(_item(
                "3.2x", "pitch 与 datasheet 一致 (x)",
                "PASS" if ok_x else "FAIL",
                value={"extracted": pitch_x, "datasheet": theo_sx},
                rule="差值 <= 0.05mm",
                detail="" if ok_x else f"x 方向差值 {_round(abs(pitch_x - theo_sx))}mm",
                expected=f"= {theo_sx} ± 0.05",
                source="datasheet",
            ))
        if theo_sy is not None and pitch_y is not None:
            ok_y = abs(pitch_y - theo_sy) <= 0.05
            items.append(_item(
                "3.2y", "pitch 与 datasheet 一致 (y)",
                "PASS" if ok_y else "FAIL",
                value={"extracted": pitch_y, "datasheet": theo_sy},
                rule="差值 <= 0.05mm",
                detail="" if ok_y else f"y 方向差值 {_round(abs(pitch_y - theo_sy))}mm",
                expected=f"= {theo_sy} ± 0.05",
                source="datasheet",
            ))

    # 3.3 焊盘不重叠：真实几何重叠检测
    overlap_found = False
    overlap_detail = ""
    min_clearance = float("inf")
    min_pair = None

    for i in range(len(pins)):
        for j in range(i + 1, len(pins)):
            b1 = pins[i].get("bbox")
            b2 = pins[j].get("bbox")
            if not b1 or not b2:
                continue
            area = _bbox_overlap_area(b1, b2)
            if area > 0:
                overlap_found = True
                overlap_detail = (
                    f"pin {pins[i]['number']} 与 pin {pins[j]['number']} 焊盘 bbox 真实重叠 "
                    f"{_round(area)}mm²"
                )
                break
            d = _bbox_min_distance(b1, b2)
            if d < min_clearance:
                min_clearance = d
                min_pair = (pins[i]["number"], pins[j]["number"])
        if overlap_found:
            break

    if overlap_found:
        items.append(_item(
            "3.3", "焊盘不重叠",
            "FAIL",
            value="重叠",
            rule="任意两个焊盘 bbox 不能几何重叠",
            detail=overlap_detail,
            expected="间距 > 0",
            source="self",
        ))
    else:
        detail = ""
        if min_clearance != float("inf"):
            detail = (
                f"最小欧氏距离 {_round(min_clearance)}mm"
                f"（pin {min_pair[0]} 与 pin {min_pair[1]}，几何上不重叠）"
            )
        items.append(_item(
            "3.3", "焊盘不重叠",
            "PASS",
            value=_round(min_clearance) if min_clearance != float("inf") else None,
            rule="任意两个焊盘 bbox 不能几何重叠",
            detail=detail,
            expected="间距 > 0",
            source="self",
        ))

    # 3.4 原点居中（按元件类型区分容差）
    comp_type = _classify_component(data.get("symbol_name", ""))
    origin_cfg = rules.get("origin", {})
    if comp_type == "connector":
        origin_tol = origin_cfg.get("connector_tolerance_mm", 20.0)
    else:
        origin_tol = origin_cfg.get("center_tolerance_mm", 0.05)

    center = None
    source_center = None

    pb = data.get("layers", {}).get("place_bound_top") or []
    if pb and pb[0].get("bbox"):
        center = pb[0]["center"]
        source_center = "place_bound_top"

    if center is None:
        pins_bbox = _pins_combined_bbox(pins)
        if pins_bbox:
            center = _bbox_center(pins_bbox)
            source_center = "pins_combined"

    if center is not None:
        offset = math.hypot(center[0], center[1])
        ok = offset <= origin_tol
        # 连接器不判 FAIL，只提示
        if comp_type == "connector" and not ok:
            status = "WARN"
        else:
            status = "PASS" if ok else "FAIL"
        items.append(_item(
            "3.4", "原点在封装中心",
            status,
            value={
                "center": [_round(center[0]), _round(center[1])],
                "source": source_center,
                "component_type": comp_type,
            },
            rule=f"{comp_type} 类容差 <= {origin_tol}mm",
            detail="" if ok else (
                f"中心偏移 {_round(offset)}mm"
                + ("（连接器允许以 pin 1 或结构基准为原点）" if comp_type == "connector" else "")
            ),
            expected=f"≤ {origin_tol}",
            source="default",
        ))
    else:
        items.append(_item(
            "3.4", "原点在封装中心", "NA",
            detail="无数据",
            expected=None, source=None,
        ))

    return items


# ============================================================
# 五、Place_Bound
# ============================================================
def check_place_bound(data: Dict, rules: Dict) -> List[Dict]:
    items = []
    cfg = rules.get("place_bound", {})
    layers = data.get("layers", {})
    pins = data.get("pins", [])
    asm = layers.get("assembly_top") or []
    pb = layers.get("place_bound_top") or []

    # ---------- 5.1 存在性 ----------
    items.append(_item(
        "5.1", "Place_Bound_Top 存在",
        "PASS" if pb else "FAIL",
        value=len(pb),
        rule="必须画 Place_Bound_Top",
        detail="" if pb else "未找到 Place_Bound_Top 层",
        expected="必须存在",
        source="default",
    ))

    if not pb:
        return items

    pb_bbox = _layer_combined_bbox(pb)
    pins_bbox = _pins_combined_bbox(pins)

    if not pb_bbox or not pins_bbox:
        items.append(_item(
            "5.2", "Place_Bound 外扩量",
            "NA", detail="缺 place_bound 或 pins 数据",
            expected=None, source=None,
        ))
        return items

    # ---------- 5.2a 覆盖检查（相对 pins bbox） ----------
    # 这个用 pins bbox 是对的：place_bound 必须覆盖所有焊盘
    contains = _bbox_contains(pb_bbox, pins_bbox)

    if not contains:
        issues = []
        if pb_bbox[1][0] < pins_bbox[1][0]:
            issues.append(f"右侧缺 {_round(pins_bbox[1][0] - pb_bbox[1][0])}mm")
        if pb_bbox[0][0] > pins_bbox[0][0]:
            issues.append(f"左侧缺 {_round(pb_bbox[0][0] - pins_bbox[0][0])}mm")
        if pb_bbox[1][1] < pins_bbox[1][1]:
            issues.append(f"上方缺 {_round(pins_bbox[1][1] - pb_bbox[1][1])}mm")
        if pb_bbox[0][1] > pins_bbox[0][1]:
            issues.append(f"下方缺 {_round(pins_bbox[0][1] - pb_bbox[0][1])}mm")

        items.append(_item(
            "5.2", "Place_Bound 覆盖焊盘",
            "FAIL",
            value={
                "pb_bbox": [[_round(x, 3) for x in pb_bbox[0]], [_round(x, 3) for x in pb_bbox[1]]],
                "pins_bbox": [[_round(x, 3) for x in pins_bbox[0]], [_round(x, 3) for x in pins_bbox[1]]],
            },
            rule="Place_Bound bbox 必须完全覆盖所有 pin 的 bbox",
            detail="Place_Bound 未覆盖焊盘：" + "，".join(issues),
            expected="覆盖所有焊盘",
            source="self",
        ))
        return items

    # ---------- 5.2b 外扩量（相对 Assembly 器件本体） ----------
    # 规范的"外扩 0.35mm"是相对器件本体算的，不是相对 pins bbox
    device_outline = _find_device_outline(asm)

    if device_outline is None:
        items.append(_item(
            "5.2", "Place_Bound 外扩量",
            "NA",
            detail="Assembly 层没有可识别的器件外框（n_segs == 4 的矩形），无法计算外扩量",
            expected=None, source=None,
        ))
        return items

    device_bbox = device_outline.get("bbox")
    device_size = _bbox_size(device_bbox)
    pb_size = _bbox_size(pb_bbox)

    if not device_size or not pb_size:
        items.append(_item(
            "5.2", "Place_Bound 外扩量",
            "NA", detail="器件外框或 place_bound 尺寸异常",
            expected=None, source=None,
        ))
        return items

    expand_x = (pb_size[0] - device_size[0]) / 2
    expand_y = (pb_size[1] - device_size[1]) / 2

    # 按元件类型选规则
    comp_type = _classify_component(data.get("symbol_name", ""))
    ic_tol = cfg.get("expand_tolerance_mm", 0.05)

    if comp_type == "ic":
        expected_val = cfg.get("ic_expand_mm", 0.35)
        ok = abs(expand_x - expected_val) <= ic_tol and abs(expand_y - expected_val) <= ic_tol
        rule_str = f"IC 类：相对器件本体单边外扩 {expected_val}mm ±{ic_tol}"
        expected_str = f"单边 {expected_val} ± {ic_tol}"
    elif comp_type == "chip":
        expected_val = cfg.get("chip_expand_mm", 0.15)
        ok = abs(expand_x - expected_val) <= ic_tol and abs(expand_y - expected_val) <= ic_tol
        rule_str = f"Chip 类：相对器件本体单边外扩 {expected_val}mm ±{ic_tol}"
        expected_str = f"单边 {expected_val} ± {ic_tol}"
    elif comp_type == "connector":
        expected_val = cfg.get("connector_expand_mm", 0.85)
        ok = 0.5 <= expand_x <= 2.5 and 0.5 <= expand_y <= 2.5
        rule_str = f"连接器：相对器件本体单边外扩 [0.5, 2.5]mm（推荐 {expected_val}）"
        expected_str = "单边 [0.5, 2.5]"
    else:
        min_e = cfg.get("min_expand_mm", 0.05)
        max_e = cfg.get("max_expand_mm", 3.0)
        ok = min_e <= expand_x <= max_e and min_e <= expand_y <= max_e
        rule_str = f"未知类型：外扩量在 [{min_e}, {max_e}]mm"
        expected_str = f"单边 [{min_e}, {max_e}]"

    pins_size = _bbox_size(pins_bbox)

    items.append(_item(
        "5.2", f"Place_Bound 外扩量（{comp_type}）",
        "PASS" if ok else "WARN",
        value={
            "expand_x": _round(expand_x),
            "expand_y": _round(expand_y),
            "pb_size": [_round(pb_size[0]), _round(pb_size[1])],
            "device_size": [_round(device_size[0]), _round(device_size[1])],
            "pins_size": [_round(pins_size[0]), _round(pins_size[1])] if pins_size else None,
            "reference": "assembly_device_outline",
        },
        rule=rule_str,
        detail="" if ok else (
            f"expand_x={_round(expand_x)}, expand_y={_round(expand_y)}"
        ),
        expected=expected_str,
        source="default",
    ))

    return items


# ============================================================
# 六、Assembly
# ============================================================
def check_assembly(data: Dict, rules: Dict) -> List[Dict]:
    items = []
    asm = data.get("layers", {}).get("assembly_top") or []

    items.append(_item(
        "6.1", "Assembly_Top 存在",
        "PASS" if asm else "FAIL",
        value=len(asm),
        rule="必须画 Assembly_Top",
        detail="" if asm else "未找到 Assembly_Top 层",
        expected="必须存在",
        source="default",
    ))

    if not asm:
        return items

    has_content = len(asm) >= 2
    combined = _layer_combined_bbox(asm)
    combined_size = _bbox_size(combined) if combined else None

    items.append(_item(
        "6.2", "Assembly 有内容",
        "PASS" if has_content else "WARN",
        value={
            "n_elements": len(asm),
            "combined_bbox_size": [_round(combined_size[0]), _round(combined_size[1])] if combined_size else None,
        },
        rule="Assembly 层应有至少 2 个元素（外框 + 标识）",
        detail="" if has_content else "元素过少",
        expected="≥ 2 元素",
        source="default",
    ))

    # 6.3：只检查元素数量（1 脚标识的语义存在性由 AI 检查负责）
    items.append(_item(
        "6.3", "Assembly 元素数",
        "PASS" if has_content else "WARN",
        value=f"{len(asm)} 个元素",
        rule="Assembly 层应有 ≥2 个元素（外框 + 1 脚标识）",
        detail=(
            "1 脚标识的存在性由 AI 语义检查判定"
            if has_content else "只有 1 个元素"
        ),
        expected="≥ 2 元素",
        source="default",
    ))

    return items


# ============================================================
# 六、Silkscreen
# ============================================================
def check_silkscreen(data: Dict, rules: Dict) -> List[Dict]:
    items = []
    silk = data.get("layers", {}).get("silkscreen_top") or []
    pins = data.get("pins", [])

    items.append(_item(
        "6.4", "Silkscreen_Top 存在",
        "PASS" if silk else "FAIL",
        value=len(silk),
        rule="必须画 Silkscreen_Top",
        detail="" if silk else "未找到 Silkscreen_Top 层",
        expected="必须存在",
        source="default",
    ))

    if not silk:
        return items

    complex_paths = [p for p in silk if p.get("n_segs", 0) >= 3]
    has_complex = len(complex_paths) > 0

    # 6.5：只检查复杂 path 数量（1 脚标识的语义存在性由 AI 检查负责）
    items.append(_item(
        "6.5", "Silkscreen 复杂 path 数",
        "PASS" if has_complex else "WARN",
        value=f"{len(complex_paths)} 个复杂 path",
        rule="Silkscreen 层应有 ≥1 个复杂 path（n_segs≥3，可能为 1 脚标识）",
        detail=(
            "1 脚标识的存在性由 AI 语义检查判定"
            if has_complex else "未找到复杂 path"
        ),
        expected="≥ 1 个",
        source="default",
    ))

    overlap_found = False
    overlap_detail = ""
    min_clearance = float("inf")

    for s in silk:
        for pin in pins:
            p_bbox = pin.get("bbox")
            if not p_bbox:
                continue
            if _segments_cross_bbox(s, p_bbox):
                overlap_found = True
                overlap_detail = (
                    f"Silkscreen 元素的线段端点落在 pin {pin['number']} 焊盘区域内"
                )
                break
            s_bbox = s.get("bbox")
            if s_bbox:
                d = _bbox_min_distance(s_bbox, p_bbox)
                if d < min_clearance:
                    min_clearance = d
        if overlap_found:
            break

    if overlap_found:
        items.append(_item(
            "6.6", "Silkscreen 不与焊盘重叠",
            "FAIL",
            value="重叠",
            rule="Silkscreen 线段端点不能落在焊盘区域内",
            detail=overlap_detail,
            expected="间距 > 0",
            source="self",
        ))
    else:
        clearance_str = ""
        if min_clearance != float("inf"):
            clearance_str = f"最小间隙 {_round(min_clearance)}mm"
        items.append(_item(
            "6.6", "Silkscreen 不与焊盘重叠",
            "PASS",
            value=_round(min_clearance) if min_clearance != float("inf") else None,
            rule="Silkscreen 线段端点不能落在焊盘区域内",
            detail=clearance_str,
            expected="间距 > 0",
            source="self",
        ))

    return items


# ============================================================
# 主入口
# ============================================================
def run_numeric_checks(
    footprint_data: Dict,
    theoretical: Optional[Dict] = None,
    rules: Optional[Dict] = None,
) -> Dict[str, Any]:
    if not isinstance(footprint_data, dict):
        raise ValueError("footprint_data 必须是 dict")

    if footprint_data.get("error"):
        return {
            "conclusion": "FAIL",
            "summary": {"total": 1, "pass": 0, "fail": 1, "warn": 0, "na": 0},
            "items": [_item(
                "0", "数据提取", "FAIL",
                detail=footprint_data["error"],
                expected=None, source=None,
            )],
            "failed": [],
            "warned": [],
        }

    rules = rules or DEFAULT_RULES

    # 元件类型
    comp_type = _classify_component(footprint_data.get("symbol_name", ""))

    all_items: List[Dict] = []
    all_items += check_naming(footprint_data, rules)
    all_items += check_pad_size(footprint_data, theoretical, rules)
    all_items += check_pitch(footprint_data, theoretical, rules)
    all_items += check_place_bound(footprint_data, rules)
    all_items += check_assembly(footprint_data, rules)
    all_items += check_silkscreen(footprint_data, rules)

    n_total = len(all_items)
    n_pass = sum(1 for x in all_items if x["status"] == "PASS")
    n_fail = sum(1 for x in all_items if x["status"] == "FAIL")
    n_warn = sum(1 for x in all_items if x["status"] == "WARN")
    n_na = sum(1 for x in all_items if x["status"] == "NA")

    failed = [x for x in all_items if x["status"] == "FAIL"]
    warned = [x for x in all_items if x["status"] == "WARN"]

    if n_fail > 0:
        conclusion = "FAIL"
    elif n_na > 0:
        conclusion = "REVIEW_REQUIRED"
    else:
        conclusion = "PASS"

    return {
        "conclusion": conclusion,
        "component_type": comp_type,
        "summary": {
            "total": n_total,
            "pass": n_pass,
            "fail": n_fail,
            "warn": n_warn,
            "na": n_na,
            "pass_rate": round(n_pass / n_total, 4) if n_total else 0.0,
        },
        "items": all_items,
        "failed": failed,
        "warned": warned,
    }


# ============================================================
# 命令行入口
# ============================================================
if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    _this = Path(__file__).resolve()
    _root = _this.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    if len(sys.argv) < 2:
        print("用法: python services/rule_checker.py <dra路径> [datasheet_json路径]")
        sys.exit(1)

    dra_path = sys.argv[1]
    theoretical = None
    if len(sys.argv) >= 3:
        try:
            theoretical = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"读取 datasheet JSON 失败: {e}", file=sys.stderr)

    from services.footprint_extractor import read_full_footprint

    print("=== 1. 提取 .dra 数据 ===")
    fp_data = read_full_footprint(dra_path=dra_path)
    if fp_data.get("error"):
        print(f"❌ {fp_data['error']}")
        sys.exit(1)

    print(f"   symbol_name = {fp_data.get('symbol_name')}")
    print(f"   units       = {fp_data.get('units')}")
    print(f"   pins        = {len(fp_data.get('pins', []))}")

    print()
    print("=== 2. 跑规则检查 ===")
    result = run_numeric_checks(fp_data, theoretical=theoretical)

    print(json.dumps(result, ensure_ascii=False, indent=2))