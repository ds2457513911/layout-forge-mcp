# drawing-ops —— 图纸操作与取值口径

> 收录原 SKILL.md **第五节**。
> 内容：新建 / 清空 / 重置 / 重建 symbol 图纸的完整自动化路径，
> 以及**四条硬约定** —— 单位与精度、尺寸取值口径、位号（refdes）口径、库落位。
>
> grep 关键词：`模板复制法`、`axlKillDesign`、`清空`、`重建`、`单位`、`精度`、
> `dim_source`、`尺寸口径`、`refdes`、`位号`、`padpath`、`pad 同目录`

## 五、★★★ 新建图纸 / 清空图纸（全自动化，无需人工建图）

> **核心结论：Allegro 完全能自动新建 `.dra` 图纸 —— `X.dra` 就是 `axlOpenDesign` 建出来的。**
> 之前"必须人工建图纸"的结论是**因为旧探测法误判 unbound 导致的错误推断**，现已被推翻。

### 5.1 新建（或重置）图纸

```skill
axlKillDesign()                                   ; 零参：直接开一张新的无名图纸
                                                  ;   等价 axlOpenDesign <unnamed> "wf"
axlOpenDesign(?design "X.dra" ?mode "wf")         ; 指定路径；"wf" = 丢弃未保存编辑，不弹确认
axlOpenDesignForBatch("X.dra" "wf")               ; ★ batch 版：不改变命令与菜单
```

**关键参数语义**（`14dsnctl.html` 原文）：

| 参数 | 含义 |
|--|--|
| `?mode "w"` | 打开可写 |
| `?mode "wf"` | **丢弃当前设计的编辑且不确认**（= 环境变量 `NOCONFIRM`）|
| 加 `l`（如 `"wl"`） | 禁用 Allegro 文件锁 |
| `?noMru t` | 不更新最近文件列表 |
| `?ignoreLock t` | 忽略 lock 文件（**Cadence 不推荐**）|

**★ 新建时的三个陷阱**：

1. **路径不存在 → 弹 Drawing Parameters 框**（卡死通道）
2. **路径已存在 → 弹 `File Exists. Overwrite?`**（`wf` 拦不住！先把目标挪开再调）
3. `axlKillDesign` / `axlOpenDesign` 会**作废所有已有 dbid 句柄**并清空选择集
   - 对策：每次新建后**重新获取** dbid，别复用旧变量

**路径写法**：`t_design` 是**文件名**（可带目录），示例用相对路径 `"u.dra"`。
建议传绝对路径 + 正斜杠。

### ★★★ 5.1b 重大限制：`axlKillDesign()` 只能建 **BOARD**，建不了 SYMBOL

实测（2026-09-29）—— 这是**必须知道**的边界，否则会以为"全自动建 symbol 图纸"已成：

| 尝试 | 结果 |
|--|--|
| `axlKillDesign()` | ✅ 零弹窗，但 `axlDesignType(t)` = **`"BOARD"`** |
| `axlSetSymbolType("package")` | ❌ 拒绝：*"Procedure can only be applied to symbol drawings"* |
| `axlSetDesignType` | ❌ **`undefined function`** —— **函数不存在**（文档 14dsnctl.html 在 axlOpenDesign 的 See Also 里推荐了它，是**文档自身的错误**）|
| `axlDBCreateSymbolSkeleton` | ❌ 文档明确 *"Do not use this function in the symbol editor"* —— 它是往 layout 放 symbol **实例**的 |

> **结论：AXL 没有"新建 symbol 图纸"的 API。**

### ★★★ 5.1c 正确解法：**官方模板复制法**（已验证可行，首选）

**核心思路**：图纸类型**内嵌在 `.dra` 文件里** —— 复制一个 symbol 模板，
打开后天然就是 symbol，**绕开了"没有改类型 API"这个死结**。

> 模板从哪来、怎么判版本（首选官方 `sym_template.dra`）→ 见**第三节**。

