# -*- coding: utf-8 -*-
"""
tools/selfcheck_skill_syntax.py —— SKILL 表达式语法自检（不需要 Allegro）

为什么需要这个脚本：
  在 probe 阶段，我们踩过的坑有 90% 是"表达式本身写错了" ——
  括号不平衡、参数顺序颠倒、符号与字符串混淆、把 API 名字记错。
  这些错误在 Allegro 侧的表现是含糊的 *Error* 提示，排查成本极高。

  而这个脚本在 Python 侧就能查出全部这类问题，且**不需要 Allegro 在跑**。

检查项：
  1. 括号平衡 + 字符串闭合（复用 skill_snippets._check_balance）
  2. 纯 ASCII（Allegro 对非 ASCII 不友好）
  3. 引用的 AXL 函数名在白名单内（防"记了一个不存在的函数"）
  4. 关键函数的参数顺序（这是最隐蔽的坑）

用法：
    python tools/selfcheck_skill_syntax.py
    python tools/selfcheck_skill_syntax.py -v     # 打印每条表达式

退出码：
    0 = 全部通过
    1 = 有失败项
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# 让脚本能从项目根目录 import services
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services import skill_snippets as S  # noqa: E402


# ============================================================
# AXL 函数白名单
# ============================================================
# 只列**已核实存在**的函数。故意不列 axlExtentSet / axlNewDrawing /
# make_axlTextBlock —— 这三个不存在，正是我们要防的重犯错误。
KNOWN_AXL_FUNCS = {
    # padstack
    "axlDBCreatePadStack",
    "axlPadstackToDisk",
    "make_axlPadStackPad",
    "make_axlPadStackDrill",
    "copy_axlPadStackPad",
    # pin
    "axlDBCreatePin",
    "make_axlPinText",
    "make_axlTextOrientation",
    # 图形
    "axlDBCreateLine",
    "axlDBCreatePath",
    "axlDBCreateRectangle",
    "axlDBCreateOpenShape",
    "axlDBCreateShape",
    "axlDBCreateText",
    # drawing 控制
    "axlDBChangeDesignExtents",
    "axlDBChangeDesignOrigin",
    # ★ 视图窗口（2026-09-30）—— 文档 08intprm.html
    #   `axlZoomBbox` 是唯一能"读回当前视图"的 API；
    #   `axlZoomPoints` 底左锚定 + 按窗口宽高比外扩；
    #   `axlExtentDB` 是几何外包框（窗口 fit 用），**不是**数据库范围。
    "axlZoomPoints",
    "axlZoomBbox",
    "axlZoomCenter",
    "axlExtentDB",
    "axlWindowFit",
    "axlOpenDesign",
    "axlSaveDesign",
    # ★ 新建 / 重置图纸（2026-09-29 真调用法复测确认存在；
    #   早期"unbound"结论是裸符号探测法的误判）
    "axlKillDesign",             # 零参新建空图纸，等价 File→New
    "axlOpenDesignForBatch",     # batch 版；★ 2 个位置参数
    "axlRenameDesign",           # 改当前图纸名（不影响磁盘文件）
    "axlDBChangeDesignUnits",    # 改设计单位
    # ★ 清空图纸
    "axlDeleteByLayer",          # 按层删数据（★ 不删 pin/via）
    "axlDeleteObject",           # 按 dbid 删（★ 删 pin 只能靠它）
    "axlEraseObject",            # ⚠️ 只是临时隐藏，不是删除
    "axlPurgePadstacks",         # 清未用 padstack
    # ★ 出 .psm（2026-09-29 实测确认存在；是 File→Create Symbol 的 API）
    "axlCompileSymbol",
    "axlSetSymbolType",
    # 查询
    "axlCurrentDesign",
    "axlGetDrawingName",
    "axlDBGetDesignUnits",
    "axlDBGetDesign",
    "axlDesignType",
    "axlIsSymbolEditor",
    # padstack 存在性 / 枚举（2026-09-29 更正：这两条都能用）
    "axlLoadPadstack",
    "axlPurgePadstack",
    # ★ 环境变量（NOCONFIRM 族 —— 弹框的"变量级"总开关）
    #   文档：algroskill/09cmdshl.html
    "axlGetVariable",
    "axlSetVariable",
    "axlUnsetVariable",
}

# 明确"不存在"的函数名 —— 若表达式里出现，直接判失败并给出正确替代
FORBIDDEN_FUNCS = {
    "axlExtentSet": "改用 axlDBChangeDesignExtents",
    "axlNewDrawing": "AXL 确无此函数；但**不等于没法新建图纸** —— 用 axlKillDesign()（零参无 UI）",
    "make_axlTextBlock": "AXL 无此函数；text block 用名字字符串引用，如 \"1\"",
    "axlDBCreatepad": "正确为 axlDBCreatePadStack",
    # ★ 2026-09-29 更正：axlDBGetPadstacks 确实存在，但它是**宏**，
    #   顶层裸调用会报 `eval: unbound variable`。必须走内联形式：
    #       length(axlDBGetDesign()->padstacks)
    #   所以这里仍然禁止裸用，但在 KNOWN_AXL_FUNCS 里保留名字，
    #   并在 _BARE_PADSTACK_GETTER 里专门拦截"裸调用"这一种写法。
    "axlDBGetPadstack": "宏，且拼写少了复数 s；正确用 axlDBGetDesign()->padstacks 或 axlLoadPadstack(name)",
    "axlPadstackGetList": "不存在；用 mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)",
    "axlDBCreatePad": "正确为 axlDBCreatePadStack",
    "axlPadstackEdit": "只能改全局属性，不能编辑单层 pad —— 不要用它改 pad 层",
    # ★ 2026-09-29：出 .psm 的正确 API 是 axlCompileSymbol，不是这些
    "axlCompCreate": "不存在；出 .psm 用 axlCompileSymbol(?symbol \"名字\" ?type \"package\")",
    "axlSaveSymbol": "不存在；出 .psm 用 axlCompileSymbol",
    "axlDBSaveSymbol": "不存在；出 .psm 用 axlCompileSymbol",
}

# ★ 禁止"裸调用"的宏/变量形式（会报 eval: unbound variable）
_BARE_EXPR_FORBIDDEN = {
    "axlDBGetPadstacks": (
        "axlDBGetPadstacks 是宏，顶层裸调用报 eval: unbound variable。"
        "请内联使用：length(axlDBGetDesign()->padstacks) 或 "
        "mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)"
    ),
}

# 允许的 SKILL 语言内建函数（不是 AXL，但会出现在表达式里）
BUILTIN_FUNCS = {
    "list", "errset", "cons", "car", "cdr", "quote", "setq", "let",
    "if", "when", "unless", "prog", "foreach", "lambda", "defun",
    "sprintf", "concat", "getd", "nil", "t",
}

# ★★★ 语法铁律：AXL 函数必须以 `func(args)` 形式调用 ★★★
# `(axlFoo ...)` 在 eval 通道里会报 "eval: not a function"。
# 只有 SKILL 语言内建函数可以写成 `(fn args)` 形式。
_AXL_CALL_PATTERN = re.compile(
    r"\((axl[A-Za-z0-9_]*|make_axl[A-Za-z0-9_]*|copy_axl[A-Za-z0-9_]*)"
    r"(?=[\s)])"
)


# ============================================================
# 检查器
# ============================================================
class Checker:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    # ---------- 基础 ----------
    def _ok(self, name: str, expr: str) -> None:
        self.passed += 1
        if self.verbose:
            print(f"  [OK]   {name}: {expr}")

    def _bad(self, name: str, expr: str, why: str) -> None:
        self.failed += 1
        self.failures.append(f"{name}: {why}\n        expr = {expr}")
        print(f"  [FAIL] {name}: {why}")
        print(f"         expr = {expr}")

    # ---------- 逐项检查 ----------
    def check_expr(self, name: str, expr: str) -> None:
        """对单条表达式跑全套检查。"""
        # 1) ★ 语法铁律：AXL 函数不能写成 (axlFoo ...)
        m = _AXL_CALL_PATTERN.search(expr)
        if m:
            self._bad(
                name, expr,
                f"AXL 函数 {m.group(1)} 被写成 `(fn ...)` 形式 —— "
                f"eval 通道会报 'not a function'。必须用 `{m.group(1)}(...)`",
            )
            return

        # 2) 括号平衡 + 字符串闭合
        try:
            S._check_balance(expr)
        except ValueError as e:
            self._bad(name, expr, f"括号/字符串问题 -> {e}")
            return

        # 3) 纯 ASCII
        try:
            expr.encode("ascii")
        except UnicodeEncodeError as e:
            self._bad(name, expr, f"含非 ASCII 字符 -> {e}")
            return

        # 4) 禁用函数（用词边界匹配，避免 axlDBCreatePad 误命中 axlDBCreatePadStack）
        for bad_fn, hint in FORBIDDEN_FUNCS.items():
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(bad_fn)}(?![A-Za-z0-9_])", expr):
                self._bad(name, expr, f"使用了不存在的函数 {bad_fn}（{hint}）")
                return

        # 4b) 禁止裸调用宏/变量形式（会报 eval: unbound variable）
        for bad_expr, hint in _BARE_EXPR_FORBIDDEN.items():
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(bad_expr)}(?![A-Za-z0-9_])", expr):
                self._bad(name, expr, hint)
                return

        # 5) 未知函数（只对 axl/make_axl 前缀查，其它前缀放过）
        for fn in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr):
            if fn.startswith("axl") or fn.startswith("make_axl") or fn.startswith("copy_"):
                if fn not in KNOWN_AXL_FUNCS:
                    self._bad(name, expr, f"疑似未知 AXL 函数: {fn}")
                    return

        self._ok(name, expr)

    def check_exprs(self, name: str, exprs) -> None:
        for i, e in enumerate(exprs):
            self.check_expr(f"{name}[{i}]", e)

    # ---------- 参数顺序专项（最隐蔽的坑）----------
    def check_param_order(self) -> None:
        print("\n[参数顺序专项] 这几个顺序写反了不会报错，只会静默出错")

        # axlDBCreateLine( points  WIDTH  LAYER )
        e = S.build_line(0, 0, 1, 1, "PACKAGE GEOMETRY/SILKSCREEN_TOP", 0.15)
        m = re.match(
            r"axlDBCreateLine\(\s*.*?\s+([\d.]+)\s+\"([^\"]+)\"\s*\)$", e
        )
        if not m:
            self._bad("line_param_order", e,
                      "顺序应为 (points width layer)，且 width 是数值、layer 是字符串")
        else:
            self._ok("line_param_order", e)

        # axlDBCreateRectangle( bBox  FILL  LAYER )
        e = S.build_filled_rect(-1, -1, 1, 1, "PACKAGE GEOMETRY/PLACE_BOUND_TOP", True)
        m = re.match(
            r"axlDBCreateRectangle\(\s*.*?\s+(t|nil)\s+\"([^\"]+)\"\s*\)$", e
        )
        if not m:
            self._bad("rect_param_order", e,
                      "顺序应为 (bBox fill layer)，fill 在层之前")
        else:
            self._ok("rect_param_order", e)

        # axlDBCreatePadStack( name  drill  pad_list  nocheck )
        layers = [S.build_pad_layer("TOP", "RECTANGLE", (1.4, 1.2))]
        e = S.build_create_padstack("TESTPAD", layers, None, True)
        m = re.match(
            r"axlDBCreatePadStack\(\"[^\"]+\"\s+nil\s+list\(.*\)\s+t\)$", e
        )
        if not m:
            self._bad("padstack_param_order", e,
                      "顺序应为 (name drill padList nocheck)")
        else:
            self._ok("padstack_param_order", e)

        # 加 errset 包裹后仍应正确
        g = S.build_create_padstack_guarded("TESTPAD", layers, None, True)
        if not (g.startswith("errset(axlDBCreatePadStack(") and g.endswith("))")):
            self._bad("padstack_guarded_form", g,
                      "errset 包裹形式应为 errset(axlDBCreatePadStack(...))")
        else:
            self._ok("padstack_guarded_form", g)

        # make_axlTextOrientation 不能出现 ?xCoord/?yCoord
        o = S.build_text_orientation(rotation=90, mirrored=True, justify="right")
        if "?xCoord" in o or "?yCoord" in o:
            self._bad("textorient_no_coord", o,
                      "make_axlTextOrientation 没有坐标参数（官方示例核实）")
        else:
            self._ok("textorient_no_coord", o)

        # make_axlPinText 的 ?number 必须是字符串、?offset 必须是坐标对
        p = S.build_pin_text("1", offset=(-0.5, 0.5), text="1")
        if '?number "1"' not in p:
            self._bad("pintext_number_str", p, "?number 必须是带引号的字符串")
        elif "?offset -0.5:0.5" not in p:
            self._bad("pintext_offset_pair", p, "?offset 必须是 x:y 坐标对，不能是标量")
        else:
            self._ok("pintext_number_str", p)
            self._ok("pintext_offset_pair", p)

        # ★ 2026-10-09 回归：?text 禁止字符串形式 —— 数字串被 Allegro 当成
        #   字块号（实测脚号几字块几、字号随脚号递增），必须走结构体
        if "make_axlTextOrientation" not in p or '?textBlock "1"' not in p:
            self._bad("pintext_struct_form", p,
                      "?text 必须传 make_axlTextOrientation 结构体"
                      "（字符串形式会被当成字块号，字号随脚号漂移）")
        else:
            self._ok("pintext_struct_form", p)

        # make_axlPadStackDrill 的字段名（★ 不是 ?diameter / ?plated）
        d = S.build_drill(0.3)
        if "?drillDiameter" not in d:
            self._bad("drill_field_diameter", d,
                      "字段名应为 ?drillDiameter（官方 pad.il 范例核实）")
        elif "?plating" not in d:
            self._bad("drill_field_plating", d,
                      "字段名应为 ?plating（取 'PLATED / 'NON_PLATED）")
        elif "?diameter" in d.replace("?drillDiameter", ""):
            self._bad("drill_no_bare_diameter", d,
                      "不应出现裸 ?diameter —— 会报 defstruct field name not known")
        else:
            self._ok("drill_field_names", d)

        # build_create_pin 必须被 errset 包住
        e = S.build_create_pin("TESTPAD", 0, 0, None, None)
        if not (e.startswith("errset(") and e.endswith("))")):
            self._bad("pin_errset_wrapped", e,
                      "axlDBCreatePin 必须用 errset 包，否则报错会打断整个脚本")
        else:
            self._ok("pin_errset_wrapped", e)

        # ★ axlSaveDesign 必须带 ?noConfirm t，否则弹交互框卡死连接
        sv = S.build_save()
        if "?noConfirm t" not in sv:
            self._bad("save_needs_noconfirm", sv,
                      "axlSaveDesign 必须带 ?noConfirm t —— 不带会弹确认框，"
                      "把 SkillBridge 连接卡死（实测 ConnectionAbortedError）")
        else:
            self._ok("save_needs_noconfirm", sv)

        # 点列表必须是 list(list(...) list(...)) 形式
        pl = S.pt_list((-1, -1), (1, 1))
        if not pl.startswith("list(list("):
            self._bad("pt_list_form", pl,
                      "点列表必须写成 list(list(x y) list(x y)) —— "
                      "裸写 list((x y) (y z)) 会报 eval: not a function")
        else:
            self._ok("pt_list_form", pl)


# ============================================================
# 用例集
# ============================================================
def run_all(verbose: bool = False) -> int:
    c = Checker(verbose=verbose)

    print("=" * 68)
    print("SKILL 表达式语法自检（不需要 Allegro）")
    print("=" * 68)

    # ---- 1. 数值格式化 ----
    print("\n[1] 数值格式化")
    cases = [
        (1.40, "1.4"), (1.0, "1"), (0.0, "0"),
        (-0.0, "0"), (2.500, "2.5"), (25.4, "25.4"),
    ]
    for v, want in cases:
        got = S.fmt_num(v)
        if got == want:
            c._ok(f"fmt_num({v})", got)
        else:
            c._bad(f"fmt_num({v})", got, f"期望 {want}")

    # ---- 2. 基础构造器 ----
    print("\n[2] 基础构造器")
    c.check_expr("sym_plain", S.sym("REGULAR"))
    c.check_expr("sym_already", S.sym("'REGULAR"))
    c.check_expr("q_plain", S.q("TOP"))
    c.check_expr("q_escape", S.q('A"B'))
    c.check_expr("fmt_point", S.fmt_point(1.4, 1.2))

    # sym 不应产生双撇号
    if S.sym("'REGULAR") == "'REGULAR":
        c._ok("sym_no_double_quote", S.sym("'REGULAR"))
    else:
        c._bad("sym_no_double_quote", S.sym("'REGULAR"), "不应出现 ''REGULAR")

    # q 应正确转义
    if S.q('A"B') == '"A\\"B"':
        c._ok("q_escape_check", S.q('A"B'))
    else:
        c._bad("q_escape_check", S.q('A"B'), '期望 "A\\"B"')

    # ---- 3. padstack ----
    print("\n[3] padstack 构造")
    c.check_expr("pad_layer_rect",
                 S.build_pad_layer("TOP", "RECTANGLE", (1.4, 1.2)))
    c.check_expr("pad_layer_circle",
                 S.build_pad_layer("TOP", "CIRCLE", (0.6, 0.6)))
    c.check_expr("pad_layer_thermal",
                 S.build_pad_layer("TOP", "CIRCLE", (0.9, 0.9), pad_type="THERMAL"))
    c.check_expr("drill_circle", S.build_drill(0.3, plated=True))
    c.check_expr("drill_npth", S.build_drill(0.3, plated=False))
    c.check_expr("drill_oblong",
                 S.build_drill(0.0, plated=True, drill_type="OBLONG",
                               size=(0.6, 0.3), offset=(0.0, 0.1)))
    c.check_expr("pad_list",
                 S.build_pad_list([S.build_pad_layer("TOP", "RECTANGLE", (1.4, 1.2))]))
    c.check_expr("pad_list_empty", S.build_pad_list([]))

    layers = [
        S.build_pad_layer("TOP", "RECTANGLE", (1.4, 1.2)),
        S.build_pad_layer("SOLDERMASK_TOP", "RECTANGLE", (1.5, 1.3)),
        S.build_pad_layer("PASTEMASK_TOP", "RECTANGLE", (1.4, 1.2)),
    ]
    c.check_expr("create_padstack", S.build_create_padstack("R1D4X1D2", layers, None, True))
    c.check_expr("create_padstack_guarded",
                 S.build_create_padstack_guarded("R1D4X1D2", layers, None, True))
    c.check_expr("padstack_to_disk_named", S.build_padstack_to_disk("R1D4X1D2"))
    c.check_expr("padstack_to_disk_all", S.build_padstack_to_disk())
    c.check_expr("padstack_exists_check",
                 S.build_padstack_exists_check("R1D4X1D2"))
    c.check_expr("padstack_name_list", S.build_padstack_name_list())

    # ★ 存在性探测必须是 axlLoadPadstack，不能退回 isFile
    pe = S.build_padstack_exists_check("R1D4X1D2")
    if "axlLoadPadstack" not in pe:
        c._bad("padstack_probe_uses_loadpadstack", pe,
               "存在性探测应使用 axlLoadPadstack —— "
               "isFile 走磁盘，漏判'内存持有但磁盘已删'的情况（实测踩过）")
    elif 'axlLoadPadstack("R1D4X1D2")' != pe:
        c._bad("padstack_probe_exact_form", pe,
               '应为 axlLoadPadstack("R1D4X1D2") 形式')
    else:
        c._ok("padstack_probe_uses_loadpadstack", pe)

    # ★ 全量名单必须是 mapcar + lambda((ps) ...) + -> 内联形式
    pl = S.build_padstack_name_list()
    if "mapcar" not in pl or "axlDBGetDesign()->padstacks" not in pl:
        c._bad("padstack_list_form", pl,
               "全量名单应为 mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)")
    elif "lambda((ps)" not in pl:
        c._bad("padstack_list_lambda_params", pl,
               "lambda 形参表必须再包一层括号：lambda((ps) ...)；"
               "写成 lambda(ps ...) 会报 'lambda: t is reserved'")
    elif re.search(r"lambda\((t|lst)\b", pl):
        c._bad("padstack_list_lambda_name", pl,
               "lambda 形参名不要用 t / lst —— 报 'lambda: t is reserved and cannot be used'")
    else:
        c._ok("padstack_list_form", pl)

    # ---- 4. pin ----
    print("\n[4] pin 构造")
    c.check_expr("pin_text_simple", S.build_pin_text("1", offset=(0.0, 0.0), text="1"))
    c.check_expr("pin_text_offset", S.build_pin_text("2", offset=(-0.5, 0.8), text="2"))
    c.check_expr("pin_text_with_orient",
                 S.build_pin_text("3", offset=(0.0, 0.0),
                                  orientation_expr=S.build_text_orientation(90, True, "right")))
    c.check_expr("create_pin_noref",
                 S.build_create_pin("R1D4X1D2", -1.1, 0.85, None, None))
    c.check_expr("create_pin_text",
                 S.build_create_pin("R1D4X1D2", 1.1, -0.85,
                                    S.build_pin_text("4"), None))
    c.check_expr("create_pin_rotated",
                 S.build_create_pin("R1D4X1D2", 0, 0,
                                    S.build_pin_text("1"), 90.0))

    # ---- 5. 图形 ----
    print("\n[5] 图形构造")
    c.check_expr("line", S.build_line(-1, -1, 1, -1, "PACKAGE GEOMETRY/SILKSCREEN_TOP", 0.15))
    c.check_exprs("rect_lines",
                  S.build_rect_lines(-1.7, -1.35, 1.7, 1.35,
                                     "PACKAGE GEOMETRY/SILKSCREEN_TOP", 0.15))
    c.check_expr("filled_rect",
                 S.build_filled_rect(-1.7, -1.35, 1.7, 1.35,
                                     "PACKAGE GEOMETRY/PLACE_BOUND_TOP", True))
    c.check_expr("hollow_rect",
                 S.build_filled_rect(-1.7, -1.35, 1.7, 1.35,
                                     "PACKAGE GEOMETRY/PLACE_BOUND_TOP", False))

    # ---- 6. 文字 ----
    print("\n[6] 文字构造")
    c.check_expr("text_orient", S.build_text_orientation())
    c.check_expr("text_orient_full", S.build_text_orientation(90, True, "right", "2"))
    c.check_expr("text", S.build_text("U*", 0.0, -2.0, "PACKAGE GEOMETRY/SILKSCREEN_TOP"))
    c.check_expr("text_rotated",
                 S.build_text("1", 0.0, 0.0, "PACKAGE GEOMETRY/SILKSCREEN_TOP",
                              text_block="2", rotation=90.0))
    c.check_expr("text_block", S.build_text_block("1"))

    # ---- 7. 范围 / 存盘 ----
    print("\n[7] 范围与存盘")
    c.check_expr("extent_set", S.build_extent_set())
    c.check_expr("extent_custom", S.build_extent_set(-10, -10, 10, 10))
    c.check_expr("change_origin", S.build_change_origin(1.0, 2.0))
    c.check_expr("save_default", S.build_save())
    c.check_expr("save_path", S.build_save("/tmp/x.dra"))
    c.check_expr("open_drawing", S.build_open_drawing("/tmp/x.dra", "wf"))

    # ---- 7b. ★ 出 .psm ----
    print("\n[7b] 出 .psm（axlCompileSymbol）")
    c.check_expr("compile_symbol_named",
                 S.build_compile_symbol("NB_XTAL4_3D2X2D5X0D7", "package"))
    c.check_expr("compile_symbol_noname", S.build_compile_symbol())
    c.check_expr("compile_symbol_notype", S.build_compile_symbol("FOO"))
    # ★ 绝不能出现 ?symbol nil —— 那会弹「另存为」框卡死通道
    _cs = S.build_compile_symbol("FOO", "package")
    if "?symbol nil" in _cs or "(?symbol nil" in _cs:
        c._bad("compile_symbol_no_nil", _cs,
               "禁止 ?symbol nil —— 会弹出「另存为」对话框把通道卡死")
    else:
        c._ok("compile_symbol_no_nil", _cs)

    # axlExtentSet 一定不能出现
    e = S.build_extent_set()
    if "axlExtentSet" in e:
        c._bad("extent_uses_correct_fn", e, "不应使用 axlExtentSet")
    else:
        c._ok("extent_uses_correct_fn", e)

    # ---- 7a2. ★ 视图窗口（Zoom）—— 让图纸"打开就居中" ----
    print("\n[7a2] 视图窗口（axlZoom*）")
    c.check_expr("zoom_points", S.build_zoom_points(-4.0, -4.0, 4.0, 4.0))
    c.check_expr("zoom_points_custom_window",
                 S.build_zoom_points(-1.0, -1.0, 1.0, 1.0, window="0"))
    c.check_expr("zoom_center", S.build_zoom_center(0.0, 0.0))
    c.check_expr("zoom_bbox_read", S.build_zoom_bbox("nil"))
    c.check_expr("extent_db_read", S.build_extent_db())
    c.check_expr("window_fit", S.build_window_fit())

    # ★ axlExtentDB 必须**无参**（多传参数只会被当"未知选项"忽略并告警）
    _edb = S.build_extent_db()
    if _edb != "axlExtentDB()":
        c._bad("extent_db_no_args", _edb, "axlExtentDB 无参，应为 axlExtentDB()")
    else:
        c._ok("extent_db_no_args", _edb)

    # ★ zoomPoints 的参数顺序：upperLeft 在前、lowerRight 在后
    #   （写反了视图会跑到"镜像"象限，肉眼看就是"封装不见了"）
    _zp = S.build_zoom_points(-4.0, -4.0, 4.0, 4.0)
    if _zp != "axlZoomPoints(nil list(4 4) list(-4 -4))":
        c._bad("zoom_points_arg_order", _zp,
               "应为 axlZoomPoints(nil <右上> <左下>) —— upperLeft 在前")
    else:
        c._ok("zoom_points_arg_order", _zp)

    # ★ 绝不能用 axlWindowFit 冒充"居中留白" —— 它是"占满窗口"
    _wf = S.build_window_fit()
    if "axlWindowFit" not in _wf:
        c._bad("window_fit_form", _wf, "应为 axlWindowFit()")
    else:
        c._ok("window_fit_form", _wf)

    # build_new_drawing_note 已被取代，必须仍抛 NotImplementedError（引导用新 API）
    try:
        S.build_new_drawing_note()
        c._bad("new_drawing_raises", "(none)", "应抛 NotImplementedError")
    except NotImplementedError:
        c._ok("new_drawing_raises", "NotImplementedError ✓")

    # ---- 7c. 新建 / 清空图纸 ----
    print("\n[7c] 新建 / 清空图纸（全自动化）")
    c.check_expr("kill_design", S.build_kill_design())
    c.check_expr("open_drawing",
                 S.build_open_drawing("C:/tmp/X.dra", "wf"))
    c.check_expr("open_drawing_batch",
                 S.build_open_drawing_for_batch("u.dra", "wf"))
    c.check_expr("delete_by_layer_one",
                 S.build_delete_by_layer("PACKAGE GEOMETRY/SILKSCREEN_TOP"))
    c.check_expr("delete_by_layer_list",
                 S.build_delete_by_layer(["ETCH/TOP", "ETCH/BOTTOM"]))
    c.check_expr("delete_by_layer_fixed",
                 S.build_delete_by_layer("BOARD GEOMETRY/OUTLINE", True))
    c.check_expr("delete_objects_pins", S.build_delete_objects("pins"))
    c.check_expr("delete_objects_ripup",
                 S.build_delete_objects("clines", ripup=True))
    c.check_exprs("clear_drawing", S.build_clear_drawing())

    # ★ 关键守卫 1：axlKillDesign 必须**零参数**调用
    _kd = S.build_kill_design()
    if "axlKillDesign()" not in _kd:
        c._bad("kill_design_zero_arg", _kd,
               "axlKillDesign 必须零参数调用")
    else:
        c._ok("kill_design_zero_arg", _kd)

    # ★ 关键守卫 2：axlOpenDesign 必须带 mode（默认 wf），否则丢编辑会弹确认框
    _od = S.build_open_drawing("C:/tmp/X.dra")
    if '"wf"' not in _od and "'wf" not in _od:
        c._bad("open_drawing_mode_wf", _od,
               "axlOpenDesign 必须显式传 \"wf\" 以免弹确认框")
    else:
        c._ok("open_drawing_mode_wf", _od)

    # ★ 关键守卫 3：axlOpenDesignForBatch 必须是**位置参数**，不是关键字参数
    _ob = S.build_open_drawing_for_batch("u.dra", "wf")
    if "?design" in _ob or "?mode" in _ob:
        c._bad("open_batch_positional", _ob,
               "axlOpenDesignForBatch 只有位置参数，不能用 ?design/?mode")
    else:
        c._ok("open_batch_positional", _ob)

    # ★ 关键守卫 4：删除对象必须先拍快照（mapcar），避免边遍历边改集合
    _do = S.build_delete_objects("pins")
    if "mapcar" not in _do:
        c._bad("delete_objects_snapshot", _do,
               "删除对象前必须 mapcar 拍快照，否则边遍历边删会让游标失效")
    else:
        c._ok("delete_objects_snapshot", _do)

    # ★ 关键守卫 5：axlDeleteByLayer 不能用于 pin（官方明确忽略 PIN 类）
    _cd = S.build_clear_drawing()
    if not any("axlDeleteObject" in e for e in _cd):
        c._bad("clear_drawing_covers_pins", str(_cd),
               "axlDeleteByLayer 不删 pin，清空图纸必须补 axlDeleteObject")
    else:
        c._ok("clear_drawing_covers_pins", "含 axlDeleteObject ✓")

    # ---- 7c-2. 模板污染校验用的内容计数 ----
    #   ★ 这个表达式极易写错：属性为空时 `axlDBGetDesign()->pins` 是 nil，
    #     直接 length(nil) 会报错。所以必须判空 + 兜底。
    for _prop in ("pins", "text", "padstacks"):
        c.check_expr(f"content_count_{_prop}", S.build_content_count(_prop))

    _cc = S.build_content_count("pins")
    for _need, _why in (
        ("length", "必须真的数长度，不能只判存在"),
        ("listp", "必须先判是否 list，nil 上取 length 会报错"),
    ):
        if _need in _cc:
            c._ok(f"content_count_has_{_need}", _cc)
        else:
            c._bad(f"content_count_has_{_need}", _cc, f"缺少 {_need}：{_why}")

    # 形参名不能是 t / lst —— 这两个在 SKILL 里有特殊语义
    import re as _re
    _params = _re.findall(r"lambda\(\((\w+)\)", _cc)
    if [p for p in _params if p in ("t", "lst")]:
        c._bad("content_count_param_name", _cc,
               "lambda 形参名不能用 t / lst（SKILL 里是保留语义）")
    else:
        c._ok("content_count_param_name", f"形参 {_params} ✓")

    # 属性名注入守卫：非法名必须被拒
    try:
        S.build_content_count("pins); axlFoo(")
        c._bad("content_count_reject_injection", "(未抛错)",
               "非法属性名必须抛 ValueError，否则能往表达式里注入代码")
    except ValueError:
        c._ok("content_count_reject_injection", "ValueError ✓")

    # ---- 7d. .dra 版本魔数探测（决定模板是否安全）----
    print("\n[7d] .dra 版本魔数探测")
    import tempfile as _tf
    from services.footprint_builder import (
        _dra_version_tag,
        _dra_major_version,
        _is_current_version_dra,
    )

    def _mk_dra(magic: bytes) -> str:
        """造一个假的 .dra（魔数落在 offset 248）"""
        fd, p = _tf.mkstemp(suffix=".dra")
        os.close(fd)
        with open(p, "wb") as f:
            f.write(b"\0" * 248 + magic + b"\0" * 64)
        return p

    # ★ 三种真实样本（2026-09-29 实测取样）
    _cases = [
        (b"all375891/28/",                      17, True,  "17.2 自建"),
        (b"allv17-08/24/.apdv16-02/22/",        17, True,  "17.2 官方符号"),
        (b"qvuv17-28/6/2dbdv16-5",              17, True,  "17.2 官方模板"),
        (b"qvuv16-04/12",                       16, False, "16.0 老版本(危险)"),
        (b"dbdv15-71/3",                        15, False, "15.7 老版本(危险)"),
    ]
    _tmp = []
    try:
        for magic, exp_major, exp_ok, label in _cases:
            _p = _mk_dra(magic)
            _tmp.append(_p)
            _maj = _dra_major_version(_p)
            _ok = _is_current_version_dra(_p)
            if _maj == exp_major and _ok == exp_ok:
                c._ok(f"dra_magic[{label}]",
                      f"major={_maj} current={_ok} ✓")
            else:
                c._bad(f"dra_magic[{label}]",
                       f"major={_maj} current={_ok}",
                       f"期望 major={exp_major} current={exp_ok}")

        # 回归守卫：官方符号库的 allv17 格式绝不能被误判为老版本
        #   （上一版 bug：只认 all375891，导致 3277 个官方符号全被拒）
        _p_of = _mk_dra(b"allv17-08/24/")
        _tmp.append(_p_of)
        if _dra_version_tag(_p_of) and _is_current_version_dra(_p_of):
            c._ok("dra_magic_allv17_regression",
                  "allv17-xx 格式被正确识别为 17.x ✓（旧版 bug 已修）")
        else:
            c._bad("dra_magic_allv17_regression",
                   str(_dra_version_tag(_p_of)),
                   "allv17-08 是 17.2 官方格式，必须判为当前版本")
    finally:
        for _p in _tmp:
            try:
                os.remove(_p)
            except OSError:
                pass

    # ---- 7e. 产物隔离 / 回滚（★ 编译前"搬走"旧 .psm，而不是删掉）----
    #   这一节的由来：2026-09-29 `.psm` 成功产出过一次（8640 B），
    #   重跑时被"覆盖保护"的 os.remove 删掉、随后编译失败 → 产物永久丢失。
    print("\n[7e] 产物隔离 / 回滚")
    from services import footprint_builder as _fb

    _qdir = _tf.mkdtemp(prefix="forgeq-").replace("\\", "/")
    _saved_trash = _fb._TRASH_DIR
    _fb._TRASH_DIR = _qdir + "/trash"          # ★ 指向临时目录，别污染项目 .trash
    try:
        _orig = _qdir + "/sample.psm"
        with open(_orig, "w", encoding="utf-8") as _f:
            _f.write("OLD-PSM-PAYLOAD")

        _q = _fb._quarantine_file(_orig, reason="selfcheck")
        if _q and not os.path.exists(_orig) and os.path.isfile(_q):
            c._ok("quarantine_moves_file", f"{os.path.basename(_q)}")
        else:
            c._bad("quarantine_moves_file", str(_q),
                   "隔离后原位置应为空、隔离件应存在")

        # 回滚必须把内容一字不差地搬回去
        if _q and _fb._restore_quarantined(_orig, _q) and os.path.isfile(_orig):
            with open(_orig, encoding="utf-8") as _f:
                _back = _f.read()
            if _back == "OLD-PSM-PAYLOAD":
                c._ok("restore_roundtrip", "内容一致 ✓")
            else:
                c._bad("restore_roundtrip", _back, "回滚后内容不一致，产物被损坏")
        else:
            c._bad("restore_roundtrip", str(_q), "回滚失败")

        # 回滚时原位已冒出"半成品"：半成品应被隔离，旧产物优先还原
        _q2 = _fb._quarantine_file(_orig, reason="selfcheck2")
        with open(_orig, "w", encoding="utf-8") as _f:
            _f.write("HALF-BAKED")
        if _q2 and _fb._restore_quarantined(_orig, _q2):
            with open(_orig, encoding="utf-8") as _f:
                _got = _f.read()
            if _got == "OLD-PSM-PAYLOAD":
                c._ok("restore_over_halfbaked", "旧产物优先还原 ✓")
            else:
                c._bad("restore_over_halfbaked", _got, "应还原旧产物")
        else:
            c._bad("restore_over_halfbaked", str(_q2), "回滚失败")

        # 文件不存在时必须返回 None —— 调用方据此中止，不能悄悄"成功"
        if _fb._quarantine_file(_qdir + "/nope.psm") is None:
            c._ok("quarantine_missing_returns_none", "None ✓")
        else:
            c._bad("quarantine_missing_returns_none", "非 None",
                   "目标不存在必须返回 None，否则调用方会误判为已挪走")
    finally:
        _fb._TRASH_DIR = _saved_trash
        import shutil as _sh
        _sh.rmtree(_qdir, ignore_errors=True)

    # ---- 7f. 编译日志诊断（★ 失败时把 Allegro 错误码翻成人话）----
    #   由来（2026-09-29 定论）：`axlCompileSymbol` 失败**只返 nil、不给原因**，
    #   长期被误判成"被弹框阻断"。真相是 Edit Check 报错 —— 原因全在
    #   `<symbol>.log` 里。这一节守住"读日志→翻译→给处方"这条链。
    print("\n[7f] 编译日志诊断（错误码 → 人话 + 处方）")
    from services.footprint_builder import (
        _COMPILE_LOG_CODES,
        _parse_symbol_log_text,
        _scan_symbol_compile_log,
        _summarize_symbol_log,
    )

    _log_sample = """(---------------------------------------------------------------------)
