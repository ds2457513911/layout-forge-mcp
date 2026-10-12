# Allegro 17.2 SKILL padstack API 核实报告

> 来源：实测本机 `D:\Dev_tools\Cadence_17.2\Cadence\Cadence_SPB_17.2-2016\doc\algroskill\`
> 文档版本：`Cadence SPB 17.2-2016`，文档日期 2017-11-26
> 核实方式：直接读官方 HTML 手册 + 官方示例脚本，**非记忆、非社区传闻**
> 核实时间：2026-09-28

---

## 一、首要纠正：`axlPadstackGetList()` 不存在

你的上下文里把这个函数列为"待验证的关键问题"，但**在 17.2 的完整文档里搜不到它**（`grep axlPadstackGetList` 在 `algroskill\` 全部 40 个 HTML 文件中 0 命中）。

同样，社区常提的 `axlDBCreatePad`（注意不是 `axlDBCreatePadStack`）也不存在。

**17.2 里 padstack 相关的真实函数全集**（从文档全文正则提取，去重）：

```
axlDBCopyPadstack        axlDBCreatePadStack      axlLoadPadstack
axlPadStackPad           axlPadstackEdit          axlPadstackSetType
axlPadstackToDisk        axlPadstackUsageTypes    axlPurgePadstack
axlPurgePadstacks        axlReplacePadstack
```

辅助函数：`axlPadFigureTypes`、`axlPadUserMaskLayers`、`axlPadDbidDoc`、`axlPadOnLayer`、`axlDBGetPad`

构造函数（**关键**）：`make_axlPadStackPad`、`make_axlPadStackDrill`、`make_axlPinText`、`make_axlTextOrientation`、`copy_axlPadStackPad`

**要枚举"当前设计里有哪些 padstack"，没有 getList 这种函数。** 替代思路：
- `axlDBGetDesign()` 拿 design dbid，再从里面取 padstack 集合
- 或者遍历所有 pin/via，用 `axlDBGetPad` 反查它们的 padstack
- 或直接 `axlPadstackToDisk()` 不带参数，**一次性把所有 padstack dump 成 .pad 文件**（文档明确写了这个用法）

---

## 二、核心函数签名（已核实）

### 2.1 `axlDBCreatePadStack` — 创建 padstack 的唯一入口

```skill
axlDBCreatePadStack( t_name  r_drill  l_pad  [ g_nocheck ] ) => l_result / nil
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `t_name` | string | padstack 名字 |
| `r_drill` | defstruct / nil | 钻孔数据。**SMD 焊盘传 `nil`** |
| `l_pad` | list | pad 定义列表。**每个 layer 最多 4 个**（对应 REGULAR/ANTIPAD/THERMAL/KEEPOUT 四种 type） |
| `g_nocheck` | t/nil | 可选。`t` 关闭校验，`nil` 做 4 项校验（见下） |

**返回值**：成功返回 padstack 的 dbid，失败返回 `nil`。

**`g_nocheck = nil` 时的 4 项校验**（文档原文）：
- Contiguous pad definitions（pad 定义是否连续）
- Anti-pad / thermal-relief pad definitions
- Existence of two pads with a drilled hole
- A drilled hole with the existence of two pads

> **实践建议**：批量生成时传 `t` 跳过校验，否则一个封装里 100 个 pin 逐个校验会很慢；最后统一用 dbdoctor 跑一次。

---

### 2.2 `r_drill` 结构：`make_axlPadStackDrill` 字段

只列封装生成用得到的，完整字段见文档：

