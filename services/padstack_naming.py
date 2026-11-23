"""焊盘命名（纯函数，零 services 依赖）。

从 geometry_solver 抽出（票 #20；旧 convention_engine 的命名逻辑一并迁入规则库）：命名规则是「代码里的格式化
生成器」——模板在规则库数据里，格式化/反解在这里（ADR-0002 D6）。放在独立模块是
为了让派生侧（padstack_derive）与求解器共用同一份实现，不产生 import 环。

- name_num / parse_name_num：数字 ↔ 规范命名段（小数点换 D）
- SHAPE_CODES：形状 → 命名首字符 + Allegro figure 名
- padstack_name：SMD/通用焊盘命名
- th_oblong_name / th_oblong_naming_match：TH 长圆主语法（POB..HOB..）生成与反推
"""

from __future__ import annotations

import re
import string
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

__all__ = [
    "SHAPE_CODES",
    "name_num",
    "parse_name_num",
    "shape_code",
    "shape_figure",
    "shape_from_figure",
    "padstack_name",
    "th_dim",
    "th_oblong_name",
    "th_oblong_naming_match",
    "format_derive_name",
    "naming_matches_dims",
]


def _r4(v: float) -> float:
    """统一收敛到 4 位小数 —— 免得 1.1000000000000001 这种串进 SKILL 表达式。"""
    return round(float(v), 4)


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
    m = re.fullmatch(r"(\d+)(?:[Dd](\d+))?", s)
    if not m:
        raise ValueError(f"非法命名数值段: {s!r}")
    head, frac = m.group(1), m.group(2)
    return float(f"{head}.{frac}") if frac else float(head)


# 形状 → 命名首字符 + Allegro figure 名
#   [规范 二] r长x宽（Rectangle）、c直径（Circle）、sq边长（Square）、ob…（Oblong）
SHAPE_CODES = {
    "rect": ("R", "RECTANGLE"), "rectangle": ("R", "RECTANGLE"),
    "circle": ("C", "CIRCLE"), "round": ("C", "CIRCLE"),
    "square": ("SQ", "SQUARE"), "sq": ("SQ", "SQUARE"),
    "oblong": ("OB", "OBLONG"), "oval": ("OB", "OBLONG"), "ob": ("OB", "OBLONG"),
}

_FIGURE_TO_SHAPE = {
    "RECTANGLE": "rect", "CIRCLE": "circle", "SQUARE": "square", "OBLONG": "oblong",
}


def _shape_entry(shape: str) -> Tuple[str, str]:
    key = str(shape).lower()
    if key not in SHAPE_CODES:
        raise ValueError(f"未知焊盘形状: {shape!r}（可选 {sorted(SHAPE_CODES)}）")
    return SHAPE_CODES[key]


def shape_code(shape: str) -> str:
    """形状 → 命名首字符（R / C / SQ / OB）。未知形状抛 ValueError。"""
    return _shape_entry(shape)[0]


def shape_figure(shape: str) -> str:
    """形状 → Allegro figure 名（RECTANGLE / CIRCLE / SQUARE / OBLONG）。"""
    return _shape_entry(shape)[1]


def shape_from_figure(figure: str) -> str:
    """figure 名 → 形状键（RECTANGLE→rect…）。未知 figure 抛 ValueError。"""
    key = str(figure).upper()
    if key not in _FIGURE_TO_SHAPE:
        raise ValueError(f"未知焊盘 figure: {figure!r}（可选 {sorted(_FIGURE_TO_SHAPE)}）")
    return _FIGURE_TO_SHAPE[key]


def padstack_name(size: Sequence[float], shape: str = "rect") -> str:
    """
    [规范 二] 焊盘命名。

    [1.4, 1.2] rect   -> "R1D4X1D2"   （与金标准 .pad 名一致）
    [0.85, 0.85] circle -> "C0D85"
    [0.4, 0.3] oblong -> "OB0D4X0D3"
    """
    ch, _fig = _shape_entry(shape)
    dims = [float(v) for v in size]
    if ch in ("C", "SQ"):                     # 单一尺寸
        return f"{ch}{name_num(dims[0])}"
    return f"{ch}{name_num(dims[0])}X{name_num(dims[1])}"


# ============================================================
# 2. TH 长圆主语法（POB{孔长}X{孔短}HOB{盘长}X{盘短}）
# ============================================================
def th_dim(v: float) -> str:
    """TH 主语法段：2 位小数、去尾零、小数点换 D（1.0→"1"、0.85→"0D85"）。"""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", "D")


def _dnum(s: str) -> float:
    return float(s.replace("D", "."))


def th_oblong_name(hole_board: Tuple[float, float],
                   pad_board: Tuple[float, float],
                   plated: bool = True) -> str:
    """按主语法派生 padstack 名（板面帧，长轴在前，2 位小数去尾零）。"""
    hl, hs = sorted([round(v, 2) for v in hole_board], reverse=True)
    pl, ps = sorted([round(v, 2) for v in pad_board], reverse=True)
    name = f"POB{th_dim(hl)}X{th_dim(hs)}HOB{th_dim(pl)}X{th_dim(ps)}"
    return name if plated else f"NP{name}"