```
1. 选模板：① 官方 sym_template.dra（首选）
           ② 退路：工作目录下的 *.dra，且 dra_major() == 17
2. os.remove 目标 .dra + 目标 .dra.lck      ← 防 File Exists 弹框
   （★ 目标是我们即将重建的副本，删掉无妨；换成有价值的产物必须 quarantine）
3. shutil.copyfile(模板, 目标)               ← 纯文件操作，零 UI
4. axlOpenDesign(?design 目标 ?mode "wf")
5. 校验 axlDesignType(nil) == "SYMBOL"
6. ★ 污染校验：数 pins / text / padstacks    ← 退路模板非空就拒绝（探针脏图）
```

★ **第 5 步不能省**：模板本身若是 BOARD，复制出来还是 BOARD，整条流程作废。

★ **第 6 步的由来**：图元创建是"叠加"不是替换 —— 模板自带的 pin 会跟新建的
pin 撞号 → `duplicate pin numbers` → `Create symbol has been aborted.`。
工作目录下的 `probe_test.dra` / `X.dra` 都是被污染过的探针图纸。

实现见 `layout-forge-mcp/services/footprint_builder.py::new_symbol_drawing()`。

### ★★★ 5.1d 两条官方"正统路径"（2026-09-29 调研所得）

**① 官方模板机制 `wizard_template_path`** —— 不是我们发明的野路子
（`algroenvvar/Paths_Settings.html`）：
> "Search path for **Allegro template databases**. Used by the **Template button in New dialogs**.
> In addition, if template files are named `new_default.*` (ex. `new_default.brd`) uses this design
> as the **starting template** if the user did not explicitly specify a template database."

- 模板可放 `.brd` / `.pad` / `.mcm` / `.sip` / **`.dra`**
- 官方 `new_design` 变量原文：**"Cadence recommends the use of template designs."**
- ★ **升级做法**：把 `sym_template.dra` 复制到 `wizard_template_path` 目录下并命名
  `new_default.dra` → Allegro **自己的** New 对话框默认就用它，比我们手工 `copyfile` 更正统，
  也免掉"工作目录里没干净模板"的脆弱性。

**② `axlSetSymbolType` 在 symbol editor 里是能用的** —— 我之前试 `"package"` 被拒，
是因为**当时当前设计不是 symbol 图纸**。文档明写：
> "This function is only available in the symbol editor."

合法参数：`"package"` / `"mechanical"` / `"format"` / `"shape"` / `"flash"`。
→ 只要模板复制后图纸已是 SYMBOL，就能**显式声明类型**，不必再依赖"模板类型必须正确"。

**③ `axlOpenDesign` 有个关键特性**（文档 14dsnctl.html）：
> "The designType of the new design is **set as the same as the current design**."

→ ★ 若当前设计是 SYMBOL，新建的图纸**也会是 SYMBOL**。这条若实测成立，
`axlKillDesign()` 建出 BOARD 的老问题就能绕开（先让当前是 symbol，再 kill）。
**待实测。**

**④ 完整"建 symbol 不丢当前设计"范式**（Cadence 版主 eDave，帖 #16749）：
```skill
currentDesign = axlCurrentDesign()
state         = axlSaveEnable()
axlSaveDesign(?design "temp.brd", ?mode "nocheck", ?noMru t)   ; 暂存当前设计
AFn_OpenDesign(?design "my.dra", ?mode "wf", ?noMru t, ?noConfirm t, ?forceNew t)
axlSetSymbolType("shape")
;; ... 画图形 ...
axlSaveDesign(?noMru t)
AFn_OpenDesign(?design "temp.brd", ?mode "wf", ?noMru t, ?noConfirm t)  ; 恢复
axlSaveDesign(?noMru t)
axlRenameDesign(currentDesign)
deleteFile("temp.brd")
axlSaveEnable(state)
```
其中 `AFn_OpenDesign` 是 eDave 写的封装（`?forceNew` 时先 `deleteFile` 目标）。

### 5.2 清空当前图纸（保留图纸本体，只删图元）

比"重建图纸"更轻量，且不丢 drawing 参数（单位/精度/范围）：