| 字段 | 类型 | 说明 |
|---|---|---|
| `usage` | string | `"Smd"` / `"Through"` / `"Through_pin"` / `"bbvia"` / `"uvia"` / `"Slot"` / `"Mounting_hole"` / `"Tooling_hole"` 等，完整列表调 `axlPadstackUsageTypes()` |
| `holeType` | symbol | `'CIRCLE_DRILL` / `'SQUARE_DRILL` / `'OVAL_SLOT` / `'RECTANGLE_SLOT` |
| `plating` | symbol | `'PLATED` / `'NON_PLATED` / `nil` |
| `drillDiameter` | float | 成品孔径 |
| `slotSize` | `(f_width f_height)` | SLOT 专用。**注意：f_width 是 Y size，f_height 是 X size（文档原文反直觉）** |
| `offset` | `(f_x f_y)` | 钻孔偏移 |
| `figure` | symbol | 钻孔符号：`'CIRCLE` / `'SQUARE` / `'HEXAGON` / `'OCTAGON` / `'CROSS` / `'DIAMOND` / `'TRIANGLE` / `'OBLONG_X` / `'OBLONG_Y` / `'RECTANGLE` / `nil` |
| `figureSize` | `(f_width f_height)` | 钻孔符号尺寸 |
| `drillChar` | string | 钻孔字符，**最多 3 字符** |
| `holeTolerance` | `(f_pos f_neg)` | 孔径公差 |
| `spanLockCount` | t/nil | `t` = 增删层时 padstack 不伸缩 |
| `pluralVia` | t/nil | 多网络 via，仅通孔可设 |

**SMD 焊盘**：`r_drill` 直接传 `nil`，不需要构造。

---

### 2.3 `l_pad` 结构：`make_axlPadStackPad` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `layer` | string / symbol | `"TOP"` / `"BOTTOM"` / `"DEFAULT_INTERNAL"`（映射所有内层）/ `"SOLDERMASK_TOP"` / `"PASTEMASK_TOP"` / `"COVERLAY_TOP"` / `'adjacent`（相邻层 keepout）/ `'backdrillStart` 等 |
| `type` | symbol | `'REGULAR` / `'ANTIPAD` / `'THERMAL` / `'KEEPOUT`。**`nil` 等同 `'REGULAR`** |
| `figure` | symbol / string | 见下方 2.4 完整清单 |
| `figureSize` | `(f_width f_height)` | 尺寸。**圆形只需填一个，另一个填 0** |
| `offset` | `(f_x f_y)` | 相对 padstack 原点的偏移 |
| `flash` | string | 热焊盘/反焊盘的 flash symbol 名（.fsm） |
| `radius` | float | ROUNDED_RECTANGLE / CHAMFERED_RECTANGLE 专用，圆角半径 |
| `corners` | string | 倒角方位，dash 分隔：`"UR-LR"` = 右上+左下。方位：`UR`/`UL`/`LR`/`LL` |
| `sides` | int | N_SIDED_POLY 专用，**偶数，6~64** |
| `inside` | float | DONUT 专用，内径 |

**三个易踩的编程约束**（文档 PROGRAMMING NOTES 原文）：
1. **N_SIDED_POLY**：`figureSize` 取宽高中的小值作为直径。`sides` 必须是 6~64 的偶数，**`sides=0` 会退化成 CIRCLE**
2. **ROUNDED_RECTANGLE / CHAMFERED_RECTANGLE**：需要 `radius` + `corners`。radius 不能超过短边的一半。**radius=0 或 corners 不匹配任何角，会退化成 RECTANGLE**
3. **DONUT**：`figureSize` 取小值作为外径。`inside` 必须小于外径。**`inside=0` 会退化成 CIRCLE**

---

### 2.4 `figure` 允许值（`axlPadFigureTypes()` 返回）

基础：`'CIRCLE`、`'SQUARE`、`'RECTANGLE`、`'OBLONG_X`、`'OBLONG_Y`、`'HEXAGON`、`'HEXAGON_X`、`'HEXAGON_Y`、`'OCTAGON`、`'CROSS`、`'DIAMOND`、`'TRIANGLE`

高级：`'N_SIDED_POLY`、`'ROUNDED_RECTANGLE`、`'CHAMFERED_RECTANGLE`、`'DONUT`

特殊：`'FLASH`（配 `?flash "name"` 用 fsm 文件）、`nil`（自动按 figureSize 推断）

**用 shape symbol（.ssm）**：`?figure "myshape"` —— 直接给 ssm 文件名（**去掉扩展名和路径**），符号需在 `PADPATH` 中能找到。
**用 flash symbol（.fsm）**：`?figure 'FLASH ?flash "myflash"` —— 同理。

> **对你项目直接有用**：`SHAPE2D65X1D185_L` 这类异形焊盘，对应的就是 `.ssm` 文件。SKILL 里直接 `?figure "shape2d65x1d185_l"` 引用即可。

