# -*- coding: utf-8 -*-
"""
geometry_solver.py —— 「器件参数 → 成品几何」求解器（攻坚 P0-2）

定位
====
`build_footprint_from_params()` 是**执行器**：它只认"成品几何"
（padstack 层定义 / pin 绝对坐标 / 已算好的矩形）。
本模块补上它前面缺的那一环 —— **编译器**：
吃"器件参数"（规格书上能直接读到的量），吐出执行器能直接用的几何 + 两个名字。

    规格书参数 ──[本模块]──> padstack / pins / silk / body / place_bound / refdes
                                   │
                                   └──> build_footprint_from_params()（执行器）

依据（全部可追溯，不凭经验拍）
==============================
  [规范] `Data/checklist/PCB_Footprint建库规范.xlsx`（下表引用其「规范项」）
  [规则] `services/rule_checker.py::DEFAULT_RULES`
  [实证] `Data/footprint/3S48000163/checklist_report_20260918_095100.json`（金标准实测）

  规范项                                 出处      本模块落点
  ----------------------------------------------------------------------
  命名 NB_<类型><引脚数>_<长>X<宽>X<高>…  [规范 一]  solve_symbol_name
  命名 小数点用字母 d 代替                [规范 一]  name_num
  命名 命名用中值（永远）                 [规范 一]  solve_symbol_name
  画实体 图上标注→取标注值 / 查表→取MAX   [用户口径] resolve_dim（2026-09-29）
  焊盘命名 r长x宽 / c直径 / sq边长        [规范 二]  solve_padstack_name
  阻焊开窗单边 +0.05                      [规范 三]  solve_padstack
  钢网开窗与焊盘等大                      [规范 三]  solve_padstack
  Place_Bound = 器件本体 + 单边外扩        [规范 三]  solve_outlines
  Assembly    = 器件本体（±0.05）         [规范 三]  solve_outlines
  原点在封装中心，不能以 pin1 为原点       [规范 四]  solve_pins
  Pin Pitch 用规格书推荐值                [规范 一]  spec["array"]["pitch_*"]
  丝印线宽 >=0.10                         [规范 四]  solve_silkscreen
  丝印不得进入阻焊开窗区                  [规范 四]  solve_silkscreen
  位号放本体外侧、不与焊盘重叠             [规范 四]  solve_refdes
  1 脚标识（IC/多引脚必做）               [规范 四]  solve_silkscreen(pin1_mark)

三条从金标准反解出来的关键结论（本模块据此实现）
================================================
1. **Place_Bound 的基准是「器件本体」，不是焊盘阵列**。
   [规范 三] 原文"按封装本体实际尺寸 + 焊盘单边外扩 0.35mm"，
   金标准 5.2 实证 `pb_size=[3.9,3.2]` `device_size=[3.2,2.5]` `expand=0.35`
   → 3.2+0.7=3.9 ✓。此前 e2e 脚本按焊盘算（3.4×2.7）就是错的。
2. **丝印的落点是「焊盘(含阻焊)外缘 + 0.06」**。
   金标准 6.6 实测"丝印与焊盘间距 = 0.06"；本体（3.2×2.5）比焊盘跨距
   （3.7×3.0 含阻焊）还小，丝印**不可能**按本体画 —— 只能贴焊盘外缘。
3. **pin 编号是逆时针绕行，起点在左下**。
   规格书 p5「CONNECTION DIAGRAM (TOP VIEW)」实测标注：
   `#1` 左下 / `#2` 右下 / `#3` 右上 / `#4` 左上。
   → e2e 脚本把 #1 放在左上、#3 放在左下，**编号错了 180°**。

设计原则
========
1. 全部是**纯函数**：不碰 Allegro、不读文件 → 可离线单测、可复现。
2. 输出带 `derivations` 审计表：每个数字都能回答"为什么是 3.9 不是 3.4"。
3. 名字与几何构成**反解闭环**：`solve_padstack_name()` 的输出可被
   `parse_name_num()` 反解回原尺寸（与 rule_checker 的 2.1 检查同源）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services import spec_library
from services.rule_checker import DEFAULT_RULES, _classify_component

__all__ = [
    "name_num", "parse_name_num",
    "solve_padstack_name", "solve_symbol_name",
    "DIM_SOURCE_DRAWING", "DIM_SOURCE_TABLE", "DIM_SOURCES", "DIM_KEYS",
    "resolve_dim", "resolve_all_dims",
    "solve_padstack", "solve_pins", "solve_outlines", "solve_pad_rects",
    "solve_silk_outline", "solve_pin1_mark", "solve_silkscreen", "solve_refdes",
    "solve", "to_build_args",
    "SPEC_3S48000163", "SPEC_FPC0510_6P",
]


# ============================================================
# 0. 小工具
# ============================================================
def _r4(v: float) -> float:
    """统一收敛到 4 位小数 —— 免得 1.1000000000000001 这种串进 SKILL 表达式。"""
    return round(float(v), 4)


def _half(v: float) -> float:
    return _r4(float(v) / 2.0)


# ============================================================
# 0.5 规格书尺寸 → 取值口径（2026-09-29 用户裁决）
# ============================================================
#: 图上**直接标注**了具体值 → 取标注值本身（带公差也取名义值，不加偏差）
DIM_SOURCE_DRAWING = "drawing"
#: 图上**没标**、需要查表 → 取表里的 **MAX**（无 MAX 用 TYP + 告警）
DIM_SOURCE_TABLE = "table"
DIM_SOURCES = (DIM_SOURCE_DRAWING, DIM_SOURCE_TABLE)

#: 「规格书来源」的尺寸键 → (spec 取值路径, 中文名)。resolve_all_dims 按此扫描。
DIM_KEYS: Dict[str, str] = {
    "body.x": "本体宽 X",
    "body.y": "本体高 Y",
    "pad.x": "焊盘宽 X",
    "pad.y": "焊盘高 Y",
    "pitch_x": "横向间距",
    "pitch_y": "纵向间距",
}


def resolve_dim(
    key: str,
    spec: Dict[str, Any],
    *,
    drawing: Optional[float] = None,
    table: Optional[Dict[str, Any]] = None,
) -> Tuple[float, Dict[str, Any], Optional[str]]:
    """
    规格书的一个尺寸 → 取值。

    口径（2026-09-29 用户裁决；**替代**旧的「有 recommended 用中值 /
    没标用中值+公差」）
    ==========================================================
      1. **图上标注了具体值** → 取**标注值本身**。
         即使标注带公差（`3.2±0.1`）也取名义值 **3.2**，**不**加上偏差。
      2. **图上没标、需查表** → 取表里的 **MAX**。
         表里没有 MAX → 取 **TYP** 并**告警**（要求人工复核，不许静默）。

    来源怎么定（优先级从高到低）
      1. `spec["dim_sources"][key]` —— 逐维指定
      2. `spec["dim_source"]`       —— 整件默认
      3. 自动推断：只给了 `drawing` → "drawing"；只给了表 → "table"

    表怎么给
      `table={"min":..,"typ":..,"max":..}` 直接传，或走
      `spec["dim_tables"][key]`。

    遗留开关（默认**关**，仅供对照 2026-09-29 之前的旧行为）
      `spec["use_max_for_geometry"] = True` → 图上有公差时改取**上偏差**：
      优先 `spec["dim_tols"][key]`，兼容老的 `spec["body_tol"]`；
      `spec["body_max"]` 给的是**绝对值**，直接采用。

    :return: (value, deriv_entry, warn_or_None)

    >>> resolve_dim("body.x", {}, drawing=3.2)[0]
    3.2
    >>> resolve_dim("body.x", {}, drawing=3.2)[1]["formula"]
    '来源=drawing；图上标注值 3.2'
    >>> resolve_dim("body.x", {}, drawing=3.2)[2] is None
    True
    >>> resolve_dim("body.x", {"dim_source": "table"}, drawing=3.2,
    ...             table={"min": 3.0, "typ": 3.2, "max": 3.4})[0]
    3.4
    >>> resolve_dim("body.x", {"dim_source": "table"}, drawing=3.2,
    ...             table={"min": 3.0, "typ": 3.2})[0]      # 无 MAX → TYP
    3.2
    >>> resolve_dim("body.x", {"dim_source": "table"}, drawing=3.2,
    ...             table={"min": 3.0, "typ": 3.2})[2] is not None
    True
    >>> resolve_dim("body.x", {"use_max_for_geometry": True,
    ...                        "body_tol": {"x": 0.1}}, drawing=3.2)[0]
    3.3
    """
    explicit = (spec.get("dim_sources") or {}).get(key)     # 逐维显式声明
    src = explicit or spec.get("dim_source")                # → 整件默认
    tbl = table if table is not None else (spec.get("dim_tables") or {}).get(key)

    if src not in DIM_SOURCES:                              # 没声明 → 自动推断
        if drawing is not None:
            src = DIM_SOURCE_DRAWING
        elif tbl:
            src = DIM_SOURCE_TABLE
        else:
            raise ValueError(
                f"尺寸 {key!r} 既没给【图上标注值】，也没有可查的【表】—— "
                f"请给 drawing=… 或 table=…，并把来源写进 "
                f"spec['dim_source']（drawing / table）"
            )

    warn: Optional[str] = None
    # ★ 整件默认 = table、但该尺寸**图上其实标了值**、又没给表 →
    #   退回标注值（只告警，不崩）。真实的规格书常是混合的：
    #   本体标在图里、焊盘只给表。硬失败会把整件卡死。
    #   但**逐维显式声明** table 却没给表 = spec 写错了 → 照旧硬失败。
    if (src == DIM_SOURCE_TABLE and not tbl and explicit is None
            and drawing is not None):
        src = DIM_SOURCE_DRAWING
        warn = (f"尺寸 {key!r} 没给表（整件默认来源是 'table'，但图上标了值 "
                f"{float(drawing):g}）—— 已按**图上标注值**取值，"
                f"请确认这确实就是图纸口径。")

    if src == DIM_SOURCE_DRAWING:
        if drawing is None:
            raise ValueError(
                f"尺寸 {key!r} 的来源标为 'drawing'（图上标注），但没给标注值")
        value = float(drawing)
        note = f"图上标注值 {value:g}"
        # ---- 遗留口径：显式要求「画实体取最大值」时才加上偏差 ----
        if spec.get("use_max_for_geometry") is True:
            _abs_max = None
            if key.startswith("body."):
                _abs_max = (spec.get("body_max") or {}).get(key.split(".", 1)[1])
            if _abs_max is not None:                       # 绝对值，直接采用
                value = float(_abs_max)
                note = f"图上标注 {drawing:g} 的上偏差 {value:g}"
            else:
                _tol = (spec.get("dim_tols") or {}).get(key)
                if _tol is None and key.startswith("body."):
                    _tol = (spec.get("body_tol") or {}).get(key.split(".", 1)[1])
                if _tol is not None:
                    value = float(drawing) + abs(float(_tol))
                    note = f"图上标注 {drawing:g} + 上偏差 {abs(float(_tol)):g}"
                note += "（遗留 use_max_for_geometry=True）"
    else:
        if not tbl:
            raise ValueError(
                f"尺寸 {key!r} 的来源标为 'table'（需查表），但没给表 —— "
                f"请补 spec['dim_tables'][{key!r}] 或直接传 table=…")
        if tbl.get("max") is not None:
            value, note = float(tbl["max"]), "表 MAX"
        elif tbl.get("typ") is not None:
            value, note = float(tbl["typ"]), "表 TYP（表里没有 MAX）"
            warn = (f"尺寸 {key!r} 的来源是**查表**，但表里只有 MIN/TYP、没有 MAX —— "
                    f"已按 **TYP {value:g}** 取值建封装，请人工复核。")
        else:
            raise ValueError(
                f"尺寸 {key!r} 的表里既没有 max 也没有 typ：{tbl!r}")

    deriv = {
        "item": f"dim.{key} {DIM_KEYS.get(key, '')}".strip(),
        "value": _r4(value),
        "formula": f"来源={src}；{note}",
        "source": ("用户口径 2026-09-29：图上标注具体值 → 取标注值本身"
                   "（`3.2±0.1` 取 3.2，不加上偏差）"
                   if src == DIM_SOURCE_DRAWING else
                   "用户口径 2026-09-29：图上无值、需查表 → 取表 MAX"
                   "（无 MAX 用 TYP 并告警）"),
    }
    return _r4(value), deriv, warn


def resolve_all_dims(
    spec: Dict[str, Any],
) -> Tuple[Dict[str, float], List[Dict[str, Any]], List[str]]:
    """
    一次性解析本件所有「规格书来源」的尺寸（`DIM_KEYS` 全表）。

    ★ 未在 spec 里出现的尺寸**跳过**（不算错）—— 例如只有本体没有阵列参数时，
      不去碰 pitch。这样 `solve_*` 单独调用也能用。

    :return: ({"body.x": 3.2, ...}, [deriv...], [warn...])
    """
    out: Dict[str, float] = {}
    deriv: List[Dict[str, Any]] = []
    warn: List[str] = []

    body = spec.get("body") or {}
    pad = spec.get("pad") or {}
    pad_size = [float(v) for v in (pad.get("size") or [])]
    arr = spec.get("array") or {}

    def _drawing_of(key: str) -> Optional[float]:
        if key.startswith("body."):
            v = body.get(key.split(".", 1)[1])
        elif key.startswith("pad."):
            i = 0 if key.endswith(".x") else 1
            v = pad_size[i] if len(pad_size) > i else (pad_size[0] if pad_size else None)
        else:
            v = arr.get(key)
        return None if v is None else float(v)

    for key in DIM_KEYS:
        if _drawing_of(key) is None and not (spec.get("dim_tables") or {}).get(key):
            continue                      # 该件没这个尺寸，跳过
        v, d, w = resolve_dim(key, spec, drawing=_drawing_of(key))
        out[key] = v
        deriv.append(d)
        if w:
            warn.append(w)
    return out, deriv, warn


def _resolved(spec: Dict[str, Any], key: str, fallback: float) -> float:
    """从 `spec["_resolved"]` 取已解析的尺寸；没有就退回原值（便于单函数直接调用）。"""
    v = (spec.get("_resolved") or {}).get(key)
    return float(fallback) if v is None else float(v)


def _centers(count: int, pitch: float, *, top_to_bottom: bool = False) -> List[float]:
    """
    count 个等间距点的中心对称坐标（原点在阵列几何中心）。

    count=2, pitch=2.2            -> [-1.1, 1.1]
    count=4, pitch=1.0, top->bottom -> [1.5, 0.5, -0.5, -1.5]
    """
    offs = [(i - (count - 1) / 2.0) * float(pitch) for i in range(count)]
    if top_to_bottom:
        offs.reverse()
    return [_r4(o) for o in offs]


# ============================================================
# 1. 命名：数字 ↔ 规范写法
# ============================================================
# [规范 一] "小数点用字母 d 代替"
#   1.4 -> "1D4"   3.2 -> "3D2"   0.7 -> "0D7"   0.85 -> "0D85"   0.4 -> "0D4"
#   整数原样：      2.0 -> "2"     8.0 -> "8"        （规范例 NB_BGA64_8X8X1_1）
def name_num(v: float) -> str:
    """
    [规范 一] 数值 → 命名段：小数点换成 `D`，整数不带小数部分。

    >>> name_num(1.4), name_num(0.85), name_num(0.4), name_num(2.0)
    ('1D4', '0D85', '0D4', '2')
    """
    f = _r4(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    s = f"{f:.4f}".rstrip("0").rstrip(".")
    int_part, frac = s.split(".")
    return f"{int(int_part)}D{frac}"


def parse_name_num(s: str) -> float:
    """
    命名段 → 数值（`name_num` 的逆）。与 rule_checker 2.1 的反解同源。

    >>> parse_name_num("1D4"), parse_name_num("0D85"), parse_name_num("2")
    (1.4, 0.85, 2.0)
    """
    s = str(s).strip()
    m = _re_fullmatch_num(s)
    if not m:
        raise ValueError(f"非法命名数值段: {s!r}")
    head, frac = m.group(1), m.group(2)
    return float(f"{head}.{frac}") if frac else float(head)


def _re_fullmatch_num(s: str):
    import re
    return re.fullmatch(r"(\d+)(?:[Dd](\d+))?", s)


# 形状 → 命名首字符 + Allegro figure 名
#   [规范 二] r长x宽（Rectangle）、c直径（Circle）、sq边长（Square）、ob…（Oblong）
_SHAPE = {
    "rect": ("R", "RECTANGLE"), "rectangle": ("R", "RECTANGLE"),
    "circle": ("C", "CIRCLE"), "round": ("C", "CIRCLE"),
    "square": ("SQ", "SQUARE"), "sq": ("SQ", "SQUARE"),
    "oblong": ("OB", "OBLONG"), "oval": ("OB", "OBLONG"), "ob": ("OB", "OBLONG"),
}


def solve_padstack_name(size: Sequence[float], shape: str = "rect") -> str:
    """
    [规范 二] 焊盘命名。

    [1.4, 1.2] rect   -> "R1D4X1D2"   （与金标准 .pad 名一致）
    [0.85, 0.85] circle -> "C0D85"
    [0.4, 0.3] oblong -> "OB0D4X0D3"
    """
    key = str(shape).lower()
    if key not in _SHAPE:
        raise ValueError(f"未知焊盘形状: {shape!r}（可选 {sorted(_SHAPE)}）")
    ch, _fig = _SHAPE[key]
    dims = [float(v) for v in size]
    if ch in ("C", "SQ"):                     # 单一尺寸
        return f"{ch}{name_num(dims[0])}"
    return f"{ch}{name_num(dims[0])}X{name_num(dims[1])}"


def solve_symbol_name(
    symbol_base: str,
    pin_count: int,
    body: Dict[str, float],
    *,
    pitch: Optional[float | Sequence[float]] = None,
    suffix: str = "",
    prefix: Optional[str] = None,
    rules: Optional[Dict[str, Any]] = None,
) -> str:
    """
    [规范 一] 封装命名：<前缀><类型><引脚数>_<长>X<宽>[X<高>][_<pitch>][_<特殊>]

    例：NB_XTAL4_3D2X2D5X0D7  ← base="XTAL" count=4 body=(3.2,2.5,0.7)
        NB_SOP8_2X1D5X3_0D5   ← base="SOP"  count=8 body=(2,1.5,3) pitch=0.5

    ★ [规范 一] 命名用**中值**（画实体才用最大值）——
      所以 body 传中值，几何用 `spec["use_max_for_geometry"]` 另行控制。
    """
    rules = rules or DEFAULT_RULES
    prefix = rules["naming"].get("prefix", "NB_") if prefix is None else prefix
    base = str(symbol_base).strip()

    dims = [body[k] for k in ("x", "y", "z") if body.get(k) is not None]
    if not dims:
        raise ValueError("body 至少要给 x / y")
    seg = "X".join(name_num(d) for d in dims)

    name = f"{prefix}{base}{int(pin_count)}_{seg}"

    if pitch is not None:
        if isinstance(pitch, (list, tuple, set)):
            pits = list(pitch)
        else:
            pits = [pitch]
        if pits:
            name += "_" + "X".join(name_num(p) for p in pits)

    if suffix:
        name += f"_{str(suffix).strip()}"

    if rules["naming"].get("no_dot", True) and "." in name:
        raise AssertionError(f"命名里出现了小数点（违反 [规范 一]）：{name}")
    return name


# ============================================================
# 2. 求解器：padstack
# ============================================================
def solve_padstack(
    spec: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    参数 → padstack 定义（含阻焊 / 钢网层）。

    [规范 三] 常规焊盘阻焊开窗单边比焊盘大 0.05~0.1mm → 取规则表值 0.05
    [规范 三] 常规 Chip 元件钢网开窗与焊盘等大       → PASTEMASK = TOP
    [规范 三] 散热焊盘（GND Pad）需为 NP Pad         → spec["pad"]["non_plated"]
    """
    rules = rules or DEFAULT_RULES
    pad = spec["pad"]
    shape = str(pad.get("shape", "rect")).lower()
    if shape not in _SHAPE:
        raise ValueError(f"未知焊盘形状: {shape!r}（可选 {sorted(_SHAPE)}）")
    _ch, fig = _SHAPE[shape]

    size = [_resolved(spec, "pad.x", float(pad["size"][0])),
            _resolved(spec, "pad.y", float(pad["size"][-1]))]
    if fig in ("CIRCLE", "SQUARE"):          # 圆形/方形只有一个尺寸
        size = [size[0], size[0]]
    if len(size) < 2:
        size = [size[0], size[0]]

    sm = float(rules["pad"].get("soldermask_expand_mm", 0.05))

    layers: List[Dict[str, Any]] = [
        {"layer": "TOP", "figure": fig, "size": [_r4(size[0]), _r4(size[1])]},
        {"layer": "SOLDERMASK_TOP", "figure": fig,
         "size": [_r4(size[0] + 2 * sm), _r4(size[1] + 2 * sm)]},
    ]
    if rules["pad"].get("pastemask_equal_pad", True):
        layers.append({"layer": "PASTEMASK_TOP", "figure": fig,
                       "size": [_r4(size[0]), _r4(size[1])]})

    # 通孔：内层 pad = 钻孔 + 0.6（[规范 二] 螺丝孔设计）
    drill = None
    mount = str(spec.get("mount", "smd")).lower()
    if mount in ("tht", "through", "through_hole", "pth"):
        # ★ 槽孔（异形孔）通孔件：焊盘尺寸由规格书直接给出，
        #   不能再用 drill+料环 那个"圆形螺丝孔"估算式 —— 那个式子只会产出圆盘。
        #   给了 `pad["drill_size"]` 就走这一支：焊盘沿用自身 figure/size，
        #   只加 SOLDERMASK，不加 PASTEMASK（通孔件不刷锡膏）。
        dsz = pad.get("drill_size")
        if dsz:
            drill = {
                "size": [_r4(float(dsz[0])), _r4(float(dsz[1]))],
                "drill_type": str(pad.get("drill_type", "OBLONG_DRILL")),
                "plated": not pad.get("non_plated", False),
                "offset": [0, 0],
            }
            layers = [
                {"layer": "TOP", "figure": fig,
                 "size": [_r4(size[0]), _r4(size[1])]},
                {"layer": "SOLDERMASK_TOP", "figure": fig,
                 "size": [_r4(size[0] + 2 * sm), _r4(size[1] + 2 * sm)]},
            ]
        else:
            d = float(spec.get("drill_diameter") or pad.get("drill") or 0.3)
            ann = float(pad.get("annular_extra_mm", 0.6))
            drill = {"diameter": _r4(d), "plated": not pad.get("non_plated", False)}
            layers = [
                {"layer": "TOP", "figure": fig, "size": [_r4(d + ann), _r4(d + ann)]},
                {"layer": "SOLDERMASK_TOP", "figure": fig,
                 "size": [_r4(d + ann + 2 * sm), _r4(d + ann + 2 * sm)]},
            ]

    pd = {
        "name": solve_padstack_name(size, shape),
        "layers": layers,
        "drill": drill,
    }
    deriv = [
        {"item": "padstack.name", "value": pd["name"],
         "formula": f"{_SHAPE[shape][0]}({name_num(size[0])})X({name_num(size[1])})",
         "source": "[规范 二] 焊盘命名 r长x宽"},
        {"item": "padstack.layers[1]", "value": [size[0] + 2 * sm, size[1] + 2 * sm],
         "formula": f"TOP + 2×{sm}",
         "source": "[规范 三] 阻焊开窗单边 +0.05"},
        {"item": "padstack.layers[2]", "value": [size[0], size[1]],
         "formula": "= TOP",
         "source": "[规范 三] 钢网与焊盘等大"},
    ]
    return pd, deriv


