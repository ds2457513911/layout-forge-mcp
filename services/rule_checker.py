# -*- coding: utf-8 -*-
"""
rule_checker.py —— 基于 Excel 规范的数值规则检查 (v10)

v10 改动（票 #22）：判定容差改由 knowledge/rules/tolerances.json 提供
（load_rules()），删除内置 DEFAULT_RULES 与旧 checker_rules.json 读取路径——
一个数字只有一个出处；库坏即拒绝检查，不再静默回退内置默认。

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

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

from services import rule_engine

logger = logging.getLogger("layout-forge")


# ============================================================
# 判定容差（票 #22：真源是 knowledge/rules/tolerances.json）
# ============================================================
#: 判定代码会直接取用的容差键 —— 缺一件就拒绝检查（见 load_rules）。
#: 这份清单是「判定逻辑依赖哪些数字」的登记；numbers 仍只写在 tolerances.json。
_REQUIRED_TOLERANCES = (
    ("naming", "prefix"), ("naming", "prefix_case_sensitive"), ("naming", "no_dot"),
    ("pad", "size_tolerance_mm"), ("pad", "pastemask_equal_pad"),
    ("pad", "overlap_tolerance_mm2"),
    ("origin", "center_tolerance_mm"), ("origin", "connector_tolerance_mm"),
    ("place_bound", "ic_expand_mm"), ("place_bound", "chip_expand_mm"),
    ("place_bound", "connector_expand_mm"), ("place_bound", "expand_tolerance_mm"),
    ("place_bound", "min_expand_mm"), ("place_bound", "max_expand_mm"),
    ("place_bound", "degenerate_outline_mm"), ("place_bound", "max_plausible_expand_mm"),
    ("silkscreen", "line_width_min_mm"), ("silkscreen", "to_pad_clearance_mm"),
    ("silkscreen", "overlap_inset_mm"),
)


#: 5.2 的覆盖容差：旧阈值文件里没有这个键，票 #22 不凭空新增容差条目，
#: 因此它是代码里的构造常量（不是可调阈值）；改动走代码评审。
_COVERAGE_TOLERANCE_MM = 0.1


def load_rules() -> Dict[str, Any]:
    """
    读规则库的判定容差，返回旧形状的嵌套 dict：{section: {key: value}}。

    ★ 每次检查现读（库只有几 KB，代价可忽略）——改 JSON 立即生效，
      不用重启 MCP。
    ★ 规则库坏了就抛 rule_engine.RuleLibraryError（不再回退内置默认）：
      宁可拒绝出检查结论，也不能拿一份与真源不一致的数字判封装。

    改容差 = 改 knowledge/rules/tolerances.json 的 TOL-* 条目；
    本文件只放判定逻辑（怎么比），不放数字。
    """
    m = rule_engine.load_library().tolerances_map
    missing = [f"{sec}.{key}" for sec, key in _REQUIRED_TOLERANCES
               if key not in (m.get(sec) or {})]
    if missing:
        raise rule_engine.RuleLibraryError(
            "规则库缺少判定容差：" + "、".join(missing)
            + "（补进 knowledge/rules/tolerances.json；缺一件都不判，绝不回退内置默认）")
    return m


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """把 over 深合并到 base，返回新 dict（不修改入参）。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def resolve_rules(rules: Optional[Dict] = None) -> Dict[str, Any]:
    """
    判定用容差表：规则库全量 ⊕ 调用方覆盖（旧 custom_rules 语义）。

    ★ 调用方只写想改的键即可（如 {"pad": {"size_tolerance_mm": 0.1}}），
      未给的键沿用规则库当前值；不再要求传整份表。
    ★ 规则库坏/缺必需键仍由 load_rules() 抛 RuleLibraryError。
    """
    return _deep_merge(load_rules(), rules or {})


