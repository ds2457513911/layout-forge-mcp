# -*- coding: utf-8 -*-
r"""
services/skill_snippets.py —— SKILL 表达式构造器

为什么不写 .il 模板文件：
  手工拼 SKILL 字符串极易出现括号不平衡、逗号多余、符号/字符串混淆
  （我们在 probe 阶段踩过全部这三种坑）。这里改成"用 Python 函数构造
  SKILL 表达式"，由函数结构保证语法正确，最后统一做一次括号平衡断言。

★★★ 语法铁律（2026-09-28 实测确认，最重要的一条）★★★

  SKILL 里 `func(args)` 与 `(func args)` 是**两种完全不同的语法**：

      axlDBGetDesignUnits()      -> 函数调用      ✓ 唯一正确形式
      (axlDBGetDesignUnits())    -> eval: not a function  ✗ 报错

  `(f a)` 的语义是"先求值 f 得到函数，再套用到 a"。AXL 函数不能作为值
  取出，因此必然报 "not a function"。**本模块所有表达式一律使用
  `func(args)` 形式。**

  → 这条铁律同时推翻了本模块早期版本（那时全部写成 `(axlFoo ...)`，
    实测在 Allegro 侧全部报错，已整体重写）。

其他 SKILL 语法要点（实测确认）：
  1. 符号（symbol）与字符串不同：
        'REGULAR     -> 符号
        "REGULAR"    -> 字符串
     大部分 API 的 ?type / ?figure 参数要**符号**。
  2. 坐标对用冒号：1.4:1.2 表示 (1.4, 1.2)
  3. 没有 `end` 关键字，块结束一律用右括号。
  4. 可选参数用 ?key value 形式。
  5. `?figureSize` 的数值单位 = 当前 drawing 的 design units。
  6. `errset` 返回值：成功 (结果)，失败 (nil 错误信息)。

★★ 属性穿透 `->` 与 lambda（2026-09-29 更正，推翻了早期误判）★★

  早期一度认为"eval 通道里 `->` 取不到值"，那是**错的**。实测：

      axlDBGetDesign()->padstacks          -> [<remote obj> x7]
      car(axlDBGetDesign()->pins)->number  -> '1'
      car(axlDBGetDesign()->pins)->xy      -> [-1.1, 0.85]
      mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)
                                           -> ['R1D4X1D2', ...]

  即 `->` 穿透、`mapcar`、`lambda` 都可用。当初
  `axlDBGetDesign()->drawing_type` 取不到值，是因为**那个属性名不存在**
  （正确 API 是 `axlDesignType()`），不能推广成"属性访问一律失效"。

  lambda 的形式：`lambda((ps) ...)` —— 形参表要再包一层括号；
  形参名避开 `t` / `lst`（报 `lambda: t is reserved`）。

参考文档：
  <Cadence>\doc\algroskill\06intedt.html   (padstack 函数)
  <Cadence>\share\pcb\examples\skill\dbcreate\  (官方范例)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# ============================================================
# 基础格式化
# ============================================================
def fmt_num(v: float) -> str:
    """
    格式化数字：去掉多余尾零。

    1.40  -> '1.4'
    1.0   -> '1'
    0.0   -> '0'
    """
    if v is None:
        raise ValueError("数值不能为 None")
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        s = "0"
    return s


def fmt_point(x: float, y: float) -> str:
    """SKILL 坐标对：1.4:1.2"""
    return f"{fmt_num(x)}:{fmt_num(y)}"


def sym(name: str) -> str:
    """
    转成 SKILL 符号字面量。

    'REGULAR' -> "'REGULAR"
    已经带撇号的会原样返回，避免出现 ''REGULAR'。
    """
    n = str(name).strip()
    if n.startswith("'"):
        return n
    return "'" + n


def q(s: str) -> str:
    """转成 SKILL 字符串字面量（转义双引号和反斜杠）。"""
    body = str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{body}"'


def call(func: str, *args: str, **kwargs: str) -> str:
    """
    构造一次 SKILL 函数调用 —— **所有表达式的唯一出口**。

    统一在这里落地 `func(args)` 语法，避免各处手写出错。

        call("axlDBCreateLine", pts, "0.15", '"TOP"')
            -> axlDBCreateLine(((0 0) (1 1)) 0.15 "TOP")

        call("axlDBCreatePadStack", q("P1"), "nil", pad_list, "t")
            -> axlDBCreatePadStack("P1" nil (list ...) t)

    可选关键字参数按 SKILL 习惯渲染成 `?key value`（key 自动驼峰化）：

        call("make_axlPinText", number='"...")', offset='0:0')
            -> make_axlPinText(?number "..." ?offset 0:0)
    """
    parts = [str(a) for a in args if a is not None]
    for k, v in kwargs.items():
        if v is None:
            continue
        parts.append(f"?{_camel(k)} {v}")
    body = " ".join(parts)
    # SKILL 的调用语法就是 func(args)，外层带括号才是错的
    return f"{func}({body})" if body else f"{func}()"


def _camel(name: str) -> str:
    """snake_case -> camelCase（SKILL 的 ?关键字 习惯）。"""
    head, *rest = name.split("_")
    return head + "".join(w.capitalize() for w in rest)


def pt(x: float, y: float) -> str:
    """
    构造 SKILL **单个点**：list(x y)

    注意与 `pt_list` 的区别 —— 很多 API 要的是"一个点"而不是"点的列表"：
        axlDBChangeDesignOrigin(list(0 0))    <- 单点
        axlDBChangeDesignExtents(list(list(-50 -50) list(50 50)))  <- 两点表
    """
    return call("list", fmt_num(x), fmt_num(y))


def pt_list(*points: Tuple[float, float]) -> str:
    """
    构造 SKILL **点列表**：((x1 y1) (x2 y2) ...)

    ★★ 踩坑记录（2026-09-28 实测）：

        list((-50 -50) (50 50))          -> ERR "eval: not a function -50"
        list(list(-50 -50) list(50 50))  -> OK

    原因：`((-50` 之间没有空格，SKILL 把 `(-50` 解析成"调用函数 -50"。
    所以点列表的每个点必须用**显式的 list() 包裹**，不能裸写括号。

    实测这几种写法都 OK：
        '((-50 -50) (50 50))
        list('(-50 -50) '(50 50))
        list(list(-50 -50) list(50 50))

    这里统一用 `list(list(...) list(...))` —— 最不依赖读者对 SKILL
    引号语义的理解。

    :param points: 若干 (x, y)
    """
    if not points:
        return "nil"
    inner = " ".join(pt(x, y) for x, y in points)
    return f"list({inner})"


def _check_balance(code: str) -> None:
    """
    断言括号平衡。括号不平衡就抛错 —— 与其让 Allegro 报费解的错，
    不如在 Python 侧立刻失败。

    注意：`func(args)` 形式下，整体**不以 '(' 开头**是正常的。
    """
    depth = 0
    in_str = False
    esc = False
    for ch in code:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"SKILL 表达式括号提前闭合:\n{code}")
    if depth != 0:
        raise ValueError(f"SKILL 表达式括号不平衡 (depth={depth}):\n{code}")
    if in_str:
        raise ValueError(f"SKILL 表达式字符串未闭合:\n{code}")


# ============================================================
# padstack 层定义
# ============================================================
# make_axlPadStackPad 支持的 figure 类型（实测 axlPadFigureTypes() 子集）
FIGURE_RECTANGLE = "RECTANGLE"
FIGURE_SQUARE = "SQUARE"
FIGURE_CIRCLE = "CIRCLE"
FIGURE_OBLONG = "OBLONG"
FIGURE_OVAL = "OVAL"
FIGURE_SHAPE = "SHAPE"  # 异形，需预置 .ssm

# 常用层名
LAYER_TOP = "TOP"
LAYER_BOTTOM = "BOTTOM"
LAYER_SOLDERMASK_TOP = "SOLDERMASK_TOP"
LAYER_SOLDERMASK_BOTTOM = "SOLDERMASK_BOTTOM"
LAYER_PASTEMASK_TOP = "PASTEMASK_TOP"
LAYER_PASTEMASK_BOTTOM = "PASTEMASK_BOTTOM"


def build_pad_layer(
    layer: str,
    figure: str,
    size: Tuple[float, float],
    pad_type: str = "REGULAR",
) -> str:
    """
    构造一层 make_axlPadStackPad 表达式。

    :param layer:     层名，如 "TOP"
    :param figure:    图形，如 "RECTANGLE"
    :param size:      (w, h)，单位 = design units
    :param pad_type:  "REGULAR" / "THERMAL" / "ANTIPAD"
    """
    w, h = size
    expr = call(
        "make_axlPadStackPad",
        layer=q(layer),
        type=sym(pad_type),
        figure=sym(figure),
        figureSize=fmt_point(w, h),
    )
    _check_balance(expr)
    return expr


def build_drill(
    diameter: float,
    plated: bool = True,
    drill_type: str = "CIRCLE_DRILL",
    size: Optional[Tuple[float, float]] = None,
    offset: Tuple[float, float] = (0.0, 0.0),
    drill_char: Optional[str] = None,
    figure: Optional[str] = None,
) -> str:
    """
    构造 make_axlPadStackDrill 表达式（钻孔定义）。

    ★ 字段名照抄 Cadence 官方范例
      (share/pcb/examples/skill/dbcreate/pad.il 的 CreateThru 过程)：

        drill_data = make_axlPadStackDrill(
                 ?holeType  'CIRCLE_DRILL
                 ?drillDiameter 42
                 ?plating 'PLATED
                 ?figure 'SQUARE
                 ?figureSize 60:60
                 ?drillChar "abc")

    ★★ 踩坑记录：字段名**不是** `?diameter` / `?plated`
      （那样写报 `iliMakeDefstruct: defstruct field name not known`）。
      正确是 `?drillDiameter` 和 `?plating`（取符号 'PLATED / 'NON_PLATED）。

    :param diameter:  钻孔直径（圆孔）
    :param plated:    是否金属化 -> ?plating 'PLATED / 'NON_PLATED
    :param drill_type: ?holeType，如 'CIRCLE_DRILL / 'OBLONG_DRILL
    :param size:      异形孔的 ?figureSize (w, h)；给了就以它为准
    :param offset:    钻孔相对焊盘中心的偏移
    :param drill_char: ?drillChar 钻孔标记字符（可选）
    """
    plating = "'PLATED" if plated else "'NON_PLATED"

    # ★ 2026-10-12 实测（LYDPJ 事故）：异形孔分支漏 ?figure 字段时，
    #   axlDBCreatePadStack **静默丢弃整个 drill**（no_check=t 不报错）——
    #   建出的 padstack holeType='none'、drillDiameter=0.0、type='smd'，
    #   TH 件变成无孔 SMD。官方范例（pad.il CreateThru）drill defstruct
    #   必须带 ?figure + ?figureSize；figure 名 = drill_type 去掉 _DRILL。
    if size is not None:
        # ★ 2026-10-13 第 4 次事故实锤（工程师否决 LYDPJ 第三版）：
        #   holeType 合法值只有 CIRCLE_DRILL / SQUARE_DRILL / OVAL_SLOT /
        #   RECTANGLE_SLOT（官方 axlDBCreatePadStack.txt 第 29-31 行）。
        #   'OBLONG_DRILL **不在列表里** → axlDBCreatePadStack 静默回落成
        #   CIRCLE_DRILL，Padstack Editor 显示 Thru Pin 而非 Slot。
        #   槽孔正确写法（SLOTA 变体实验逐字段对拍金标准通过）：
        #     ?holeType 'OVAL_SLOT ?plating ?slotSize 宽:长
        #     ?figure 'OBLONG_Y ?figureSize 宽:长
        #   slotSize 顺序 = (X宽 Y长)（本地帧）；反序会变成 OBLONG_X 横槽。
        _dt = drill_type.upper()
        if _dt.endswith("_SLOT"):
            expr = call(
                "make_axlPadStackDrill",
                holeType=sym(_dt),
                plating=plating,
                slotSize=fmt_point(*size),
                figure=sym(figure) if figure else None,
                figureSize=fmt_point(*size) if figure else None,
                offset=fmt_point(*offset) if offset != (0.0, 0.0) else None,
                drillChar=q(drill_char) if drill_char else None,
            )
        else:
            # ★ figure 必须显式给对：'OBLONG 不是合法 drill figure（合法的是
            #   OBLONG_X / OBLONG_Y），错名会被 no_check 静默零化（2026-10-13
            #   LYDPJ 二次事故实锤）。约定引擎按金标准派生 OBLONG_Y（本地竖向）。
            # ★★ 2026-10-13 三次事故实锤：异形孔还必须带 ?drillDiameter（槽宽
            #   = min(size)）——官方 pad.il CreateThru 范例圆孔就是
            #   ?drillDiameter 42 + ?figure SQUARE + ?figureSize 60:60 三件套。
            #   漏 drillDiameter 时 no_check 静默零化整个 drill（.pad 仍显示
            #   PadType: through！唯一可信判据 = extracta 回读 DRILL 行）。
            drill_figure = figure or (
                _dt[:-len("_DRILL")] if _dt.endswith("_DRILL") else _dt
            )
            expr = call(
                "make_axlPadStackDrill",
                holeType=sym(_dt),
                drillDiameter=fmt_num(min(size)),
                plating=plating,
                figure=sym(drill_figure),
                figureSize=fmt_point(*size),
                offset=fmt_point(*offset) if offset != (0.0, 0.0) else None,
                drillChar=q(drill_char) if drill_char else None,
            )
    else:
        expr = call(
            "make_axlPadStackDrill",
            holeType=sym(drill_type),
            drillDiameter=fmt_num(diameter),
            plating=plating,
            drillChar=q(drill_char) if drill_char else None,
        )
    _check_balance(expr)
    return expr


def build_pad_list(layer_exprs: Sequence[str]) -> str:
    """把若干 pad 表达式组装成 SKILL 列表。"""
    if not layer_exprs:
        return "nil"
    return call("list", *layer_exprs)


# ============================================================
# padstack 创建
# ============================================================
def build_create_padstack(
    name: str,
    layer_exprs: Sequence[str],
    drill_expr: Optional[str] = None,
    no_check: bool = True,
) -> str:
    """
    构造完整的 padstack 创建表达式（含 errset 包装，返回值为 list 或 nil）。

    签名（文档核实）：
        axlDBCreatePadStack( t_name r_drill l_pad [g_nocheck] ) => dbid/nil

    errset 包装的原因：Allegro 侧报错（例如图形与尺寸不兼容）时若不用 errset，
    会直接抛错打断整个脚本；用 errset 后返回 (nil errmsg)，我们能读到原因。
    """
    pad_list = build_pad_list(layer_exprs)
    drill = drill_expr if drill_expr else "nil"
    check = "t" if no_check else "nil"
    expr = call("axlDBCreatePadStack", q(name), drill, pad_list, check)
    _check_balance(expr)
    return expr


def build_create_padstack_guarded(
    name: str,
    layer_exprs: Sequence[str],
    drill_expr: Optional[str] = None,
    no_check: bool = True,
) -> str:
    """上一层 + errset 包装，返回 (ok result) 便于判断。"""
    inner = build_create_padstack(name, layer_exprs, drill_expr, no_check)
    expr = call("errset", inner)
    _check_balance(expr)
    return expr


def build_padstack_to_disk(name: Optional[str] = None) -> str:
    """
    构造落盘表达式。

    签名：axlPadstackToDisk( [t_padName] [t_outPadName] ) => t/nil
    不带参数 = 把所有 padstack 全部 dump 出来。
    """
    if name:
        expr = call("axlPadstackToDisk", q(name))
    else:
        expr = call("axlPadstackToDisk")
    _check_balance(expr)
    return expr


# ============================================================
# padstack 存在性查询（★ 2026-09-29 重大更正）
# ============================================================
def build_padstack_name_list() -> str:
    r"""
    构造"列出当前 drawing 内**所有** padstack 名字"的表达式。

    ★★★ 重要更正（2026-09-29 实测）★★★

    早期结论「AXL 没有列出 padstack 的 API」是**错的**，根源是当时
    对 eval 通道的属性穿透能力存在误判。实测证据：

        axlDBGetDesign()->padstacks          -> [<remote obj>, ...]  7 个
        length(axlDBGetDesign()->padstacks)  -> 7
        car(axlDBGetDesign()->padstacks)->name
                                             -> 'PROBE_R5D7X1D9'
        mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)
                                             -> ['PROBE_R5D7X1D9', 'R1D4X1D2',
                                                 'R1D4X1D2_NEW', 'QT1', 'QT2',
                                                 'QT3', 'QRT']

    即：`->` 属性穿透在 eval 通道里**是可用的**，`mapcar` + `lambda`
    也能正常求值。之前 `axlDBGetDesign()->drawing_type` 取不到值，
    是因为该属性本身不存在（正确 API 是 `axlDesignType()`），
    不能推广成"属性访问一律失效"。

    ★ 笔误说明：函数名是 `axlDBGetPadstacks`（复数 s），
      不是 `axlDBGetPadstack`；而且它是**宏**，顶层裸调用会报
      `eval: unbound variable`。要继续用它必须走 `->` 或 `length()` 这种
      内联形式。这里不用它，统一用 `axlDBGetDesign()->padstacks`。

    ★ lambda 的形式约束（实测踩坑）：
        lambda((ps) ...)     正确 —— 形参表再包一层括号
        lambda(ps ...)       报 `lambda: t is reserved and cannot be used`
        lambda(lst ...)      报同一个错（`lst` 也接近保留字）
      形参名不要用 `t` / `lst` 这类名字。

    用途：建 padstack 前的一次性重名检测，比逐名 `axlLoadPadstack`
    少一轮往返，能一次拿到全量名单。

    返回：SKILL 表达式，求值结果 = 名字字符串列表。
    """
    expr = call(
        "mapcar",
        "lambda((ps) ps->name)",
        "axlDBGetDesign()->padstacks",
    )
    _check_balance(expr)
    return expr


def build_padstack_exists_check(name: str) -> str:
    """
    构造"当前 drawing 里是否已有同名字 padstack"的**单名**查询表达式。

    背景（2026-09-28 实测）：
        axlDBCreatePadStack 遇到重名时**不覆盖**，直接失败，
        而且 errset 只返回 `(nil)` —— 拿不到任何错误信息，
        上层只能看到含糊的 `errset 捕获错误: 未知错误`。
        所以建之前先主动查一次，把"重名"变成明确的可读提示。

    ★★★ 本函数已改为基于 `axlLoadPadstack`（2026-09-29 实测确认）★★★

        axlLoadPadstack("R1D4X1D2")   -> <remote object@0x19c8f569f08>  存在
        axlLoadPadstack("r1d4x1d2")   -> <remote object@0x19c8f569f08>  大小写不敏感!
        axlLoadPadstack("NOPE_ZZZ")   -> None                          不存在

    这是**最直接、最准**的存在性判定，直接问 Allegro 内存，
    不受"磁盘上 .pad 被清掉但内存仍持有"的影响（这正是早期
    文件系统方案漏判 `R1D4X1D2` 的原因）。

    历史上曾用 `isFile(".../<name>.pad")` 走文件系统，现已**降级为
    双保险的次要手段**（见 footprint_builder._padstack_exists）。

    返回：该 padstack 存在时返回 remote object（非 nil），否则 nil。
    """
    expr = call("axlLoadPadstack", q(name))
    _check_balance(expr)
    return expr


# ============================================================
# pin 放置
# ============================================================
def build_pin_text(
    number: str,
    offset: Tuple[float, float] = (0.0, 0.0),
    text: Optional[str] = None,
    orientation_expr: Optional[str] = None,
    text_block: str = "1",
    justify: Optional[str] = "center",
) -> str:
    """
    构造 make_axlPinText 表达式。

    ★ 用法照抄 Cadence 官方示例（axldbctpin.il）的**结构体形式**：

        make_axlPinText(?number "1FFE" ?offset -100:0
            ?text make_axlTextOrientation(?textBlock "2"
                        ?rotation 90 ?mirrored t ?justify "right"))

    ★★ **为什么禁止简化写法 `?text "N"`（2026-10-09 实测 CSTJ-421 事故）**：
      官方示例里有一行 `make_axlPinText(?number "2" ?offset -100:0 ?text "2")`，
      看似"文字内容 = 2"。**实际 Allegro 把数字字符串 ?text 当成【字块号】**——
      实测结果：1 号脚 → text block 1（字高 0.508mm）、2 号脚 → block 2（0.787）、
      … 6 号脚 → block 6（2.032）。**引脚号字号随脚号递增**，
      与人工件（全部 block 1）对拍立即穿帮。
      官方示例恰好 pin 号 = "2" = 字块 "2"，把坑掩盖了。
      → 因此本函数**无条件走结构体形式**，`text` 参数保留但**不再生效**
        （显示内容本来就是 ?number，见 extracta 回读）。

    ★ 字块口径（人工模板 14/14）：引脚号与位号同字块 —— **block "1"**、
      justify "center"。想改口径显式传 text_block / justify。

    ★ 注意：官方示例里写成 `(make_axlPinText ...)`，那是**直接贴在 CIW 里的
      顶层求值形式**。通过 skillbridge 的 eval 通道发送时必须用
      `make_axlPinText(...)` —— 实测 `(f a)` 形式报 "eval: not a function"。

    硬约束（官方示例里的错误用例反推出）：
      - ?number **必须是字符串**（传 123 报错）→ 这里强制 str()
      - ?offset **必须是坐标对**（传 -100 报错）→ 这里强制 (x, y)
      - ?number 不能含非法字符（"2!" 报错）

    :param offset: 文字相对 pin 锚点的偏移。(x, y) 两元素，别传标量。
    :param text: 【已废弃】保留参数位仅为兼容旧调用方，**不生效**。
    :param orientation_expr: 已构造好的 make_axlTextOrientation 表达式；
                             给了就以它为准（text_block / justify 失效）。
    """
    ox, oy = offset if isinstance(offset, (tuple, list)) else (0.0, 0.0)

    if orientation_expr is not None:
        text_arg = orientation_expr
    else:
        # ★ 2026-10-09 修复：绝不再传字符串 ?text（见 docstring 的事故记录）
        text_arg = build_text_orientation(
            rotation=0.0, mirrored=False,
            justify=justify or "center", text_block=text_block,
        )

    expr = call(
        "make_axlPinText",
        number=q(str(number)),
        offset=fmt_point(ox, oy),
        text=text_arg,
    )
    _check_balance(expr)
    return expr


def build_create_pin(
    padstack_name: str,
    x: float,
    y: float,
    pin_text_expr: Optional[str] = None,
    rotation: Optional[float] = None,
) -> str:
    """
    构造 pin 创建表达式（含 errset 包装）。

    签名（文档核实）：
        axlDBCreatePin( t_padstack l_anchorPoint r_pinText/nil [f_rotation] )
            => (dbid drc_flag) / nil

    硬约束（文档 + 实测）：
      - 只能在 Package / Mechanical symbol drawing 里用
      - pin 必须落在 drawing 的 extents 范围内，否则 SPMHA1-70
      - r_pinText 传 nil 建出来的是机械 pin（无编号）
    """
    anchor = fmt_point(x, y)
    text = pin_text_expr if pin_text_expr else "nil"
    if rotation is not None:
        inner = call("axlDBCreatePin", q(padstack_name), anchor, text,
                     fmt_num(rotation))
    else:
        inner = call("axlDBCreatePin", q(padstack_name), anchor, text)
    expr = call("errset", inner)
    _check_balance(expr)
    return expr


# ============================================================
# 丝印 / Assembly / Place_Bound
# ============================================================
def build_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str,
    width: float = 0.15,
) -> str:
    """
    构造一条线段。

    ★ 签名已核对文档（03dbcre8.html）：
        axlDBCreateLine( l_points  [f_width]  [t_layer]  [t_netname] )
            => l_result / nil

    注意参数顺序：**宽度在前，层在后**。写成 (层 宽) 会被静默当成
    t_layer=宽度、f_width=层名，导致建出来的线宽错乱。

    l_points 是点列表：((x1 y1) (x2 y2))，坐标是**绝对坐标**，
    n 个点生成 n-1 条线段。
    """
    pts = pt_list((x1, y1), (x2, y2))
    expr = call("axlDBCreateLine", pts, fmt_num(width), q(layer))
    _check_balance(expr)
    return expr


def build_polyline(
    points: Sequence[Tuple[float, float]],
    layer: str,
    width: float = 0.15,
) -> str:
    """
    用**一次** `axlDBCreateLine` 画出整条折线（n 个点 → n-1 段，**1 个图元**）。

    ★★ 为什么需要它（2026-09-29 金标准对拍得出的硬约束）★★
      金标准 `PACKAGE GEOMETRY/SILKSCREEN_TOP` 一共只有 **3 个图元**：
          [0] 4 段三角形（1 个复杂 path）
          [1] 短横线（1 段）
          [2] 短横线（1 段）
      如果逐段分别调 `axlDBCreateLine`，同样的形状会变成 5 个图元，
      于是这两条检查项必然对不上：
          · 「Assembly 元素数」      （金标准 2，逐段画会变 7）
          · 「Silkscreen 复杂 path 数」（金标准 1，逐段画会变 0）
      → 所以凡是"连成串"的线段，必须**一次调用画完**。

    点的写法复用 `pt_list`（内含 `list()` 包裹的踩坑修正）。
    """
    pts = pt_list(*[(float(x), float(y)) for x, y in points])
    expr = call("axlDBCreateLine", pts, fmt_num(width), q(layer))
    _check_balance(expr)
    return expr


def build_rect_polyline(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str,
    width: float = 0.15,
) -> str:
    """
    矩形外框 —— **一次调用画完的闭合折线**（5 个点，回到起点）。

    ★ 与 `build_rect_lines` 的区别：后者返回 4 条独立表达式 = 4 个图元；
      本函数返回 1 条表达式 = 1 个图元。金标准的 assembly 本体框
      实测就是 **1 个 4 段对象**（`n_segs=4`、`is_rect=false`），
      所以轮廓层要走这条路径，元素数才对得上。

    ★ 注意 Place_Bound **不要**用这个 —— 金标准的 Place_Bound 是
      `axlDBCreateRectangle` 建的**真矩形对象**（`is_rect=true`），
      见 `build_filled_rect`。

    :return: 单条 axlDBCreateLine 表达式
    """
    return build_polyline(
        [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)],
        layer, width,
    )


def build_rect_lines(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str,
    width: float = 0.15,
) -> List[str]:
    """
    用 4 条线段画矩形外框 —— 丝印 / Assembly / Place_Bound 都常用。

    为什么不用 axlDBCreateShape：矩形 shape 在某些层（如 PACKAGE GEOMETRY）
    的表现与线段不同，且做 outline 时线段更通用、更容易被检查规则识别。

    :return: 4 条 axlDBCreateLine 表达式
    """
    return [
        build_line(x1, y1, x2, y1, layer, width),  # 下边
        build_line(x2, y1, x2, y2, layer, width),  # 右边
        build_line(x2, y2, x1, y2, layer, width),  # 上边
        build_line(x1, y2, x1, y1, layer, width),  # 左边
    ]


def build_filled_rect(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str,
    fill: bool = True,
) -> str:
    """
    Place_Bound 通常需要一个填充矩形。

    ★ 签名已核对文档（03dbcre8.html）：
        axlDBCreateRectangle( l_bBox  [g_fill]  [t_layer]  [t_netname] )
            => l_result / nil

    参数顺序：**fill 在前，层在后**。
    l_bBox 是**对角两点**：((x1 y1) (x2 y2))。
    注意：未填充的矩形会忽略 t_netname。
    """
    pts = pt_list((x1, y1), (x2, y2))
    fill_s = "t" if fill else "nil"
    expr = call("axlDBCreateRectangle", pts, fill_s, q(layer))
    _check_balance(expr)
    return expr


# ============================================================
# 文字（位号 / 值 / 1 脚标识）
# ============================================================
def build_text_orientation(
    rotation: float = 0.0,
    mirrored: bool = False,
    justify: Optional[str] = None,
    text_block: str = "1",
) -> str:
    """
    构造 make_axlTextOrientation 结构体。

    ★ 参数完全照抄 Cadence 官方示例
    (share/pcb/examples/skill/dbcreate/axldbctpin.il)：

        make_axlTextOrientation(?textBlock "2"
                ?rotation 90 ?mirrored t ?justify "right")

    重要：**没有 ?xCoord / ?yCoord 参数** —— 位置是由调用方
    （axlDBCreatePin 的 anchorPoint、axlDBCreateText 的 anchorPoint）给的。

    :param text_block: text block 名字符串，如 "1" / "2"
    :param justify:    "left" / "center" / "right"
    """
    expr = call(
        "make_axlTextOrientation",
        textBlock=q(text_block),
        rotation=fmt_num(rotation),
        mirrored="t" if mirrored else "nil",
        justify=q(justify) if justify else None,
    )
    _check_balance(expr)
    return expr


def build_text(
    text: str,
    x: float,
    y: float,
    layer: str,
    text_block: str = "1",
    rotation: float = 0.0,
    justify: Optional[str] = None,
) -> str:
    """
    构造文字。

    ★ 签名已核对文档（03dbcre8.html）：
        axlDBCreateText( t_text  l_anchorPoint  r_textOrientation
                         [t_layer]  [o_attach] ) => l_result / nil

    l_anchorPoint 是点列表 (list x y)；
    r_textOrientation 由 make_axlTextOrientation 构造（必需参数）。
    含换行的文本会生成多个 text 对象，返回值是 dbid 列表。

    :param justify: "left" / "center" / "right"；None 时**不传该参数**，
                    由 Allegro 用默认对齐。
                    ★ 人工模板里的位号是 LEFT 对齐（实测 14/14），
                      需要复现时显式传 "left"。
    """
    orient = build_text_orientation(rotation=rotation, text_block=text_block,
                                    justify=justify)
    anchor = pt(x, y)
    expr = call("axlDBCreateText", q(text), anchor, orient, q(layer))
    _check_balance(expr)
    return expr


def build_text_block(
    name: str = "1",
) -> str:
    """
    引用一个 text block（文字样式），返回其**名字符串字面量**。

    ★ 重要：**没有 make_axlTextBlock 这个函数**（文档 0 命中）。
    Allegro 的 text block 是 drawing 配置里的一个列表（可在
    Setup -> Text Sizes 里看到），用名字引用，形如 "1" "2" "3"。

    ★ make_axlTextOrientation 的参数已按官方示例核实为：
        ?textBlock / ?rotation / ?mirrored / ?justify
      **没有 ?xCoord / ?yCoord**（位置由调用方的 anchorPoint 给）。

    所以这个函数的职责就只是把 text block 名包成字符串。
    """
    return q(str(name))


# ============================================================
# 板图范围 / 存盘
# ============================================================
def build_extent_set(
    x1: float = -50.0,
    y1: float = -50.0,
    x2: float = 50.0,
    y2: float = 50.0,
) -> str:
    """
    设置 drawing extents（图纸有效范围）。

    ★ 这是避免 SPMHA1-70 (Pin is outside of the extents) 的关键一步。
    新建的 .dra 图纸 extents 常常不包含原点，导致 pin 放不进去。

    ★ 函数名已核对文档（14dsnctl.html）：
        axlDBChangeDesignExtents( l_bBox ) => t / nil

    注意：**不叫 axlExtentSet**（那个函数不存在，属于常见误记）。
    l_bBox 形式：((-50 -50) (50 50))

    文档警告：如果新范围比现有对象小（有东西落在外面），会返回 nil 失败；
    也可能是 extents 超出数据库整数范围。所以扩展方向是安全的，收缩要小心。
    """
    expr = call("axlDBChangeDesignExtents", pt_list((x1, y1), (x2, y2)))
    _check_balance(expr)
    return expr


def build_change_origin(
    x: float = 0.0,
    y: float = 0.0,
) -> str:
    """
    平移设计原点。

    ★ 签名已核对文档（14dsnctl.html）：
        axlDBChangeDesignOrigin( l_point ) => t / nil

    这是**偏移量**：负值把数据库往左/下移，正值往右/上移。
    """
    expr = call("axlDBChangeDesignOrigin", pt(x, y))
    _check_balance(expr)
    return expr


# ------------------------------------------------------------
# 视图窗口（Zoom）—— 让图纸"打开就在正中间"
# ------------------------------------------------------------
#: ★★★ 为什么必须有这组（2026-09-30 实测，用户反馈）★★★
#:
#: 「新建的封装配打开后缩在画布**左下角**」——
#: 根因不是几何画错了（几何必须以原点为基准，不能动），而是
#: **.dra 里存着"上一帧视图窗口"**：
#:   ① 官方 `sym_template.dra`（mil 制）自带一个"原点在左下角"的视图；
#:   ② 我们复制模板 → 打开 → 建几何 → 存盘，
#:      视图窗口原封不动跟着存进新文件；
#:   ③ 用户一打开，看到的就是模板那帧视图 → 封装缩在左下角。
#:
#: 关键实测结论（推翻"改 extents 就能改视图"的直觉）：
#:   · `axlDBChangeDesignExtents` **只在缩小时"夹"一下视图**，
#:     视图比新范围大就会被裁到新范围；**不会**因为范围变大而放宽视图。
#:   · `axlExtentDB()` 返回的是"几何外包框"（窗口 fit 用），**不是**数据库范围。
#:   · `axlZoomBbox(nil)` 返回的才是**当前视图窗口**（design units）——
#:     这是唯一能"读回"视图做闭环校验的 API。
#:
#: 所以正确姿势是**显式设视图**：
#:     axlDBChangeDesignExtents(...)   ; 把"图纸范围"摆成以原点对称
#:     axlZoomPoints(...)              ; 把"视图窗口"摆成以原点对称
#:     axlZoomBbox(nil)                ; 读回校验（底左锚定 + 宽高比外扩 → 需修正一次）
def build_zoom_points(
    x1: float = -5.0,
    y1: float = -5.0,
    x2: float = 5.0,
    y2: float = 5.0,
    window: str = "nil",
) -> str:
    """
    精确设定某窗口的**视图范围**（缩放窗口）。

    ★ 签名已核对文档（`08intprm.html` "axlZoomPoints"）：
        axlZoomPoints( x_window  upperLeft_xy  lowerRight_xy ) => t / nil

    ▸ `x_window`：窗口 id，`nil` = 当前活动窗口
    ▸ 文档原文："The zoom maintains a 1:1 aspect ratio, the coordinates
      provided will be fitted into the active window size."
      —— 会**保持宽高比**把给定矩形铺满窗口，所以最终视图通常比请求的大一点。

    ★★ 实测行为（2026-09-30）：**底左锚定 + 按窗口宽高比向右/上外扩**。
        请求 ±4 的正方形（窗口宽高比 ≈1.19）→ 实得
        `[[-4, -4], [5.5226, 4]]`（左边界仍是 -4，右边多出来 1.52）。
        所以"想正好以原点居中"必须再修正一次平移，见
        `footprint_builder.frame_drawing()` 的闭环。

    :param x1..y2: 请求的矩形范围，**单位跟随当前 drawing 单位（本项目 mm）**
    """
    expr = call("axlZoomPoints", window, pt(x2, y2), pt(x1, y1))
    _check_balance(expr)
    return expr


def build_zoom_center(x: float = 0.0, y: float = 0.0,
                      window: str = "nil") -> str:
    """
    把视图**中心**平移到指定坐标（缩放倍率不变）。

    ★ 签名已核对文档（`08intprm.html` "axlZoomCenter"）：
        axlZoomCenter( x_window  xy ) => t / nil

    文档附注："It may adjust the point if centering results in the display
    bounding extents are outside the design extents."
    —— 想"缩放 + 居中"用 `axlZoomInOut`；本函数只挪中心。

    ★ 实测（2026-09-30）：在视图已经**包含**原点时，它**不会**把视图
      对称化（返回值 t，但 `axlZoomBbox` 不变）。所以居中要靠
      `build_zoom_points` 的坐标修正闭环，别指望这个函数兜底。
    """
    expr = call("axlZoomCenter", window, pt(x, y))
    _check_balance(expr)
    return expr


def build_zoom_bbox(window: str = "nil") -> str:
    """
    **读回**某窗口的视图范围（design units）。

    ★ 签名已核对文档（`08intprm.html` "axlZoomBbox"）：
        axlZoomBbox( x_window ) => bBox

    返回 `((x1 y1) (x2 y2))`。这是"图纸/视图居中"闭环里**唯一可读的反馈量**。

    ★ 别和 `axlExtentDB()` 混：
        `axlExtentDB()`      = 几何外包框（窗口 fit 用）
        `axlZoomBbox(nil)`   = 当前视图窗口（会随 zoom 命令变）
    """
    return call("axlZoomBbox", window)


def build_extent_db() -> str:
    """
    **读回**几何外包框（窗口 fit 的依据）。

    ★ 签名已核对文档（`25dbmisc.html` "axlExtentDB"）：
        axlExtentDB() => l_bBox / nil

    ▸ **无参**！实测多传参数不会报错，只会打
      `UserWarning: (axlExtentDB): Unrecognized options ...` 然后忽略。
    ▸ 符号（dra）模式 = 「所有可见对象」的外包框。
    """
    return call("axlExtentDB")


def build_window_fit() -> str:
    """
    窗口 fit（= Allegro 命令 `zoom fit` 的 API 形态）。

    ★ 签名已核对文档（`08intprm.html` "axlWindowFit"）：`axlWindowFit() => l_bBox`

    ▸ **dra（symbol）模式**：文档原文 "performs a fit such that all
      visible objects occupy maximum window area" —— 会让封装**占满**窗口，
      并不是"留白居中"。想要留白请用 `build_zoom_points`。
    """
    return call("axlWindowFit")


def build_save(
    design_path: Optional[str] = None,
    no_confirm: bool = True,
) -> str:
    """
    保存当前 drawing。

    ★ 完整签名（文档 14dsnctl.html 第 995 行）：
        axlSaveDesign( ?design t_design ?mode t_option ?noMru g_noMru
                       ?noConfirm g_noConfirm ?writeModel g_write ) => t_design/nil

    ★★ 踩坑记录（2026-09-28 实测）：
        `axlSaveDesign()` 不带参数调用时，Allegro **可能弹出交互对话框**，
        导致 SkillBridge 连接被阻塞/中止
        （报 `ConnectionAbortedError(10053)`，Allegro 侧一直等待输入）。

        必须传 **`?noConfirm t`** 来抑制确认框，让保存变成非交互操作。

    :param design_path: 另存路径；None 时存回原位
    :param no_confirm:  是否抑制确认对话框（**默认 True，别改**）
    """
    expr = call(
        "axlSaveDesign",
        design=q(design_path) if design_path else None,
        noConfirm="t" if no_confirm else None,
    )
    _check_balance(expr)
    return expr


# ============================================================
# 环境变量（NOCONFIRM 族）—— 弹框的"变量级"总开关
# ============================================================
#: 自动化会话应当设上的 NOCONFIRM 变量族。
#: 出处：`<Cadence>/doc/algroenvvar/Drawing_Settings.html`（Script Settings 段）
#:
#: ★ 为什么必须有这一层：官方文档自己写明 —— `?mode "wf"` **等价于设 NOCONFIRM**，
#:   但 `wf` **只在 `axlOpenDesign` / `axlOpenDesignForBatch` 里被识别**。
#:   其它 API（`axlCompileSymbol`、`axlPadstackToDisk` …）根本不认 `wf`，
#:   想让它们不弹框，**唯一办法就是设变量**。
#:
#: ★ 这四个都是**全局状态**：设上之后连用户手工操作也失去确认保护，
#:   所以必须"用时设、用完恢复"（见 `build_restore_variables`）。
AUTO_NOCONFIRM_VARS: Tuple[str, ...] = (
    "NOCONFIRM",                  # 通用确认框（官方：should be used only with scripts）
    "noconfirm_savedb",           # ★ 覆盖已有数据库保存时的警告 → File Exists. Overwrite?
    "noconfirm_uprev",            # ★ 打开旧版本图纸的升级警告
    "padstack_nowarning_display", # Padstack Editor 保存警告（官方：provided for automation）
)


def _var_name(name: Any) -> str:
    """校验环境变量名。防注入：只允许标识符形态。"""
    n = str(name).strip()
    if not n.isidentifier():
        raise ValueError(f"非法环境变量名: {name!r}")
    return n


def _var_list(names: Sequence[str]) -> str:
    """['A','B'] -> '( "A" "B" )  （SKILL 的**带引号列表**字面量）"""
    return "'(" + " ".join(q(_var_name(n)) for n in names) + ")"