```skill
axlDeleteByLayer("PACKAGE GEOMETRY/SILKSCREEN_TOP")   ; 删整层
axlDeleteByLayer(list("ETCH/TOP" "ETCH/BOTTOM"))      ; 批量删多层
axlDeleteByLayer("BOARD GEOMETRY/SILKSCREEN_TOP" 'fixed) ; 'fixed = 连 FIXED 属性一起删
-> 返回删除的元素个数
```

| 事实 | 说明 |
|--|--|
| **不删 pin / via** | 这是官方明确行为！`PIN`、`VIA_CLASS`、`DRC_ERROR_CLASS`、`ROUTER_PLAN`、`CAVITY` 类**被忽略** |
| 会删 symbol 关联的 pin escape 等数据 | |
| **不删 symbol 定义内部的对象** | |
| 删动态铜皮还需删 `BOUNDARY` 类 | |

**要删 pin，得用**：

```skill
axlDeleteObject(lo_dbid)          ; 传 dbid 或 dbid 列表
axlDeleteObject(lo_dbid 'ripup)   ; 带 etch rip-up
```

**获取全部 pin 的 dbid 列表**：

```skill
mapcar(lambda((p) p) axlDBGetDesign()->pins)          ; 全部 pin
mapcar(lambda((t) t) axlDBGetDesign()->text)          ; 全部文字
mapcar(lambda((l) l) axlDBGetDesign()->lines)         ; 全部线
```

**完整清空套路（推荐）**：

```skill
foreach(x axlDBGetDesign()->pins axlDeleteObject(x))
foreach(x axlDBGetDesign()->text axlDeleteObject(x))
foreach(x axlDBGetDesign()->lines axlDeleteObject(x))
foreach(x axlDBGetDesign()->rects axlDeleteObject(x))
foreach(x axlDBGetDesign()->shapes axlDeleteObject(x))
```

> ★ **注意**：`foreach` 体内不要修改正在遍历的集合（删 pin 会让 `->pins` 失效）。
> 安全写法是**先 `mapcar` 取快照，再遍历快照删**：
> ```skill
> ps = mapcar(lambda((p) p) axlDBGetDesign()->pins)
> foreach(x ps axlDeleteObject(x))
> ```
> 但 SkillBridge 的 `eval_skill` 是单行求值，多语句要用 `prog1(...)` 或 `let(...)` 串起来。

### 5.3 删除整层定义（不是清内容，是删层本身）

```skill
axlLayerDelete(t_layerName)       ; 删一整层定义（要求该层为空）
axlPurgePadstacks()               ; 清掉未使用的 padstack
```

### 5.4 「重建」的完整自动化序列

```
1. axlDeleteByLayer / foreach+axlDeleteObject   -> 清空旧图元（或直接 axlKillDesign 重开）
2. axlDBChangeDesignUnits("millimeters" ...)    -> 设单位
3. axlDBChangeDesignExtents(...)                -> 设范围
4. axlCompileSymbol 前：axlSetSymbolType('package) / 确认已在 symbol editor
5. 创建 pin / 文字 / 线
6. 位号必须落在 REF DES/* 层（见 5.4.2）
7. axlCompileSymbol(?symbol "NAME" ?type "package")
```

### ★ 5.4.2 位号（refdes）：放哪层 / 写什么字（2026-09-30 以模板库为准）

```
REF DES/ASSEMBLY_TOP      ; ★ 默认 —— 14/14 个人工模板都用这层
REF DES/SILKSCREEN_TOP    ; 可用，但实测只有 1/14 例外件追加过
PACKAGE GEOMETRY/*        ; ✗ Allegro **不认**是位号 → Create Symbol 报 SPMHCS-1
```

- 位号**必须落在 `REF DES` 类**，否则 `axlCompileSymbol` 报
  `ERROR(SPMHCS-1): Symbol is missing a refdes.` + `SPMHA1-291`
  （图纸里明明有 `U*` 这段文字也一样不认）。