# ============================================================
# 元件类型识别
# ============================================================
CONNECTOR_KEYWORDS = [
    "con", "conn", "connector", "hdmi", "usb", "typec", "type-c", "type_c",
    "fpc", "btb", "wtb", "dsub", "rj45", "sata", "sd", "tf",
    "sim", "earphone", "jack", "socket", "header", "plug", "receptacle",
    # 2026-10-12 金标准对拍补：这些系列原点取结构基准/引脚排，
    # 误判成 ic 会被 0.5mm 容差误 FAIL（type_c×3、zif×12、ngff、ddr、audio）
    "type_c", "zif", "ngff", "audio", "ddr", "sodimm", "wifi",
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
    # 2026-10-12 金标准对拍补：DFN/EMI 微型件金标准一律不画丝印，
    # 按 chip 口径走 6.4 NA（emi10 2.5×1mm 9 脚也画不下丝印）
    "dfn", "emi",
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
    cfg = rules["naming"]

    prefix = cfg["prefix"]
    case_sensitive = cfg["prefix_case_sensitive"]

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

    if cfg["no_dot"]:
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


def _pin_rule_subject(pin: Dict) -> Dict[str, Any]:
    """把 pin 读数压成规则引擎要的事实（票 #17：形态判定的输入）。

    事实来源全部是读数本身，缺失就是缺失：
      - mount：有 ~DRILL 层 → tht，否则 smd（extracta 的 SMD pad 不带 DRILL）
      - drill.shape：DRILL 行的 hole_type（"OVAL SLOT" / "CIRCLE_DRILL"）
      - layers：pin 上出现过的层名（保持读数顺序）
      - name / pad_count：pin 名字与焊盘个数
    """
    layers: List[str] = []
    drill_shape = None
    for pad in pin.get("pads") or []:
        layer = pad.get("layer")
        if isinstance(layer, str) and layer not in layers:
            layers.append(layer)
        if layer == "DRILL":
            shape = pad.get("hole_type")
            if shape:
                drill_shape = shape
    return {
        "id": str(pin.get("number", "")),
        "mount": "tht" if "DRILL" in layers else "smd",
        "layers": layers,
        "drill": {"shape": drill_shape} if drill_shape else None,
        "name": pin.get("name", ""),
        "pad_count": len(pin.get("pads") or []),
    }


def _th_oblong_gate(pin: Dict) -> Optional[tuple]:
    """旧 2.8/2.10 的入口条件：有 ETCH/TOP + DRILL，且孔形像长圆孔。

    旧路径用 drill.figure_name 判断（不是 hole_type），这里保留同一口径，
    以保证「holeType 非 OVAL SLOT」的 2.7 FAIL 仍能转播、且不误伤圆孔件。
    """
    etch = _get_etch_pad(pin)
    drill = _get_pad_by_layer(pin, "DRILL")
    if not etch or not drill:
        return None
    hole_fig = str(drill.get("figure_name") or "").upper()
    name = str(pin.get("name") or "").upper()
    if "OBLONG" not in hole_fig and "OB" not in name:
        return None
    return etch, drill


def _th_oblong_violations(pin: Dict) -> List[str]:
    """2.7 转播：旧约定引擎里属于 2.7 的 FAIL（孔零 / holeType 非槽孔）。"""
    gate = _th_oblong_gate(pin)
    if not gate:
        return []
    etch, drill = gate
    name = str(pin.get("name") or "")
    if name.upper().startswith("NP"):
        return []
    hw, hh = (drill.get("size") or [0, 0])[:2]
    if hw <= 0 or hh <= 0:
        return ["DRILL 实义为零（孔未钻/被静默零化）"]
    ht = str(drill.get("hole_type") or "").upper().replace("_", " ")
    if ht and "OVAL SLOT" not in ht:
        return [f"oblong 孔 holeType={drill.get('hole_type')}——应建成 Slot"
                "（OVAL_SLOT + slotSize），当前 Editor 显示 Thru Pin"]
    return []


def _ring_observed(pin: Dict) -> Optional[Dict[str, float]]:
    gate = _th_oblong_gate(pin)
    if not gate:
        return None
    etch, drill = gate
    pw, ph = (etch.get("size") or [0, 0])[:2]
    hw, hh = (drill.get("size") or [0, 0])[:2]
    if hw <= 0 or hh <= 0:
        return None
    return {"ring_x": (pw - hw) / 2, "ring_y": (ph - hh) / 2}


def _naming_observed(pin: Dict, form_id: str) -> Optional[Dict[str, Any]]:
    etch = _get_etch_pad(pin)
    drill = _get_pad_by_layer(pin, "DRILL")
    if not etch or not drill:
        return None
    pw, ph = (etch.get("size") or [0, 0])[:2]
    hw, hh = (drill.get("size") or [0, 0])[:2]
    if hw <= 0 or hh <= 0 or pw <= 0 or ph <= 0:
        return None
    out: Dict[str, Any] = {"name": pin.get("name", "")}
    if form_id == "FORM-MASK-TH-CIRCLE":
        out.update({"hole_dia": max(hw, hh), "pad_dia": max(pw, ph)})
    else:
        out.update({"hole_long": max(hw, hh), "hole_short": min(hw, hh),
                    "pad_long": max(pw, ph), "pad_short": min(pw, ph)})
    return out


def _paste_side(pin: Dict, side: str) -> Optional[Dict]:
    want = ("PASTEMASK/TOP", "PASTEMASK_TOP") if side == "TOP" else (
        "PASTEMASK/BOTTOM", "PASTEMASK_BOTTOM")
    for pad in pin.get("pads") or []:
        layer = str(pad.get("layer") or "")
        if any(layer == w or layer.endswith("/" + w) or layer == "PIN/" + w for w in want):
            return pad
    return None


def _finding_item(rid: str, title: str, findings: List[Dict[str, Any]],
                  lib: Any, default_rules: List[str]) -> Dict:
    """把同一报告编号下的多条 finding 聚合成历史 id 的 item。

    聚合优先级 FAIL > WARN > SKIP(→NA) > PASS；无适用 pin 时按旧行为记 PASS。
    """
    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
    status = "PASS"
    if findings:
        status = min((str(f.get("status")) for f in findings),
                     key=lambda s: order.get(s, 9))
        if status == "SKIP":
            status = "NA"

    shown = [f for f in findings if str(f.get("status")) ==
             ("SKIP" if status == "NA" else status)]
    detail = " | ".join(
        f"pin{f.get('subject')}: {f.get('detail')}" for f in shown
        if f.get("detail"))[:1200]

    if status == "FAIL":
        value = f"{len(shown)} 处违反约定"
    elif status == "WARN":
        value = f"{len(shown)} 处非标准（允许但提示）"
    elif status == "NA":
        value = f"{len(shown)} 个 pin 无法判定"
    else:
        value = "符合库约定"
        exempt = [f for f in findings if f.get("exempt")]
        if exempt:
            detail = " | ".join(
                f"pin{f.get('subject')}: {f.get('detail')}" for f in exempt)

    rule_ids: List[str] = []
    for f in findings:
        rid_ = f.get("rule_id")
        if rid_ and rid_ not in rule_ids:
            rule_ids.append(rid_)
    for rid_ in default_rules:
        if rid_ not in rule_ids:
            rule_ids.append(rid_)

    tools_rules = [lib.rules_by_id[r] for r in rule_ids if r in lib.rules_by_id]
    display = "；".join(
        str(r.get("display") or r.get("name") or r) for r in tools_rules) or title
    forms: List[str] = []
    support_by_form: Dict[str, int] = {}
    for f in findings:
        fid = f.get("form")
        if fid and fid not in forms:
            forms.append(fid)
        if fid and f.get("support") is not None:
            support_by_form[fid] = f.get("support")
    source_ref = next((f.get("source_ref") for f in findings if f.get("source_ref")), None)

    item = _item(rid, title, status, value=value, rule=display,
                 detail=detail, expected=display[:200], source="default")
    item.update({
        "rule_id": rule_ids[0] if rule_ids else None,
        "rule_ids": rule_ids,
        "forms": forms,
        "direction": "assert",
        "rule_source": tools_rules[0].get("source") if tools_rules else None,
        "source_ref": source_ref,
        "support": sum(support_by_form.values()) if support_by_form else None,
        "support_by_form": support_by_form,
        "rule_set_hash": lib.fingerprint,
    })
    return item


def check_pad_size(
    data: Dict,
    theoretical: Optional[Dict],
    rules: Optional[Dict] = None,
) -> List[Dict]:
    rules = resolve_rules(rules)
    items = []
    cfg = rules["pad"]
    tol = cfg["size_tolerance_mm"]

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
    # 票 #17：判定改走 knowledge/rules 的规则库（RULE-MASK-EXPAND）。
    #   - 参数按形态分组（TH 长圆 / TH 圆 / SMD），allowed 与旧阈值文件同一口径
    #     （[0.05, 0.13]、eps 0.005），故状态与旧 2.2 一致；
    #   - finding 带 rule_id / 形态 / 出处 / 支持件数 / 整库哈希，便于溯源；
    #   - 规则库不合法 → load_library 直接抛错，绝不带病判定。
    lib = rule_engine.load_library()
    mask_rule = lib.rules_by_id.get("RULE-MASK-EXPAND")
    if mask_rule is None:
        raise RuntimeError(
            "规则库缺 RULE-MASK-EXPAND（knowledge/rules/rules.json），2.2 拒绝判定"
        )
    sm_statuses = []
    sm_verdicts = []
    sm_forms = []
    sm_skips = []
    sm_bad = []
    sample_ex = sample_ey = None
    for pin in pins:
        etch = _get_etch_pad(pin)
        sm = _get_pad_by_layer(pin, "PIN/SOLDERMASK_TOP")
        if not etch or not sm or not sm.get("size"):
            continue
        ew, eh = etch["size"]
        sw, sh = sm["size"]
        if ew == 0 or eh == 0:
            continue
        ex, ey = (sw - ew) / 2, (sh - eh) / 2
        if sample_ex is None:
            sample_ex, sample_ey = ex, ey
        verdict = rule_engine.judge(
            lib, "RULE-MASK-EXPAND", _pin_rule_subject(pin),
            {"expand_x": ex, "expand_y": ey},
        )
        status = verdict.get("status")
        sm_statuses.append(status)
        sm_verdicts.append(verdict)
        form_id = verdict.get("form")
        if form_id and form_id not in sm_forms:
            sm_forms.append(form_id)
        if status == "SKIP":
            sm_skips.append(str(verdict.get("detail") or ""))
        elif status != "PASS":
            sm_bad.append(f"pin{verdict.get('subject')} {verdict.get('detail')}")

    allowed_pairs = sorted({tuple(v["allowed"]) for v in sm_verdicts if v.get("allowed")})
    allowed_lo = min(p[0] for p in allowed_pairs) if allowed_pairs else None
    allowed_hi = max(p[1] for p in allowed_pairs) if allowed_pairs else None
    rule_display = mask_rule.get("display") or mask_rule.get("name")

    if not sm_statuses:
        items.append(_item(
            "2.2", "阻焊开窗外扩", "NA",
            detail="无 SOLDERMASK_TOP 数据",
            expected=None, source=None,
        ))
        items[-1].update({
            "rule_id": "RULE-MASK-EXPAND",
            "forms": [],
            "rule_source": mask_rule.get("source"),
            "support": None,
            "support_by_form": {},
            "rule_set_hash": lib.fingerprint,
        })
    else:
        if sm_bad:
            sm_status = "WARN"
            sm_detail = "；".join(sm_bad[:3])
        elif sm_skips:
            sm_status = "NA"
            n_pass = sum(1 for s in sm_statuses if s == "PASS")
            passed = f"{n_pass} 个 pin 通过；" if n_pass else ""
            sm_detail = (f"{passed}{len(sm_skips)} 个 pin 未判定（SKIP，不判 PASS）："
                         + "；".join(sm_skips[:3]))
        else:
            sm_status = "PASS"
            sm_detail = ""
        support_by_form = {}
        for v in sm_verdicts:
            fid, sup = v.get("form"), v.get("support")
            if fid and sup is not None:
                support_by_form[fid] = sup
        support = sum(support_by_form.values()) if support_by_form else None
        source_ref = next((v.get("source_ref") for v in sm_verdicts
                           if v.get("source_ref")), None)
        items.append(_item(
            "2.2", "阻焊开窗外扩",
            sm_status,
            value={"单边x": _round(sample_ex), "单边y": _round(sample_ey)},
            rule=rule_display,
            detail=sm_detail,
            expected=(f"单边 [{allowed_lo}, {allowed_hi}]" if allowed_lo is not None else None),
            source="default",
        ))
        items[-1].update({
            "rule_id": "RULE-MASK-EXPAND",
            "forms": sm_forms,
            "rule_source": mask_rule.get("source"),
            "source_ref": source_ref,
            "support": support,
            "support_by_form": support_by_form,
            "rule_set_hash": lib.fingerprint,
            "direction": "assert",
            "report_id": "2.2",
        })

    # ---------- 2.3 钢网与焊盘等大 ----------
    if cfg["pastemask_equal_pad"]:
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

    # ---------- 2.7 TH 焊盘层完整性（2026-10-12 LYDPJ 事故新增） ----------
    # TH（通孔插件）padstack 必须双面：ETCH/TOP + ETCH/BOTTOM + 双面阻焊。
    # 实锤：LYDPJ-H23CB1Y-053N 生成时 padstack 建成 SMD 类型（无 drill、
    # 无 BOTTOM、Soldermask_Bottom=None），双面板底面无铜无法焊接，而当时
    # 六大项检查全 PASS —— 检查器此前完全没有 TH 层概念。
    # 判定 TH（满足其一即查）：
    #   a) 任一 pin pads 带 DRILL 或 ETCH/BOTTOM 层（成品特征）
    #   b) spec 库按 symbol_name 命中且 mount == "tht"（设计意图，
    #      能抓「TH 件被建成 SMD」的坏件——坏件自身无 TH 特征）
    # ★ 层名匹配用后缀：extracta 报的阻焊层带类别前缀（PIN/SOLDERMASK_TOP），
    #   etch 层则无前缀（ETCH/TOP）——endswith 兼容两种形态
    _TH_NEED = ("ETCH/TOP", "ETCH/BOTTOM", "SOLDERMASK_TOP", "SOLDERMASK_BOTTOM")

    def _pin_pads(_pin):
        return [_pd for _pd in _pin.get("pads", []) if isinstance(_pd, dict)]

    def _has_layer(_pin, suffix):
        return any(
            str(_pd.get("layer", "")).upper() == suffix
            or str(_pd.get("layer", "")).upper().endswith("/" + suffix)
            for _pd in _pin_pads(_pin)
        )

    def _th_pin_missing(_pin):
        """四面层只查「真镀通孔」脚（2026-10-13 全量对拍修正）：
        - 有 DRILL 行 + padstack 名非 NP 前缀 → 镀通孔，查四面层；
        - 无 DRILL 行 → SMD 脚或 drill 被零化（后者由 2.7b 抓），此处豁免；
        - NP 前缀 → NPTH 机械孔，无铜是预期，豁免。
        （此前一刀切查所有 pin，混合封装的 SMD 脚/NPTH 孔成片误 FAIL——
          全量对拍 17 件实锤：nb_conn75 的 B1-B8、nb_ddr4 的 A11/A12 等）"""
        _has_drill = _has_layer(_pin, "DRILL")
        if not _has_drill:
            return []
        if str(_pin.get("name", "")).upper().startswith("NP"):
            return []
        _have = set()
        for _pd in _pin_pads(_pin):
            _have.add(str(_pd.get("layer", "")).upper())
        return [l for l in _TH_NEED
                if not any(h == l or h.endswith("/" + l) for h in _have)]

    _all_layers = set()
    for _pin in pins:
        for _pd in _pin.get("pads", []):
            if isinstance(_pd, dict):
                _all_layers.add(str(_pd.get("layer", "")).upper())
    _is_th = bool(_all_layers & {"DRILL", "ETCH/BOTTOM"})
    _th_src = "pins 含 TH 特征"
    if not _is_th:
        _hit = None
        try:
            from services.spec_library import load_spec as _load_spec
            _hit = _load_spec(str(data.get("symbol_name", "")))
        except Exception:
            _hit = None
        if _hit and str(_hit.get("mount", "")).lower() == "tht":
            _is_th = True
            _th_src = "spec 库 mount=tht"

    if _is_th and pins:
        _th_missing = [(f"pin{_pin.get('number')}", _th_pin_missing(_pin)) for _pin in pins]
        _th_missing = [(n, m) for n, m in _th_missing if m]
        if _th_missing:
            items.append(_item(
                "2.7", "TH 焊盘层完整性",
                "FAIL",
                value=f"{len(_th_missing)}/{len(pins)} pin 缺层",
                rule="TH 件 padstack 必须 ETCH/TOP+ETCH/BOTTOM+双面阻焊（双面板底面可焊）",
                detail=" | ".join(f"{n}: 缺 {'/'.join(m)}" for n, m in _th_missing[:3]),
                expected="四面层齐全",
                source="default",
            ))
        else:
            items.append(_item(
                "2.7", "TH 焊盘层完整性",
                "PASS",
                value=f"{len(pins)} pin 四面层齐全（判定: {_th_src}）",
                rule="TH 件 padstack 必须 ETCH/TOP+ETCH/BOTTOM+双面阻焊",
                detail="",
                expected="四面层齐全",
                source="default",
            ))

        # ---- 2.7b TH 钻孔实义（2026-10-13 第 0 层升级）----
        # extracta 解析时零尺寸 DRILL 行被丢弃 → pin pads 里没有 DRILL
        # 条目 = 孔未钻/被静默零化。.pad 的 PadType 字符串是 holeType 残留
        # 不可信（LYDPJ 二次事故实锤：PadType:through 但 drill 全零被否）。
        # ★ 只抓「双面铜都在却没有孔」的脚——那是铁定的断孔；单面铜（SMD
        #   脚/NPTH 孔）无钻孔是合法形态，不能误杀（全量对拍 17 件教训）。
        _drill_missing = [
            f"pin{_pin.get('number')}" for _pin in pins
            if _has_layer(_pin, "ETCH/BOTTOM")
            and not _has_layer(_pin, "DRILL")
            and not str(_pin.get("name", "")).upper().startswith("NP")
        ]
        if _drill_missing:
            items.append(_item(
                "2.7", "TH 焊盘层完整性", "FAIL",
                value=f"{len(_drill_missing)}/{len(pins)} pin 无 DRILL 实义",
                rule="TH 件每脚必须有真实钻孔（extracta DRILL 行非零）",
                detail="pins: " + ",".join(_drill_missing[:8]),
                expected="DRILL 实义存在且非零",
                source="default",
            ))

        # ---- 2.8 / 2.9 / 2.10 构造约定符合性（票 #18：判定平移）----
        # 2.8 环宽 / 2.9 padstack 命名 / 2.10 钢网策略全部由 knowledge/rules
        # 驱动；历史报告编号由规则的 report_id 映射回 2.8/2.9/2.10。
        conv: Dict[str, List[Dict[str, Any]]] = {"2.8": [], "2.9": [], "2.10": []}
        _27_fails: List[str] = []
        for _pin in pins:
            _subj = _pin_rule_subject(_pin)
            _form = rule_engine.match_form(lib, _subj)
            _fid = _form.get("id") if _form else None
            _pin_name = str(_pin.get("name") or "")
            # 旧约定引擎在 2.7 硬失败（孔零 / holeType 错）时
            # 提前 return，环宽与钢网都不判；这里保留同一短路口径，避免
            # 把「无法判」变成 PASS/NA 之外的新状态。
            _violations = _th_oblong_violations(_pin)
            _27_fails.extend(
                f"pin{_pin.get('number')}: {m}" for m in _violations)
            if _fid == "FORM-MASK-TH-OBLONG" and not _violations:
                # 观测拿不到时仍调 judge：缺键必须落成 SKIP，不许当成 PASS。
                _ring = _ring_observed(_pin) or {}
                conv["2.8"].append(rule_engine.judge(
                    lib, "RULE-ANNULAR-RING", _subj, _ring))
                # NP 的豁免由规则库 exempt 数据决定；检查器不再硬编码跳过，
                # 否则规则库只驱动了一半。
                _top = _paste_side(_pin, "TOP")
                conv["2.10"].append(rule_engine.judge(
                    lib, "RULE-PASTE-TOP-PRESENT", _subj,
                    {"paste_top_present": 1 if _top else 0}))
                if _top is not None:
                    _etch = _get_etch_pad(_pin) or {}
                    _ts = _top.get("size") or [0, 0]
                    _es = _etch.get("size") or [0, 0]
                    conv["2.10"].append(rule_engine.judge(
                        lib, "RULE-PASTE-TOP-EQ-PAD", _subj,
                        {"paste_top_dx": abs(_ts[0] - _es[0]),
                         "paste_top_dy": abs(_ts[1] - _es[1])}))
                _bot = _paste_side(_pin, "BOTTOM")
                conv["2.10"].append(rule_engine.judge(
                    lib, "RULE-PASTE-BOTTOM-ABSENT", _subj,
                    {"paste_bottom_present": 1 if _bot else 0}))
            # 旧 2.9 只查 figure_name 能认出的长圆/圆孔；空名/空 figure_name
            # 的件旧路径直接放行，这里保留同一口径（hole_type 仍用于选形态）。
            _fig = str((_get_pad_by_layer(_pin, "DRILL") or {}).get("figure_name") or "").upper()
            if (_pin_name.strip()
                    and _fid in ("FORM-MASK-TH-OBLONG", "FORM-MASK-TH-CIRCLE")
                    and ("OBLONG" in _fig or "CIRCLE" in _fig)):
                _nobs = _naming_observed(_pin, _fid) or {}
                conv["2.9"].append(rule_engine.judge(
                    lib, "RULE-PADSTACK-NAMING", _subj, _nobs))

        items.append(_finding_item(
            "2.8", "TH 环宽约定", conv["2.8"], lib, ["RULE-ANNULAR-RING"]))
        items.append(_finding_item(
            "2.10", "TH 钢网策略", conv["2.10"], lib,
            ["RULE-PASTE-TOP-PRESENT", "RULE-PASTE-TOP-EQ-PAD",
             "RULE-PASTE-BOTTOM-ABSENT"]))

        if _27_fails:
            items.append(_item(
                "2.7", "TH 钻孔 holeType", "FAIL",
                value=f"{len(_27_fails)} 处违反约定",
                rule="oblong 槽孔必须 OVAL_SLOT（'OBLONG_DRILL 非法，静默回落 "
                     "CIRCLE_DRILL → Editor 显示 Thru Pin）",
                detail=" | ".join(_27_fails[:3]),
                expected="holeType=OVAL SLOT", source="default",
            ))
        items.append(_finding_item(
            "2.9", "TH padstack 命名语法", conv["2.9"], lib,
            ["RULE-PADSTACK-NAMING"]))
    else:
        items.append(_item(
            "2.7", "TH 焊盘层完整性", "NA",
            detail="SMD 特征（无 drill / 无 BOTTOM / spec 非 tht）",
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
    # 2026-10-12 金标准校准：bbox 面积重叠 < overlap_tolerance_mm2 视为
    # 擦边（bbox 是保守矩形，相邻贴紧焊盘常互相 grazes），不算 FAIL
    ov_tol = float(rules["pad"]["overlap_tolerance_mm2"])
    overlap_found = False
    overlap_detail = ""
    overlap_area = 0.0
    min_clearance = float("inf")
    min_pair = None

    for i in range(len(pins)):
        for j in range(i + 1, len(pins)):
            b1 = pins[i].get("bbox")
            b2 = pins[j].get("bbox")
            if not b1 or not b2:
                continue
            area = _bbox_overlap_area(b1, b2)
            if area > ov_tol:
                overlap_found = True
                overlap_area = area
                overlap_detail = (
                    f"pin {pins[i]['number']} 与 pin {pins[j]['number']} 焊盘 bbox 真实重叠 "
                    f"{_round(area)}mm²"
                )
                break
            if area > 0:
                # 擦边级重叠：记录但不算 FAIL
                if area > overlap_area:
                    overlap_area = area
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
        if overlap_area > 0:
            detail += (
                f"；存在 ≤{ov_tol}mm² 的 bbox 擦边重叠（{_round(overlap_area)}mm²），"
                "判为贴紧不算重叠"
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
    origin_cfg = rules["origin"]
    if comp_type == "connector":
        origin_tol = origin_cfg["connector_tolerance_mm"]
    else:
        origin_tol = origin_cfg["center_tolerance_mm"]

    center = None
    source_center = None

    pb = data.get("layers", {}).get("place_bound_top") or []
    # ★ 取 bbox 面积最大的元素当外框中心 —— pb[0] 可能是小元素/标注，
    #   2026-10-12 金标准对拍实锤（ddr4 偏 37mm 就是取错元素）
    if pb:
        _pb_best = max(
            (el for el in pb if el.get("bbox")),
            key=lambda el: _bbox_area(el.get("bbox")),
            default=None,
        )
        if _pb_best is not None:
            center = _pb_best.get("center") or _bbox_center(_pb_best["bbox"])
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
    cfg = rules["place_bound"]
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

    # ---------- ★ 5.2a 前置：pb_bbox 退化（零面积点）→ NA ----------
    # npth/pth 安装孔件金标准只在孔心画一个 place_bound 点（bbox 面积 0），
    # 覆盖检查对这种件必然 FAIL —— 是机械孔惯例，不是缺陷。
    pb_w = pb_bbox[1][0] - pb_bbox[0][0]
    pb_h = pb_bbox[1][1] - pb_bbox[0][1]
    if abs(pb_w) < 1e-6 or abs(pb_h) < 1e-6:
        items.append(_item(
            "5.2", "Place_Bound 覆盖焊盘",
            "NA",
            value={"pb_bbox": [[_round(x, 3) for x in pb_bbox[0]], [_round(x, 3) for x in pb_bbox[1]]]},
            rule="Place_Bound 退化为零面积点（机械孔/基准点惯例），覆盖检查不适用",
            detail="place_bound 是点元素，转 AI/人工确认是否为安装孔类",
            expected=None, source="self",
        ))
        return items

    # ---------- 5.2a 覆盖检查（相对 pins bbox） ----------
    # 这个用 pins bbox 是对的：place_bound 必须覆盖所有焊盘
    # 2026-10-12 金标准校准：金标准惯例允许 place_bound 比焊盘 bbox 小
    # ≤0.1mm（dfn1006 等「上方缺 0.05~0.08mm」成片假 FAIL），加容差：
    # 把 pins bbox 向内缩 cov_tol 后仍须被 pb 完全包含
    cov_tol = _COVERAGE_TOLERANCE_MM
    contains = (
        pb_bbox[0][0] <= pins_bbox[0][0] + cov_tol and
        pb_bbox[0][1] <= pins_bbox[0][1] + cov_tol and
        pb_bbox[1][0] >= pins_bbox[1][0] - cov_tol and
        pb_bbox[1][1] >= pins_bbox[1][1] - cov_tol
    )

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

    # ---------- ★ 退化外框守卫（2026-10-12 金标准对拍实锤） ----------
    # Assembly 外框识别退化（如 RJ45 识别成 0.5×0.5 的小元素）时，
    # 外扩会被算成 5~12mm 的鬼数 —— 转 NA，别拿它判 WARN/FAIL。
    degenerate = float(cfg["degenerate_outline_mm"])
    if min(device_size[0], device_size[1]) < degenerate:
        items.append(_item(
            "5.2", "Place_Bound 外扩量",
            "NA",
            value={
                "device_size": [_round(device_size[0]), _round(device_size[1])],
                "pb_size": [_round(pb_size[0]), _round(pb_size[1])],
                "reference": "assembly_device_outline",
            },
            rule="器件外框任一边 < "
                 f"{degenerate}mm，疑似外框识别退化",
            detail=(
                f"识别到的器件外框 {_round(device_size[0])}×{_round(device_size[1])}mm "
                "过小，外扩量不可信，转人工复核"
            ),
            expected=None,
            source="self",
        ))
        return items

    expand_x = (pb_size[0] - device_size[0]) / 2
    expand_y = (pb_size[1] - device_size[1]) / 2

    # 外扩超出物理合理范围 → 大概率外框还是识别错了（宁可 NA 不误报）
    max_plausible = float(cfg["max_plausible_expand_mm"])
    if (max(expand_x, expand_y) > max_plausible):
        items.append(_item(
            "5.2", "Place_Bound 外扩量",
            "NA",
            value={
                "expand_x": _round(expand_x), "expand_y": _round(expand_y),
                "device_size": [_round(device_size[0]), _round(device_size[1])],
                "pb_size": [_round(pb_size[0]), _round(pb_size[1])],
                "reference": "assembly_device_outline",
            },
            rule=f"外扩量 > {max_plausible}mm 超出物理合理范围，疑似外框识别退化",
            detail=f"expand_x={_round(expand_x)}, expand_y={_round(expand_y)}，转人工复核",
            expected=None,
            source="self",
        ))
        return items

    # 按元件类型选规则
    comp_type = _classify_component(data.get("symbol_name", ""))
    ic_tol = cfg["expand_tolerance_mm"]

    if comp_type == "ic":
        expected_val = cfg["ic_expand_mm"]
        ok = abs(expand_x - expected_val) <= ic_tol and abs(expand_y - expected_val) <= ic_tol
        rule_str = f"IC 类：相对器件本体单边外扩 {expected_val}mm ±{ic_tol}"
        expected_str = f"单边 {expected_val} ± {ic_tol}"
    elif comp_type == "chip":
        expected_val = cfg["chip_expand_mm"]
        ok = abs(expand_x - expected_val) <= ic_tol and abs(expand_y - expected_val) <= ic_tol
        rule_str = f"Chip 类：相对器件本体单边外扩 {expected_val}mm ±{ic_tol}"
        expected_str = f"单边 {expected_val} ± {ic_tol}"
    elif comp_type == "connector":
        expected_val = cfg["connector_expand_mm"]
        ok = 0.5 <= expand_x <= 2.5 and 0.5 <= expand_y <= 2.5
        rule_str = f"连接器：相对器件本体单边外扩 [0.5, 2.5]mm（推荐 {expected_val}）"
        expected_str = "单边 [0.5, 2.5]"
    else:
        min_e = cfg["min_expand_mm"]
        max_e = cfg["max_expand_mm"]
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
    # 2026-10-12 金标准对拍校准：惯例 assembly 只画外框 1 个元素，
    # 1 脚标识的存在性归 AI 语义检查（本函数注释既有的口径），
    # 几何层只要求「有外框」，≥2 的旧要求过严（39/253 假 WARN）
    min_elements = 1

    # 6.1：机械孔件（npth/pth）金标准惯例不画 Assembly → NA
    low_name = data.get("symbol_name", "").lower()
    is_mech = bool(re.search(r"(^|_)n?pth", low_name))
    if not asm and is_mech:
        items.append(_item(
            "6.1", "Assembly_Top 存在",
            "NA",
            value=len(asm),
            rule="机械孔/安装件允许不画 Assembly",
            detail="npth/pth 机械件无 Assembly 为金标准惯例",
            expected="建议有，机械件缺省允许",
            source="default",
        ))
        return items

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

    has_content = len(asm) >= min_elements
    combined = _layer_combined_bbox(asm)
    combined_size = _bbox_size(combined) if combined else None

    items.append(_item(
        "6.2", "Assembly 有内容",
        "PASS" if has_content else "WARN",
        value={
            "n_elements": len(asm),
            "combined_bbox_size": [_round(combined_size[0]), _round(combined_size[1])] if combined_size else None,
        },
        rule=f"Assembly 层应有至少 {min_elements} 个元素"
             + ("" if min_elements > 1 else "（chip 类只有外框即可）"),
        detail="" if has_content else "元素过少",
        expected=f"≥ {min_elements} 元素",
        source="default",
    ))

    # 6.3：只检查元素数量（1 脚标识的语义存在性由 AI 检查负责）
    items.append(_item(
        "6.3", "Assembly 元素数",
        "PASS" if has_content else "WARN",
        value=f"{len(asm)} 个元素",
        rule=f"Assembly 层应有 ≥{min_elements} 个元素"
             + ("" if min_elements > 1 else "（chip 类无 1 脚标识）"),
        detail=(
            "1 脚标识的存在性由 AI 语义检查判定"
            if has_content else "只有 1 个元素"
        ),
        expected=f"≥ {min_elements} 元素",
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
    comp_type = _classify_component(data.get("symbol_name", ""))
    inset = float(rules["silkscreen"]["overlap_inset_mm"])

    # 6.4：chip 类（0201/0402 等无极性件）与 ≤3 脚小件（二极管/LED/DFN
    # 2L/SOT-23-3）金标准惯例不画丝印或丝印有无不一 → NA；其余保持 FAIL
    if comp_type == "chip" or (pins and len(pins) <= 3):
        items.append(_item(
            "6.4", "Silkscreen_Top 存在",
            "PASS" if silk else "NA",
            value=len(silk),
            rule="chip/≤3 脚微型件允许不画 Silkscreen（金标准丝印有无不一，交 AI 语义层）",
            detail="" if silk else "微型件无丝印为惯例，不判失败",
            expected="建议有，缺省允许",
            source="default",
        ))
    else:
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

    # 6.5（旧「Silkscreen 复杂 path 数」已于 2026-10-12 删除：
    #   自造概念 + 金标准 55 件假阳性，1 脚标识的语义存在性
    #   本就归 AI 语义检查负责，几何层不再猜。）

    overlap_found = False
    overlap_detail = ""
    min_clearance = float("inf")

    for s in silk:
        for pin in pins:
            p_bbox = pin.get("bbox")
            if not p_bbox:
                continue
            # ★ 内缩判据：端点须深入焊盘 >inset mm 才算真重叠
            #   （金标准惯例：丝印框端点常贴到焊盘 bbox 边上，2026-10-12 校准）
            if _segments_cross_bbox(s, p_bbox, margin=-inset):
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
def check_spec_unconsumed(theoretical: Optional[Dict]) -> List[Dict]:
    """0.1 spec 守卫（2026-10-13）：AI 读图声明"schema 未消费的约束"逐条显名。

    与 pads.txt 列守卫同一哲学：规格书里存在、但本工具建模范围没有的约束，
    忽略可以，忽略而不告诉你不行。每条 WARN，不翻转结论，但必须出现在报告里
    供工程师裁定（可能沉淀为 save_case 或新规则）。
    """
    if not isinstance(theoretical, dict):
        return []
    payload = theoretical.get("theoretical_land_params") or theoretical
    raw = payload.get("unconsumed")
    if not isinstance(raw, list) or not raw:
        return []
    items: List[Dict] = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            continue
        items.append(_item(
            "0.1", "spec 守卫（未消费声明）", "WARN",
            value=str(it.get("item", "")),
            detail=f"规格书约束未被本工具建模，需人工裁定：{it.get('value', '')}"
                   + (f"（{it.get('note', '')}）" if it.get("note") else ""),
            expected="显名并人工裁定",
            source="datasheet",
        ))
    return items


def check_shape_pads(footprint_data: Dict) -> List[Dict]:
    """0.2 SHAPE 自定义轮廓焊盘显名（2026-10-13 建模裁定）。

    figure='SHAPE' = 自绘轮廓焊盘（语料 106 行，如 JOPEN25X30_S-L-P）：
    extracta 只给包围盒尺寸，轮廓几何在 shape symbol 里、本工具不建模。
    2.x 尺寸/间距规则按包围盒近似仍然成立，但形状类目测项（轮廓与
    规格书一致性等）必须人工复核——显名，不做无声近似。
    """
    if not isinstance(footprint_data, dict):
        return []
    hits: List[str] = []
    seen = set()
    for pin in footprint_data.get("pins") or []:
        if not isinstance(pin, dict):
            continue
        for pd in pin.get("pads") or []:
            if not isinstance(pd, dict):
                continue
            if (pd.get("figure_name") or "").strip().upper() != "SHAPE":
                continue
            sn = pd.get("shape_name") or ""
            key = (pin.get("number"), sn)
            if key in seen:
                continue
            seen.add(key)
            hits.append(f"pin{pin.get('number')}:{sn}" if sn
                        else f"pin{pin.get('number')}:（无 PADSHAPENAME）")
    if not hits:
        return []
    shown = ", ".join(hits[:8]) + ("…" if len(hits) > 8 else "")
    return [_item(
        "0.2", "SHAPE 自定义轮廓焊盘", "WARN",
        value=shown,
        detail=f"{len(hits)} 处焊盘为自绘轮廓（extracta 仅给包围盒，"
               f"轮廓几何未建模），形状相关目测项需人工复核",
        expected="显名并人工复核",
        source=None,
    )]


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

    rules = resolve_rules(rules)

    # 元件类型
    comp_type = _classify_component(footprint_data.get("symbol_name", ""))

    all_items: List[Dict] = []
    all_items += check_spec_unconsumed(theoretical)
    all_items += check_shape_pads(footprint_data)
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

    # 覆盖率（票 #21）：本次输入的每个焊盘先归属形态，再与规则表对差集。
    # 两类缺口分开返回：有形态但无规则（静态）、无形态（运行时）。
    lib = rule_engine.load_library()
    subjects = [_pin_rule_subject(pin) for pin in footprint_data.get("pins") or []
                if pin.get("pads")]

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
        "rule_set": rule_engine.rule_set_info(lib),
        "coverage": rule_engine.coverage(lib, subjects),
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