def build_get_variable(name: str) -> str:
    """
    读一个环境变量：`axlGetVariable("NAME")` ⇒ 值 / nil

    文档：`algroskill/09cmdshl.html`
    """
    expr = call("axlGetVariable", q(_var_name(name)))
    _check_balance(expr)
    return expr


def build_set_variable(name: str, value: bool = True) -> str:
    """
    设一个环境变量：`axlSetVariable("NAME" t)` / `axlSetVariable("NAME" nil)`

    ★ 注意 SKILL 的 `t` / `nil` 是**裸符号**，不能加引号写成 `"t"`。
    """
    expr = call("axlSetVariable", q(_var_name(name)), "t" if value else "nil")
    _check_balance(expr)
    return expr


def build_unset_variable(name: str) -> str:
    """清一个环境变量：`axlUnsetVariable("NAME")`（回到"未设"状态）"""
    expr = call("axlUnsetVariable", q(_var_name(name)))
    _check_balance(expr)
    return expr


def build_get_variables(names: Sequence[str] = AUTO_NOCONFIRM_VARS) -> str:
    """
    一条表达式读**多个**变量，返回等长列表 —— 省掉 N 次往返。

        mapcar(lambda((v) axlGetVariable(v)) '("NOCONFIRM" "noconfirm_savedb" ...))

    ★ 形参名用 `v`：必须避开 `t` / `lst`（SKILL 里这两个名字有特殊语义）。
    ★ 返回的是**位置对应**的列表，调用方需按同一顺序 zip 回名字。
    """
    expr = f"mapcar(lambda((v) axlGetVariable(v)) {_var_list(names)})"
    _check_balance(expr)
    return expr