**★ 口径（2026-09-30 实测 + 用户裁决：以 `Data\footprint\` 里的模板为主）**

把 `Data/footprint/*/*.dra`（14 个模板，去掉 `AUTOSAVE.dra`）全部用
extracta 的 `CLASS = "REF DES"` 那一路扫一遍，结果**高度一致**：

| 项 | 实测结果 |
|--|--|
| CLASS/SUBCLASS | **`REF DES/ASSEMBLY_TOP`** —— 14/14 全中 |
| 文本 | **`#REF`**（少数写 `REF#`，Cadence 等价写法） |
| 对齐 | `LEFT` |
| 位置 | 大多在**本体中心附近**（人工件 `3S48000163` = `(-0.15, 0.04)`，本体中心在 (0,0)） |

→ 所以**默认就是**：`#REF` @ `REF DES/ASSEMBLY_TOP`、居中于本体、LEFT 对齐。
  **不要再默认放丝印层 `U*`**（那是本项目 2026-09-30 之前的旧默认，已废）。

- 文本两种写法都能编译：`U*`（旧式）/ `#REF`（17.x 推荐，模板都在用）。
- `justify` 走 `make_axlTextOrientation(?justify "left")`，合法值
  `"left"` / `"center"` / `"right"`；不传则该参数整体省略。
- 只想读位号做校验 → 见 8.1（extracta 视图的 `CLASS = "REF DES"` 那一路）。

**★ 怎么离线核对（不用开 Allegro）**：extracta 4 路视图的第 4 路输出里，
`S!REF DES!ASSEMBLY_TOP!...!TEXT!...` 那条就是位号；详见 8.1。

### ★★★ 5.4.2b 引脚号（PIN_NUMBER）字块：禁止字符串 ?text（2026-10-09 实测事故）

**症状**：一张图里引脚号**字号各不相同**，且字高随**脚号递增**
（1 号脚最小、6 号脚最大），截图一眼就能看出大小不一。

**根因**：`make_axlPinText` 的**简化写法** `?text "N"`（官方示例
`axldbctpin.il` 里恰好 pin 号="2"、字块="2"，把坑掩盖了）——
**Allegro 把数字字符串 ?text 当成【字块号】**。实测生成件：
1 脚→block 1（字高 0.508mm）、2 脚→block 2（0.787）、…、6 脚→block 6（2.032）。

**正解**：`?text` **必须传结构体**：

```skill
make_axlPinText(?number "3" ?offset x:y
    ?text make_axlTextOrientation(?textBlock "1" ?rotation 0
                ?mirrored nil ?justify "center"))
```

**★ 口径（人工件 `nb_audio6` 实测）**：引脚号与位号**同字块** ——
`textBlock "1"` + `justify "center"`（人工件 6/6 全是 block 1 / CENTER；
1 脚因贴边旋转 90° 用 RIGHT，属个例）。`skill_snippets.build_pin_text`
已在 2026-10-09 改为**无条件结构体形式**，`text` 参数保留但不生效。

**怎么离线核对**：extracta `FULL_GEOMETRY` 视图里引脚号是
`PACKAGE GEOMETRY/PIN_NUMBER` 的 TEXT 记录，**GD6 就是字块号**
（解析器 `extracta_reader._parse_geom_file` 已回填 `text_block` 字段）；
人工件对拍两条硬标准：**6 脚字块一致**、**与 #REF 同字块**。
注意 `design->text`（SkillBridge）**看不到**引脚号 —— 它们不是独立
text dbid，extracta 才读得到。

### ★★★ 5.4.1 单位 / 精度：量化误差的真凶（2026-09-29 实证）

**这条决定「生成件能不能逐值对上人工金标准」，新建图纸后必须紧接着设。**

**完整签名（文档 `algroskill/14dsnctl.html` → `axlDBChangeDesignUnits`）：**

```skill
axlDBChangeDesignUnits( t_units/nil  x_accuracy/nil  x_drcCount/nil ) ⇒ x_drcCount/nil

axlDBChangeDesignUnits("millimeters" 4)   ; 官方示例 1：mils/0 -> millim/4
axlDBChangeDesignUnits(nil 2)             ; 官方示例 2：只改精度，保持单位
```

合法单位串：`"mils"` / `"millimeters"` / `"inches"` / `"centimeters"` / `"microns"`。

**★ 两条硬约束（都踩过）：**

1. **返回值不能当成败判据** —— 它是 `drcCount/nil`，**DRC 关闭时恒返回 nil**；
   单位串传错也是静默返回 nil、**不报错**。
   → 调完一律**复读** `axlDBGetDesignUnits()` 校验（实测返回列表 `["millimeters" 4]`）。
2. **必须在画任何图元之前调** —— 文档明写 "This function reruns DRC if enabled"。
   画完再切 = 白切。

**★ 量化误差：为什么「官方模板复制法」会带系统偏差**

| | 单位 | 精度 | 步长 |
|--|--|--|--|
| Cadence 官方 `sym_template.dra` | `mils` | `1` | 整数 mil = 25.4 µm |
| 人工金标准 `.dra`（实测） | `millimeters` | `0.0001` | 0.1 µm |

→ 从官方模板复制出来的新图纸直接画 mm 值，会被**量化到整数 mil**：

```
1.10 mm -> 43 mil = 1.0922 mm    (±7.8 µm)
0.85 mm -> 33 mil = 0.8382 mm
```

这就是「生成件与金标准总有 ~0.01 mm 级差异、逐值对拍差一点」的根因 ——
**不是求解器错，是图幅精度不够**。默认就该切 `("millimeters" 4)`。

**怎么看出来（不需要开 Allegro）：** 用 extracta 读 `J!` 头行，字段
`[6]`=精度、`[7]`=单位：

```
J!<path>!<date>!<x1>!<y1>!<x2>!<y2>!0.0001!millimeters!   ← 人工金标准
J!<path>!<date>!<x1>!<y1>!<x2>!<y2>!1!mils!               ← 模板复制件
```

**精度经验值（官方表）：** mils `0~4`（>2 不建议，Gerber 会出问题）；
millimeters `0~4`（取 **4**）。

### ★★★ 5.4.3 尺寸取值口径：图上标注 vs 查表（2026-09-29 用户裁决）

**这条决定「画进图里的实体尺寸取哪个数」，比 5.4.1 更靠前 —— 先定值，再谈精度。**

规格书解析时，一个尺寸的取法**看它在规格书里从哪来**：

| 来源 | 取法 |
|--|--|
| 图上**标注了具体值** | 取**标注值本身** —— `3.2±0.1` 取 **3.2**，**不**加上偏差 |
| 图上没标、**需要查表** | 取表里的 **MAX**；表里没 MAX → 取 **TYP + 告警** |

**命名永远用中值**，不随口径变（[规范一]）；受影响的只有 Assembly /
Place_Bound 等实体几何。

**★ 已废除的旧口径**（别再用）：
「该页有 recommended 单词 → 用中值；没有 → 中值 + 公差」。
`has_recommended_value` / `body_tol` 保留兼容但**不再影响取值**。

**实现（本项目 `services/geometry_solver.py`）**

```python
resolve_dim(key, spec, *, drawing=None, table=None) -> (value, deriv, warn)
resolve_all_dims(spec) -> ({key: value}, [deriv], [warn])   # 覆盖 6 个尺寸键
# spec["dim_source"]   整件默认 "drawing" / "table"
# spec["dim_sources"]  逐维覆盖 {"body.x": "table"}
# spec["dim_tables"]   逐维表 {"body.x": {"min":3.0,"typ":3.2,"max":3.4}}
```

覆盖键：`body.x` / `body.y` / `pad.x` / `pad.y` / `pitch_x` / `pitch_y`。
`solve()` **入口只解析一次**，结果挂 `spec["_resolved"]`，下游 `solve_padstack` /
`solve_pins` / `solve_outlines` / `solve_pad_rects` 统一从那里读 ——
**别让 body / pad / pitch 各自算一套口径**。

**★ 两个必须避开的坑（都实测过）**

1. **别让「整件默认 table」把整个器件炸掉**。真实规格书常是**混合**的：
   本体标在图里、焊盘只给表。所以 ——
   - 只声明了**整件**默认 `table`、某维没给表但**图上有值** → 退回标注值 + **告警**（不崩）；
   - **逐维显式**声明 `table` 却没给表 = spec 写错 → **硬失败**（不静默猜）。
2. **`spec` 要先 `dict(spec)` 浅拷贝再挂 `_resolved`**。调用方大量用
   `dict(SPEC_XXX, ...)` 造变体，一旦原对象被写脏，测试之间会互相串味。

**判定「这个件走哪一支」只看一件事：规格书有没有 MIN/TYP/MAX 尺寸表。**
"Max" 出现在别处（如回流焊曲线的时间/温度）**不算** —— 那是工艺参数，不是封装尺寸。

**实例**：3S48000163 p5 全页尺寸都标在图上（`3.2±0.1` / `1.4` / `0.8` /
`1.2` / `0.5`），**无**尺寸表 → 取标注值 →
Assembly **3.2×2.5**、Place_Bound **3.9×3.2**，**与人工金标准一致**
（旧口径给 3.3×2.6 / 4.0×3.3，对不上）。

### ★ 5.4.4 库落位：`.dra` 和 `.pad` 同目录就够，**不需要挂 `padpath`**
（2026-09-30 用户裁决 + 两条实测）

**结论先行：** `Data\footprint\<PN>\` 的事实约定就是
**「一个封装 = 一个文件夹，`.dra` + 它用到的 `.pad` 全放里面」**，
照抄即可。**不要**去写 env / 改 `padpath` / `psmpath` / 调挂载 API。

**依据：**

1. **约定（人工件 13/13 全中）** —— 用工具扫 `Data/footprint` 全部料号：
   ```
   3S48000163/            nb_xtal4_3d2x2d5x0d7.dra + r1d4x1d2.pad
   AOZ22559QI/            nb_qfn30_....dra        + r0d2x0d45.pad ×4
   DB_typec_...-24P/      nb_type_c24_....dra     + *.pad ×6
   ```
   → 「符号与其 padstack 同目录」是既有事实标准，影子库照做就行。

2. **实测：`.dra` 自带 padstack 副本，读取端根本不依赖外部 `.pad`**
   （`probe_pad_resolve.py`，extracta 离线跑，造三种隔离环境对比）：

   | 环境 | ETCH/TOP 焊盘尺寸读出来了吗 |
   |--|--|
   | A. 同目录有 `.dra` + `.pad` | ✅ 1.2 × 1.4（+ SOLDERMASK 1.3×1.5） |
   | B. 同目录**只有** `.dra`（无 pad） | ✅ **完全一样** |
   | C. `.dra` + 被**改名**的 `.pad` | ✅ **完全一样** |

   → 说明 padstack 定义随 drawing 落进了 `.dra`，
     外部 `.pad` 是「可编辑母版」而不是「贴图资源」。

3. 所以「影子库挂 padpath」这个攻坚项**关闭**，替换成一条**约定守卫**：
   落位后检查「有符号但一个 `.pad` 都没有」的孤儿目录并告警
   （`library_install.verify_colocation()`）。
   ★ 只告警不算失败 —— 因为第 2 条已经证明缺 pad 也能回读。

**顺带一条已裁决不做的事：** 人工金标准 `3S48000163` 的焊盘被转了 90°
（padstack 名 `R1D4X1D2` = 1.4×1.2，实物却是 1.2×1.4，规格书 p5 明写 1.4×1.2）。
用户裁决 2026-09-30：**「只是焊盘方向问题，完全没影响，没必要搞」**
→ 不复现、不再追，只在在线对拍的 [B] 段如实展示。

### 5.5 其他可用的辅助 API（本次全表筛查得出）

| 函数 | 用途 |
|--|--|
| `axlRenameDesign(t_design)` | 改当前图纸名（**不影响磁盘文件**，只改内存中的名字 + Save 默认名）|
| `axlDBChangeDesignUnits(...)` | 改设计单位 |
| `axlPurgePadstacks()` | 清未用 padstack |
| `axlClearSelSet()` | 清空选择集 |
| `axlAddSelectAll()` | 全选（配合 findAll 用）|
| `axlEraseObject(lo_dbid)` | ⚠️ **只是临时隐藏**，不是删除！`AXL shell` 退出会恢复 |

---