def _lenient_num(seg: str) -> Optional[float]:
    """宽松反解命名段：1D / 1D0 / 1 都读成 1.0（历史名字有悬空 D）。"""
    text = str(seg).strip().upper().replace("D", ".")
    if text.endswith("."):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def naming_matches_dims(name: str, dims: Sequence[float], shape: str = "rect",
                        tol: float = 1e-6) -> bool:
    """名字是否已经表达了这组尺寸（判定侧用尺寸反推，ADR-0002 D6）。

    只认形状首字符 + 数值段；小数点/悬空 D 的写法差异不影响结论。
    """
    ch, _fig = _shape_entry(shape)
    base = re.sub(r"OS[XY][0-9D.]+$", "", str(name or "").upper())
    if ch in ("C", "SQ"):
        m = re.fullmatch(re.escape(ch) + r"([0-9D.]+)", base)
        got = _lenient_num(m.group(1)) if m else None
        return got is not None and abs(got - float(dims[0])) <= tol
    m = re.fullmatch(re.escape(ch) + r"([0-9D.]+)X([0-9D.]+)", base)
    if not m:
        return False
    got0 = _lenient_num(m.group(1))
    got1 = _lenient_num(m.group(2))
    want = [float(v) for v in dims]
    return (got0 is not None and got1 is not None
            and abs(got0 - want[0]) <= tol and abs(got1 - want[-1]) <= tol)


def format_derive_name(spec: Mapping[str, Any]) -> str:
    """把规则库给出的命名规格渲染成 padstack 名（ADR-0002 D6：模板在数据，格式化在代码）。

    spec 由 rule_engine.resolve_derive() 产出：
      {"style": "th_oblong", "template": "POB{hole_long}X{hole_short}HOB{pad_long}X{pad_short}",
       "values": {占位符: 数值}}
      {"style": "padstack_name", "shape": "rect", "dims": [长, 宽]}

    模板占位符按 TH 主语法编码（2 位小数去尾零、小数点换 D）；padstack_name 走通用命名。
    """
    if not isinstance(spec, Mapping):
        raise ValueError(f"命名规格应为对象，得到 {type(spec).__name__}")
    style = spec.get("style")
    if style == "th_oblong":
        template = str(spec.get("template") or "")
        values: Dict[str, Any] = dict(spec.get("values") or {})
        out: list = []
        for literal, field, _fmt, _conv in string.Formatter().parse(template):
            out.append(literal)
            if field is None:
                continue
            if field not in values:
                raise ValueError(f"命名模板占位符 {{{field}}} 没有对应数值")
            out.append(th_dim(float(values[field])))
        return "".join(out)
    if style == "padstack_name":
        dims = [float(v) for v in (spec.get("dims") or [])]
        return padstack_name(dims, str(spec.get("shape") or ""))
    raise ValueError(f"未知命名方式 {style!r}（可选 th_oblong / padstack_name）")


def th_oblong_naming_match(name: str, hole_board: Tuple[float, float],
                           pad_board: Tuple[float, float], shape: str,
                           plated: bool) -> str:
    """
    判定 padstack 名是否符合库命名语法（数值比对，格式变体鲁棒）。
    hole_board/pad_board 均为板面帧 (长, 短)。
    返回标签：主语法 / NP:主语法 / NP:{孔OB}(盘省略) / other
    """
    base = re.sub(r"OS[XY][0-9D.]+$", "", (name or "").upper())
    hole = sorted([round(v, 2) for v in hole_board if v], reverse=True)
    pad = sorted([round(v, 2) for v in pad_board if v], reverse=True)
    if not hole or not pad:
        return "other"

    def eq(a: float, b: float) -> bool:
        return abs(a - b) <= 0.011

    if shape == "oblong":
        m = re.fullmatch(
            r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)H(?:OB|O)?([0-9D]+)X([0-9D]+)", base)
        if m:
            flag, hw, hh, pw, ph = m.groups()
            if (eq(_dnum(hw), hole[0]) and eq(_dnum(hh), hole[1])
                    and eq(_dnum(pw), pad[0]) and eq(_dnum(ph), pad[1])):
                tag = "P{孔OB}H{盘OB}"
                return tag if plated else "NP:" + tag
            return "other"
        m = re.fullmatch(r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)", base)
        if m:
            flag, hw, hh = m.groups()
            if eq(_dnum(hw), hole[0]) and eq(_dnum(hh), hole[1]):
                return "NP:{孔OB}(盘省略)" if not plated else "other"
        return "other"
    if shape == "circle":
        m = re.fullmatch(r"(NP)?P?C([0-9D]+)H(?:OB|C)?([0-9D]+)", base)
        if m:
            flag, h, p = m.groups()
            if eq(_dnum(h), hole[0]) and eq(_dnum(p), pad[0]):
                tag = "PC{孔}HC{盘}"
                return tag if plated else "NP:" + tag
        return "other"
    return "other"
