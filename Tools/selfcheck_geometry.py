# -*- coding: utf-8 -*-
"""
selfcheck_geometry.py —— `services/geometry_solver.py` 的离线自检

不需要 Allegro、不需要联网。全部用**可追溯的基准**对拍：
  [规范] Data/checklist/PCB_Footprint建库规范.xlsx
  [规则] services/rule_checker.py::DEFAULT_RULES
  [金标] Data/footprint/3S48000163/checklist_report_20260918_095100.json
         Data/footprint/3S48000163/nb_xtal4_3d2x2d5x0d7.*（人工建的成功产物）
  [规格] 3S48000163产品规格书_20220916_S1.pdf  p5（DIMENSIONS / CONNECTION DIAGRAM）

分节：
  [1] 命名 ↔ 数值 round-trip
  [2] padstack（命名 + 阻焊 / 钢网层）
  [3] pin 坐标与编号走向
  [4] 本体 / Place_Bound / 丝印外扩（金标准对拍）
  [5] 拓扑切换（2×N 双排）
  [6] 防错（非法输入必须抛错，不许静默产出垃圾）

用法：
    ./.venv/Scripts/python.exe Tools/selfcheck_geometry.py
"""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services import geometry_solver as G          # noqa: E402


# ============================================================
# 框架
# ============================================================
class C:
    def __init__(self) -> None:
        self.ok = 0
        self.bad = 0
        self.failed: list[tuple[str, str]] = []

    def _ok(self, name: str, detail: str = "") -> None:
        self.ok += 1

    def _bad(self, name: str, got, want: str) -> None:
        self.bad += 1
        self.failed.append((name, f"got={got!r}  want={want}"))

    def chk(self, name: str, cond: bool, got, want: str) -> None:
        if cond:
            self._ok(name)
        else:
            self._bad(name, got, want)

    def eq(self, name: str, got, want, tol: float = 1e-9) -> None:
        if isinstance(want, (list, tuple)):
            ok = (isinstance(got, (list, tuple)) and len(got) == len(want)
                  and all(abs(float(a) - float(b)) <= tol
                          for a, b in zip(got, want)))
        elif isinstance(want, (int, float)) and not isinstance(want, bool):
            ok = abs(float(got) - float(want)) <= tol
        else:
            ok = got == want
        if ok:
            self._ok(name)
        else:
            self._bad(name, got, repr(want))

    def raises(self, name: str, fn, *a, **kw) -> None:
        try:
            fn(*a, **kw)
        except (ValueError, AssertionError, KeyError, TypeError):
            self._ok(name)
        except Exception as e:                       # noqa: BLE001
            self._bad(name, f"抛了 {type(e).__name__}", "ValueError/AssertionError")
        else:
            self._bad(name, "未抛错", "应抛 ValueError/AssertionError")


def hr(t: str) -> None:
    print("\n" + "=" * 68)
    print(f"  {t}")
    print("=" * 68)


# ============================================================
# 基准常量（全部来自上面列的源）
# ============================================================
GS = G.solve(G.SPEC_3S48000163)          # 金标准几何（一次求解，多处复用）


