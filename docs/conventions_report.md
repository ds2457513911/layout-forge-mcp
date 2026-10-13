# 构造规约挖掘报告（第 0 层）

- 挖掘时间：2026-10-13 10:31:08
- 语料库：`C:\Users\ds245\Documents\Work_Bmorn\Layout\Data\PCB_Footprint`（253 件 .dra，失败 0）
- 机器真源：`knowledge/conventions.json`；逐件事实：`Tools/log/conventions_padstacks.json`

## 约定总表

## smd（样例 384，被 pin 引用 359）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | TOP+~TPM+~TSM×344, TOP+~TSM×11, TOP+~TFM+~TSM×3, TOP×1 |
| hole_figure | ×359 |
| slot_type | ×359 |
| plated | P×187, N×172 |

## th_circle（样例 13，被 pin 引用 12）

| 约定项 | 取值 → 支持度 |
|--|--|
| layers | BOTTOM+TOP+internal_pad_def+~BSM+~TPM+~TSM+~DRILL×5, BOTTOM+TOP+internal_pad_def+~BSM+~TSM+~DRILL×2, BOTTOM+TOP+internal_pad_def+~BPM+~BSM+~TPM+~TSM+~DRILL×1, BOTTOM+GND+TOP+VCC+internal_pad_def+~BSM+~TSM+~DRILL×1, BOTTOM+GND+TOP+VCC+internal_pad_def+~BPM+~BSM+~TPM+~TSM+~DRILL×1 |
| hole_figure | CIRCLE×12 |
| slot_type | CIRCLE_DRILL×12 |
| plated | P×8, N×4 |
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
| layers | BOTTOM+TOP+internal_pad_def+~BSM+~TPM+~TSM+~DRILL×22, BOTTOM+TOP+internal_pad_def+~BSM+~TSM+~DRILL×3, BOTTOM+TOP+internal_pad_def+~BPM+~BSM+~TPM+~TSM+~DRILL×1 |
| hole_figure | OBLONG_Y×26 |
| slot_type | OVAL SLOT×26 |
| plated | P×23, N×3 |
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
| layers | BOTTOM+TOP+internal_pad_def+~BSM+~TSM+~DRILL×13, BOTTOM+TOP+internal_pad_def+~BSM+~TPM+~TSM+~DRILL×3, BOTTOM+GND+TOP+VCC+internal_pad_def+~BSM+~TSM+~DRILL×1, BOTTOM+L10+L11GND+L2GND+L3_VAL+L4_VAL+L5GND+L6DEBUG+L7DEBUG+L8GND+L9+TOP+internal_pad_def+~BSM+~TSM+~DRILL×1 |
| hole_figure | ×7, SQUARE×4, HEXAGON_X×4, OCTAGON×2, CROSS×1 |
| slot_type | CIRCLE_DRILL×18 |
| plated | N×13, P×5 |
| ring_x | 0.0×2, -0.508×2, 0.125×2, -0.5×1, -0.75×1 |
| ring_y | 0.0×2, -0.508×2, 0.125×2, -0.5×1, -0.75×1 |
| mask_expand_x | 0.05×10, 0.864×1, 0.61×1, 0.5×1, 0.75×1 |
| mask_expand_y | 0.05×10, 0.864×1, 0.61×1, 0.5×1, 0.75×1 |
| paste_top=pad尺寸 | 3/3（有 paste 的件中） |
| paste_bottom 存在 | 0/18 |
| 命名语法 | [('other', 18)] |
