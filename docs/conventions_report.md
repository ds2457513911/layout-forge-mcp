# 构造规约挖掘报告（第 0 层）

- 挖掘时间：2026-10-13 16:39:33
- 语料库：`C:\Users\ds245\Documents\Work_Bmorn\Layout\Data\PCB_Footprint`（253 件 .dra，失败 0）
- 机器真源：`knowledge/conventions.json`；逐件事实：`Tools/log/conventions_padstacks.json`

## 约定总表

## smd（样例 384，被 pin 引用 359）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | ETCH/TOP+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_TOP×344, ETCH/TOP+PIN/SOLDERMASK_TOP×11, ETCH/TOP+PIN/FILMMASK_TOP+PIN/SOLDERMASK_TOP×3, ETCH/TOP×1 |
| hole_figure | ×359 |
| slot_type | ×359 |
| plated | ×359 |

## th_circle（样例 13，被 pin 引用 12）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | ETCH/BOTTOM+ETCH/TOP+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×5, ETCH/BOTTOM+ETCH/TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×2, ETCH/BOTTOM+ETCH/TOP+PIN/PASTEMASK_BOTTOM+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×1, ETCH/BOTTOM+ETCH/TOP+GND+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+VCC+internal_pad_def+DRILL×1, ETCH/BOTTOM+ETCH/TOP+GND+PIN/PASTEMASK_BOTTOM+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+VCC+internal_pad_def+DRILL×1 |
| hole_figure | CIRCLE×12 |
| slot_type | CIRCLE_DRILL×12 |
| plated | P×8, N×4 |
| drill_char | ×6, E×2, H×1, U×1, L×1 |
| ring_x | 0.0×4, 0.2×2, 0.325×1, 0.15×1, 0.95×1 |
| ring_y | 0.0×4, 0.2×2, 0.325×1, 0.15×1, 0.95×1 |
| mask_expand_x | 0.05×9, 0.075×2 |
| mask_expand_y | 0.05×9, 0.075×2 |
| paste_top=pad尺寸 | 7/7（有 paste 的件中） |
| paste_bottom 存在 | 2/12 |
| 命名语法 | [('PC{孔}HC{盘}', 8), ('other', 4)] |

## th_oblong（样例 32，被 pin 引用 26）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | ETCH/BOTTOM+ETCH/TOP+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×22, ETCH/BOTTOM+ETCH/TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×3, ETCH/BOTTOM+ETCH/TOP+PIN/PASTEMASK_BOTTOM+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×1 |
| hole_figure | OBLONG_Y×26 |
| slot_type | OVAL SLOT×26 |
| plated | P×23, N×3 |
| drill_char | ×7, OE×5, if×4, OJ×2, %×2 |
| ring_x | 0.3×16, 0.2×6, -0.95×1, 0.0×1, 0.15×1 |
| ring_y | 0.3×15, 0.2×6, -1.1×1, 0.0×1, 0.15×1 |
| mask_expand_x | 0.05×22, 0.075×2, 0.95×1, 0.2×1 |
| mask_expand_y | 0.05×22, 0.075×2, 1.1×1, 0.375×1 |
| paste_top=pad尺寸 | 23/23（有 paste 的件中） |
| paste_bottom 存在 | 1/26 |
| 命名语法 | [('P{孔OB}H{盘OB}', 23), ('NP:{孔OB}(盘省略)', 3)] |

## th_other（样例 22，被 pin 引用 18）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | ETCH/BOTTOM+ETCH/TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×13, ETCH/BOTTOM+ETCH/TOP+PIN/PASTEMASK_TOP+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×3, ETCH/BOTTOM+ETCH/TOP+GND+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+VCC+internal_pad_def+DRILL×1, ETCH/BOTTOM+ETCH/TOP+L10+L11GND+L2GND+L3_VAL+L4_VAL+L5GND+L6DEBUG+L7DEBUG+L8GND+L9+PIN/SOLDERMASK_BOTTOM+PIN/SOLDERMASK_TOP+internal_pad_def+DRILL×1 |
| hole_figure | ×7, SQUARE×4, HEXAGON_X×4, OCTAGON×2, CROSS×1 |
| slot_type | CIRCLE_DRILL×18 |
| plated | N×13, P×5 |
| drill_char | ×6, A×4, L×2, G×2, N×2 |
| ring_x | 0.0×2, -0.508×2, 0.125×2, -0.5×1, -0.75×1 |
| ring_y | 0.0×2, -0.508×2, 0.125×2, -0.5×1, -0.75×1 |
| mask_expand_x | 0.05×10, 0.864×1, 0.61×1, 0.5×1, 0.75×1 |
| mask_expand_y | 0.05×10, 0.864×1, 0.61×1, 0.5×1, 0.75×1 |
| paste_top=pad尺寸 | 3/3（有 paste 的件中） |
| paste_bottom 存在 | 0/18 |
| 命名语法 | [('other', 18)] |

## 列守卫：已豁免且实际有值（知悉项）

- `r0` 非空×451 —— 行标记
- `r2` 非空×451 —— 记录序号
- `r4` 非空×451 —— FIXFLAG 标志位
- `r5` 非空×384 —— VIAFLAG 标志位
- `r9` 非空×451 —— XOFF（drill 行恒 0）
- `r10` 非空×451 —— YOFF（drill 行恒 0）
- `r14` 非空×451 —— TRELWIDTH 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r22` 非空×451 —— APADHGHT 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r23` 非空×451 —— APADXOFF 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r0` 非空×4592 —— 行标记
- `r2` 非空×4592 —— 记录序号
- `r4` 非空×4592 —— FIXFLAG 标志位
- `r5` 非空×3894 —— VIAFLAG 标志位
- `r12` 非空×106 —— PADSHAPENAME flash 型焊盘引用名（语料 106 行有值；不建模 flash 焊盘，显式豁免待后续裁定）
- `r13` 非空×66 —— TRELSHAPE1 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r14` 非空×1435 —— TRELWIDTH 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r15` 非空×1435 —— TRELHGHT 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r16` 非空×1435 —— TRELXOFF 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r17` 非空×1435 —— TRELYOFF 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r20` 非空×72 —— APADSHAPE1 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r21` 非空×1435 —— APADWIDTH 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r22` 非空×1435 —— APADHGHT 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r23` 非空×1435 —— APADXOFF 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
- `r24` 非空×1435 —— APADYOFF 负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）