def build_set_variables(names: Sequence[str] = AUTO_NOCONFIRM_VARS) -> str:
    """
    一条表达式把**多个**变量设成 `t` —— 自动化入口处一次调用搞定。

        errset(foreach(v '("NOCONFIRM" ...) axlSetVariable(v t)) t)

    ★ 为什么要套 `errset`：个别变量在特定版本/产品级别下可能不被支持，
      一个名字报错不该把整条通道带崩。`errset(expr t)` 的第二参 `t`
      表示"失败也不要把错误信息打到 CIW"。
    """
    expr = f"errset(foreach(v {_var_list(names)} axlSetVariable(v t)) t)"
    _check_balance(expr)
    return expr


def build_restore_variables(previous: Dict[str, Any]) -> str:
    """
    按"当初读到的旧值"逐条恢复现场（照抄 Cadence 论坛版主 eDave
    `AFn_OpenDesign` 里的保存-恢复范式）。

        previous = {"NOCONFIRM": None, "noconfirm_savedb": True}
        -> (list axlUnsetVariable("NOCONFIRM")
                 axlSetVariable("noconfirm_savedb" t))

    语义：
        旧值**为真** -> `axlSetVariable(name t)`（人家本来就开着，设回去）
        旧值**为空** -> `axlUnsetVariable(name)`（人家本来就没设，清掉）

    :param previous: {变量名: 旧值}。**每个名字都要有**；缺项按"未设"处理。
    """
    stmts: List[str] = []
    for name, old in (previous or {}).items():
        n = _var_name(name)
        stmts.append(build_set_variable(n, True) if old
                     else build_unset_variable(n))
    expr = "(list " + " ".join(stmts) + ")" if stmts else "nil"
    _check_balance(expr)
    return expr