---

### 2.5 `axlDBCreatePin` — 放置 pin

```skill
axlDBCreatePin( t_padstack/o_padstackDbid  l_anchorPoint  r_pinText/nil  [ f_rotation ] ) => l_result / nil
```

**返回值**：`(dbid_of_pin  t_or_nil)` —— `cadr` 为 `t` 表示产生了 DRC。

**三条硬约束**（文档 Notes 原文）：
1. **只能在 Symbol Editor 里用**
2. **只能用在 package 和 mechanical symbol 绘图里**。在别的类型绘图里建 pin 会报错
3. `r_pinText` 传 `nil` → 创建机械 pin（无编号）

**padstack 自动加载**：如果 `t_padstack` 指定的 padstack 不在 layout 里，函数会**按 `PADPATH` 顺序自动去库里找并加载**。这意味着 .pad 文件只要在 PADPATH 下，SKILL 脚本可以直接引用名字。

**`r_pinText` 结构**：
```skill
make_axlPinText(
    ?number "1"                    ; pin 编号，字符串
    ?offset -100:0                 ; 编号文字偏移
    ?text (make_axlTextOrientation ; 或直接给 textBlock 字符串
        ?textBlock "2"
        ?rotation 90
        ?mirrored t                ; t / nil / 'GEOMETRY
        ?justify "right"           ; "left" / "center" / "right"
    )
)
```

---

### 2.6 `axlPadstackToDisk` — 落盘

```skill
axlPadstackToDisk( [ t_padName ] [ t_outPadName ] ) => t / nil
```

**三种用法**（文档原文示例）：
```skill
axlPadstackToDisk()                          ; 把 layout 里所有 padstack 全 dump 出来
axlPadstackToDisk("pad60cir36d")             ; 指定 padstack，落盘为 pad60cir36d.pad
axlPadstackToDisk("pad60cir36d" "mypadstack"); 改名落盘为 mypadstack.pad
```

> **对你项目直接有用**：`axlPadstackToDisk()` 不带参数，可以作为"枚举当前设计所有 padstack"的替代方案（因为 `axlPadstackGetList` 不存在）。落盘后再用 Python 扫目录即可。

---

### 2.7 `axlPadstackEdit` — 编辑（**限制很硬，重点看**）

```skill
axlPadstackEdit( nil nil )                              => l_attributes
axlPadstackEdit( o_dbid/t_Padstack  s_name  g_value )   => t / nil
axlPadstackEdit( o_dbid/t_Padstack  [[s_name g_value] ...] ) => t / nil
```

**关键限制（文档原文，直接引用）**：

> "Currently **only global padstack settings are supported. Editing pad layer characteristics is not allowed.**"

**这印证了社区说法——`axlPadstackEdit` 不能改单层焊盘尺寸。** 能改的只是全局属性：

| 属性名 | 值 | 说明 |
|---|---|---|
| `usage` | `'Smd` / `'Through` / `'Through_pin` / `'Surface_pin` / `'Bbvia` / `'Through_via` / `'uvia` / `'Bond_finger` / `'Die_pad` / `'Fiducial` / `'Mechanical_hole` / `'Mounting_hole` / `'Slot` | 改分类 |
| `holeType` | 见 2.2 | 改孔型 |
| `drillSizeWidth` / `drillSizeHeight` | float | 改孔径 |
| `fixed` / `spanLockCount` / `pluralVia` 等 | t/nil | 杂项开关 |

**多属性批量改时顺序敏感**：文档明确说"把 CIRCULAR drill 改成 slot，必须先给 holeType，再给 drillSizeWidth，再给 drillSizeHeight"。

**副作用**：某些改动会让 DRC / 动态铜皮失效（out of date）。

---

### 2.8 `axlDBCopyPadstack` — 复制（**这是你的方案 C 的基础**）

```skill
axlDBCopyPadstack( o_dbid/t_padstackName  lt_startEnd  [ g_dontTrim ] ) => o_dbid / nil
```

- 新 padstack 名字**自动从原名加后缀派生**（不能自己指定）
- `lt_startEnd` 必须是合法的 ETCH 层对
- `g_dontTrim` **已废弃，忽略**
- 如果复制出来的 padstack 没被使用，**下次 dbdoctor 会把它删掉**