# ============================================================
# 3. 求解器：pin 坐标（含编号走向）
# ============================================================
# [规范 四] 原点在封装中心。编号走向由规格书 TOP VIEW 决定。
_CCW_FROM = {                     # 2×2 方阵：从起点逆时针绕行 (sign_x, sign_y)
    "bottom-left":  [(-1, -1), (1, -1), (1, 1), (-1, 1)],
    "bottom-right": [(1, -1), (1, 1), (-1, 1), (-1, -1)],
    "top-right":    [(1, 1), (-1, 1), (-1, -1), (1, -1)],
    "top-left":     [(-1, 1), (1, 1), (1, -1), (-1, -1)],
}


def solve_pins(
    spec: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """
    阵列参数 → 每个 pin 的绝对坐标（mm，原点=封装中心）。

    `array` 语义：**rows = 沿 y 方向的个数，cols = 沿 x 方向的个数**。
      3S48000163（4 角）      -> rows=2, cols=2, pitch_x=2.2, pitch_y=1.7
      SOP8（左右两列各 4 个）  -> rows=4, cols=2, pitch_x=5.0, pitch_y=1.27

    拓扑与编号走向：
      * `cols==2 and rows==2` —— 方阵 4 角：逆时针绕行，
        起点由 `spec["pin1_corner"]` 指定（默认 bottom-left → 3S48000163 规格书实测）
      * `cols==2 and rows>2`  —— 左右两列（DIP/SOP 标准）：左列自下而上，
        右列自上而下
      * `rows==2 and cols>2`  —— 上下两排：下排从左到右，上排从右到左
      * 其它                  —— 按行从左到右（兜底，会告警）

    :return: (pins, derivations, warnings)

    ★ **显式引脚表**（2026-09-30 加，FPC 连接器等非规则阵列用）：
      `spec["pins_override"] = [{"number","x","y","padstack"?,\"pad_size\"?}, …]`
      给了就**直接采用**，不再走 rows×cols 阵列 —— 因为"信号脚 + 固定脚"
      这类器件的焊盘尺寸/行距都不统一，规则阵列表达不了。
      `padstack` = 该脚用的 padstack 名（None → 主 padstack）；
      `pad_size` = 该脚焊盘尺寸 [w,h]（None → 主 pad 尺寸，含阻焊计算用）。
    """
    arr = spec.get("array") or {}
    rows = int(arr.get("rows", 0))
    cols = int(arr.get("cols", 0))

    warnings: List[str] = []
    deriv: List[Dict[str, Any]] = []

    # ---- 0) ★ 显式引脚表优先 ----
    ov = spec.get("pins_override")
    if ov:
        pins: List[Dict[str, Any]] = []
        for i, p in enumerate(ov, start=1):
            pins.append({
                "number": str(p.get("number", i)),
                "x": _r4(float(p["x"])), "y": _r4(float(p["y"])),
                "padstack": p.get("padstack"),
                "pad_size": list(p["pad_size"]) if p.get("pad_size") else None,
            })
        declared = spec.get("pin_count")
        if declared is not None and int(declared) != len(pins):
            warnings.append(
                f"spec['pin_count']={declared} 与 pins_override 的 {len(pins)} 个不一致")
        deriv.append({
            "item": "pins.order", "value": f"{len(pins)} 个（显式引脚表）",
            "formula": "pins_override 逐条采用",
            "source": "非规则阵列（信号脚+固定脚）—— 规格书 RECOMMEND PCB LAYOUT",
        })
        return pins, deriv, warnings

    if rows <= 0 or cols <= 0:
        raise ValueError("spec['array'] 必须给 rows / cols（>0）")

    px = _resolved(spec, "pitch_x", float(arr.get("pitch_x", 0.0)))
    py = _resolved(spec, "pitch_y", float(arr.get("pitch_y", 0.0)))
    if px <= 0 or py <= 0:
        raise ValueError("spec['array'] 必须给正的 pitch_x / pitch_y（单位 mm）")

    # x 坐标（按列，左→右）；y 坐标（按行，上→下）
    xs = _centers(cols, px)
    ys_top2bottom = _centers(rows, py, top_to_bottom=True)

    pins: List[Dict[str, Any]] = []
    warnings: List[str] = []
    deriv: List[Dict[str, Any]] = []

    if rows == 2 and cols == 2:
        corner = str(spec.get("pin1_corner", "bottom-left")).lower()
        if corner not in _CCW_FROM:
            raise ValueError(f"未知 pin1_corner: {corner!r}（可选 {sorted(_CCW_FROM)}）")
        for idx, (sx, sy) in enumerate(_CCW_FROM[corner], start=1):
            ci = 0 if sx < 0 else cols - 1
            ri = rows - 1 if sy < 0 else 0
            pins.append({"number": str(idx),
                         "x": _r4(xs[ci]), "y": _r4(ys_top2bottom[ri])})
        deriv.append({
            "item": "pins.order", "value": f"2×2 逆时针，起点 {corner}",
            "formula": "左下→右下→右上→左上",
            "source": "规格书 p5 CONNECTION DIAGRAM (TOP VIEW)：#1左下/#2右下/#3右上/#4左上",
        })
    elif cols == 2 and rows > 2:
        # 左右两列（DIP / SOP 标准走向）：左列自下而上 → 右列自上而下
        seq: List[Tuple[int, int]] = []
        seq += [(0, ri) for ri in range(rows - 1, -1, -1)]        # 左列 下→上
        seq += [(cols - 1, ri) for ri in range(rows)]             # 右列 上→下
        for idx, (ci, ri) in enumerate(seq, start=1):
            pins.append({"number": str(idx),
                         "x": _r4(xs[ci]), "y": _r4(ys_top2bottom[ri])})
        deriv.append({
            "item": "pins.order", "value": f"{rows}×2 左右两列 DIP/SOP 走向",
            "formula": "左列自下而上 → 右列自上而下",
            "source": "[规范 四] + DIP/SOP 通用惯例",
        })
    elif rows == 2 and cols > 2:
        # 上下两排：下排从左到右 → 上排从右到左
        seq = [(ci, rows - 1) for ci in range(cols)] + \
              [(ci, 0) for ci in range(cols - 1, -1, -1)]
        for idx, (ci, ri) in enumerate(seq, start=1):
            pins.append({"number": str(idx),
                         "x": _r4(xs[ci]), "y": _r4(ys_top2bottom[ri])})
        deriv.append({
            "item": "pins.order", "value": f"2×{cols} 上下两排",
            "formula": "下排从左到右 → 上排从右到左",
            "source": "[规范 四] + 排针通用惯例",
        })
    else:
        # 单排或其它：按行从左到右
        for idx in range(rows * cols):
            ci, ri = (idx % cols, idx // cols) if rows == 1 else (idx // rows, idx % rows)
            pins.append({"number": str(idx + 1),
                         "x": _r4(xs[ci]), "y": _r4(ys_top2bottom[ri])})
        deriv.append({"item": "pins.order", "value": "按行从左到右",
                      "formula": f"{rows}×{cols}", "source": "兜底（待按规格书细化）"})
        warnings.append(f"拓扑 {rows}×{cols} 使用的是兜底编号走向，"
                        f"请对照规格书 TOP VIEW 核实 pin 编号。")

    # pin_count 交叉校验
    declared = spec.get("pin_count")
    if declared is not None and int(declared) != len(pins):
        warnings.append(
            f"spec['pin_count']={declared} 与阵列算出的 {rows}×{cols}={len(pins)} 不一致")

    deriv.append({
        "item": "pins.coords", "value": f"{len(pins)} 个",
        "formula": f"x=±pitch_x×(cols-1)/2={_r4(px*(cols-1)/2)}，"
                   f"y=±pitch_y×(rows-1)/2={_r4(py*(rows-1)/2)}",
        "source": "[规范 四] 原点在封装中心",
    })
    return pins, deriv, warnings


# ============================================================
# 4. 求解器：本体 / Place_Bound 外框
# ============================================================
def solve_outlines(
    spec: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """
    参数 → (body 矩形, place_bound 矩形, pad bbox, derivations, warnings)

    ★★ 最容易写错的一处：**Place_Bound 的基准是「器件本体」，不是焊盘阵列。**
       [规范 三] "按封装本体实际尺寸 + 焊盘单边外扩 0.35mm"
       金标准 5.2 实证 pb=3.9×3.2，device=3.2×2.5，expand=0.35 → 3.2+0.7=3.9 ✓

    外扩量按元件类型取 [规则] `place_bound.*_expand_mm`：
       ic 0.35 / chip 0.15 / connector 0.85
    """
    rules = rules or DEFAULT_RULES
    body = spec["body"]
    if float(body["x"]) <= 0 or float(body["y"]) <= 0:
        raise ValueError("body.x / body.y 必须为正（单位 mm）")

    # [规范 一] **命名用中值**（永远）；**画实体**取哪个值按「尺寸来源」定
    #   （2026-09-29 用户口径，实现见 resolve_dim）：
    #     · 图上标注了具体值 → 取**标注值本身**
    #       （3S48000163 p5 直接标 `3.2±0.1` → 本体 3.2×2.5，**不**加上偏差）
    #     · 图上没标、需查表 → 取表 **MAX**（无 MAX 用 TYP + 告警）
    #   ★ 旧的「有 recommended 用中值 / 没标用中值+公差」已被取代；
    #     `spec["use_max_for_geometry"]=True` 仍能强制走旧口径（仅供对照）。
    warnings: List[str] = []
    bx = _resolved(spec, "body.x", float(body["x"]))
    by = _resolved(spec, "body.y", float(body["y"]))
    use_max = bool(spec.get("use_max_for_geometry", False))

    # 元件类型 → 外扩量
    ctype = spec.get("component_type")
    if not ctype:
        hint = spec.get("symbol_name_hint") or spec.get("symbol_base") or ""
        ctype = _classify_component(str(hint))
    key = f"{ctype}_expand_mm"
    pb_cfg = rules["place_bound"]
    # ★ 逐件覆盖优先：某些件的外伸焊盘比本体宽得多（音频插座、
    #   带固定脚的连接器…），按"本体 + 类型默认外扩"算出来的 Place_Bound
    #   根本盖不住焊盘 —— 这时用 spec["place_bound_expand_mm"] 显式给值。
    if spec.get("place_bound_expand_mm") is not None:
        expand = float(spec["place_bound_expand_mm"])
    else:
        expand = pb_cfg.get(key)
    if expand is None:
        expand = pb_cfg.get("ic_expand_mm", 0.35)
        warnings.append(f"元件类型 '{ctype}' 无对应外扩规则，已按 ic 的 "
                        f"{expand}mm 处理 —— 请人工确认。")
    expand = float(expand)

    dev = [_r4(bx), _r4(by)]
    pb = [_r4(bx + 2 * expand), _r4(by + 2 * expand)]

    # ★ 本体中心在"成品坐标系"里的位置（默认原点）。
    #   绝大多数芯片类器件本体居中于原点，所以默认 [0, 0] 不用管。
    #   但**连接器 / 插座**这类件的原点常取在「本体上边缘 × 中心线」
    #   （规格书就是这么标注的，pins_override 也按这个基准写），
    #   此时本体中心必然不在原点 —— 用 body_center 把本体/Place_Bound 摆正。
    #   ★ 语义：body_center = 本体中心的坐标（不是偏移量）。
    _bc = spec.get("body_center") or (0.0, 0.0)
    ocx, ocy = _r4(float(_bc[0])), _r4(float(_bc[1]))

    body_rect = {"x1": _r4(_half(-dev[0]) + ocx), "y1": _r4(_half(-dev[1]) + ocy),
                 "x2": _r4(_half(dev[0]) + ocx), "y2": _r4(_half(dev[1]) + ocy),
                 "width": float(rules.get("assembly", {}).get("line_width_mm", 0.10))}
    pb_rect = {"x1": _r4(_half(-pb[0]) + ocx), "y1": _r4(_half(-pb[1]) + ocy),
               "x2": _r4(_half(pb[0]) + ocx), "y2": _r4(_half(pb[1]) + ocy)}

    # ---- ★ 浮点贴边防护（2026-10-12 加，任务 #3）----
    #   Place_Bound 边缘与焊盘**阻焊边缘**「差 < 0.01mm」时，规则检查的
    #   覆盖/间隙浮点比较必 FAIL —— LMBR 0.35→0.40、LYDPJ 1.85→1.90
    #   两次实机应验。此处自动把外扩再让 0.05，杜绝复发。
    #   对拍用 solve_pad_rects（每脚含阻焊的实际矩形）——数组件与
    #   pins_override 连接器统一覆盖，不受 array 兜底记录失真的影响。
    _guard_note = None
    try:
        _prects = solve_pad_rects(spec, rules)
    except Exception:                            # noqa: BLE001
        _prects = []
    if _prects:
        _pbb = {
            "x1": min(r["x1"] for r in _prects), "x2": max(r["x2"] for r in _prects),
            "y1": min(r["y1"] for r in _prects), "y2": max(r["y2"] for r in _prects),
        }
        for _ in range(20):                      # 上限防死循环（每次 +0.05）
            _touch = any(
                abs(pb_rect[_k] - _pbb[_k]) < 0.01
                for _k in ("x1", "x2", "y1", "y2")
            )
            if not _touch:
                break
            expand = _r4(expand + 0.05)
            pb = [_r4(bx + 2 * expand), _r4(by + 2 * expand)]
            pb_rect = {"x1": _r4(_half(-pb[0]) + ocx), "y1": _r4(_half(-pb[1]) + ocy),
                       "x2": _r4(_half(pb[0]) + ocx), "y2": _r4(_half(pb[1]) + ocy)}
            _guard_note = ("浮点贴边防护：Place_Bound 边缘与焊盘阻焊边差 <0.01mm，"
                           "外扩自动 +0.05（2026-10-12 加，LMBR/LYDPJ 两次应验）")
    if _guard_note:
        warnings.append(f"⚠️ {_guard_note}")
    _bc_note = None
    if ocx or ocy:
        _bc_note = {
            "item": "body_center", "value": [ocx, ocy],
            "formula": "spec['body_center']（本体中心在成品坐标系中的位置）",
            "source": "连接器类：原点常取「本体上边缘 × 中心线」，"
                      "本体中心随之偏离原点（规格书标注基准）",
        }

    # 焊盘阵列 bbox（含阻焊外扩）—— 供丝印避让与覆盖校验用
    arr = spec.get("array") or {}
    pad_size = [_resolved(spec, "pad.x", float(spec["pad"]["size"][0])),
                _resolved(spec, "pad.y", float(spec["pad"]["size"][-1]))]
    if len(pad_size) < 2:
        pad_size = [pad_size[0], pad_size[0]]
    sm = float(rules["pad"].get("soldermask_expand_mm", 0.05))
    span_x = float(arr.get("pitch_x", 0)) * (int(arr.get("cols", 2)) - 1) + pad_size[0] + 2 * sm
    span_y = float(arr.get("pitch_y", 0)) * (int(arr.get("rows", 2)) - 1) + pad_size[1] + 2 * sm
    pad_bbox = {"span": [_r4(span_x), _r4(span_y)],
                "half": [_half(span_x), _half(span_y)]}

    if pb[0] < pad_bbox["span"][0] or pb[1] < pad_bbox["span"][1]:
        warnings.append(
            f"⚠️ Place_Bound（{pb[0]}×{pb[1]}）比焊盘阵列（含阻焊 {pad_bbox['span'][0]}×"
            f"{pad_bbox['span'][1]}）还小 —— 违反 [规范 三] '需覆盖所有焊盘'。"
            f"多半是本体尺寸小于焊盘跨距（3.2×2.5 本体 vs 3.6×2.9 焊盘跨距即是此例），"
            f"建议改用 '本体 + 外扩' 与 '焊盘 bbox + 外扩' 取大者。")

    _src_note = ("图上标注值" if not use_max
                 else "图上标注值的上偏差（遗留 use_max_for_geometry）")
    deriv = [
        {"item": "body(assembly)", "value": dev,
         "formula": f"本体 {dev[0]}×{dev[1]}（{_src_note}）",
         "source": "[规范 三] Assembly 与本体一致 ±0.05；[规范 一] 命名用中值 / "
                   "画实体按用户口径：图上标注→取标注值；查表→取 MAX"},
        {"item": "place_bound.expand", "value": expand,
         "formula": f"{ctype}_expand_mm",
         "source": "[规范 三] 本体 + 单边外扩（ic 0.35 / chip 0.15 / connector 0.85）"},
        {"item": "place_bound.size", "value": pb,
         "formula": f"{dev[0]}+2×{expand} × {dev[1]}+2×{expand}",
         "source": f"基准=本体（{_src_note}）；"
                   f"金标准 5.2 实测 3.9×3.2 同此口径"},
        {"item": "pad_array.bbox(含阻焊)", "value": pad_bbox["span"],
         "formula": f"pitch×({int(arr.get('cols',2))}-1)+pad+2×{sm} 等",
         "source": "用于丝印避让 / Place_Bound 覆盖校验"},
    ]
    if _bc_note:
        deriv.append(_bc_note)
    if _guard_note:
        deriv.append({
            "item": "place_bound.float_guard", "value": expand,
            "formula": f"{ctype}_expand_mm + 0.05×n（贴边自动让位）",
            "source": _guard_note,
        })
    return body_rect, pb_rect, pad_bbox, deriv, warnings


# ============================================================
# 5. 求解器：丝印
# ============================================================
def solve_pad_rects(
    spec: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    每个引脚焊盘（**含阻焊开窗**）的矩形。

    丝印避让、1 脚标识定位、Place_Bound 覆盖校验都要用，
    所以单独拎出来做一等输入 —— 之前这几处各自重算了一遍，容易算歪。

    ★ `spec["pad"]["rotation"]` 支持 90/270：
      金标准那份实物就是把 1.4×1.2 的 padstack **旋转 90°** 放的
      （实测板上焊盘是 1.2×1.4 —— 与规格书 LAND PATTERN 的 1.4×1.2 不符，
       见该件缺陷说明）。这里显式支持旋转，别再用"反正差不多"糊过去。
    """
    rules = rules or DEFAULT_RULES
    pins, _, _ = solve_pins(spec)
    ps = [_resolved(spec, "pad.x", float(spec["pad"]["size"][0])),
          _resolved(spec, "pad.y", float(spec["pad"]["size"][-1]))]
    if len(ps) < 2:
        ps = [ps[0], ps[0]]
    rot = float(spec["pad"].get("rotation", 0) or 0) % 180.0
    if abs(rot - 90.0) < 1e-9:
        ps = [ps[1], ps[0]]
    sm = float(rules["pad"].get("soldermask_expand_mm", 0.05))
    hx, hy = ps[0] / 2.0 + sm, ps[1] / 2.0 + sm
    rects: List[Dict[str, Any]] = []
    for p in pins:
        x, y = float(p["x"]), float(p["y"])
        # ★ 显式引脚表可带每脚 pad_size（固定脚与信号脚尺寸不同）
        if p.get("pad_size"):
            w, h = float(p["pad_size"][0]), float(p["pad_size"][-1])
            if abs(rot - 90.0) < 1e-9:
                w, h = h, w
            _hx, _hy = w / 2.0 + sm, h / 2.0 + sm
        else:
            _hx, _hy = hx, hy
        rects.append({
            "pin": str(p["number"]),
            "x1": _r4(x - _hx), "y1": _r4(y - _hy),
            "x2": _r4(x + _hx), "y2": _r4(y + _hy),
        })
    return rects


def _clip_span(
    lo: float, hi: float, cuts: Sequence[Tuple[float, float]], min_len: float,
) -> List[Tuple[float, float]]:
    """
    一维区间 [lo,hi] 挖掉 cuts=[(a,b),…] 后剩下的段（只保留长度 > min_len 的）。

    >>> _clip_span(-1.6, 1.6, [(-2.05, -0.15), (0.15, 2.05)], 0.05)
    [(-0.15, 0.15)]
    """
    segs: List[Tuple[float, float]] = [(float(lo), float(hi))]
    for a, b in cuts:
        a, b = float(a), float(b)
        nxt: List[Tuple[float, float]] = []
        for s0, s1 in segs:
            if b <= s0 + 1e-12 or a >= s1 - 1e-12:      # 不相交
                nxt.append((s0, s1))
                continue
            if a > s0 + 1e-12:                          # 左残段
                nxt.append((s0, a))
            if b < s1 - 1e-12:                          # 右残段
                nxt.append((b, s1))
        segs = nxt
    return [(a, b) for a, b in segs if (b - a) > min_len + 1e-12]


def solve_silk_outline(
    spec: Dict[str, Any],
    pad_rects: Sequence[Dict[str, Any]],
    body_rect: Dict[str, Any],
    *,
    clearance: float,
    width: float,
    min_len: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """
    本体 4 条边**逐边裁剪** → 丝印残段。

    ★★ 核心规则（金标准在线反解，2026-09-29）：
       边上的某段，只要与任一「焊盘(含阻焊) 矩形外扩 clearance」相交，
       就必须挖掉 —— 因为丝印不得进入阻焊开窗区（[规范 四]）。

    金标准实测吻合：
       上/下边 y=±1.25 落在上下两排焊盘的 y 范围内 → 让位，
       焊盘 x 占 [±0.45,±1.75] 外扩 0.20 → 只剩 x∈[-0.25,0.25]
       → 实测短横线 (-0.24,1.25)→(0.23,1.25) ✓
       左/右边 x=±1.6 整段压在焊盘上 → 一根本都不剩 ✓
    """
    min_len = float(spec.get("silk_min_seg_len", 0.05)
                    if min_len is None else min_len)
    bx1, by1 = float(body_rect["x1"]), float(body_rect["y1"])
    bx2, by2 = float(body_rect["x2"]), float(body_rect["y2"])

    lines: List[List[List[float]]] = []
    per_edge: List[Dict[str, Any]] = []

    # ---- 水平两条边（下 / 上）：在 x 上裁剪 ----
    for tag, ye in (("bottom", by1), ("top", by2)):
        cuts = [(float(r["x1"]) - clearance, float(r["x2"]) + clearance)
                for r in pad_rects
                if (float(r["y1"]) - clearance) <= ye <= (float(r["y2"]) + clearance)]
        segs = _clip_span(bx1, bx2, cuts, min_len)
        for a, b in segs:
            lines.append([[_r4(a), _r4(ye)], [_r4(b), _r4(ye)]])
        per_edge.append({"edge": tag, "at": _r4(ye), "axis": "x",
                         "blocked_by": len(cuts), "kept": len(segs)})

    # ---- 垂直两条边（左 / 右）：在 y 上裁剪 ----
    for tag, xe in (("left", bx1), ("right", bx2)):
        cuts = [(float(r["y1"]) - clearance, float(r["y2"]) + clearance)
                for r in pad_rects
                if (float(r["x1"]) - clearance) <= xe <= (float(r["x2"]) + clearance)]
        segs = _clip_span(by1, by2, cuts, min_len)
        for a, b in segs:
            lines.append([[_r4(xe), _r4(a)], [_r4(xe), _r4(b)]])
        per_edge.append({"edge": tag, "at": _r4(xe), "axis": "y",
                         "blocked_by": len(cuts), "kept": len(segs)})

    items = [{"kind": "outline", "lines": [seg], "width": width}
             for seg in lines]
    warnings: List[str] = []
    if not items:
        warnings.append(
            "⚠️ 本体 4 条边被焊盘**全部占满**（含 "
            f"{clearance}mm 避让后无残留）—— 丝印无法沿本体绘制，"
            "已退回「焊盘(含阻焊) bbox 外缘 + 0.06」包框，请人工确认。")
    return items, per_edge, warnings


def solve_pin1_mark(
    spec: Dict[str, Any],
    pad_rects: Sequence[Dict[str, Any]],
    *,
    width: float,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], List[str]]:
    """
    1 脚标识 —— **三角形**（[规范 四] IC / 多引脚必做）。

    ★ 形态同样从金标准反解：
       三角落在 1 脚焊盘**外侧**（沿 y），底边平行于焊盘外缘；
       顶点朝向器件（贴近焊盘），底边朝外。
       金标准实测：apex(-1.09,-1.70)、base (-1.40,-2.01)→(-0.81,-2.01)
         = 焊盘(含阻焊) 下缘 -1.60 再外移 0.10 → -1.70；底边再外移 0.30
         = 宽 0.60 × 高 0.30，中心对齐 1 脚 x (-1.10)

    ★ 该三角在**丝印和 Assembly 两层都有**（金标准两层实测一致）。

    :return: (item, info, warnings)
    """
    warnings: List[str] = []
    if spec.get("pin1_mark") is False:
        return None, {"style": "none", "note": "调用方显式关闭"}, []

    corner = str(spec.get("pin1_corner", "bottom-left")).lower()
    sx = -1 if "left" in corner else 1
    sy = -1 if "bottom" in corner else 1

    r1 = next((r for r in pad_rects if str(r.get("pin")) == "1"), None)
    if r1 is None:
        warnings.append("未找到 pin 1 的焊盘矩形 —— 1 脚标识跳过。")
        return None, {"style": "none", "note": "缺 pin1 焊盘"}, warnings

    clr = float(spec.get("pin1_mark_clearance", 0.10))
    W = float(spec.get("pin1_mark_width", 0.60))
    H = float(spec.get("pin1_mark_height", 0.30))
    axis = str(spec.get("pin1_mark_axis", "y")).lower()   # 金标准走的是 "y"
    cx = float(spec.get("pin1_mark_center",
                        (float(r1["x1"]) + float(r1["x2"])) / 2.0))

    if axis == "y":
        near = (float(r1["y1"]) - clr) if sy < 0 else (float(r1["y2"]) + clr)
        far = near - H if sy < 0 else near + H
        apex = [_r4(cx), _r4(near)]
        b_l = [_r4(cx - W / 2.0), _r4(far)]
        b_r = [_r4(cx + W / 2.0), _r4(far)]
    else:                                                  # 沿 x 外侧
        near = (float(r1["x1"]) - clr) if sx < 0 else (float(r1["x2"]) + clr)
        far = near - H if sx < 0 else near + H
        apex = [_r4(near), _r4(cx)]
        b_l = [_r4(far), _r4(cx - W / 2.0)]
        b_r = [_r4(far), _r4(cx + W / 2.0)]

    segs = [[apex, b_l], [b_l, b_r], [b_r, apex]]
    item = {"kind": "pin1_mark", "lines": segs, "width": width,
            "corner": corner}
    info = {"style": "triangle", "corner": corner, "axis": axis,
            "apex": apex, "base": [b_l, b_r], "segments": segs,
            "size": [_r4(W), _r4(H)], "clearance": clr,
            "layers": list(spec.get("pin1_mark_layers",
                                    ["silkscreen", "assembly"]))}
    return item, info, warnings


def solve_silkscreen(
    spec: Dict[str, Any],
    pad_bbox: Dict[str, Any],
    place_bound: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
    pad_rects: Optional[Sequence[Dict[str, Any]]] = None,
    body_rect: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """
    参数 → 丝印图元列表。

    ★★ 形态从金标准**在线反解**得出（2026-09-29 读金标准 .dra 的
       `PACKAGE GEOMETRY/SILKSCREEN_TOP`，该层只有 3 个元素）：

         [0] 三角形（1 脚标识）
             (-1.09,-1.70)→(-1.40,-2.01)→(-0.81,-2.01)→(-0.81,-1.98)→(-1.09,-1.70)
         [1] 短横线 (-0.24,-1.25)→(0.23,-1.25)   ← 本体下边 y=-1.25 的残段
         [2] 短横线 (-0.24, 1.25)→(0.23, 1.25)   ← 本体上边 y=+1.25 的残段

       → **丝印不是整框**，而是 **「本体轮廓 + 逐边避让裁剪」**：
         本体 4 条边各自挖掉「与焊盘(含阻焊) + clearance 冲突」的区间，
         剩下的残段才画。

    :param body_rect: 画丝印用的本体矩形。**默认 None 时取 spec 的中值本体**；
       上层 `solve()` 会传 Assembly 用的那个本体 —— 保证
       **丝印与 Assembly 同源**，不会出现"丝印 2.5、Assembly 2.6"的打架。

    策略（`spec["silk_style"]`）：
      "auto"   本体轮廓 + 逐边避让裁剪（默认，= 金标准形态）
      "frame"  强制本体整框（压焊盘也照画，只告警）
      "none"   不画
      兜底      4 条边被焊盘挖光时，退回「焊盘(含阻焊) bbox + 0.06」包框并告警

    [规范 四] 丝印线宽 >=0.10；丝印不得进入阻焊开窗区（间距 >= 0.20）；
              1 脚标识（IC / 多引脚必做）。
    """
    rules = rules or DEFAULT_RULES
    cfg = rules["silkscreen"]
    w_min = float(cfg.get("line_width_min_mm", 0.10))
    clearance = float(spec.get("silk_pad_clearance",
                               cfg.get("to_pad_clearance_mm", 0.20)))
    width = float(spec.get("silk_width", max(w_min, 0.15)))
    style = str(spec.get("silk_style", "auto")).lower()

    deriv: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if style == "none":
        return [], {"style": "none"}, deriv, warnings

    if width < w_min - 1e-9:
        warnings.append(f"丝印线宽 {width} 小于规范下限 {w_min}（[规范 四]）—— 已抬到下限。")
        width = w_min

    rects = list(pad_rects) if pad_rects is not None else solve_pad_rects(spec, rules)
    if body_rect is None:
        body = spec["body"]
        bh = [_half(float(body["x"])), _half(float(body["y"]))]
        body_rect = {"x1": _r4(-bh[0]), "y1": _r4(-bh[1]),
                     "x2": _r4(bh[0]), "y2": _r4(bh[1]), "width": width}
    else:
        bh = [_half(abs(float(body_rect["x2"]) - float(body_rect["x1"]))),
              _half(abs(float(body_rect["y2"]) - float(body_rect["y1"])))]
    ph = list(pad_bbox["half"])                      # 焊盘(含阻焊) bbox 半宽
    pbh = [_half(abs(place_bound["x2"] - place_bound["x1"])),
           _half(abs(place_bound["y2"] - place_bound["y1"]))]

    silk_items: List[Dict[str, Any]] = []
    chosen = "outline"
    outline_lines = 0
    per_edge: List[Dict[str, Any]] = []

    if style == "frame":
        chosen = "frame"
        silk_items.append({"kind": "frame", **body_rect})
        deriv.append({
            "item": "silk.box", "value": [_r4(2 * bh[0]), _r4(2 * bh[1])],
            "formula": "本体外框整框（强制 silk_style=frame）",
            "source": "[规范 四] 丝印沿本体；调用方强制整框，压焊盘风险自担",
        })
        if all(ph[i] > bh[i] for i in (0, 1)):
            warnings.append(
                f"强制整框：本体（{2*bh[0]}×{2*bh[1]}）比焊盘跨距"
                f"（{2*ph[0]}×{2*ph[1]}，含阻焊）还小 —— 丝印会压焊盘。")
    else:
        items_o, per_edge, w_o = solve_silk_outline(
            spec, rects, body_rect, clearance=clearance, width=width)
        warnings += w_o
        outline_lines = len(items_o)
        if items_o:
            silk_items += items_o
            blocked = [e for e in per_edge if e["kept"] == 0]
            deriv.append({
                "item": "silk.outline", "value": f"{outline_lines} 段残段",
                "formula": "本体 4 条边 −「焊盘(含阻焊) 外扩 "
                           f"{clearance}」冲突区间",
                "source": "[规范 四] 丝印不得进入阻焊开窗区；"
                          "★ 形态由金标准在线反解（本体轮廓 + 逐边裁剪）",
            })
            for e in per_edge:
                deriv.append({
                    "item": f"silk.edge_{e['edge']}",
                    "value": f"保留 {e['kept']} 段（被 {e['blocked_by']} 个焊盘挡）",
                    "formula": f"{e['axis']} 方向区间裁剪 @ {e['at']}",
                    "source": "[金标] 金标准只留了上下两条短横线、左右整条被挖光",
                })
            if blocked:
                warnings.append(
                    "整条被挖光的边：" + "、".join(e["edge"] for e in blocked)
                    + "（[金标] 金标准同样如此 —— 该件左右边整条压在焊盘上）。")
        else:
            # 兜底：贴焊盘(含阻焊) 外缘包框 —— 保留给"本体完全被焊盘盖住"的怪件
            chosen = "fallback_edge"
            gap = float(spec.get("silk_min_clearance", 0.06))
            half = [ph[i] + gap for i in (0, 1)]
            silk_items.append({
                "kind": "fallback", "x1": _r4(-half[0]), "y1": _r4(-half[1]),
                "x2": _r4(half[0]), "y2": _r4(half[1]), "width": width,
            })
            deriv.append({
                "item": "silk.fallback_box", "value": [_r4(2 * half[0]), _r4(2 * half[1])],
                "formula": f"焊盘(含阻焊) bbox + 2×{gap}",
                "source": "[规范 四] 不得进入阻焊开窗区（本体无残段可用时的兜底）",
            })

    # ---- 1 脚标识（[规范 四] IC / 多引脚必做）----
    mark_item, pin1_info, w_m = solve_pin1_mark(spec, rects, width=width)
    warnings += w_m
    if mark_item is not None:
        silk_items.append(mark_item)
        deriv.append({
            "item": "silk.pin1_mark", "value": f"三角形 @ {pin1_info['corner']}",
            "formula": f"apex={pin1_info['apex']}，base={pin1_info['base']}"
                       f"（{pin1_info['size'][0]}×{pin1_info['size'][1]}）",
            "source": "[规范 四] 1 脚标识必须清晰、唯一；"
                      "★ 形态/尺寸由金标准在线反解（apex 朝器件、"
                      "贴 1 脚焊盘外缘外移 0.10、底宽 0.60 高 0.30）",
        })
        # ★ 1 脚标识常常伸到 Place_Bound 之外（金标准那份就是）——
        #   按 [规范] 丝印本应落在 PB 内，所以这里必须吭声，不能装看不见。
        _mx = [p[0] for seg in mark_item["lines"] for p in seg]
        _my = [p[1] for seg in mark_item["lines"] for p in seg]
        if (max(abs(min(_mx)), abs(max(_mx))) > pbh[0] + 1e-9
                or max(abs(min(_my)), abs(max(_my))) > pbh[1] + 1e-9):
            warnings.append(
                f"⚠️ 1 脚标识（x {min(_mx):.2f}~{max(_mx):.2f}，"
                f"y {min(_my):.2f}~{max(_my):.2f}）伸出 Place_Bound"
                f"（x ±{pbh[0]} / y ±{pbh[1]}）—— [金标] 人工金标准那份"
                f"**同样伸出**，属该件既有做法；若要求丝印不出 PB，"
                f"请调小 pin1_mark_width / pin1_mark_height / pin1_mark_clearance。")

    info = {"style": chosen, "box": [_r4(2 * bh[0]), _r4(2 * bh[1])],
            "width": width, "to_pad_clearance": clearance,
            "clearance": clearance,
            "outline_segments": outline_lines,
            "items": len(silk_items),
            "min_gap_to_pad": (float(spec.get("silk_min_clearance", 0.06))
                               if chosen == "fallback_edge" else None),
            "pin1": pin1_info,
            "per_edge": per_edge if chosen == "outline" else None}
    return silk_items, info, deriv, warnings


# ============================================================
# 6. 求解器：位号
# ============================================================
def solve_refdes(
    spec: Dict[str, Any],
    place_bound: Dict[str, Any],
    silk_info: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """
    位号文字（`#REF`）的位置 / 层 / 文本。

    ★ 口径已改为「以人工模板库为准」（2026-09-30 用户裁决 + 实测）
    ============================================================
    实测 `Data\\footprint\\*/` 下 **14 个模板无一例外**：位号文字都在
        CLASS/SUBCLASS = **REF DES/ASSEMBLY_TOP**
        内容           = **`#REF`**（个别写 `REF#`，Cadence 等价写法）
        对齐           = LEFT
    （唯一例外是 USB Type-C 那件额外多放了 `#REFDES`@REF DES/SILKSCREEN_TOP，
      属个例，不采纳为默认。）
    → 所以本项目默认也跟着放 **装配层一条 `#REF`**，不再放丝印层 `U*`。

    位置策略由 `spec["refdes_place"]` 决定：
      · `"body_center"`（**默认**）→ 本体中心 + (`refdes_dx`, `refdes_dy`)
        —— 模板里位号基本都在本体中心附近（人工件 `3S48000163` 是
           `(-0.15, 0.04)`，即中心偏移 -0.15 / +0.04）。
      · `"outside"`（旧行为，保留）→ 放本体外侧下方，与焊盘无重叠，
        y = 本体中心 ∓ (Place_Bound 半高 + gap + 字高/2)（[规范 四]）。
    显式给出 `refdes_x` / `refdes_y` 时**直接作为绝对坐标**，优先级最高
    —— 用于逐值复现某件人工金标准。

    放置层由 `spec["refdes_targets"]` 决定（默认 `["refdes_assembly"]`）：
      · `["refdes_assembly"]` → REF DES/ASSEMBLY_TOP（**默认**）
      · `["refdes"]`          → REF DES/SILKSCREEN_TOP
      · 两者都列 → 一次放两层（执行器 `add_text` 已支持）

    :param body: 本体（Assembly 外框）矩形 {"x1","y1","x2","y2"}；
                 缺省时退回用 Place_Bound 中心，仅位置略有差别。
    """
    rules = rules or DEFAULT_RULES
    gap = float(spec.get("refdes_gap", 0.10))
    text_h = float(spec.get("refdes_text_height", 0.80))  # [规范 四] 字高 >= 0.8
    pbh = _half(abs(place_bound["y2"] - place_bound["y1"]))

    # ---- 参考中心：优先本体中心，缺 body 时退 Place_Bound 中心 ----
    if body is not None:
        cx = _r4((float(body["x1"]) + float(body["x2"])) / 2.0)
        cy = _r4((float(body["y1"]) + float(body["y2"])) / 2.0)
    else:
        cx = _r4((float(place_bound["x1"]) + float(place_bound["x2"])) / 2.0)
        cy = _r4((float(place_bound["y1"]) + float(place_bound["y2"])) / 2.0)

    dx = float(spec.get("refdes_dx", 0.0))
    dy = float(spec.get("refdes_dy", 0.0))
    place = str(spec.get("refdes_place", "body_center")).strip().lower()

    if place == "outside":
        side = "bottom" if "bottom" in str(
            spec.get("refdes_side", "bottom")).lower() else "top"
        sign = -1.0 if side == "bottom" else 1.0
        x = cx + dx
        y = cy + sign * (pbh + gap + text_h / 2.0)
        formula = (f"本体中心({cx}, {cy}) + "
                   f"{'−' if sign < 0 else '+'}(PB半高 {pbh} + {gap} "
                   f"+ 字高/2 {_r4(text_h / 2)})")
        source = "[规范 四] 位号放本体外侧、与焊盘无重叠；字高 >= 0.8"
    else:                                        # body_center（模板口径）
        x = cx + dx
        y = cy + dy
        formula = f"本体中心({cx}, {cy}) + 偏移({dx}, {dy})"
        source = ("人工模板库口径：位号居中于本体（14/14 模板实测）")

    # ---- 绝对坐标覆盖（逐值复现人工金标准用）----
    if "refdes_x" in spec:
        x = float(spec["refdes_x"])
    if "refdes_y" in spec:
        y = float(spec["refdes_y"])

    ref = {"text": str(spec.get("refdes_text", "#REF")),
           "x": _r4(x), "y": _r4(y),
           "text_block": str(spec.get("refdes_text_block", "1"))}
    _tg = spec.get("refdes_targets")
    if _tg:
        ref["targets"] = [str(t) for t in
                          (_tg if isinstance(_tg, (list, tuple)) else [_tg])]
    _jf = spec.get("refdes_justify", "left")
    if _jf:
        ref["justify"] = str(_jf)
    deriv = [{
        "item": "refdes.pos", "value": [ref["x"], ref["y"]],
        "formula": formula, "source": source,
    }, {
        "item": "refdes.layer", "value": ref.get("targets") or ["refdes_assembly"],
        "formula": "REF DES/ASSEMBLY_TOP",
        "source": ("人工模板库口径：14/14 模板位号都在装配层 "
                   "（Data/footprint/*/*.dra 实测）"),
    }, {
        "item": "refdes.text", "value": ref["text"],
        "formula": "#REF", "source": "人工模板库口径（Cadence 占位符写法）",
    }]
    warnings: List[str] = []
    if text_h < 0.8 - 1e-9:
        warnings.append(f"位号字高 {text_h} 小于 [规范 四] 下限 0.8mm。")
    if body is None:
        warnings.append("solve_refdes 未收到本体矩形，位号位置按 Place_Bound 中心估算。")
    return ref, deriv, warnings


# ============================================================
# 7. 总入口
# ============================================================
# ★ spec 顶层键面白名单（2026-10-13 未知键守卫）：solver + convention_engine
#   消费的全部键 + 工具层信息键。新增消费点时必须同步本清单——
#   守卫哲学与 pads.txt 列守卫/spec 守卫一致：忽略可以，忽略而不告诉你不行。
SPEC_KNOWN_KEYS = frozenset({
    # ---- 符号 / 命名 ----
    "symbol_base", "symbol_name", "symbol_name_hint", "suffix",
    "component_type", "pin_count",
    # ---- 尺寸来源口径（resolve_all_dims）----
    "dim_source", "dim_sources", "dim_tables", "dim_tols",
    "use_max_for_geometry",
    # ---- 焊盘 / 阵列 / 孔 ----
    "pad", "array", "pitch", "drill_diameter", "mount",
    "pins_override", "pin_names",            # pin_names 仅记录（tools.py 写入）
    # ---- TH 约定引擎（apply_conventions）----
    "padstack", "paste_bottom", "extra_padstacks",
    # ---- 轮廓 ----
    "body", "body_center", "body_max", "body_tol",
    "place_bound_expand_mm",
    "pin1_corner", "pin1_mark", "pin1_mark_axis", "pin1_mark_center",
    "pin1_mark_clearance", "pin1_mark_height", "pin1_mark_layers",
    "pin1_mark_width",
    # ---- 丝印 ----
    "silk_style", "silk_width", "silk_pad_clearance",
    "silk_min_clearance", "silk_min_seg_len",
    # ---- 位号 ----
    "refdes_place", "refdes_text", "refdes_x", "refdes_y",
    "refdes_dx", "refdes_dy", "refdes_gap", "refdes_justify",
    "refdes_side", "refdes_targets", "refdes_text_block",
    "refdes_text_height",
})


def guard_spec_keys(spec: Dict[str, Any]) -> List[str]:
    """spec 未知键守卫：返回 warnings（每批未知键一条 WARN 文案）。

    下划线前缀键（_resolved 等内部键）不告警。
    """
    unknown = sorted(
        k for k in spec
        if k not in SPEC_KNOWN_KEYS and not str(k).startswith("_")
    )
    if not unknown:
        return []
    return [f"spec 含 {len(unknown)} 个未消费键（本工具不认识、已忽略，"
            f"若为规格书约束请人工裁定）: {', '.join(unknown)}"]


def solve(
    spec: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    器件参数 → 完整成品几何（可直接喂 `build_footprint_from_params`）。

    :param spec: 见 `SPEC_3S48000163`（最小字段：symbol_base / pad / array /
                 body / pin_count）
    :return: {
        "success": bool,
        "symbol_name": str, "padstack": {...}, "pins": [...],
        "silk": [ 图元, ... ],        # {"kind","lines":[[[x,y],[x,y]],…],"width"}
        "assembly": [ 本体矩形, 1脚三角 ],   # ★ 金标准 assembly 也是 2 个元素
        "body": rect, "place_bound": rect,
        "pad_rects": [...],           # 每脚焊盘(含阻焊)矩形，丝印避让依据
        "refdes": {...}, "silk_info": {...}, "pad_bbox": {...},
        "derivations": [ {item, value, formula, source}, ... ],
        "warnings": [str], "error": str|None,
    }
    """
    rules = rules or DEFAULT_RULES
    deriv: List[Dict[str, Any]] = []
    warnings: List[str] = []
    warnings += guard_spec_keys(spec)     # ★ 未知键显名（不阻断）

    # ---- 0) ★ 尺寸来源口径：先一次性解析「规格书来源」的尺寸 ----
    #   用户口径（2026-09-29）：
    #     · 图上标注了具体值   → 取**标注值本身**（带公差也不加上偏差）
    #     · 图上没标、需查表   → 取表 **MAX**（无 MAX 用 TYP + 告警）
    #   结果挂进 spec["_resolved"]，下游 solve_* 统一从那里读 ——
    #   保证 body / pad / pitch 用的是**同一个口径**，不会各处各算一套。
    spec = dict(spec)                     # 不改调用者传进来的 dict
    try:
        _dims, d0, w0 = resolve_all_dims(spec)
        spec["_resolved"] = _dims
    except Exception as e:                       # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}",
                "derivations": deriv, "warnings": warnings}
    deriv += d0
    warnings += w0

    try:
        padstack, d1 = solve_padstack(spec, rules)
        pins, d2, w2 = solve_pins(spec)
        body_rect, pb_rect, pad_bbox, d3, w3 = solve_outlines(spec, rules)
        pad_rects = solve_pad_rects(spec, rules)
        silk, silk_info, d4, w4 = solve_silkscreen(
            spec, pad_bbox, pb_rect, rules, pad_rects, body_rect)
        refdes, d5, w5 = solve_refdes(spec, pb_rect, silk_info, rules, body_rect)

        # ---- Assembly 层：本体外框 + 1 脚标识 ----
        #   ★ 金标准实测：Assembly 层**也有那个 1 脚三角形**
        #     （assembly_top 元素 2 个：三角形 + 3.2×2.5 本体框）。
        mark = silk_info.get("pin1") or {}
        assembly: List[Dict[str, Any]] = [dict(body_rect)]
        if mark.get("style") == "triangle" and "assembly" in (mark.get("layers") or []):
            assembly.append({"kind": "pin1_mark",
                             "lines": [list(seg) for seg in mark.get("segments", [])],
                             "width": float(body_rect.get("width", 0.10)),
                             "corner": mark.get("corner")})

        symbol_name = spec.get("symbol_name") or solve_symbol_name(
            symbol_base=spec["symbol_base"],
            pin_count=spec.get("pin_count") or len(pins),
            body=spec["body"],
            pitch=spec.get("pitch"),
            suffix=spec.get("suffix", ""),
            rules=rules,
        )
    except Exception as e:                       # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}",
                "derivations": deriv, "warnings": warnings}

    deriv += d1 + d2 + d3 + d4 + d5
    warnings += w2 + w3 + w4 + w5

    deriv.append({
        "item": "symbol_name", "value": symbol_name,
        "formula": f"{rules['naming']['prefix']}{spec['symbol_base']}{len(pins)}"
                   f"_" + "X".join(name_num(spec['body'][k])
                                   for k in ("x", "y", "z") if spec['body'].get(k) is not None),
        "source": "[规范 一] <前缀><类型><引脚数>_<长>X<宽>[X<高>]；命名用中值",
    })

    return {
        "success": True,
        "symbol_name": symbol_name,
        "padstack": padstack,
        # ★ 额外 padstack（固定脚等第二尺寸焊盘）；builder 逐个创建，
        #   pins 里用 "padstack" 名引用
        "extra_padstacks": [dict(e) for e in (spec.get("extra_padstacks") or [])],
        "pins": pins,
        "silk": silk,
        "body": body_rect,
        "assembly": assembly,
        "pad_rects": pad_rects,
        "place_bound": pb_rect,
        "refdes": refdes,
        "silk_info": silk_info,
        "pad_bbox": pad_bbox,
        "derivations": deriv,
        "warnings": warnings,
        "error": None,
    }


def to_build_args(
    geo: Dict[str, Any], *, fresh_symbol: bool = True,
    template_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    把 `solve()` 的结果转成 `build_footprint_from_params(**kwargs)` 的入参。

    ★ silk / assembly 都传**列表**：
      · `silk`      = 本体残段（短横线）+ 1 脚三角
      · `assembly`  = Assembly 层图元列表（本体框 + 1 脚三角）
                      ★ 2026-10-12 起走显式 `assembly` 参数（执行器逐项画），
                      不再借用 `body` 通道 —— body 只作无 assembly 时的兜底。
    """
    if not geo.get("success"):
        raise ValueError(f"几何求解失败：{geo.get('error')}")
    return {
        "padstack": geo["padstack"],
        "extra_padstacks": geo.get("extra_padstacks") or [],
        "pins": geo["pins"],
        "silk": geo["silk"],
        "assembly": geo.get("assembly"),
        "body": None if geo.get("assembly") else geo["body"],
        "place_bound": geo["place_bound"],
        "refdes": geo["refdes"],
        "fresh_symbol": fresh_symbol,
        "symbol_name": geo["symbol_name"],
        "template_path": template_path,
    }


# ============================================================
# 8. 回归样例 —— ★ 2026-10-09 起真源迁到 knowledge/specs/*.json
# ============================================================
# 以前这三份 spec **硬编码在本文件**，有三个毛病：
#   1. 加一颗料 = 改源码，还要过 selfcheck_geometry 225 项断言
#   2. MCP 工具读不到（solve_footprint_geometry 逐字段构造 spec，与常量无关）
#   3. 和真实库 `Data\footprint\` 两套账
# 现在**真源是 `knowledge/specs/<符号名>.json`**（含 family / source_pdf / notes 方法论），
# 由 `services/spec_library.py` 读写（save_spec / load_spec / find_spec）。
# 这里只保留**加载后的常量名**，兼容 `Tools/e2e_*.py` 与 `selfcheck_geometry` 的既有引用。
#
# ★ 每份 spec 的**提取方法论与已知不确定项**见对应 JSON 的 `notes` 字段：
#     3S48  —— 尺寸来源口径（图上标注 vs MIN/MAX 表）为什么取标注值
#     FPC   —— 非对称引脚 + 双 padstack 的写法先例
#     CSTJ  —— 比例 k=10.70pt/mm 的推导、1 号焊盘宽 1.42 为何不圆整
#   （改 spec 请改 JSON，别再往本文件里塞常量。）


def _load_builtin(name: str, attr: str) -> Dict[str, Any]:
    """
    从 spec 库加载内置样例。

    ★ 库缺失时**抛错而不静默降级** —— 这三份是所有几何自检的基线，
    静默降级会让 225 项断言在错误基准上"全绿"。
    """
    spec = spec_library.load_spec(name)
    if spec is None:
        raise RuntimeError(
            f"内置样例 spec 缺失：knowledge/specs/{name.lower()}.json\n"
            f"  真源已迁到 spec 库（services/spec_library.py 维护）。\n"
            f"  若被误删：从备份恢复，或按 JSON 的 notes 字段重跑解析。\n"
            f"  受影响常量：{attr}")
    return spec


SPEC_3S48000163: Dict[str, Any] = _load_builtin(
    "NB_XTAL4_3D2X2D5X0D7", "SPEC_3S48000163")
SPEC_FPC0510_6P: Dict[str, Any] = _load_builtin(
    "NB_FPC0510_6P_4D77X3D6", "SPEC_FPC0510_6P")
SPEC_CSTJ421_AJ35: Dict[str, Any] = _load_builtin(
    "NB_CSTJ421_AJ35_6P", "SPEC_CSTJ421_AJ35")


def _demo() -> None:                          # pragma: no cover
    import json
    g = solve(SPEC_3S48000163)
    print(json.dumps({k: v for k, v in g.items()
                      if k in ("symbol_name", "padstack", "pins", "body",
                               "place_bound", "refdes", "silk_info", "warnings")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":                    # pragma: no cover
    _demo()
