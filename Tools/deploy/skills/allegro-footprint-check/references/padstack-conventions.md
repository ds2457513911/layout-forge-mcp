# TH 件 padstack 层清单与构造约定

> 从 SKILL.md 剥出（原「核心原则」下的 TH 件小节）。**建或检查 mount=tht 封装前必读。**
> 真源：`knowledge/conventions.json`（253 件金标准挖掘 + 工程师裁定），check 与 create 读同一份。

### TH 件 padstack 层清单（2026-10-12 LYDPJ 事故新增，2026-10-13 二次修正）

建或检查**通孔插件（mount=tht）**封装时，padstack 必须满足
（真源 `knowledge/conventions.json`，由 253 件金标准挖掘+裁定产生；
check 与 create 读同一份约定）：

1. **双面铜箔**：`ETCH/TOP` + `ETCH/BOTTOM` —— 缺 BOTTOM = 双面板底面无铜，无法焊接
2. **双面阻焊**：`SOLDERMASK_TOP` + `SOLDERMASK_BOTTOM`（单边外扩 0.05）
3. **钢网**：顶面 `PASTEMASK_TOP` = 焊盘尺寸；底面无（25/26 金标准口径）。
   ~~"TH 不放钢网"~~ 旧口径作废（2026-10-13 修正）。
   **家族例外**：个别金标准（LYDPJ/audio 族）自带 `BPM=焊盘尺寸`——须 spec
   显式声明 `paste_bottom: true`，引擎放行并出 WARN 提示（2.10），不静默。
   ★ 构造 API 层名陷阱：内层要传 `DEFAULT_INTERNAL`（db/extracta 显示为
   `internal_pad_def`）；直接传视图名会被 no_check **静默忽略**（缺内层焊盘）。
   另：pad 层 figure 名会被 API 按 figureSize 方向**自动改写**
   （OBLONG_X+竖尺寸→OBLONG_Y，no_check 不报）——金标准 Editor 手工路径
   才能保留 OBLONG_X+竖尺寸；形状/尺寸一致，仅名字不同，验收对拍时
   此列差异需单独向工程师说明，不可当作"全字段一致"。
4. **DRILL 实义是生死线（四次静默陷阱实锤）**：
   - **oblong 槽孔必须用 `?holeType 'OVAL_SLOT`**（第 4 课，2026-10-13 工程师
     否决第 3 版）：合法 holeType 只有 `CIRCLE_DRILL / SQUARE_DRILL /
     OVAL_SLOT / RECTANGLE_SLOT`（官方 axlDBCreatePadStack.txt）——
     `'OBLONG_DRILL` 不在列表里，被**静默回落成 CIRCLE_DRILL**：
     尺寸/figure 全对、`.pad` 仍显示 through，但 Padstack Editor 显示
     **Thru Pin 而非 Slot**。正确槽孔写法（三变体实验对拍金标准通过）：
     `?holeType 'OVAL_SLOT ?plating ?slotSize {宽}:{长}
     ?figure 'OBLONG_Y ?figureSize {宽}:{长}`（本地帧，X宽 Y长；
     slotSize 反序会变 OBLONG_X 横槽）。
   - 普通异形钻孔（`*_DRILL`）是**三件套**，缺一都被 `no_check=t` 静默零化：
     `?drillDiameter {槽宽=min(size)}` + `?figure 'OBLONG_Y` +
     `?figureSize {宽}:{长}`。漏 `?figure` → 丢整个 drill（PadType: smd）；
     figure 写 `'OBLONG`（非法名，合法只有 `OBLONG_X`/`OBLONG_Y`）→
     零化 figure/尺寸；漏 `?drillDiameter` → 同样全零
     （`.pad` 仍显示 `PadType: through`！holeType 残留会骗人）。
   - 金标准口径：本地竖孔 + pin 旋转 90° → 板面横槽；槽型 OVAL SLOT；
     环宽标准 0.3/边（紧凑 0.2 仅高密度 type-c）
   - **drillChar（钻头表字符）是设计级数据，不照抄不强制**（2026-10-13 定性）：
     extracta DRILL 行的 drillChar（金标准=OA）是 NC 钻头表引用，**随图纸变**
     （语料实锤：同族槽孔有空/OA/if，圆孔=H），不是 padstack 构造约定——
     builder 不设置、规则不校验；验收对拍时该列差异属设计级预期差异，
     与 figure 名 X/Y 差异一样需单独说明，不得当作"全字段一致"。
   - **验证口诀：`.pad` 的 PadType 字符串不可信，必须 extracta 回读 DRILL 行
     （size>0 + figure 非空 + holeType 列）**——规则 2.7 已内置 holeType 校验
     （oblong 孔 holeType≠OVAL SLOT 即 FAIL）
5. **命名语法**：`POB{孔长}X{孔短}HOB{盘长}X{盘短}`（长轴在前、板面尺寸序）；
   `NP` 前缀 = 非金属化机械孔（环宽豁免）；`OS{X|Y}{off}` = 槽偏移后缀
6. 检查兜底：规则 **2.7（层完整性+钻孔实义）/ 2.8（环宽）/ 2.9（命名）/ 2.10（钢网）**
   FAIL 禁止交付；builder 出厂自检（extracta 回读）FAIL 同样不 success。
7. **Package Height Max 必设**（2026-10-14 LYDPJ 复核第 1 条错误）：
   Editor 的 Setup→Areas→Package Height 本质 = 给 PLACE_BOUND shape 挂
   `PACKAGE_HEIGHT_MAX` 属性。`build_footprint_from_params(package_height=…)`
   已内置原子挂接（值取规格书侧视图「板面以上本体高」）。
   API 实锤（探针三轮 + 官方示例 axldbctprop.il）：
   - 函数名是 **`axlDBAddProp`**（`axlDBAddProperty` 在 17.2 不存在）；
   - 属性形态 = flat list：`axlDBAddProp(dbid list("NAME" value))`；
   - ★ **回读必须 `axlDBGetProperties(dbid)`** —— `dbid->PROP` 箭头只暴露
     系统属性，用户属性读不到也不出现在 `->?`（挂成功也会被误判失败）；
   - 值落库形态 `'4.2 MM'`（字符串带单位）；extracta 回读**看不到**属性，
     验收时 Package Height 以挂接回读/Editor 人工确认为准。
8. **paste_bottom 是 per-padstack 键**（2026-10-14 LYDPJ v5 实锤）：
   金标准可能主 padstack 带 BPM、extra padstack 不带——在 padstack 字典里
   逐个声明，禁止 spec 级全局键（会把 extras 一起挂上 BPM）；engine 派生
   结果回填该键保证二次 apply_conventions 幂等。