示例：
```skill
newPadId = axlDBCopyPadstack(padId '("ETCH/TOP" "ETCH/BOTTOM"))   ; 完全复制
newPadId = axlDBCopyPadstack("VIA" '("TOP" "GND") nil)            ; 限定层范围
```

---

### 2.9 官方示例文件位置（**强烈建议先读这两个**）

| 文件 | 内容 | 价值 |
|---|---|---|
| `<cdsroot>/share/pcb/examples/skill/dbcreate/pad.il` | **14.5 KB，11 个完整 padstack 创建范例** | ★★★★★ 直接抄 |
| `<cdsroot>/share/pcb/examples/skill/dbcreate/axldbctpin.il` | `axlDBCreatePin` 测试驱动 | ★★★★ |

本机实际路径：
```
D:\Dev_tools\Cadence_17.2\Cadence\Cadence_SPB_17.2-2016\share\pcb\examples\skill\dbcreate\pad.il
D:\Dev_tools\Cadence_17.2\Cadence\Cadence_SPB_17.2-2016\share\pcb\examples\skill\dbcreate\axldbctpin.il
```

`pad.il` 覆盖的场景（**几乎正好对上你 13 个封装的 pad 类型**）：

| 过程名 | 场景 | 对应你的封装 |
|---|---|---|
| `CreateSMD` | 最简 SMD 矩形 | `nb_l2`、`nb_sod-323` |
| `CreateThru` | 通孔 + flash + shape | `nb_qfn13` 等通孔件 |
| `CreateMask` | SMD + SOLDERMASK | **正好对应你 dump 出的 +0.05 外扩** |
| `CreateCoverlayMask` | SMD + COVERLAY | 柔性板/FPC 件 |
| `CreateUserMask` | SMD + 用户自定义 mask | — |
| `Create2PadExamples` | 多层 pad + `?layer "DEFAULT_INTERNAL"` | 多层板 |
| `CreateDonut` | 圆环焊盘 | — |
| `CreateCamfer` | 倒角矩形 | — |
| `CreateNPoly` | N 边形 | — |
| `CreateKO` | keepout（同层 + 相邻层） | — |
| `CreateSlot` | 长圆孔 SLOT | `POB…HOB…` 类 |
| `CreateCounter` | 沉头孔 bore/sink | — |
| `CreateBackdrill` | 背钻 | — |

`CreateMask` 原文（**直接对应你 dump 出的层结构规律**）：
```skill
procedure( CreateMask()
    (let (name)
    name = "solder"
    pad_list = nil
    pad_list = cons(make_axlPadStackPad(
                            ?layer "TOP", ?type 'REGULAR,
                            ?figure 'RECTANGLE, ?figureSize 25:60) pad_list)
    pad_list = cons(make_axlPadStackPad(
                            ?layer "SOLDERMASK_TOP", ?type 'REGULAR,
                            ?figure 'RECTANGLE, ?figureSize 35:70) pad_list)
    ps_id = axlDBCreatePadStack(name, nil, pad_list t)
))
```

注意这里 `25:60` → `35:70`：每边各外扩 5 个单位。**这与你 dump 出的"SOLDERMASK 单边外扩 +0.05mm"是同一个规律**（只是单位可能是 mil）。

---

## 三、对你项目结论的影响

### 3.1 `POB…HOB…` 椭圆焊盘可以直接建

不需要预置 padstack。文档明确支持：
```skill
pad = make_axlPadStackPad(
        ?layer "TOP" ?type 'REGULAR
        ?figure 'OBLONG_X            ; 或 'OBLONG_Y
        ?figureSize 1.4:0.6
      )
drill = make_axlPadStackDrill(
        ?usage "Through" ?holeType 'OVAL_SLOT
        ?slotSize 1.2:2.0            ; 注意 f_width=Y, f_height=X
        ?plating 'PLATED
      )
```
这推翻了上下文里"POB 需预置"的判断 —— **POB/NPOB 可全自动**。

### 3.2 `PC` / `NPC` 圆通孔也可以直接建