def main() -> int:
    c = C()

    # ========================================================
    # [1] 命名 ↔ 数值 round-trip
    # ========================================================
    hr("[1] 命名 ↔ 数值（[规范 一] 小数点用 D 代替）")
    cases = [
        (1.4, "1D4"), (1.2, "1D2"), (3.2, "3D2"), (2.5, "2D5"),
        (0.7, "0D7"), (0.85, "0D85"), (0.4, "0D4"), (2.0, "2"), (8.0, "8"),
    ]
    for v, s in cases:
        c.eq(f"name_num({v})", G.name_num(v), s)
        c.eq(f"parse_name_num({s})", G.parse_name_num(s), v)
    # round-trip：名字→数值→名字 必须稳定
    for v, s in cases:
        c.eq(f"roundtrip({s})", G.name_num(G.parse_name_num(s)), s)
    c.raises("parse_name_num 非法", G.parse_name_num, "1X4")
    c.raises("parse_name_num 空", G.parse_name_num, "")

    # ========================================================
    # [2] padstack：命名 + 阻焊/钢网层
    # ========================================================
    hr("[2] padstack —— 金标准 .pad 名 = R1D4X1D2")
    c.eq("padstack.name", GS["padstack"]["name"], "R1D4X1D2")
    layers = {L["layer"]: L for L in GS["padstack"]["layers"]}
    c.eq("TOP.size", layers["TOP"]["size"], [1.4, 1.2])
    c.eq("SOLDERMASK.size", layers["SOLDERMASK_TOP"]["size"], [1.5, 1.3])
    c.eq("PASTEMASK.size", layers["PASTEMASK_TOP"]["size"], [1.4, 1.2])
    # [金标 2.2] 阻焊开窗单边 0.05
    c.eq("阻焊单边外扩", (layers["SOLDERMASK_TOP"]["size"][0] - 1.4) / 2, 0.05)
    # [金标 2.3] 钢网 = 焊盘
    c.eq("钢网=焊盘", layers["PASTEMASK_TOP"]["size"], layers["TOP"]["size"])
    # 各种形状命名（[规范 二]）
    c.eq("圆形命名", G.solve_padstack_name([0.85], "circle"), "C0D85")
    c.eq("方形命名", G.solve_padstack_name([0.4], "square"), "SQ0D4")
    c.eq("椭圆命名", G.solve_padstack_name([0.4, 0.3], "oblong"), "OB0D4X0D3")
    c.raises("未知形状", G.solve_padstack_name, [1.0, 1.0], "triangle")

    # ========================================================
    # [3] pin 坐标与编号走向
    # ========================================================
    hr("[3] pin 坐标 —— 规格书 p5 CONNECTION DIAGRAM (TOP VIEW)")
    got = {p["number"]: (p["x"], p["y"]) for p in GS["pins"]}
    # 规格书实测：#1 左下 / #2 右下 / #3 右上 / #4 左上（逆时针）
    c.eq("#1 左下", got["1"], (-1.10, -0.85))
    c.eq("#2 右下", got["2"], (1.10, -0.85))
    c.eq("#3 右上", got["3"], (1.10, 0.85))
    c.eq("#4 左上", got["4"], (-1.10, 0.85))
    c.eq("pin 数", len(GS["pins"]), 4)
    # [金标 3.1] pitch_x=2.2 / pitch_y=1.7
    c.eq("pitch_x 实测", got["2"][0] - got["1"][0], 2.2)
    c.eq("pitch_y 实测", got["3"][1] - got["2"][1], 1.7)
    # [规范 四] 原点在封装中心 → 所有 pin 坐标和 ≈ 0
    c.eq("原点在中心 (Σx)", sum(p["x"] for p in GS["pins"]), 0.0)
    c.eq("原点在中心 (Σy)", sum(p["y"] for p in GS["pins"]), 0.0)
    # 换起点必须真的改变走向（防"起点参数被忽略"）
    _spec = dict(G.SPEC_3S48000163, pin1_corner="top-left")
    _g = G.solve(_spec)
    c.eq("pin1_corner=top-left → #1 左上",
         (_g["pins"][0]["x"], _g["pins"][0]["y"]), (-1.10, 0.85))
    # solve() 的契约是「不抛异常，返回 success=False + error」
    _r = G.solve(dict(G.SPEC_3S48000163, pin1_corner="middle"))
    c.chk("非法 pin1_corner 被拒",
          (not _r["success"]) and "pin1_corner" in str(_r.get("error")),
          _r.get("error"), "error 含 pin1_corner")
    # 但底层求解器本身必须抛错（便于单测/复用）
    c.raises("solve_pins 直接抛错", G.solve_pins,
             dict(G.SPEC_3S48000163, pin1_corner="middle"))

    # ========================================================
    # [4] 本体 / Place_Bound / 丝印 —— 金标准对拍
    # ========================================================
    hr("[4] 外框与丝印 —— 与 checklist_report 实测值对拍")
    # ★ 尺寸来源口径（用户裁决 2026-09-29）：3S48000163 p5 的尺寸**全标在图上**
    #   （`3.2±0.1` / `1.4` / `0.8` / `1.2` / `0.5`），没有 MIN/TYP/MAX 尺寸表
    #   → 取**标注值本身**（3.2×2.5），**不**加上偏差
    body = GS["body"]
    c.eq("body 宽 = 图上标注 3.2（不加公差）", abs(body["x2"] - body["x1"]), 3.2)
    c.eq("body 高 = 图上标注 2.5（不加公差）", abs(body["y2"] - body["y1"]), 2.5)
    pb = GS["place_bound"]
    c.eq("Place_Bound 宽 = 3.2 + 0.35×2", abs(pb["x2"] - pb["x1"]), 3.9)
    c.eq("Place_Bound 高 = 2.5 + 0.35×2", abs(pb["y2"] - pb["y1"]), 3.2)
    c.eq("外扩量 X（金标 0.35）",
         (abs(pb["x2"] - pb["x1"]) - 3.2) / 2, 0.35)
    # ★ 关键防回归：外扩基准必须是「本体」而非「焊盘阵列」
    #   按焊盘算会得到 3.7+0.7=4.4 —— 这正是之前 e2e 写错的取向
    c.chk("外扩基准=本体(不是焊盘)", abs(abs(pb["x2"] - pb["x1"]) - 3.9) < 1e-9,
          abs(pb["x2"] - pb["x1"]), "3.9（按焊盘算会是 4.4）")
    # ★ 新口径**与人工金标准一致**（旧口径 3.3×2.6 / 4.0×3.3 已废）
    _bp = [abs(pb["x2"] - pb["x1"]), abs(pb["y2"] - pb["y1"])]
    c.chk("新口径 PB = 人工金标准的 3.9×3.2", _bp == [3.9, 3.2], _bp, "[3.9, 3.2]")
    # ★ 命名**永远**用中值 —— 不随画实体口径变
    c.eq("命名用中值", GS["symbol_name"], "NB_XTAL4_3D2X2D5X0D7")
    # 遗留开关仍能回到旧行为（仅供对照），且**命名不变**
    _gr = G.solve(dict(G.SPEC_3S48000163, use_max_for_geometry=True))
    _rb, _rp = _gr["body"], _gr["place_bound"]
    c.eq("遗留口径 → body 3.3（中值+公差）", abs(_rb["x2"] - _rb["x1"]), 3.3)
    c.eq("遗留口径 → PB = 4.0×3.3",
         [abs(_rp["x2"] - _rp["x1"]), abs(_rp["y2"] - _rp["y1"])], [4.0, 3.3])
    c.eq("两种口径下命名一致", _gr["symbol_name"], GS["symbol_name"])
    # [金标 5.2] pins_size = [3.7, 3.0]（含阻焊）
    c.eq("焊盘阵列 bbox（含阻焊）", GS["pad_bbox"]["span"], [3.7, 3.0])

    # ---- 丝印：形态由金标准**在线反解**（2026-09-29 读金标准 .dra）
    #   [金标] SILKSCREEN_TOP 只有 3 个元素：
    #         2 条短横线（本体上下边的残段）+ 1 个 1 脚标识三角。
    si = GS["silk_info"]
    silk_lines = [it for it in GS["silk"] if it["kind"] == "outline"]
    marks = [it for it in GS["silk"] if it["kind"] == "pin1_mark"]
    c.eq("丝印残段数 = 2（金标 2 条短横线）", len(silk_lines), 2)
    c.eq("1 脚标识数 = 1（金标 1 个三角）", len(marks), 1)
    c.eq("丝印图元总数 = 3（金标 3）", len(GS["silk"]), 3)
    c.eq("丝印策略", si["style"], "outline")
    c.eq("丝印-焊盘避让 = [规范 四] 0.20", si["clearance"], 0.20)
    c.chk("丝印线宽 >= 0.10", si["width"] >= 0.10, si["width"], ">= 0.10")
    # 短横线落在**本体上下边**（★ 与 Assembly 同源本体 3.2×2.5 → y=±1.25，
    # 与人工金标准的 ±1.25 一致）
    c.eq("短横线的 y（本体上下边）",
         sorted({round(p[1], 4) for it in silk_lines for p in it["lines"][0]}),
         [-1.25, 1.25])
    # 端点 = 焊盘内缘(0.35) − 避让(0.20) = 0.15
    _dash = sorted(silk_lines, key=lambda it: it["lines"][0][0][1])[0]["lines"][0]
    c.eq("短横线端点（焊盘内缘 − 0.20）",
         [round(_dash[0][0], 4), round(_dash[1][0], 4)], [-0.15, 0.15])
    c.eq("短横线到焊盘内缘间隙", round(0.35 - _dash[1][0], 4), 0.20)
    # 左右两条边被**完全挖光**（[金标] 金标准同样如此：整条压在焊盘上）
    _pe = {e["edge"]: e for e in si["per_edge"]}
    c.eq("左边整条被挖光", _pe["left"]["kept"], 0)
    c.eq("右边整条被挖光", _pe["right"]["kept"], 0)
    c.eq("下边保留 1 段", _pe["bottom"]["kept"], 1)
    c.eq("上边保留 1 段", _pe["top"]["kept"], 1)
    # 1 脚三角：apex 贴 1 脚焊盘外缘（-1.50）再外移 0.10；底宽 0.60 高 0.30
    p1 = si["pin1"]
    c.eq("1 脚标识形态", p1["style"], "triangle")
    c.eq("1 脚标识角落", p1["corner"], "bottom-left")
    c.eq("三角顶点 = 焊盘下缘(−1.50) − 0.10", p1["apex"], [-1.1, -1.6])
    c.eq("三角底边左端", p1["base"][0], [-1.4, -1.9])
    c.eq("三角底边右端", p1["base"][1], [-0.8, -1.9])
    c.eq("三角尺寸（金标 0.60×0.30）", p1["size"], [0.6, 0.3])
    c.eq("三角与焊盘间隙", round(-1.5 - p1["apex"][1], 4), 0.10)
    # Assembly 层 = 本体框 + 1 脚三角（[金标] assembly_top 也是 2 个元素）
    c.eq("Assembly 元素数 = 2（金标 2）", len(GS["assembly"]), 2)
    c.chk("Assembly 含 1 脚三角",
          GS["assembly"][1].get("kind") == "pin1_mark",
          GS["assembly"][1].get("kind"), "pin1_mark")
    # ★ 三角伸到 Place_Bound 之外 —— 金标准亦如此，故只能告警不能断言"不超"
    c.chk("1 脚三角超出 Place_Bound 时有告警",
          any("1 脚标识" in w and "Place_Bound" in w for w in GS["warnings"]),
          [w for w in GS["warnings"] if "1 脚" in w][:1],
          "含 '1 脚标识…Place_Bound' 的告警")
    # ★ 金标准回归：用"金标准自己的口径"(图上标注本体 + 焊盘旋转90°) 复现，
    #   逐值与实测对拍，容差 0.02（金标准是人工手绘，末位有 0.01~0.02 抖动）
    _gold = G.solve(dict(G.SPEC_3S48000163,
                         pad={"shape": "rect", "size": [1.4, 1.2], "rotation": 90}))
    _gl = [it for it in _gold["silk"] if it["kind"] == "outline"]
    _gd = sorted(_gl, key=lambda it: it["lines"][0][0][1])[0]["lines"][0]
    c.chk("[金标复现] 短横线 x ∈ [-0.24, 0.23]",
          abs(_gd[0][0] - (-0.24)) <= 0.02 and abs(_gd[1][0] - 0.23) <= 0.02,
          [round(_gd[0][0], 4), round(_gd[1][0], 4)], "[-0.24, 0.23] ±0.02")
    c.chk("[金标复现] 短横线 y = ±1.25",
          abs(abs(_gd[0][1]) - 1.25) <= 0.02,
          _gd[0][1], "±1.25 ±0.02")
    _gm = _gold["silk_info"]["pin1"]
    c.chk("[金标复现] 三角 apex (-1.09,-1.70)",
          abs(_gm["apex"][0] - (-1.09)) <= 0.02 and abs(_gm["apex"][1] - (-1.70)) <= 0.02,
          _gm["apex"], "[-1.09, -1.70] ±0.02")
    c.chk("[金标复现] 三角底边 y = -2.01",
          all(abs(b[1] - (-2.01)) <= 0.02 for b in _gm["base"]),
          [b[1] for b in _gm["base"]], "-2.01 ±0.02")
    # 位号：默认口径 = **居中于本体**（人工模板库 14/14 实测，2026-09-30 裁决）
    #   —— 旧口径「放本体外侧」只作为 refdes_place="outside" 保留。
    ref = GS["refdes"]
    c.chk("位号默认落在本体内（居中口径）",
          min(body["y1"], pb["y1"]) <= ref["y"] <= max(body["y2"], pb["y2"])
          and min(body["x1"], pb["x1"]) <= ref["x"] <= max(body["x2"], pb["x2"]),
          (ref["x"], ref["y"]),
          f"落在 ({(body['x1'], body['y1'])}..{(body['x2'], body['y2'])}) 内")
    _ref_out = G.solve(dict(G.SPEC_3S48000163, refdes_place="outside"))["refdes"]
    c.chk("位号 outside 模式在本体外侧",
          _ref_out["y"] < min(body["y1"], pb["y1"]),
          _ref_out["y"], f"< {min(body['y1'], pb['y1'])}")
    # 元件类型决定外扩量（ic 0.35 / chip 0.15 / connector 0.85）
    for ctype, exp in (("chip", 0.15), ("connector", 0.85)):
        _g2 = G.solve(dict(G.SPEC_3S48000163, component_type=ctype))
        c.eq(f"{ctype} 外扩", (_g2["place_bound"]["x2"] - _g2["place_bound"]["x1"]
                               - 3.2) / 2, exp)
    # 未知类型必须告警（不许静默按 ic 处理）
    _g3 = G.solve(dict(G.SPEC_3S48000163, component_type="exotic"))
    c.chk("未知类型有告警", any("exotic" in w for w in _g3["warnings"]),
          _g3["warnings"][:1], "含 'exotic' 的告警")

    # ========================================================
    # [5] 拓扑切换：2×N 双排
    # ========================================================
    hr("[5] 拓扑 —— SOP8 左右两列（DIP 走向）")
    sop = {
        "symbol_base": "SOP", "component_type": "ic", "mount": "smd",
        "pad": {"shape": "rect", "size": [0.6, 1.5]},
        # rows = 沿 y 的个数（每列 4 个），cols = 沿 x 的个数（左右 2 列）
        "array": {"rows": 4, "cols": 2, "pitch_x": 5.0, "pitch_y": 1.27},
        "pin_count": 8,
        "body": {"x": 6.0, "y": 3.9, "z": 1.75},
        "body_tol": {"x": 0.2, "y": 0.2},
    }
    gs = G.solve(sop)
    c.eq("SOP8 pin 数", len(gs["pins"]), 8)
    gp = {p["number"]: (p["x"], p["y"]) for p in gs["pins"]}
    # 左列自下而上 → 右列自上而下（DIP 标准）
    c.eq("SOP8 #1 左下", gp["1"], (-2.5, -1.905))
    c.eq("SOP8 #4 左上", gp["4"], (-2.5, 1.905))
    c.eq("SOP8 #5 右上", gp["5"], (2.5, 1.905))
    c.eq("SOP8 #8 右下", gp["8"], (2.5, -1.905))
    c.eq("SOP8 名字", gs["symbol_name"], "NB_SOP8_6X3D9X1D75")
    c.eq("SOP8 pitch_y 实测", gp["4"][1] - gp["3"][1], 1.27)
    # 2×2 方阵不得走 DIP 规则（两者在 2×2 时会冲突，方阵规则优先）
    c.eq("2×2 走方阵规则（#2 右下）", got["2"], (1.10, -0.85))
    # 上下两排（rows=2, cols>2）
    _g7 = G.solve(dict(sop, array={"rows": 2, "cols": 4,
                                   "pitch_x": 1.27, "pitch_y": 5.0},
                       pin_count=8))
    _p7 = {p["number"]: (p["x"], p["y"]) for p in _g7["pins"]}
    c.eq("上下两排 #1 左下", _p7["1"], (-1.905, -2.5))
    c.eq("上下两排 #4 右下", _p7["4"], (1.905, -2.5))
    c.eq("上下两排 #5 右上", _p7["5"], (1.905, 2.5))

    # ========================================================
    # [6] 防错
    # ========================================================
    hr("[6] 防错 —— 非法输入必须显式失败")
    bad_specs = [
        ("缺 pad", dict(G.SPEC_3S48000163, pad={})),
        ("pad 形状非法", dict(G.SPEC_3S48000163, pad={"shape": "hex", "size": [1, 1]})),
        ("缺 array", dict(G.SPEC_3S48000163, array={})),
        ("pitch 为 0", dict(G.SPEC_3S48000163,
                            array={"rows": 2, "cols": 2, "pitch_x": 0, "pitch_y": 1.7})),
        ("body.x<=0", dict(G.SPEC_3S48000163, body={"x": 0, "y": 2.5})),
    ]
    for label, sp in bad_specs:
        r = G.solve(sp)
        c.chk(f"非法输入被拒: {label}",
              (not r["success"]) and bool(r.get("error")),
              (r.get("success"), r.get("error")), "success=False 且有 error")
    # 命名里出现小数点必须报错（[规范 一] no_dot）
    c.raises("命名含小数点被拒", G.solve_symbol_name, "X", 4,
             {"x": 3.2, "y": 2.5}, suffix="v1.0",
             rules={"naming": {"prefix": "NB_", "no_dot": True}})
    # pin_count 与阵列不符要告警而非静默
    _g4 = G.solve(dict(G.SPEC_3S48000163, pin_count=6))
    c.chk("pin_count 不符有告警", any("pin_count" in w for w in _g4["warnings"]),
          _g4["warnings"][:1], "含 'pin_count' 的告警")
    # 逐维**显式**声明 table 却没给表 = spec 写错 → 必须硬失败（不许静默糊过去）
    _g8 = G.solve(dict(G.SPEC_3S48000163, dim_sources={"body.x": "table"}))
    c.chk("显式 table 无表 → 硬失败",
          (not _g8["success"]) and "body.x" in str(_g8.get("error")),
          _g8.get("error"), "error 含 'body.x'")
    # 连"图上标注值"都没给、又没表 → 也必须失败（不许猜）
    _g9 = G.solve({k: v for k, v in G.SPEC_3S48000163.items() if k != "body"})
    c.chk("缺 body → 硬失败",
          (not _g9["success"]) and bool(_g9.get("error")),
          (None if _g9["success"] else _g9.get("error")), "success=False")
    # 丝印风格：none 必须真的不画
    _g5 = G.solve(dict(G.SPEC_3S48000163, silk_style="none"))
    c.eq("silk_style=none", len(_g5["silk"]), 0)
    # frame 模式：强制按本体画（即使比焊盘小）
    _g6 = G.solve(dict(G.SPEC_3S48000163, silk_style="frame"))
    c.eq("silk_style=frame 用本体尺寸（图上标注 3.2×2.5）",
         _g6["silk_info"]["box"], [3.2, 2.5])
    c.eq("silk_style=frame 只画 1 个矩形图元", len(_g6["silk"]), 2)  # 整框 + 1脚三角

    # ========================================================
    # [6.5] 尺寸来源口径 —— 图上有标注 vs 需要查表（2026-09-29 用户裁决）
    # ========================================================
    hr("[6.5] 尺寸取值口径 —— 图上标注→取标注值 / 查表→取 MAX")
    # ① 图上标注（默认）→ 取标注值本身，不加公差
    _d = {d["item"]: d for d in GS["derivations"]}
    c.eq("来源标为 drawing", "来源=drawing" in _d["dim.body.x 本体宽 X"]["formula"],
         True)
    c.eq("取的是标注值 3.2", _d["dim.body.x 本体宽 X"]["value"], 3.2)
    c.chk("body_tol 给了也不再影响取值",
          abs(GS["body"]["x2"] - GS["body"]["x1"]) == 3.2, 3.2, 3.2)
    # ② 查表 → 取 MAX
    _t = G.solve(dict(G.SPEC_3S48000163,
                      dim_sources={"body.x": "table", "body.y": "table"},
                      dim_tables={"body.x": {"min": 3.0, "typ": 3.2, "max": 3.4},
                                  "body.y": {"min": 2.3, "typ": 2.5, "max": 2.7}}))
    c.eq("查表取 MAX → body 3.4×2.7",
         [abs(_t["body"]["x2"] - _t["body"]["x1"]),
          abs(_t["body"]["y2"] - _t["body"]["y1"])], [3.4, 2.7])
    c.eq("查表取 MAX → PB = 3.4+0.7 × 2.7+0.7",
         [abs(_t["place_bound"]["x2"] - _t["place_bound"]["x1"]),
          abs(_t["place_bound"]["y2"] - _t["place_bound"]["y1"])], [4.1, 3.4])
    c.eq("查表口径下命名仍用中值", _t["symbol_name"], "NB_XTAL4_3D2X2D5X0D7")
    # ③ 查表但表里没 MAX → 用 TYP，且**必须告警**（不许静默）
    _t2 = G.solve(dict(G.SPEC_3S48000163,
                       dim_sources={"body.x": "table", "body.y": "table"},
                       dim_tables={"body.x": {"min": 3.0, "typ": 3.2},
                                   "body.y": {"min": 2.3, "typ": 2.5}}))
    c.eq("无 MAX → 用 TYP（3.2×2.5）",
         [abs(_t2["body"]["x2"] - _t2["body"]["x1"]),
          abs(_t2["body"]["y2"] - _t2["body"]["y1"])], [3.2, 2.5])
    c.chk("无 MAX 有告警", any("没有 MAX" in w for w in _t2["warnings"]),
          _t2["warnings"][:1], "含 '没有 MAX' 的告警")
    # ④ 整件默认 table、只给了本体表 → pad/pitch 退回标注值 + 告警（不崩）
    _t3 = G.solve(dict(G.SPEC_3S48000163, dim_source="table",
                       dim_tables={"body.x": {"min": 3.0, "typ": 3.2, "max": 3.4},
                                   "body.y": {"min": 2.3, "typ": 2.5, "max": 2.7}}))
    c.chk("整件默认 table 不影响 pad/pitch",
          _t3["success"] and not any("pad.x" in w for w in _t3["warnings"]
                                     if "没给表" not in w),
          _t3["success"], True)
    c.chk("pad/pitch 退回标注值时有告警",
          any("没给表" in w and "pad.x" in w for w in _t3["warnings"]),
          [w for w in _t3["warnings"] if "没给表" in w][:1], "含 'pad.x' 的告警")
    # ⑤ 命名**永远**用中值：各口径下符号名必须一致
    c.eq("三种口径命名全一致",
         {GS["symbol_name"], _t["symbol_name"], _t2["symbol_name"],
          _t3["symbol_name"]}, {"NB_XTAL4_3D2X2D5X0D7"})
    # ⑥ resolve_dim 的纯函数契约（不进 solve 也能单独用）
    c.eq("resolve_dim 图上标注", G.resolve_dim("body.x", {}, drawing=3.2)[0], 3.2)
    c.eq("resolve_dim 查表取 MAX",
         G.resolve_dim("body.x", {"dim_source": "table"}, drawing=3.2,
                       table={"min": 3.0, "typ": 3.2, "max": 3.4})[0], 3.4)
    c.raises("resolve_dim 显式 table 无表 → 抛错", G.resolve_dim, "body.x",
             {"dim_sources": {"body.x": "table"}}, table=None, drawing=3.2)

    # ========================================================
    # [7] extracta 视图与位号 —— 「静默缺口」防线（2026-09-29 加）
    # ========================================================
    hr("[7] extracta 视图 / 位号 —— 防 REF DES 静默缺口回归")
    from services import footprint_extractor as FE
    from services import extracta_reader as ER        # noqa: F401（纯解析，不需 Allegro）

    _vf = os.path.join(_ROOT, "services", "extracta_views", "footprint_views.txt")
    _v = open(_vf, encoding="utf-8").read() if os.path.isfile(_vf) else ""
    c.chk("视图文件存在", bool(_v), os.path.isfile(_vf), True)
    # ★ 第 4 路视图是"位号读得到"的**唯一**依据；被删掉就退回静默缺口
    c.eq("FULL_GEOMETRY 视图数（几何 + REF DES）", _v.count("\nFULL_GEOMETRY"), 2)
    c.chk('视图含 CLASS = "REF DES"', 'CLASS = "REF DES"' in _v, True, True)
    c.chk("几何视图仍限定 PACKAGE GEOMETRY",
          'CLASS = "PACKAGE GEOMETRY"' in _v, True, True)
    _er_src = open(os.path.join(_ROOT, "services", "extracta_reader.py"),
                   encoding="utf-8").read()
    c.chk("reader 走 4 路输出", "out_refdes" in _er_src, True, True)
    c.chk("reader 报 _gaps", '"_gaps"' in _er_src, True, True)

    # ---- 位号：口径 = 人工模板库（2026-09-30 用户裁决）----
    #   实测 Data/footprint/*/ 下 14/14 模板都是：
    #     REF DES/ASSEMBLY_TOP + `#REF` + LEFT 对齐
    #   → 本项目默认也必须如此；旧默认（丝印层 `U*`）已废。
    _rs = G.solve(G.SPEC_3S48000163)
    c.eq("refdes 默认文字 = #REF（模板口径）",
         _rs["refdes"].get("text"), "#REF")
    c.chk("refdes 默认层 = refdes_assembly（REF DES/ASSEMBLY_TOP）",
          _rs["refdes"].get("targets") == ["refdes_assembly"],
          _rs["refdes"].get("targets"), "['refdes_assembly']")
    c.eq("refdes 默认对齐 = left（模板实测）",
         _rs["refdes"].get("justify"), "left")
    # 人工金标准实测：`#REF` @ REF DES/ASSEMBLY_TOP，坐标 (-0.15, 0.04)
    c.eq("refdes 坐标复现人工金标准 x = -0.15", _rs["refdes"].get("x"), -0.15)
    c.eq("refdes 坐标复现人工金标准 y = 0.04", _rs["refdes"].get("y"), 0.04)
    # 不给 dx/dy 时应落在本体正中心（模板"居中"口径）
    _rc = G.solve(dict(G.SPEC_3S48000163, refdes_dx=0.0, refdes_dy=0.0))["refdes"]
    c.eq("refdes 无偏移 → 本体中心 x", _rc.get("x"), 0.0)
    c.eq("refdes 无偏移 → 本体中心 y", _rc.get("y"), 0.0)
    # 旧行为（放本体外侧）仍可用
    _ro = G.solve(dict(G.SPEC_3S48000163, refdes_place="outside"))["refdes"]
    c.chk("refdes_place='outside' 落到本体下方",
          _ro.get("y") is not None and _ro["y"] < -1.25,
          _ro.get("y"), "y < -1.25（本体下边界）")
    # 绝对坐标覆盖优先级最高
    _ra = G.solve(dict(G.SPEC_3S48000163, refdes_x=1.0, refdes_y=2.0))["refdes"]
    c.eq("refdes_x/y 绝对覆盖 x", _ra.get("x"), 1.0)
    c.eq("refdes_x/y 绝对覆盖 y", _ra.get("y"), 2.0)

    _rt = G.solve(dict(G.SPEC_3S48000163,
                       refdes_targets="refdes_assembly"))["refdes"]
    c.chk("refdes_targets 字符串 → 列表",
          _rt.get("targets") == ["refdes_assembly"],
          _rt.get("targets"), "['refdes_assembly']")
    _rt2 = G.solve(dict(G.SPEC_3S48000163,
                        refdes_targets=["refdes", "refdes_assembly"]))["refdes"]
    c.chk("refdes_targets 列表原样透传",
          _rt2.get("targets") == ["refdes", "refdes_assembly"],
          _rt2.get("targets"), "['refdes', 'refdes_assembly']")
    # 执行器默认层必须跟着走（否则编译会报 SPMHCS-1 或放错层）
    _fb_src = open(os.path.join(_ROOT, "services", "footprint_builder.py"),
                   encoding="utf-8").read()
    c.chk("执行器 refdes 默认层 = refdes_assembly",
          'refdes.get("target", "refdes_assembly")' in _fb_src, True, True)
    c.chk("执行器 refdes 默认文字 = #REF",
          'refdes.get("text", "#REF")' in _fb_src, True, True)
    c.chk("执行器支持 justify 透传", "justify=justify" in _fb_src, True, True)
    c.chk("执行器层映射含 REF DES/ASSEMBLY_TOP",
          '"refdes_assembly": "REF DES/ASSEMBLY_TOP"' in _fb_src, True, True)

    # ---- 影子库「同目录」约定守卫（2026-09-30：原「挂 padpath」项已关闭）----
    #   现有模板库 14/14 都是 dra + pad 同目录；实测 .dra 自带 padstack 副本，
    #   缺 .pad 也能读 → 所以只需保证同目录落位，不写 env / 不改 padpath。
    from services import library_install as LI
    import tempfile as _tf
    _tmp = _tf.mkdtemp(prefix="coloc_")
    try:
        _ok_dir = os.path.join(_tmp, "ok")
        _or_dir = os.path.join(_tmp, "orphan")
        os.makedirs(_ok_dir)
        os.makedirs(_or_dir)
        for _d in (_ok_dir, _or_dir):
            open(os.path.join(_d, "S.dra"), "w").close()
        open(os.path.join(_ok_dir, "R1D4X1D2.pad"), "w").close()
        _c1 = LI.verify_colocation(_ok_dir)
        _c2 = LI.verify_colocation(_or_dir)
        c.chk("同目录守卫：dra+pad → ok", _c1["ok"] is True, _c1["ok"], True)
        c.chk("同目录守卫：只有 dra → 报孤儿",
              _c2["ok"] is False and _c2["orphans"] == ["S"], _c2, "ok=False, orphans=['S']")
        c.chk("同目录守卫：目录不存在 → error",
              LI.verify_colocation(os.path.join(_tmp, "nope"))["error"] is not None,
              "无 error", "有 error")
    finally:
        import shutil as _sh
        _sh.rmtree(_tmp, ignore_errors=True)
    _li_src = open(os.path.join(_ROOT, "services", "library_install.py"),
                   encoding="utf-8").read()
    c.chk("library_install 不调用任何挂载 API（无 axlSetVariable）",
          "axlSetVariable" not in _li_src, True, True)
    c.chk("library_install 写明「不需要挂 padpath」的结论",
          "不需要挂 padpath" in _li_src, True, True)

    # 缺口上报：缺位号**不**阻断几何判据（阻断 = 白唤起一次 Allegro）
    c.chk("缺口=空（正常件）",
          FE._extracta_gaps({"texts": [1], "ref_des": [1]}) == [], "有缺口", "[]")
    c.chk("缺口=报出（无位号）",
          len(FE._extracta_gaps({"texts": [1], "ref_des": []})) == 1, "未报出", "1 条")
    c.chk("缺口优先用 reader 给的 _gaps",
          FE._extracta_gaps({"_gaps": ["x"], "texts": [1], "ref_des": [1]}) == ["x"],
          "未优先", "['x']")
    c.chk("缺位号仍算几何完整（不回退）",
          FE._is_extracta_result_complete(
              {"pins": [{"pads": [1]}], "layers": {"assembly_top": [1]},
               "ref_des": []}), True, True)
    c.chk("缺几何才算不完整（回退）",
          not FE._is_extracta_result_complete({"pins": [], "layers": {}}), True, True)

    # ========================================================
    # [8] 图纸取景：「打开就居中」（2026-09-30 用户反馈）
    # ========================================================
    print("\n" + "=" * 68)
    print("  [8] 图纸取景 —— 防「封装缩在画布左下角」回归")
    print("=" * 68)
    _fb_src2 = open(os.path.join(_ROOT, "services", "footprint_builder.py"),
                    encoding="utf-8").read()
    _ss_src = open(os.path.join(_ROOT, "services", "skill_snippets.py"),
                   encoding="utf-8").read()
    _mcp_src = open(os.path.join(_ROOT, "mcp_server", "tools.py"),
                    encoding="utf-8").read()
    import inspect as _insp
    from services import footprint_builder as _FB2
    from services import skill_snippets as S

    c.chk("存在 frame_drawing()", callable(getattr(_FB2, "frame_drawing", None)),
          "缺失", "callable")

    # ★ 顺序铁律：必须先 frame（设视图）再 save —— .dra 存的是"上一帧视图"
    #   注意用 rfind：export_psm 里也有一处 save_drawing，取最后一处才是
    #   build_footprint 的那一次。
    _i_frame = _fb_src2.find('r = frame_drawing(workspace_id=workspace_id)')
    _i_save = _fb_src2.rfind('r = save_drawing(workspace_id=workspace_id)')
    c.chk("frame 在 save 之前调用",
          -1 < _i_frame < _i_save, f"frame@{_i_frame} save@{_i_save}",
          "frame < save")

    _sig = _insp.signature(_FB2.frame_drawing).parameters
    c.chk("frame_drawing 默认 max_iter >= 3",
          int(_sig["max_iter"].default) >= 3, _sig["max_iter"].default, ">=3")
    c.chk("frame_drawing 默认 extent_scale > 1",
          float(_sig["extent_scale"].default) > 1.0,
          _sig["extent_scale"].default, ">1.0")
    c.chk("frame_drawing 默认不动盘（save=False）",
          _sig["save"].default is False, _sig["save"].default, False)

    _bsig = _insp.signature(_FB2.build_footprint).parameters
    c.chk("build_footprint 有 frame_view 且默认 True",
          _bsig.get("frame_view") is not None
          and _bsig["frame_view"].default is True,
          _bsig.get("frame_view"), "默认 True")

    # ★ 结论必须写进注释/文档串 —— 防回归到"以为改 extents 就能改视图"
    c.chk("footprint_builder 写明「.dra 会存上一帧视图」",
          "上一帧视图" in _fb_src2, True, True)
    c.chk("footprint_builder 写明 zoomPoints 是「底左锚定」",
          "底左锚定" in _fb_src2, True, True)
    # ★ 反面教材也要留痕：axlZoomCenter 不兜底
    c.chk("skill_snippets 写明 zoomCenter 不兜底",
          "不会" in _ss_src and "对称化" in _ss_src, True, True)

    for _fn in ("build_zoom_points", "build_zoom_bbox", "build_zoom_center",
                "build_extent_db", "build_window_fit"):
        c.chk(f"skill_snippets 有 {_fn}()",
              callable(getattr(S, _fn, None)), "缺失", "callable")

    c.chk("MCP 注册了 frame_drawing_view 工具",
          "def frame_drawing_view" in _mcp_src, True, True)
    c.chk("MCP build_footprint_from_params 透传 frame_view",
          "frame_view=frame_view" in _mcp_src, True, True)

    # ========================================================
    # [9] FPC0510 6P —— 双 padstack + 显式引脚表（2026-09-30 加）
    #     COTEX FPC-0510 系列 0.5mm pitch H1.0 前掀盖下接触
    # ========================================================
    print("\n" + "=" * 68)
    print("  [9] FPC0510 6P —— 显式引脚表 / 双 padstack / 多 padstack 透传")
    print("=" * 68)
    g9 = G.solve(G.SPEC_FPC0510_6P)
    c.chk("SPEC_FPC0510_6P 求解成功",
          bool(g9.get("success")), g9.get("error"), "success")
    _p9 = {p["number"]: (p["x"], p["y"]) for p in g9.get("pins", [])}
    c.eq("FPC pin 数 = 8（6 信号 + 2 固定）", len(_p9), 8)
    c.eq("FPC pin1 @ (-1.25, -2.235)", _p9.get("1"), (-1.25, -2.235))
    c.eq("FPC pin6 @ (1.25, -2.235)（B=2.50）", _p9.get("6"), (1.25, -2.235))
    c.eq("FPC pin7 固定脚 @ (-2.135, 0.575)（D=4.27）",
         _p9.get("7"), (-2.135, 0.575))
    c.eq("FPC pin8 固定脚 @ (2.135, 0.575)", _p9.get("8"), (2.135, 0.575))
    c.eq("FPC 信号 padstack = R0D3X0D65（0.30×0.65）",
         g9["padstack"]["name"], "R0D3X0D65")
    c.chk("FPC 固定 padstack = R0D3X1D15（0.30×1.15）",
          [e["name"] for e in g9.get("extra_padstacks", [])] == ["R0D3X1D15"],
          [e["name"] for e in g9.get("extra_padstacks", [])], "['R0D3X1D15']")
    c.chk("FPC 每脚 padstack 引用正确",
          all(p.get("padstack") in ("R0D3X0D65", "R0D3X1D15")
              for p in g9["pins"]), True, True)
    c.eq("FPC 本体 = 4.77×3.60（A×C，CKT 表 6P 行）",
         [round(g9["body"]["x2"] - g9["body"]["x1"], 4),
          round(g9["body"]["y2"] - g9["body"]["y1"], 4)], [4.77, 3.6])
    c.eq("FPC Place_Bound = 6.47×5.30（connector 外扩 0.85）",
         [round(g9["place_bound"]["x2"] - g9["place_bound"]["x1"], 4),
          round(g9["place_bound"]["y2"] - g9["place_bound"]["y1"], 4)],
         [6.47, 5.3])
    c.chk("FPC Place_Bound 覆盖焊盘突出（pad 顶 -2.56 ≥ -2.65）",
          g9["place_bound"]["y1"] <= -2.56,
          g9["place_bound"]["y1"], "<= -2.56")
    c.chk("solve() 支持显式 symbol_name 覆盖（防 FPC05108 歧义名）",
          g9.get("symbol_name") == "NB_FPC0510_6P_4D77X3D6",
          g9.get("symbol_name"), "NB_FPC0510_6P_4D77X3D6")
    c.chk("build_footprint 支持 extra_padstacks",
          "extra_padstacks" in _insp.signature(_FB2.build_footprint).parameters,
          True, True)
    c.chk("MCP build_footprint_from_params 透传 extra_padstacks",
          "extra_padstacks=extra_padstacks" in _mcp_src, True, True)
    c.chk("builder pins 循环用每脚 padstack（p.get('padstack') or pad_name）",
          "p.get(\"padstack\") or pad_name" in _fb_src2, True, True)

    # ---- [9b] assembly 执行器 gap 回归（2026-10-12 修）----
    #   此前 build_footprint 的 assembly 阶段只画 body（纯本体矩形），
    #   solver 产出的 assembly 列表（本体框 + pin1_mark 三角）没有入口，
    #   FPC/LYDPJ 建库时 1 脚三角标只能 CLI 补画（fix_fpc/fix_lydpj 补位脚本）。
    #   修法：执行器加显式 `assembly` 参数逐项画；to_build_args 默认携带。
    print("\n  [9b] assembly 执行器 gap 回归 —— pin1_mark 一次成型")
    _a9 = G.to_build_args(g9)
    _asm9 = _a9.get("assembly") or []
    c.chk("to_build_args 的 assembly 列表含 pin1_mark",
          any(it.get("kind") == "pin1_mark" for it in _asm9), True, True)
    c.chk("to_build_args 有 assembly 时不再借用 body 通道（body=None）",
          _a9.get("body") is None, True, True)
    c.chk("build_footprint 支持 assembly 参数",
          "assembly" in _insp.signature(_FB2.build_footprint).parameters,
          True, True)
    c.chk("MCP build_footprint_from_params 透传 assembly",
          "assembly=assembly" in _mcp_src, True, True)
    c.chk("builder assembly 阶段逐项画（assembly 优先于 body）",
          ("if assembly is not None:" in _fb_src2
           and '_draw_outlines(assembly, "assembly", 0.10)' in _fb_src2),
          True, True)
    c.chk("drill_type 默认 OBLONG_DRILL（裸 'OBLONG' 是非法 holeType）",
          'drill.get("drill_type", "OBLONG_DRILL")' in _fb_src2, True, True)

    # ========================================================
    # [10] CSTJ-421-1010BG —— 首个**通孔（THT）槽孔**件
    #   规格书：Tools/test/CSTJ-421-1010BG.pdf p1「RECOMMEND PCB LAYOUT」
    #   分辨率 k = 10.70 pt/mm；原点 = 中心线 × 本体上边缘
    #   （所以每个引脚坐标都直接等于图上标注值 —— 见 SPEC 注释）
    # ========================================================
    print("\n" + "=" * 68)
    print("  [10] CSTJ-421-1010BG —— 通孔槽孔件 / 三 padstack / body_center")
    print("=" * 68)
    S10 = G.SPEC_CSTJ421_AJ35
    g10 = G.solve(S10)
    c.chk("SPEC_CSTJ421_AJ35 求解成功",
          bool(g10.get("success")), g10.get("error"), "success")
    _p10 = {p["number"]: (p["x"], p["y"]) for p in g10.get("pins", [])}
    c.eq("CSTJ 引脚数 = 6", len(_p10), 6)
    # ★ Y 值 = 图上标注（本体上边缘为基准，向下为负）
    c.eq("CSTJ pin1 @ (-4.14, -2.75)（图上 4.85&3.43 / 2.75）",
         _p10.get("1"), (-4.14, -2.75))
    c.eq("CSTJ pin2 @ (4.25, -2.75)（图上 4.25 / 2.75）",
         _p10.get("2"), (4.25, -2.75))
    c.eq("CSTJ pin3 @ (-4.23, -5.35)（图上 5.35）",
         _p10.get("3"), (-4.23, -5.35))
    c.eq("CSTJ pin4 @ (-4.45, -11.50)（图上 4.45 / 11.50）",
         _p10.get("4"), (-4.45, -11.50))
    c.eq("CSTJ pin5 @ (4.55, -9.00)（图上 4.55 / 9.00）",
         _p10.get("5"), (4.55, -9.00))
    c.eq("CSTJ pin6 @ (4.55, -11.60)（图上 4.55 / 11.60）",
         _p10.get("6"), (4.55, -11.60))
    # ---- padstack：三种，且都带槽孔 ----
    c.eq("CSTJ 主 padstack = OB1D2X2D2（1.20×2.20）",
         g10["padstack"]["name"], "OB1D2X2D2")
    c.chk("CSTJ 主 padstack 走 THT 槽孔支路（不带 PASTEMASK、焊盘=图上尺寸）",
          [ld["layer"] for ld in g10["padstack"]["layers"]]
          == ["TOP", "SOLDERMASK_TOP"]
          and g10["padstack"]["layers"][0]["size"] == [1.2, 2.2],
          [ld["layer"] for ld in g10["padstack"]["layers"]], "['TOP','SOLDERMASK_TOP']")
    c.eq("CSTJ 主孔 = 槽孔 0.60×1.60",
         g10["padstack"]["drill"]["size"], [0.6, 1.6])
    c.eq("CSTJ 孔型 = OBLONG_DRILL",
         g10["padstack"]["drill"]["drill_type"], "OBLONG_DRILL")
    _e10 = {e["name"]: e for e in g10.get("extra_padstacks", [])}
    c.chk("CSTJ 额外 padstack 数 = 2（名字集合）",
          sorted(_e10) == ["OB1D2X2D0", "R1D42X1D4"],
          sorted(_e10), "['OB1D2X2D0', 'R1D42X1D4']")
    c.eq("CSTJ 5/6 号 padstack 孔 = 0.60×1.40（图上 2-2.00）",
         _e10["OB1D2X2D0"]["drill"]["size"], [0.6, 1.4])
    c.eq("CSTJ 5/6 号焊盘 = 1.20×2.00",
         _e10["OB1D2X2D0"]["layers"][0]["size"], [1.2, 2.0])
    c.eq("CSTJ 1 号焊盘 = 矩形 1.42×1.40（图上 4.85-3.43）",
         _e10["R1D42X1D4"]["layers"][0]["size"], [1.42, 1.4])
    c.eq("CSTJ 1 号孔 = 0.70×1.00",
         _e10["R1D42X1D4"]["drill"]["size"], [0.7, 1.0])
    c.chk("CSTJ 每脚 padstack 引用正确",
          [p.get("padstack") for p in g10["pins"]]
          == ["R1D42X1D4", "OB1D2X2D2", "OB1D2X2D2",
              "OB1D2X2D2", "OB1D2X2D0", "OB1D2X2D0"],
          [p.get("padstack") for p in g10["pins"]],
          "['R1D42X1D4','OB1D2X2D2'x3,'OB1D2X2D0'x2]")
    # ---- body_center：本体中心偏移 ----
    c.eq("CSTJ 本体 = 7.59×12.20",
         [round(g10["body"]["x2"] - g10["body"]["x1"], 4),
          round(g10["body"]["y2"] - g10["body"]["y1"], 4)], [7.59, 12.2])
    c.eq("CSTJ 本体中心 = (-0.15, -6.10)（body_center 生效）",
         [round((g10["body"]["x1"] + g10["body"]["x2"]) / 2, 4),
          round((g10["body"]["y1"] + g10["body"]["y2"]) / 2, 4)], [-0.15, -6.1])
    c.chk("CSTJ 无 body_center 时本体仍居中（默认行为不回退）",
          round(G.solve(dict(S10, body_center=None))["body"]["x1"], 4)
          == -3.795,
          G.solve(dict(S10, body_center=None))["body"]["x1"], "-3.795")
    # ---- place_bound 外扩覆盖外伸焊盘 ----
    _pb10 = g10["place_bound"]
    c.chk("CSTJ Place_Bound 覆盖最外焊盘（X 需 ≤-5.05 / ≥+5.15）",
          _pb10["x1"] <= -5.05 and _pb10["x2"] >= 5.15,
          (_pb10["x1"], _pb10["x2"]), "x1<=-5.05 且 x2>=5.15")
    c.chk("CSTJ place_bound_expand_mm 逐件覆盖生效（1.60 而非 connector 0.85）",
          round((_pb10["x2"] - _pb10["x1"]) - 7.59, 4) == 3.2,
          _pb10["x2"] - _pb10["x1"], "7.59+3.20=10.79")
    c.chk("CSTJ 无 Place_Bound 过小告警",
          not any("Place_Bound" in w for w in g10.get("warnings") or []),
          g10.get("warnings"), "[]")
    # ---- 位号 / 丝印 ----
    c.eq("CSTJ 位号跟随本体中心", (g10["refdes"]["x"], g10["refdes"]["y"]),
         (-0.15, -6.1))
    c.chk("CSTJ 丝印非空且含 1 脚标识",
          bool(g10["silk"]) and any(s.get("kind") == "pin1_mark"
                                    for s in g10["silk"]),
          [s.get("kind") for s in g10["silk"]], "含 pin1_mark")
    # ★ 只校验 outline 那几条线 —— 1 脚三角是**贴在 1 脚焊盘旁**的，
    #   而 1 脚焊盘本身就在本体框外（-4.14 vs 框边 -3.945），越界属正常。
    c.chk("CSTJ 丝印 outline 不越出 Assembly 轮廓",
          all(-3.945 - 1e-6 <= x <= 3.645 + 1e-6
              and -12.2 - 1e-6 <= y <= 0 + 1e-6
              for s in g10["silk"] if s.get("kind") == "outline"
              for seg in s.get("lines", []) for x, y in seg),
          [seg for s in g10["silk"] if s.get("kind") == "outline"
           for seg in s.get("lines", [])], "全在框内")
    c.chk("CSTJ 1 脚三角锚在 1 脚焊盘附近（X 对齐 ±0.5 / Y 距 ≤1.5）",
          all(abs(x - (-4.14)) <= 0.5 and abs(y - (-2.75)) <= 1.5
              for s in g10["silk"] if s.get("kind") == "pin1_mark"
              for seg in s.get("lines", []) for x, y in seg),
          [(x, y) for s in g10["silk"] if s.get("kind") == "pin1_mark"
           for seg in s.get("lines", []) for x, y in seg], "X≈-4.14, Y∈[-4.25,-1.25]")
    # ---- derivations 可追溯 ----
    c.chk("CSTJ body_center 记进 derivations",
          any(d.get("item") == "body_center" for d in g10["derivations"]),
          [d.get("item") for d in g10["derivations"]], "含 body_center")

    # ========================================================
    # 汇总
    # ========================================================
    total = c.ok + c.bad
    print("\n" + "=" * 68)
    if c.bad:
        print(f"  结果: {c.ok}/{total} 通过，{c.bad} 失败")
        for n, d in c.failed:
            print(f"    ✗ {n}: {d}")
        return 1
    print(f"  结果: {c.ok}/{total} 全部通过 ✓")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