(    CREATE SYMBOL                                                    )
(---------------------------------------------------------------------)

Create Symbol of type: PACKAGE
    Name      = x.psm

Create symbol started.

 ERROR(SPMHCS-1): Symbol is missing a refdes.

ERROR(SPMHA1-291): Create symbol has been aborted.
"""
    _ent = _parse_symbol_log_text(_log_sample)
    _codes = [e["code"] for e in _ent]
    if _codes == ["SPMHCS-1", "SPMHA1-291"]:
        c._ok("log_parse_two_entries", f"{_codes} ✓")
    else:
        c._bad("log_parse_two_entries", str(_codes),
               "应只解析 ERROR(CODE) 行，忽略表头与普通正文")

    # 每个码都必须能给出中文解释 + 处方（不能只留英文原文）
    _no_meaning = [e["code"] for e in _ent
                   if not e.get("meaning") or not e.get("hint")]
    if not _no_meaning:
        c._ok("log_codes_have_meaning", "全部含人话+处方 ✓")
    else:
        c._bad("log_codes_have_meaning", str(_no_meaning),
               f"{_no_meaning} 缺中文解释/处方，需补进 _COMPILE_LOG_CODES")

    # 关键错误码必须全在映射表里（防以后手抖删掉）
    _need_codes = {"SPMHCS-1", "SPMHA1-298", "SPMHA1-301",
                   "SPMHA1-291", "SPMHA1-70", "SPMHGE-268"}
    _missing_codes = _need_codes - set(_COMPILE_LOG_CODES)
    if not _missing_codes:
        c._ok("log_codes_complete",
              f"{len(_COMPILE_LOG_CODES)} 个码 ✓")
    else:
        c._bad("log_codes_complete", str(_missing_codes),
               "关键错误码缺失")

    # 有 ERROR 时 summary 必须以 ERROR 为主，不能被 WARNING 顶掉
    _sw = _summarize_symbol_log(_parse_symbol_log_text(
        "WARNING(SPMHA1-298): dup\nERROR(SPMHCS-1): no refdes\n"))
    if _sw and "编译未通过" in _sw and "SPMHCS-1" in _sw:
        c._ok("log_summary_prefers_error", _sw[:44])
    else:
        c._bad("log_summary_prefers_error", str(_sw),
               "有 ERROR 时应以 ERROR 为主")

    if _summarize_symbol_log([]) is None:
        c._ok("log_summary_empty_none", "None ✓")
    else:
        c._bad("log_summary_empty_none", "非 None",
               "无条目应返回 None，否则调用方会误判为失败")

    # ★ 归集后的日志名带时间戳（<stem>_20260929-155731.log）也要能命中
    _scan_dir = _tf.mkdtemp(prefix="forgelog-").replace("\\", "/")
    try:
        _fp = os.path.join(_scan_dir, "mysym_20260929-155731.log")
        with open(_fp, "w", encoding="utf-8") as _f:
            _f.write("ERROR(SPMHA1-298): Create symbol duplicate pin numbers.\n")
        _d = _scan_symbol_compile_log([_scan_dir], "mysym")
        if _d.get("log_file") and \
                [e["code"] for e in _d["entries"]] == ["SPMHA1-298"]:
            c._ok("log_scan_timestamped_name",
                  os.path.basename(_d["log_file"]))
        else:
            c._bad("log_scan_timestamped_name", str(_d)[:90],
                   "应能匹配 <stem>_<时间戳>.log（collect_run_logs 的改名格式）")

        # 找不到日志时必须返回空壳而不是抛异常
        _d2 = _scan_symbol_compile_log([_scan_dir], "nonexistent_symbol_xyz")
        if _d2.get("log_file") is None and _d2.get("entries") == []:
            c._ok("log_scan_missing_safe", "空结果 ✓")
        else:
            c._bad("log_scan_missing_safe", str(_d2)[:90],
                   "找不到日志应返回空壳，不能抛异常")
    finally:
        import shutil as _sh2
        _sh2.rmtree(_scan_dir, ignore_errors=True)

    # ---- 8. 参数顺序专项 ----
    c.check_param_order()

    # ---- 9. 完整封装脚本 ----
    print("\n[9] 完整封装脚本（18 步组合）")
    pins = [
        {"number": "1", "x": -1.10, "y": 0.85},
        {"number": "2", "x": 1.10, "y": 0.85},
        {"number": "3", "x": -1.10, "y": -0.85},
        {"number": "4", "x": 1.10, "y": -0.85},
    ]
    script = S.build_full_footprint_script(
        padstack_name="R1D4X1D2",
        pad_layers=[
            S.build_pad_layer("TOP", "RECTANGLE", (1.4, 1.2)),
            S.build_pad_layer("SOLDERMASK_TOP", "RECTANGLE", (1.5, 1.3)),
            S.build_pad_layer("PASTMASK_TOP", "RECTANGLE", (1.4, 1.2)),
        ],
        pins=pins,
        silk={"x1": -1.7, "y1": -1.35, "x2": 1.7, "y2": 1.35, "width": 0.15},
        body={"x1": -1.6, "y1": -1.25, "x2": 1.6, "y2": 1.25, "width": 0.1},
        place_bound={"x1": -1.7, "y1": -1.35, "x2": 1.7, "y2": 1.35},
        refdes={"text": "U*", "x": 0.0, "y": -2.0, "text_block": "1"},
    )
    c.check_exprs("full_script", script)

    expected_steps = 1 + 1 + 4 + 4 + 4 + 1 + 1 + 1 + 1  # 18
    if len(script) == expected_steps:
        c._ok("full_script_step_count", f"{len(script)} 步（期望 {expected_steps}）")
    else:
        c._bad("full_script_step_count", f"{len(script)} 步", f"期望 {expected_steps} 步")

    # 顺序：extents 必须在 pin 之前
    if "axlDBChangeDesignExtents" in script[0] and "axlDBCreatePin" in script[2]:
        c._ok("full_script_order", "extents → padstack → pins ✓")
    else:
        c._bad("full_script_order", str(script[:3]),
               "路径应为 extents → padstack → pins")

    # 整脚本必须没有 (axlFoo ...) 形式
    bad_form = [e for e in script if _AXL_CALL_PATTERN.search(e)]
    if bad_form:
        c._bad("full_script_no_paren_call", bad_form[0],
               "脚本里存在 (axlFoo ...) 形式，会导致 eval: not a function")
    else:
        c._ok("full_script_no_paren_call", f"{len(script)} 步全部使用 func(args) 形式 ✓")

    # ---- 10. 回复帧解码 ----
    # 这一段覆盖 _decode_skill_reply：协议是 'success <python字面量>\n'，
    # 解码错会让所有 API 返回值失真（如 nil 变成字符串 'nil'）。
    print("\n[10] skillbridge 回复帧解码")
    try:
        from services.skillbridge_writer import _decode_skill_reply as dec
    except Exception as e:  # pragma: no cover
        c._bad("decode_import", str(e), "无法导入 _decode_skill_reply")
        dec = None

    if dec is not None:
        decode_cases = [
            ('success ["millimeters",4]\n', ["millimeters", 4]),
            ('success nil\n', None),
            ('success t\n', True),
            ('success 1\n', 1),
            ('success "probe_test"\n', "probe_test"),
            ('success ["a",1]\n', ["a", 1]),
        ]
        for raw, want in decode_cases:
            got = dec(raw, None)
            if got == want:
                c._ok(f"decode {raw.strip()}", repr(got))
            else:
                c._bad(f"decode {raw.strip()}", repr(got),
                       f"期望 {want!r} —— 检查是否有 nil/t 未归一")

        # failure 帧必须抛错，不能静默当成功
        try:
            dec('failure ("eval" 0 t nil ("*Error* foo"))\n', None)
            c._bad("decode_failure_raises", "(未抛错)",
                   "failure 帧必须抛 RuntimeError，否则错误会被静默吞掉")
        except RuntimeError:
            c._ok("decode_failure_raises", "RuntimeError ✓")
        except Exception as e:
            c._bad("decode_failure_raises", repr(e), "应抛 RuntimeError")

        # 多行代码必须被 eval_skill 拦下（会产生多条回复导致积压）
        try:
            from services.skillbridge_writer import AllegroSession
            s = AllegroSession.__new__(AllegroSession)
            s._ws = object()
            s.eval_skill("a\nb")
            c._bad("eval_rejects_multiline", "(未拦截)",
                   "eval_skill 必须拒绝多行代码 —— 多行会产生多条回复、"
                   "造成约 15 帧的管道滞后错位")
        except Exception as e:
            name = type(e).__name__
            if name == "SkillCallError":
                c._ok("eval_rejects_multiline", "SkillCallError ✓")
            else:
                c._bad("eval_rejects_multiline", name,
                       "应抛 SkillCallError（多行 = 管道错位风险）")

    # ---- 11. 重名三层探测（纯 Python 假 session）----
    # 这一段不需要 Allegro：用一个假 session 模拟"内存有 / 内存无 / 查不了"，
    # 专门防住 2026-09-28 那次漏判（磁盘空、内存有 -> 建 padstack 含糊失败）。
    print("\n[11] padstack 重名三层探测")
    try:
        from services import footprint_builder as FB
    except Exception as e:  # pragma: no cover
        c._bad("probe_import", str(e), "无法导入 footprint_builder")
        FB = None

    if FB is not None:
        class _FakeSession:
            """按名称表回答的假 session：名字集合即'内存里的 padstack'。"""
            def __init__(self, names=None, fail=False):
                self.names = names
                self.fail = fail

            def eval_skill(self, code):
                from services.skillbridge_writer import SkillCallError
                if self.fail:
                    raise SkillCallError("eval_skill", RuntimeError("boom"))
                if "mapcar" in code:
                    return list(self.names) if self.names else None
                if "length(axlDBGetDesign()->padstacks)" in code:
                    return len(self.names) if self.names is not None else 0
                if "axlLoadPadstack" in code:
                    # 从表达式里抠出名字
                    import re as _re
                    m = _re.search(r'axlLoadPadstack\("([^"]+)"\)', code)
                    nm = m.group(1).lower() if m else ""
                    return object() if (self.names and nm in
                                        {n.lower() for n in self.names}) else None
                return None

        # 11a. 内存有 —— 这正是早期漏判的场景（磁盘空、内存有）
        s1 = _FakeSession(names=["R1D4X1D2", "QT1"])
        r = FB._padstack_exists(s1, "R1D4X1D2")
        if r["exists"] is True and r["via"] == "memory_list":
            c._ok("probe_mem_hit", f"{r['exists']} via={r['via']}")
        else:
            c._bad("probe_mem_hit", repr(r),
                   "内存全量名单命中时必须 exists=True、via=memory_list")

        # 11b. 内存有该集合，但目标名不在其中 -> False
        r = FB._padstack_exists(s1, "NOPE_ZZZ")
        if r["exists"] is False:
            c._ok("probe_mem_miss", f"{r['exists']} via={r['via']}")
        else:
            c._bad("probe_mem_miss", repr(r),
                   "名单里没有该名字时必须 exists=False（不能退成 None）")

        # 11c. 大小写不敏感（Allegro 侧实测 axlLoadPadstack 大小写不敏感）
        r = FB._padstack_exists(s1, "r1d4x1d2")
        if r["exists"] is True:
            c._ok("probe_case_insensitive", f"{r['exists']}")
        else:
            c._bad("probe_case_insensitive", repr(r),
                   "重名判定必须大小写不敏感（R1D4X1D2 == r1d4x1d2）")

        # 11d. 内存全量名单查不了 -> 退到第 2 层单名探测
        class _MemFailSession(_FakeSession):
            def eval_skill(self, code):
                from services.skillbridge_writer import SkillCallError
                if "mapcar" in code or "length(" in code:
                    raise SkillCallError("eval_skill", RuntimeError("mem fail"))
                if "axlLoadPadstack" in code:
                    return object() if "R1D4X1D2" in code else None
                return None

        s2 = _MemFailSession()
        r = FB._padstack_exists(s2, "R1D4X1D2")
        if r["exists"] is True and r["via"] == "axlLoadPadstack":
            c._ok("probe_fallback_load", f"{r['exists']} via={r['via']}")
        else:
            c._bad("probe_fallback_load", repr(r),
                   "第 1 层失败时应收敛到 axlLoadPadstack（via=axlLoadPadstack）")

        # 11e. ★ 图元创建成功判定（2026-09-29 新增）
        #      实测返回形态：line -> [[obj], None]；rect -> [obj, None]
        #      绝不能靠 `if raw:` 判（结构一变就静默失效）
        ok_cases = [
            [[object()], None],        # axlDBCreateLine 实测形态
            [object(), None],          # axlDBCreateRectangle 实测形态
            [object(), "drc"],         # 有 DRC 的矩形
            object(),                  # 裸对象
        ]
        bad_cases = [
            None,
            [None],
            [],
            [None, "err"],
            "nil",
            "None",
        ]
        for v in ok_cases:
            if FB._is_creation_ok(v):
                c._ok("creation_ok_true", repr(v)[:36])
            else:
                c._bad("creation_ok_true", repr(v)[:36],
                       "该形态代表创建成功，必须判 True")
        for v in bad_cases:
            if not FB._is_creation_ok(v):
                c._ok("creation_ok_false", repr(v)[:36])
            else:
                c._bad("creation_ok_false", repr(v)[:36],
                       "该形态代表创建失败，必须判 False")

        # 11f. 失败提示必须提到 extents（最常见的真实原因）
        hint = FB._creation_failure_hint(None)
        if "extent" in hint.lower():
            c._ok("creation_hint_mentions_extents", hint[:52])
        else:
            c._bad("creation_hint_mentions_extents", hint[:52],
                   "失败提示必须提到 extents —— 实测最主要原因是 extents 过小/未初始化")

    # ---- 12. 目录约定 / 日志归集（services/paths.py）----
    #   约定：图纸与产物 -> Tools/test/   日志 -> Tools/log/
    #   ★ 最容易出的错是**误伤产物**：把 .dra/.pad/.psm 当成日志搬走。
    print("\n[12] 目录约定 / 日志归集")
    from services import paths as PA

    for _name, _got, _want in (
        ("paths.TEST_DIR", PA.as_posix(PA.TEST_DIR), PA.as_posix(PA.TOOLS_DIR) + "/test"),
        ("paths.LOG_DIR", PA.as_posix(PA.LOG_DIR), PA.as_posix(PA.TOOLS_DIR) + "/log"),
    ):
        if _got == _want:
            c._ok(_name, _got)
        else:
            c._bad(_name, _got, f"期望 {_want}")

    # 12a. 运行痕迹识别 —— 正例
    for _n in ("allegro.jrl", "allegro.jrl,1", "nb_xtal4.log", "nb_xtal4.log,2",
               "master.tag", "probe_result.txt", "batch_drc.log",
               "skillbridge_server.log"):
        if PA.is_run_artifact(_n):
            c._ok(f"artifact_yes {_n}", "run-artifact ✓")
        else:
            c._bad(f"artifact_yes {_n}", "(判为 False)",
                   "这是运行痕迹，必须归到 Tools/log")

    # 12b. ★ 关键守卫：绝不能误伤产物 / 脚本 / 文档
    for _n in ("X.dra", "probe_test.dra", "r1d4x1d2.pad",
               "NB_XTAL4_3D2X2D5X0D7.psm", "collect_logs.py",
               "skill_api_probe.il", "selfcheck_skill_syntax.py",
               "ALLEGRO_SKILL_API_核实报告.md", "X.dra.lck"):
        if not PA.is_run_artifact(_n):
            c._ok(f"artifact_no {_n}", "protected ✓")
        else:
            c._bad(f"artifact_no {_n}", "(判为 True)",
                   "产物/脚本/文档绝不能被当成日志搬走")

    # 12c. 重名不覆盖，且 `x.log,1` 的时间戳要插在**第一个点之前**
    _d = _tf.mkdtemp(prefix="forgelog-").replace("\\", "/")
    try:
        with open(_d + "/x.log,1", "w", encoding="utf-8") as _f:
            _f.write("A")
        _t = PA._unique_target(_d, "x.log,1", "20260929-150000")
        if os.path.basename(_t) == "x_20260929-150000.log,1":
            c._ok("unique_target_rolled", os.path.basename(_t))
        else:
            c._bad("unique_target_rolled", os.path.basename(_t),
                   "应为 x_20260929-150000.log,1 —— 时间戳插到末尾会丢掉 ,1 的可读性")
        # ★ 不重名时必须保持原名（`plain.log` 没建过，所以不该被加时间戳）
        if os.path.basename(PA._unique_target(_d, "plain.log", "T")) == "plain.log":
            c._ok("unique_target_free", "plain.log 不重名时保持原名 ✓")
        else:
            c._bad("unique_target_free", "名字被改",
                   "不重名时不该加时间戳")
    finally:
        import shutil as _sh2
        _sh2.rmtree(_d, ignore_errors=True)

    # 12d. collect_run_logs 端到端：产物留在原地，痕迹搬进 log/
    _src = _tf.mkdtemp(prefix="forgesrc-").replace("\\", "/")
    _dst = _tf.mkdtemp(prefix="forgedst-").replace("\\", "/")
    try:
        for _n, _body in (("allegro.jrl", "j"), ("nb.log", "l"),
                          ("master.tag", "t"), ("probe_result.txt", "p"),
                          ("X.dra", "dra"), ("r1d4x1d2.pad", "pad"),
                          ("run.py", "py")):
            with open(f"{_src}/{_n}", "w", encoding="utf-8") as _f:
                _f.write(_body)
        _r = PA.collect_run_logs(_src, log_dir=_dst)
        _moved = sorted(os.path.basename(m["from"]) for m in _r["moved"])
        _want = sorted(["allegro.jrl", "nb.log", "master.tag", "probe_result.txt"])
        if _moved == _want:
            c._ok("collect_moves_artifacts", ", ".join(_moved))
        else:
            c._bad("collect_moves_artifacts", str(_moved), f"期望 {_want}")

        _left = sorted(os.listdir(_src))
        _keep = sorted(["X.dra", "r1d4x1d2.pad", "run.py"])
        if _left == _keep:
            c._ok("collect_keeps_products", ", ".join(_left))
        else:
            c._bad("collect_keeps_products", str(_left),
                   f"产物/脚本必须留在原地，期望剩下 {_keep}")

        # 源目录 == 目标目录：不能自搬自
        _r2 = PA.collect_run_logs(_dst, log_dir=_dst)
        if not _r2["moved"] and not _r2["scanned"]:
            c._ok("collect_skips_self", "源==目标时整目录跳过 ✓")
        else:
            c._bad("collect_skips_self", str(_r2["scanned"]),
                   "源目录等于目标目录时必须跳过，否则会自己搬自己")

        # ★ 绝不抛异常（列日志失败不能拖垮建模流程）
        try:
            _r3 = PA.collect_run_logs(_src + "/does-not-exist", log_dir=_dst)
            c._ok("collect_never_raises", f"不存在的目录 -> moved={len(_r3['moved'])}")
        except Exception as _e:
            c._bad("collect_never_raises", repr(_e),
                   "collect_run_logs 绝不能抛异常")
    finally:
        import shutil as _sh3
        _sh3.rmtree(_src, ignore_errors=True)
        _sh3.rmtree(_dst, ignore_errors=True)

    # ---- 13. 环境变量 NOCONFIRM 族（弹框的变量级总开关）----
    #  出处：<Cadence>/doc/algroenvvar/Drawing_Settings.html（Script Settings 段）
    #  ★ 为什么必须有：`?mode "wf"` **只在 axlOpenDesign* 里被识别**，
    #    其它 API（axlCompileSymbol / axlPadstackToDisk …）不认它，只能靠变量。
    print("\n[13] NOCONFIRM 变量族（弹框的变量级总开关）")
    _need = {"NOCONFIRM", "noconfirm_savedb", "noconfirm_uprev"}
    _missing = _need - set(S.AUTO_NOCONFIRM_VARS)
    if _missing:
        c._bad("noconfirm_family_complete", str(S.AUTO_NOCONFIRM_VARS),
               f"缺少官方变量 {sorted(_missing)}")
    else:
        c._ok("noconfirm_family_complete",
              f"{len(S.AUTO_NOCONFIRM_VARS)} 个: {', '.join(S.AUTO_NOCONFIRM_VARS)}")

    # 单变量三件套
    c.check_expr("var_get_one", S.build_get_variable("NOCONFIRM"))
    c.check_expr("var_set_one", S.build_set_variable("NOCONFIRM"))
    c.check_expr("var_unset_one", S.build_unset_variable("noconfirm_savedb"))

    _g1 = S.build_get_variable("NOCONFIRM")
    if _g1 == 'axlGetVariable("NOCONFIRM")':
        c._ok("var_get_shape", _g1)
    else:
        c._bad("var_get_shape", _g1, '应为 axlGetVariable("NOCONFIRM")')

    # ★ 关键守卫：SKILL 的 t 是**裸符号**，绝不能写成字符串 "t"
    _s1 = S.build_set_variable("NOCONFIRM", True)
    if _s1 == 'axlSetVariable("NOCONFIRM" t)' and '"t"' not in _s1:
        c._ok("var_set_bare_t", _s1)
    else:
        c._bad("var_set_bare_t", _s1,
               'SKILL 的 t 是裸符号 —— 写成 "t"（字符串）会被当成真值字符串，语义错误')

    if '"nil"' not in S.build_set_variable("X_test_var", False):
        c._ok("var_set_false_bare", S.build_set_variable("X_test_var", False))
    else:
        c._bad("var_set_false_bare", S.build_set_variable("X_test_var", False),
               "置假应写裸 nil，不能写字符串")

    # 批量形态（省往返）
    c.check_expr("var_get_many", S.build_get_variables())
    c.check_expr("var_set_many", S.build_set_variables())

    _gs = S.build_get_variables(("NOCONFIRM", "noconfirm_savedb"))
    if _gs.count("axlGetVariable") == 1 and "mapcar" in _gs:
        c._ok("var_get_many_one_roundtrip", _gs)
    else:
        c._bad("var_get_many_one_roundtrip", _gs,
               "批量读必须一条 mapcar 表达式搞定（N 次往返会拖慢每个 MCP 调用）")

    # ★ 关键守卫：批量设必须用 errset 包住 —— 某个变量不被支持时不能带崩通道
    _ss = S.build_set_variables()
    if _ss.startswith("errset(") and _ss.count("axlSetVariable") == 1:
        c._ok("var_set_many_guarded", _ss)
    else:
        c._bad("var_set_many_guarded", _ss,
               "批量设必须 errset 包住且只发一条表达式")

    # 恢复现场：旧值真 → 设回 t；旧值空 → unset
    _r = S.build_restore_variables({"NOCONFIRM": None,
                                    "noconfirm_savedb": True})
    c.check_expr("var_restore", _r)
    if 'axlUnsetVariable("NOCONFIRM")' in _r and \
       'axlSetVariable("noconfirm_savedb" t)' in _r:
        c._ok("var_restore_branches", _r)
    else:
        c._bad("var_restore_branches", _r,
               "旧值为空必须 unset（回到默认＝会弹框的安全侧），"
               "旧值为真必须设回 t")

    if S.build_restore_variables({}) == "nil":
        c._ok("var_restore_empty", "空表 -> nil ✓（不能生成 () 这种非法空调用）")
    else:
        c._bad("var_restore_empty", S.build_restore_variables({}),
               "空表应产出 nil")

    # 回复归一化 —— 三种形态都要能吃
    _names = list(S.AUTO_NOCONFIRM_VARS)
    _cases = [
        ([None, True, None, None], 1),
        ("(nil t nil nil)", 1),
        ("nil", 0),
        (None, 0),
    ]
    _all_ok = True
    for _raw, _want_true in _cases:
        _d = S.build_normalize_var_reply(_raw, _names)
        if sum(1 for v in _d.values() if v) != _want_true:
            _all_ok = False
            c._bad("var_normalize", repr(_raw),
                   f"应得到 {_want_true} 个真值，实得 {_d}")
            break
    if _all_ok:
        c._ok("var_normalize",
              "3 种回复形态（Python 列表 / SKILL 打印串 / nil）均归一正确 ✓")

    # ★ 防注入：变量名只允许标识符
    _inj_ok = True
    for _bad_name in ("NO CONFIRM", 'x;load("evil")', "", "1abc", "a.b"):
        try:
            S.build_set_variable(_bad_name)
            c._bad("var_name_injection", repr(_bad_name),
                   "非法变量名必须抛 ValueError")
            _inj_ok = False
        except ValueError:
            pass
    if _inj_ok:
        c._ok("var_name_injection", "非法变量名一律 ValueError ✓")

    # ★ 符号类型（旧结论"AXL 无此 API"已被推翻）
    c.check_expr("set_symbol_type", S.build_set_symbol_type("package"))
    if S.build_set_symbol_type("PACKAGE") == 'axlSetSymbolType("package")':
        c._ok("set_symbol_type_norm", "大小写归一 + 加引号 ✓")
    else:
        c._bad("set_symbol_type_norm", S.build_set_symbol_type("PACKAGE"),
               "应归一成小写并加引号")
    try:
        S.build_set_symbol_type("evil")
        c._bad("set_symbol_type_whitelist", "evil",
               "只允许 package/mechanical/format/shape/flash")
    except ValueError:
        c._ok("set_symbol_type_whitelist", "非白名单值 ValueError ✓")

    # ★ 会话级自动开关：默认开；通道死掉时必须**不抛异常**（不能拖垮正常返回）
    try:
        from services import skillbridge_writer as _W

        if _W.AUTO_NOCONFIRM is True:
            c._ok("session_noconfirm_default", "默认开启 ✓")
        else:
            c._bad("session_noconfirm_default", str(_W.AUTO_NOCONFIRM),
                   "默认应当开启（本模块的调用全是脚本化的）")

        class _DeadSession(_W.AllegroSession):
            """通道已死的假会话：任何 eval 都报错。"""
            def __init__(self):                       # noqa: D107
                self.workspace_id = "0"
                self._ws = None
                self.auto_noconfirm = True
                self._nc_previous = None

            def eval_skill(self, code):               # noqa: D102
                raise RuntimeError("channel dead")

        _d = _DeadSession()
        _d._apply_noconfirm()                         # 不许抛
        if isinstance(_d._nc_previous, dict) and \
           set(_d._nc_previous) == set(S.AUTO_NOCONFIRM_VARS):
            c._ok("session_apply_survives_dead_channel",
                  "读不到旧值 -> 全部按未设处理（安全侧）✓")
        else:
            c._bad("session_apply_survives_dead_channel", repr(_d._nc_previous),
                   "读不到旧值时应退化为全部 False，退出时统一 unset")
        _d._restore_noconfirm()                       # 同样不许抛
        if _d._nc_previous is None:
            c._ok("session_restore_clears_state", "恢复后状态已清 ✓")
        else:
            c._bad("session_restore_clears_state", repr(_d._nc_previous),
                   "恢复后必须把自己记录的旧值清掉")

        _off = _W.AllegroSession(auto_noconfirm=False)
        if _off.auto_noconfirm is False:
            c._ok("session_noconfirm_opt_out", "可显式关闭 ✓")
        else:
            c._bad("session_noconfirm_opt_out", str(_off.auto_noconfirm),
                   "auto_noconfirm=False 必须生效")
    except Exception as _e:
        c._bad("session_noconfirm_wiring", repr(_e),
               "AllegroSession 的 NOCONFIRM 接线应可在离线环境构造与调用")

    # ---- 14. ★ drawing extents 覆盖计算（防"图元落在范围外被静默丢弃"）----
    #
    #   2026-10-08 新增。事故：CSTJ-421 的 `auto_extent` 只按**引脚**外扩，
    #   而连接器的 Place_Bound 比引脚分布更大 → 下边缘差 0.10mm 落在范围外 →
    #   `axlDBCreateRectangle` **静默返回 nil**（不报错、不弹框），
    #   87 步里只有 1 步失败，定位了很久。
    #   本段把「覆盖全部几何」这条钉成回归。
    print("\n[14] auto extents 覆盖计算（footprint_builder）")
    try:
        from services import footprint_builder as _FB
        from services import geometry_solver as _GS

        _r = _FB._outline_xy_points({"x1": -1, "y1": -2, "x2": 3, "y2": 4,
                                     "width": 0.1})
        if sorted(_r) == [(-1.0, -2.0), (3.0, 4.0)]:
            c._ok("outline_xy_points_rect", str(_r))
        else:
            c._bad("outline_xy_points_rect", str(_r), "矩形应抽出对角两点")

        _l = _FB._outline_xy_points(
            {"lines": [[[0, 0], [1, 0]], [[1, 0], [1, 1]]], "width": 0.15})
        # ★ 顶点会重复（相邻线段共享端点）—— 对 min/max 无影响，故按集合比。
        if set(_l) == {(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)}:
            c._ok("outline_xy_points_lines", str(_l))
        else:
            c._bad("outline_xy_points_lines", str(_l),
                   "折线应抽出全部顶点（允许相邻线段共享端点的重复）")

        _mix = _FB._outline_xy_points([
            {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
            {"lines": [[[9, 9], [10, 10]]]},
        ])
        if (9.0, 9.0) in _mix and (1.0, 1.0) in _mix and len(_mix) == 4:
            c._ok("outline_xy_points_mixed", str(_mix))
        else:
            c._bad("outline_xy_points_mixed", str(_mix), "矩形 + 折线混排都应抽到")

        _dirty = [None, [], {}, [None], [{"width": 0.1}],
                  {"x1": "x", "y1": 0, "x2": 1, "y2": 1},
                  {"lines": [[[0, "y"]]]}, "不是字典", 42]
        for _d in _dirty:
            try:
                _got = _FB._outline_xy_points(_d)
            except Exception as _e:                       # noqa: BLE001
                c._bad("outline_xy_points_robust", repr(_d),
                       f"脏数据不应抛异常，实际 {_e!r}")
                break
            if _got:
                c._bad("outline_xy_points_robust", repr(_d),
                       f"脏数据应返回空表，实际 {_got!r}")
                break
        else:
            c._ok("outline_xy_points_robust",
                  f"None/空/脏数据 {len(_dirty)} 例都不抛异常且返回空表 ✓")

        # margin = max(2.0, 最大焊盘半尺寸 + 1.0)
        _m = _FB._compute_auto_extents(
            [{"x": 0, "y": 0}],
            padstack={"layers": [{"layer": "TOP", "size": [1.2, 2.2]}]})
        if abs(_m[0] + 2.1) < 1e-9 and abs(_m[2] - 2.1) < 1e-9:
            c._ok("auto_extents_margin_from_pad",
                  f"2.2 焊盘 -> 半尺寸 1.1 -> margin 2.1 ✓ {_m}")
        else:
            c._bad("auto_extents_margin_from_pad", str(_m),
                   "margin 应取 max(2.0, 焊盘半尺寸 + 1.0)")

        _z = _FB._compute_auto_extents([])
        if _z == (-2.0, -2.0, 2.0, 2.0):
            c._ok("auto_extents_empty", str(_z))
        else:
            c._bad("auto_extents_empty", str(_z), "空几何应退化为 ±2.0 方框")

        # ★ 真件回归：CSTJ-421
        _g = _GS.solve(_GS.SPEC_CSTJ421_AJ35)
        _pins = _g["pins"]
        _pb = _g["place_bound"]
        _new = _FB._compute_auto_extents(
            _pins, _g["silk"], _g["assembly"], _pb,
            padstack=_g["padstack"], extra_padstacks=_g["extra_padstacks"],
            refdes=_g["refdes"])
        _old = _FB._compute_auto_extents(
            _pins, padstack=_g["padstack"],
            extra_padstacks=_g["extra_padstacks"])

        _pts = _FB._outline_xy_points(_g["silk"]) \
            + _FB._outline_xy_points(_g["assembly"]) \
            + _FB._outline_xy_points(_pb) \
            + [(float(p["x"]), float(p["y"])) for p in _pins]
        _out = [pt for pt in _pts
                if not (_new[0] - 1e-9 <= pt[0] <= _new[2] + 1e-9
                        and _new[1] - 1e-9 <= pt[1] <= _new[3] + 1e-9)]
        if not _out:
            c._ok("cstj421_extents_cover_all_geometry",
                  f"{len(_pts)} 个几何点全在新 extents "
                  f"({_new[0]},{_new[1]})-({_new[2]},{_new[3]}) 内 ✓")
        else:
            c._bad("cstj421_extents_cover_all_geometry", str(_out[:3]),
                   "几何点必须在 auto extents 内 —— 否则 Allegro 静默丢弃该图元")

        if _old[1] > _pb["y1"] + 1e-9:
            c._ok("cstj421_old_pins_only_was_short",
                  f"[复现旧 bug] 只按引脚 -> y1={_old[1]} "
                  f"> Place_Bound 下边 {_pb['y1']} → 矩形会被静默丢弃 ✓")
        else:
            c._bad("cstj421_old_pins_only_was_short",
                   f"old_y1={_old[1]}, pb_y1={_pb['y1']}",
                   "本用例应能复现「只看引脚就漏掉 Place_Bound」")

        if _new[1] < _pb["y1"] - 1e-9:
            c._ok("cstj421_new_covers_place_bound",
                  f"新 extents y1={_new[1]} < Place_Bound 下边 {_pb['y1']} ✓")
        else:
            c._bad("cstj421_new_covers_place_bound",
                   f"new_y1={_new[1]}, pb_y1={_pb['y1']}",
                   "新 extents 下界必须低于 Place_Bound 下边")
    except Exception as _e:
        c._bad("auto_extents_wiring", repr(_e),
               "extents 覆盖计算应可在离线环境构造与调用")

    # ---- 汇总 ----
    print("\n" + "=" * 68)
    total = c.passed + c.failed
    print(f"结果: {c.passed}/{total} 通过, {c.failed} 失败")
    if c.failed:
        print("\n失败明细：")
        for f in c.failures:
            print(f"  - {f}")
    print("=" * 68)

    return 1 if c.failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="SKILL 表达式语法自检（不需要 Allegro）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每条通过的表达式")
    args = ap.parse_args()
    sys.exit(run_all(verbose=args.verbose))


if __name__ == "__main__":
    main()