def build_normalize_var_reply(raw: Any, names: Sequence[str]) -> Dict[str, bool]:
    """
    把 `build_get_variables()` 的回复归一化成 `{变量名: bool}`。

    ★ 为什么需要这一步：回复能否被 translator 正规解码**不保证** ——
      解不出来时 `_decode_skill_reply` 会退化成**原样字符串**
      （SKILL 的列表打成 `(nil t nil t)`，不是 Python 字面量，
      `ast.literal_eval` 直接失败）。所以这里再兜一层文本解析。
    """
    names = list(names)

    # 形态 1：解码成功，是序列
    if isinstance(raw, (list, tuple)):
        vals = list(raw)
    # 形态 2：字符串，可能是 SKILL 列表的打印形式
    elif isinstance(raw, str):
        body = raw.strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        vals = body.split() if body else []
    # 形态 3：单值 / nil
    elif raw is None:
        vals = []
    else:
        vals = [raw]

    out: Dict[str, bool] = {}
    for i, n in enumerate(names):
        v = vals[i] if i < len(vals) else None
        if isinstance(v, str):
            out[n] = v.strip().lower() not in ("nil", "", "false")
        else:
            out[n] = bool(v)
    return out


def build_open_drawing(path: str, mode: str = "wf") -> str:
    """
    打开 / 新建一个 drawing。

    ★ 完整签名（文档 14dsnctl.html "axlOpenDesign"）：
        axlOpenDesign( ?design t_design ?mode t_mode ?noMru g_noMru
                       ?ignoreLock g_ignore ) => t_design /nil

    ▸ mode 语义：
        "w"   可写
        "wf"  ★ 可写 + **丢弃当前未保存编辑且不弹确认框**
              （= 环境变量 NOCONFIRM；自动化必用这个）
        加 "l"（如 "wl"）禁用 Allegro 文件锁
    ▸ ?noMru t  不更新最近文件列表

    ★★ 结论修正（2026-09-29，用真调用法复测）：
        早期"axlOpenDesign 运行时 unbound"的结论是**裸符号探测法的误判**。
        该函数**确实存在且可用** —— 项目里的 `X.dra` 就是它建出来的。

    ★★★ 唯一的地雷：**path 指向不存在的文件时会弹 Drawing Parameters 框**
        文档原文：
          "If t_design does not exist on disk, the standard Allegro PCB
           Editor Drawing Parameters dialog box appears."
        → 调用永不返回，通道错位，只能在 CIW 里重启服务恢复。

    ★ 因此自动化的正确姿势是：
        1) 要"新建一张干净图纸" → 用 `build_kill_design()`（无参，不涉及路径）
        2) 要打开已有图纸        → 先 `os.path.exists(path)` 确认后再调本函数
        3) 不要在自动化里传"打算新建但还不存在"的路径

    :param path: .dra 文件路径（须已存在；建议绝对路径 + 正斜杠）
    :param mode: "wf"（默认，无确认）/ "w" / "wl"
    """
    expr = call("axlOpenDesign", design=q(path), mode=q(mode))
    _check_balance(expr)
    return expr