```skill
drill = make_axlPadStackDrill(?holeType 'CIRCLE_DRILL ?drillDiameter 0.85
                              ?plating 'NON_PLATED)   ; NPC
drill = make_axlPadStackDrill(?holeType 'CIRCLE_DRILL ?drillDiameter 0.85
                              ?plating 'PLATED)       ; PC
```

### 3.3 只有 `SHAPE…` 异形仍需预置 .ssm

因为 shape symbol 是不可参数化的几何图形。但引用方式已明确：`?figure "shape2d65x1d185_l"`（小写，去扩展名）。

**所以真实覆盖度分层应该修正为**：

| 复杂度 | pad 类型 | 能否全自动 | 修正说明 |
|---|---|---|---|
| ⭐ | `R` 矩形 SMD | ✅ | 不变 |
| ⭐ | `C` 圆形 SMD | ✅ | 不变 |
| ⭐⭐ | `PC` / `NPC` 圆通孔 | ✅ | **从"需预置"提升为"可全自动"** |
| ⭐⭐ | `POB…HOB…` 椭圆插件 | ✅ | **从"需预置"提升为"可全自动"** |
| ⭐⭐ | `NPOB` 非金属椭圆 | ✅ | **同上** |
| ⭐⭐⭐ | `SHAPE…` 异形 | ⚠️ 需预置 .ssm | 不变，靠 `?figure "name"` 引用 |

**结论修正：从"约 70% 可全自动"提升到"约 90% 可全自动"，只剩 SHAPE 异形需要预置。**

### 3.4 padstack 库问题被部分解决

原本的困境是"公司没有统一的 padstack 库"。现在有三条出路：

- **A**：SKILL 直接建（`axlDBCreatePadStack`）→ 能建就建，建完 `axlPadstackToDisk` 落盘
- **C**：复制现有 `.pad` 改参数 → `axlDBCopyPadstack` + `axlPadstackEdit`
- **新 D**：**自动生成 padstack 库** —— 扫一遍 datasheet 需求，批量 SKILL 建 padstack，`axlPadstackToDisk` 落到统一目录，再设进 `PADPATH`。**这本身可以成为 layout-forge-mcp 的一个 feature。**

---

## 四、仍必须在 CIW 里实测的项（不能用文档替代）

文档只保证"17.2 有这些函数"，不保证"本机 license/配置下能跑通"。以下必须实测：

1. `probe_stage1_api_exist()` —— 确认 33 个函数/构造函数在本机全部存在
2. `probe_stage2_create()` —— 在 **Package Symbol** 绘图里真的建一个 padstack + 放一个 pin
3. `axlPadstackUsageTypes()` / `axlPadFigureTypes()` —— 打印本机支持的全集（可能比文档多/少）
4. `axlDBGetDesign()->userUnits` —— 确认当前 drawing 单位（mil 还是 mm），**这是最多的坑来源**

探针脚本已写好：`Tools/skill_api_probe.il`

---

## 五、单位陷阱（特别提醒）

SKILL 里 `?figureSize 25:60` 的数值单位 = **当前 drawing 的 design units**，不是绝对 mm。

- 你 dump 出的 `nb_qfn13`、`nb_mos8` 是 **mil** 单位封装
- `R5D7X1D9` 这种 d 代小数点的命名，`5.7 x 1.9` 显然是 **mm**

**这意味着**：生成 SKILL 脚本时，必须先确认目标 drawing 的单位，再做换算。或者**统一把 drawing 设成 millimeter**，把换算逻辑集中到 Python 侧一处，避免混乱。

---

## 六、其他被误解的项

| 上下文里的说法 | 核实结果 |
|---|---|
| `axlPadstackGetList()` 待验证 | ❌ **不存在**。用 `axlPadstackToDisk()` 无参版本替代 |
| `axlDBCreatePadStack` 待验证 | ✅ 存在，签名已确认 |
| `axlPadstackEdit` "可能只支持全局设置" | ✅ 确认，文档明说不能编辑 pad layer 特性 |
| `axlLoadPadstack` 待验证 | ✅ 存在（`03dbcre8.html` 有引用），但文档细节较少，需实测 |
| POB 焊盘需预置 padstack | ❌ 可用 `'OBLONG_X` + `'OVAL_SLOT` 参数化生成 |
| SHAPE 焊盘需预置 shape symbol | ✅ 确认，但引用方式明确 |