def build_save_design(name: str) -> str:
    """
    另存当前设计（等价 File → Save As）。

    ★ 签名（文档 14dsnctl.html "axlSaveDesign"）：
        axlSaveDesign( ?design t_design [ ?file g_file ] ) => t_design / nil

    ★★★ 落点是 **Allegro 工作目录**，不是图纸目录 ★★★
        与 `axlOpenDesign` 同源的坑：调用前必须先 `axlShell("cd <dir>")`，
        否则 .dra 会漏到 Allegro 的启动目录。

    ★ 返回值 = **新设计名**（实测 `axlSaveDesign(?design "ZZZZZ")` → `"ZZZZZ"`），
      可以直接当判据用（不像 `axlCompileSymbol` 恒 nil）。

    ★★ 用途（2026-09-30 实测归纳）★★
        绕开 `axlOpenDesign` 的「同名内存命中」：
        打开一个**磁盘上存在、且名字与内存里任何设计都不同**的文件时，
        Allegro 必然从磁盘读取；打开**同名**文件时则可能直接激活内存副本
        （图纸里会残留上一次的图元，表现为 pin / 丝印数量整齐翻倍）。
        → 做法：用唯一临时名打开模板，再 `axlSaveDesign` 改成正式名。

    :param name: 目标设计名（不含 .dra；相对名落 Allegro 工作目录）
    """
    expr = call("axlSaveDesign", design=q(name))
    _check_balance(expr)
    return expr


def build_kill_design() -> str:
    """
    ★★★ 新建一张全新的空图纸（替换当前图纸）—— 等价于 File → New ★★★

    ★ 完整签名（文档 14dsnctl.html "axlKillDesign"）：
        axlKillDesign( ) ⇒ t/nil

      Description:
        "Same as ( axlOpenDesign <unnamed> "wf" ), where <unnamed> is the
         standard Allegro PCB Editor name provided for an unnamed design."

      Value Returned:
        t    New design opened, replacing current design.
        nil  No new design opened.

      Note:
        "This will void all axl dbid handles and clear the selection set."

    ★★★ 为什么这是"全自动建图纸"的最优解 ★★★
        1. **零参数** —— 不存在"传错路径导致弹 Drawing Parameters 框"的风险
        2. **内部用 "wf" 模式** —— 丢弃未保存编辑时**不弹确认框**
        3. 完全无 UI，可安全用于 SkillBridge 自动化通道

    ▸ 用途：
        - 每次生成封装前**重置图纸**，彻底解决"图元叠加污染"问题
          （比 he 手工 File→New 或逐层 axlDeleteByLayer 都干净）
        - 换代 toolkit 后立刻接 `axlDBChangeDesignUnits` / extents 设置

    ★ 副作用：**作废所有已持有的 dbid 句柄**并清空选择集
        → 调用后必须重新 `axlDBGetDesign()` 取对象，别复用旧 dbid

    ★ 注意：新建出来的图纸 designType 会**继承当前图纸的类型**，
        所以若需要 Package symbol，应保证调用前就在 symbol editor 里
        （或调用 `axlSetDesignType` / 用已设好类型的模板图纸）。
    """
    expr = call("axlKillDesign")
    _check_balance(expr)
    return expr


def build_change_units(units: str, accuracy: Optional[int] = None) -> str:
    """
    ★★★ 切换当前 drawing 的 **单位 / 精度** ★★★

    ★ 完整签名（文档 14dsnctl.html "axlDBChangeDesignUnits"）：
        axlDBChangeDesignUnits( t_units/nil  x_accuracy/nil  x_drcCount/nil )
            ⇒ x_drcCount / nil

      Description:
        "Changes the units and accuracy of the design. To maintain the
         current design value, use nil for t_units or x_accuracy.
         When you change units, also change accuracy to maintain adequate
         precision within the database."

      官方示例：
        axlDBChangeDesignUnits("millimeters" 4)
            Changes a design from mils/0 to millim/4 (accuracy maintains
            database precision).
        axlDBChangeDesignUnits(nil 2)
            Increases accuracy from 0 to 2 and keeps the same units.

    ★ 合法单位串（照抄官方表）：`"mils"` / `"millimeters"` / `"inches"` /
      `"centimeters"` / `"microns"`。用错会返回 nil 且**不报错**，
      所以调用后必须复读 `axlDBGetDesignUnits()` 校验。

    ★ 精度经验值（官方表）：
        mils        0 .. 4   （>2 不建议，Gerber 会出问题）
        millimeters 0 .. 4   ← mm 制取 **4**（= 0.1 µm 步长）
      精度不够 → 坐标被**量化**：
        mils/1 存 1.1 mm 会落成 43 mil = 1.0922 mm（±7.8 µm 系统性误差）。
      这正是「官方 sym_template.dra 是 mils/0 → mm 封装带量化误差」的根因。

    ★ 为什么要切：金标准 `.dra` 实测是 **millimeters / 精度 0.0001**，
      而官方模板复制出来的新图纸是 **mils / 1**。不切就永远对不上金标准。

    ★ 副作用（文档明写）：
        - "This function may take extra time on large designs"（对空白图纸很快）
        - "This function reruns DRC if enabled"（会重跑一次 DRC）
      → 必须在**画任何图元之前**调用，别画完再切。

    :param units:    "millimeters" 等；None 时保持原单位只改精度
    :param accuracy: 精度位数；None 时保持原精度只改单位
    """
    args = ['"%s"' % units if units is not None else "nil"]
    if accuracy is not None:
        args.append(str(int(accuracy)))
    expr = call("axlDBChangeDesignUnits", *args)
    _check_balance(expr)
    return expr


def build_open_drawing_for_batch(path: str, mode: str = "wf") -> str:
    """
    批量模式打开 drawing（不改变命令与菜单）。

    ★ 完整签名（文档 14dsnctl.html "axlOpenDesignForBatch"）：
        axlOpenDesignForBatch( t_design t_mode ) ==> t_design/nil

    ★★★ 注意：这是**两个位置参数**，不是关键字参数！
        ✅ axlOpenDesignForBatch("u.dra" "wf")      ← 文档示例
        ❌ axlOpenDesignForBatch(?design "u.dra")   ← 错

    ▸ 与 axlOpenDesign 的区别：
        - "commands and menus are not changed"（batch 场景不干扰 UI）
        - **没有 ?noMru / ?ignoreLock** 参数
        - 返回值：t_design if opened, or nil if error

    ★ 同样的地雷：t_design 不存在于磁盘 → 弹 Drawing Parameters 框。

    :param path: .dra 文件路径（须已存在）
    :param mode: "w" / "wf"（"wf" = 无确认丢弃编辑）
    """
    expr = call("axlOpenDesignForBatch", q(path), q(mode))
    _check_balance(expr)
    return expr


def build_delete_by_layer(
    layers: Union[str, Sequence[str]],
    include_fixed: bool = False,
) -> str:
    """
    删除一个或多个层上的**全部数据**（清空图纸的主力 API）。

    ★ 完整签名（文档 25dbmisc.html "axlDeleteByLayer"）：
        axlDeleteByLayer( t_layerName/lt_layerName [nil/'fixed] ) ==> x_cnt/nil

      Arguments:
        t_layerName   层名，格式 <class>/<subclass>
        lt_layerName  层名列表
        'fixed        ★ 可选，**连 FIXED 属性一起删**（默认跳过 FIXED 对象）
      Value Returned:
        x_cnt   删掉的元素个数

    ★★★ 重要行为边界（官方原文）★★★
        - **不删 pin，也不删 via** ← 关键！
          "Certain classes, such as DRC_ERROR_CLASS, PIN, VIA_CLASS,
           ROUTER_PLAN and CAVITY, are ignored."
        - 会删 symbol 关联的 pin escape 等数据
        - **不删 symbol 定义内部的对象**
        - 动态铜皮还要额外删对应 `BOUNDARY` 类
        → 所以要清 pin 必须用 `axlDeleteObject`（见 build_delete_objects）

    :param layers: 单个层名或层名列表，如
                   "PACKAGE GEOMETRY/SILKSCREEN_TOP"
    :param include_fixed: True 时传 'fixed，连锁定对象一起删
    """
    if isinstance(layers, str):
        layer_expr = q(layers)
    else:
        layer_expr = "list(" + " ".join(q(x) for x in layers) + ")"

    args = [layer_expr]
    if include_fixed:
        args.append("'fixed")
    expr = call("axlDeleteByLayer", *args)
    _check_balance(expr)
    return expr


def build_delete_objects(
    collection: str = "pins",
    ripup: bool = False,
    snapshot: bool = True,
) -> str:
    """
    删除当前图纸里的某一类对象（**包括 pin**，弥补 axlDeleteByLayer 的盲区）。

    ★ 完整签名（文档 06intedt.html "axlDeleteObject"）：
        axlDeleteObject( o_dbid/lo_dbid [g_mode] ) ⇒ t/nil

      Arguments:
        o_dbid/lo_dbid  dbid，或 dbid 列表
        g_mode          'ripup —— 启用 etch rip-up（同 Allegro delete 的 ripup 选项）
      Value Returned:
        t    删了一个或多个
        nil  一个都没删

    ★★ 为什么要先拍快照 ★★
        `axlDeleteObject` 会**改动 `axlDBGetDesign()->pins` 这个集合本身**。
        在 `foreach` 里边遍历边删会让游标失效（标准 Allegro 陷阱，同
        "不要修改正在遍历的集合"）。
        → 先 `mapcar` 把 dbid 全部取出来存成快照列表，再遍历快照删。

        ⚠️ SkillBridge 的 eval_skill 是**单行求值**，所以这里用
        `prog1` 把多条语句串成一行：
            prog1(ps = mapcar(...) foreach(x ps axlDeleteObject(x)))

    ▸ 支持的 collection（对应 `axlDBGetDesign()->xxx`）：
        pins / text / lines / rects / shapes / arcs / vias / clines
        ⚠️ 具体字段名以 `axlDBGetDesign()` 返回值为准，可先探测一次。

    :param collection: `axlDBGetDesign()->` 后面的集合名，默认 "pins"
    :param ripup:      True 加 `'ripup`（删 net 时 rip-up 走线）
    :param snapshot:   是否先拍快照再删（**默认 True，别关**）
    """
    get_expr = f"axlDBGetDesign()->{collection}"
    del_args = "x 'ripup" if ripup else "x"

    if snapshot:
        expr = (
            f"prog1(__snap = mapcar(lambda((x) x) {get_expr}) "
            f"foreach(x __snap axlDeleteObject({del_args})) "
            f"length(__snap))"
        )
    else:
        expr = f"foreach(x {get_expr} axlDeleteObject({del_args}))"
    _check_balance(expr)
    return expr


def build_clear_drawing(
    include_pins: bool = True,
    include_text: bool = True,
    include_geometry: bool = True,
) -> List[str]:
    """
    清空当前图纸里的图元（**保留图纸本体、单位、范围**）。

    比 `build_kill_design()` 更保守：图纸参数不丢，只删内容。

    ★ 典型用法（解决"图元叠加污染"）：
        在 `build_footprint` 前先清一遍，保证每次生成都是干净的。

    ▸ 分层策略：
        几何（线/矩形/弧/形状） → `axlDeleteByLayer` 按层删（高效）
        pin                      → `axlDeleteObject`（因为按层删不碰 pin）
        文字                     → `axlDeleteObject`（refdes 文字要在编译前删掉旧的）

    :return: SKILL 表达式列表（按顺序执行）
    """
    steps: List[str] = []

    if include_geometry:
        # 常见 package symbol 层；不存在的层返回 0，无害
        steps.append(build_delete_by_layer([
            "PACKAGE GEOMETRY/SILKSCREEN_TOP",
            "PACKAGE GEOMETRY/ASSEMBLY_TOP",
            "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
            "PACKAGE GEOMETRY/DISPLAY_TOP",
            "REF DES/SILKSCREEN_TOP",
            "REF DES/ASSEMBLY_TOP",
            "BOARD GEOMETRY/SILKSCREEN_TOP",
            "BOARD GEOMETRY/OUTLINE",
        ]))
        # 动态铜皮需连 BOUNDARY 一起删
        steps.append(build_delete_objects("shapes"))

    if include_pins:
        steps.append(build_delete_objects("pins"))

    if include_text:
        steps.append(build_delete_objects("text"))

    return steps


def build_content_count(prop: str) -> str:
    """
    ★ 数当前 drawing 里某个内容属性有多少个元素（用于**模板污染校验**）。

    ★ 为什么后面要套一层 `lambda`：属性为空时 `axlDBGetDesign()->pins`
      返回 `nil`，而 `length(nil)` 会直接报
      `*Error* length: argument #1 should be a list` —— 所以必须先判空。

    表达式语义：
        `(lambda((x) if(listp(x) length(x) if(x 1 0))) axlDBGetDesign()-><prop>)`
           x = nil（空）-> 0
           x = list     -> length(x)
           x = 单个 dbid-> 1

    ★ 形参名取 `x`：**必须避开 `t` 和 `lst`** —— 这两个名字在 SKILL 里有
      特殊语义（`t` 是布尔真、`lst` 是某些内建的约定形参名），拿来当
      lambda 形参会出怪事。

    ★ 可用属性（本机实测）：
        ✅ `pins` / `text` / `padstacks`
        ❌ `lines` / `shapes` / `rectangles` —— **不在 `axlDBGetDesign()`
           的属性表里**，读恒为 0（不代表没建成），不能当判据

    :param prop: 属性名，如 "pins" / "text" / "padstacks"
    """
    if not str(prop).isidentifier():
        raise ValueError(f"非法属性名: {prop!r}")
    expr = f"(lambda((x) if(listp(x) length(x) if(x 1 0))) axlDBGetDesign()->{prop})"
    _check_balance(expr)
    return expr


def build_new_drawing_note() -> str:
    """
    ★★★ 结论修正（2026-09-29）★★★

    早期结论"AXL 没有新建图纸的方法，必须人工建"是**完全错误的** ——
    成因是裸符号探测法把 `axlKillDesign` / `axlOpenDesign` 误判为 unbound。

    正确结论（均已用真调用法 + 实际执行验证）：

      1. **`axlKillDesign()`** —— 零参数新建空图纸，无 UI，最稳
         等价于 `axlOpenDesign <unnamed> "wf"`，即 File → New
      2. **`axlOpenDesign(?design p ?mode "wf")`** —— 打开指定图纸
         项目里的 `X.dra` 就是这么建/打开的
      3. **`axlOpenDesignForBatch("p" "wf")`** —— batch 版（2 位置参数）

    ★ 唯一约束：`axlOpenDesign` 的路径**必须已存在**，否则弹 Drawing
      Parameters 框卡死通道 → 新建场景请直接用 `axlKillDesign()`。

    ★ Allegro **确实没有** `axlNewDrawing` 这个函数（全文档 0 命中）——
      这一点是对的，但**不等于"没法新建图纸"**。

    实现见：`build_kill_design()` / `build_open_drawing()` /
             `build_open_drawing_for_batch()`
    """
    raise NotImplementedError(
        "已被 build_kill_design() / build_open_drawing() 取代。"
        "新建图纸用 axlKillDesign()（零参、无 UI），"
        "或 axlOpenDesign(?design <已存在的路径> ?mode \"wf\")。"
    )


def build_compile_symbol(
    symbol_name: Optional[str] = None,
    symbol_type: Optional[str] = None,
) -> str:
    """
    ★★★ 出 .psm —— 生成封装符号（= 界面里的 File → Create Symbol）★★★

    ★ 完整签名（文档 14dsnctl.html 第 2341 行）：
        axlCompileSymbol( ?symbol t_name ?type t_type ) ⇒ t_symbolName /nil

      Description:
        "Compiles and edit checks the current (symbol) design and saves the
         compiled version on disk under the name t_name.
         If t_name is not provided, then t_name is the current drawing name.
         ★ If t_name is provided as nil, you are prompted for the name. ★
         Uses the symbol type in determining some of the edit checks."

      Note:
        "This function is only available in the symbol editor.
         This is a SKILL interface to the Allegro PCB Editor 'create symbol'
         command and thus has the same behavior."

    ★★★ 致命陷阱（2026-09-29 实测踩中，卡死通道）★★★
        **绝不能传 `?symbol nil`** —— 那会弹出「另存为」对话框，
        调用永不返回，SkillBridge 队列错位，必须重启服务才能恢复。

        正确用法（无 UI，全自动）：
            axlCompileSymbol(?symbol "NB_XTAL4_3D2X2D5X0D7" ?type "package")

    :param symbol_name: 编译后的符号名（**必填**；None 时用当前图纸名）
    :param symbol_type: 符号类型，可选 "package" / "mechanical" / "format"
                        / "shape" / "flash"；None 时用当前图纸类型
    :return: SKILL 表达式
    """
    if symbol_name is None:
        # 用当前图纸名 —— 安全，不弹框
        expr = call(
            "axlCompileSymbol",
            type=q(symbol_type) if symbol_type else None,
        )
    else:
        expr = call(
            "axlCompileSymbol",
            symbol=q(symbol_name),
            type=q(symbol_type) if symbol_type else None,
        )
    _check_balance(expr)
    return expr


def build_set_symbol_type(symbol_type: str = "package") -> str:
    """
    ★ 显式声明当前图纸的符号类型：`axlSetSymbolType("package")`

    ★★★ 这条推翻了本项目的旧结论（2026-09-29 调研更正）★★★

      早期记的是"AXL 没有改 symbol 类型的 API"，实测报错
      *"Procedure can only be applied to symbol drawings"* ——
      **那是"当时不在 symbol editor 里"，不是 API 不存在。**

      官方文档明确：`axlSetSymbolType`
      "**is only available in the symbol editor**" ——
      只要图纸本身已经是 symbol 类型，它就能用。

    → 价值：`new_symbol_drawing()` 复制模板后可以**显式声明类型**，
      不再依赖"模板文件本身必须是 Package symbol"这一脆弱前提。

    :param symbol_type: `package` / `mechanical` / `format` / `shape` / `flash`
    """
    allowed = ("package", "mechanical", "format", "shape", "flash")
    typ = str(symbol_type).strip().lower()
    if typ not in allowed:
        raise ValueError(f"非法符号类型 {symbol_type!r}，只允许 {allowed}")
    expr = call("axlSetSymbolType", q(typ))
    _check_balance(expr)
    return expr


# ============================================================
# 组合：完整封装骨架
# ============================================================
def build_full_footprint_script(
    padstack_name: str,
    pad_layers: Sequence[str],
    pins: Sequence[Dict[str, Any]],
    body: Optional[Dict[str, float]] = None,
    silk: Optional[Dict[str, float]] = None,
    place_bound: Optional[Dict[str, float]] = None,
    refdes: Optional[Dict[str, Any]] = None,
    drill_expr: Optional[str] = None,
) -> List[str]:
    """
    组装"建一个完整封装"的 SKILL 表达式序列。

    本函数只负责**生成表达式**，不负责执行 —— 执行与结果收集在
    footprint_builder.py 里，好处是这里可以单独做单元测试（不需要 Allegro）。

    :param padstack_name: padstack 名
    :param pad_layers:    该 padstack 的各层表达式（build_pad_layer 产出）
    :param pins:          引脚列表，每项 {"number": "1", "x": 0.0, "y": 0.0}
    :param body:          本体轮廓 {"x1","y1","x2","y2","width"}（Assembly 层）
    :param silk:          丝印轮廓 {"x1","y1","x2","y2","width"}
    :param place_bound:   Place_Bound {"x1","y1","x2","y2"}
    :param refdes:        位号 {"text":"U1","x":..,"y":..,"size":..}
    :return: SKILL 表达式列表（按执行顺序）
    """
    steps: List[str] = []

    # 1) 先扩 extents，否则 pin 放不进去
    steps.append(build_extent_set())

    # 2) 建 padstack
    steps.append(build_create_padstack_guarded(padstack_name, pad_layers, drill_expr))

    # 3) 放 pin
    for p in pins:
        ptxt = build_pin_text(
            number=str(p.get("number", "")),
            offset=p.get("text_offset", (0.0, 0.0)),
            text=p.get("text"),
        )
        steps.append(
            build_create_pin(
                padstack_name,
                float(p.get("x", 0.0)),
                float(p.get("y", 0.0)),
                ptxt,
                p.get("rotation"),
            )
        )

    # 4) 丝印
    if silk:
        steps.extend(
            build_rect_lines(
                silk["x1"], silk["y1"], silk["x2"], silk["y2"],
                "PACKAGE GEOMETRY/SILKSCREEN_TOP",
                silk.get("width", 0.15),
            )
        )

    # 5) Assembly 本体轮廓
    if body:
        steps.extend(
            build_rect_lines(
                body["x1"], body["y1"], body["x2"], body["y2"],
                "PACKAGE GEOMETRY/ASSEMBLY_TOP",
                body.get("width", 0.1),
            )
        )

    # 6) Place_Bound
    if place_bound:
        steps.append(
            build_filled_rect(
                place_bound["x1"], place_bound["y1"],
                place_bound["x2"], place_bound["y2"],
                "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
            )
        )

    # 7) 位号（缺这个 Create Symbol 会报 SPMHCS-1: missing a refdes）
    if refdes:
        steps.append(
            build_text(
                str(refdes.get("text", "U*")),
                float(refdes.get("x", 0.0)),
                float(refdes.get("y", 0.0)),
                "PACKAGE GEOMETRY/SILKSCREEN_TOP",
                text_block=str(refdes.get("text_block", "1")),
                rotation=float(refdes.get("rotation", 0.0)),
            )
        )

    # 8) padstack 落盘
    steps.append(build_padstack_to_disk(padstack_name))

    # 9) 存 drawing
    steps.append(build_save())

    return steps
