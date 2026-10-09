# -*- coding: utf-8 -*-
"""
mcp_server/prompts.py —— MCP 提示模板

Prompt 是给客户端 AI 看的"操作说明书"。
本模块只包含字符串模板，不 import 任何业务模块，方便单独复制到 SKILL.md。

Prompt 分类：
  - extract_land_pattern          ：单页 datasheet 提取参数
  - full_footprint_check          ：端到端流程 SOP
  - check_naming_semantic         ：命名规范语义检查
  - check_pin_number_semantic     ：pin number 对照检查
  - check_polarity_marker         ：极性标识检查
"""
from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """把所有 prompt 注册到给定的 FastMCP 实例。"""

    # ============================================================
    # Prompt 1: 从 datasheet 图片提取焊盘参数（保留）
    # ============================================================
    @mcp.prompt()
    def extract_land_pattern(pdf_stem: str, page: int) -> str:
        """指导 AI 从规格书某页图片中提取焊盘参数。"""
        return f"""你是一个硬件封装参数提取助手。请读取以下资源：

- 图片：datasheet://{pdf_stem}/page/{page}
- 元信息：datasheet://{pdf_stem}/info

任务：从这张 land pattern 图纸中提取焊盘参数。

注意事项：
- 图片可能旋转 90 度，请先在心里旋转再读数。
- pin 号必须是整数，不要输出 "1" 或 1.0。
- 所有尺寸统一为 mm；如果图中标注 mil，请换算（1 mil = 0.0254 mm）。
- spacing_x / spacing_y 若图中无明确标注，填 null，不要猜测。
- 如果图纸模糊或无法识别，在 note 字段说明，不要编造数值。

输出严格 JSON：
{{
  "unit": "mm",
  "pads": [
    {{"pin": 1, "width": 0.3, "height": 0.6}}
  ],
  "spacing_x": 0.5,
  "spacing_y": null,
  "tolerance": {{
    "width":     {{"min": -0.03, "max": 0.03}},
    "height":    {{"min": -0.03, "max": 0.03}},
    "spacing_x": {{"min": -0.05, "max": 0.05}},
    "spacing_y": null
  }},
  "note": ""
}}

输出后，请立即调用 validate_land_pattern_json 校验。

---

【公差提取规则】
- 图纸写 `0.30±0.03` → tolerance = {{"min": -0.03, "max": 0.03}}
- 图纸写 `0.30 +0.03/-0.01` → tolerance = {{"min": -0.01, "max": 0.03}}
- 图纸没标 ± → 该维度 tolerance 填 null
- 图纸模糊 → 该维度填 null，note 里说明
- **不要给没看到 ± 的尺寸编造公差**
"""

    # ============================================================
    # Prompt 2: 命名规范语义检查（新）
    # ============================================================
    @mcp.prompt()
    def check_naming_semantic(
        symbol_name: str,
        pdf_stem: str,
        page: int,
    ) -> str:
        """指导 AI 检查封装命名是否与 datasheet 一致（语义部分）。"""
        return f"""你是硬件封装命名规范审查员。

任务：检查封装名字 '{symbol_name}' 是否符合规范，并与 datasheet 对比。

请读取 datasheet 页面：datasheet://{pdf_stem}/page/{page}

Python 已检查的部分（无需重复）：
- 前缀是否为 NB_ 或 nb_
- 名字中是否含小数点
- 名字中是否含数字

你需要做的是语义对比：

1. **类型对比**：名字里的封装类型标识（如 SOD-323、QFN48、HDMI19、FPC10 等）与 datasheet 的实际封装类型是否一致？
2. **引脚数对比**：名字里出现的数字，是否包含 datasheet 的引脚数？
3. **尺寸对比**：名字里的尺寸（如 2D7X1D4X0D75 = 2.7×1.4×0.75 mm）与 datasheet 的封装外形尺寸是否一致？
4. **pitch 对比**：名字里的 pitch（如 0D5 = 0.5mm）与 datasheet 是否一致？

输出严格 JSON，不要额外解释：
{{
  "type_match": true,
  "pin_count_match": true,
  "dimension_match": true,
  "pitch_match": true,
  "parsed_name": {{
    "prefix": "NB_",
    "type": "SOD-323",
    "pin_count": 2,
    "dimensions": [2.7, 1.4, 0.75],
    "pitch": null,
    "special": null
  }},
  "datasheet_actual": {{
    "type": "SOD-323",
    "pin_count": 2,
    "dimensions": [2.7, 1.4, 0.75],
    "pitch": null
  }},
  "issues": [],
  "note": ""
}}

注意：
- 如果某个字段 datasheet 上没写，对应值填 null，issues 里说明"datasheet 未提供"
- issues 里只放真正的不一致，没问题留空数组
"""

    # ============================================================
    # Prompt 3: pin number 语义检查（新）
    # ============================================================
    @mcp.prompt()
    def check_pin_number_semantic(
        pdf_stem: str,
        page: int,
        dra_pins_json: str,
    ) -> str:
        """指导 AI 逐个 pin 对比 datasheet 定义和 .dra 里的 pin 列表。"""
        return f"""你是硬件封装 pin 定义审查员。

任务：对比 datasheet 里的 pin 定义与 Allegro .dra 里的实际 pin 列表。

datasheet 页面：datasheet://{pdf_stem}/page/{page}

.dra 里已提取的 pin 列表（JSON）：
{dra_pins_json}

检查项：

1. **pin 数量**：datasheet 声明的 pin 数与 .dra 里的 pin 数是否一致？
2. **pin 编号**：每个 pin 的编号是否与 datasheet 一致？有没有缺号/重号/多余？
3. **pin 功能**：如果 datasheet 有 pin function 表（GND/VCC/NC/IO 等），.dra 里 pin 的顺序/编号是否对应正确？

输出严格 JSON，不要额外解释：
{{
  "dra_pin_count": 2,
  "datasheet_pin_count": 2,
  "count_match": true,
  "pin_numbers_match": true,
  "missing_pins": [],
  "extra_pins": [],
  "renumbered_pins": [],
  "function_mismatches": [],
  "issues": [],
  "note": ""
}}

注意：
- pin_numbers_match 表示"编号集合"是否一致（[1,2,3] vs [1,2,3] = true）
- missing_pins 是 datasheet 有但 .dra 缺的 pin
- extra_pins 是 .dra 有但 datasheet 没有的 pin
- renumbered_pins 形如 [{{"dra": "1", "datasheet": "2"}}]
- function_mismatches 形如 [{{"pin": "1", "dra": "IO", "datasheet": "GND"}}]
- 如果 datasheet 没有 pin function 表，function_mismatches 留空
"""

    # ============================================================
    # Prompt 4: 极性标识检查（新）
    # ============================================================
    @mcp.prompt()
    def check_polarity_marker(
        pdf_stem: str,
        page: int,
        asm_silk_json: str,
    ) -> str:
        """指导 AI 检查 Assembly / Silkscreen 层的极性标识和 1 脚标识。"""
        return f"""你是硬件封装极性标识审查员。

任务：检查 Assembly 层和 Silkscreen 层是否包含正确的极性标识和 1 脚标识。

datasheet 页面：datasheet://{pdf_stem}/page/{page}

已提取的 Assembly / Silkscreen 层元素（JSON）：
{asm_silk_json}

检查项：

1. **1 脚标识**：Assembly 和 Silkscreen 是否都有 1 脚标识？
   - 常见形式：三角形、圆圈、斜杠、数字 "1"、短边/缺角等
2. **极性标识**（仅对极性元件，如二极管、电容、IC）：
   - 二极管：竖线端为负极（K），或有 +/- 标识
   - 电解电容：+ 号标识
   - IC：通常有 pin 1 标识就够了
3. **层一致性**：Assembly 和 Silkscreen 的标识方向是否一致？

输出严格 JSON：
{{
  "pin1_marker": {{
    "assembly": true,
    "silkscreen": true,
    "evidence": "Assembly 层有三角形 1 脚标识"
  }},
  "polarity_marker": {{
    "required": false,
    "assembly": null,
    "silkscreen": null,
    "evidence": ""
  }},
  "issues": [],
  "note": ""
}}

注意：
- 如果 datasheet 显示这是无极性元件（如电阻），polarity_marker.required = false
- 如果 datasheet 显示这是有极性元件但 .dra 里没有标识，issues 里说明
"""

    # ============================================================
    # Prompt 5: 端到端流程 SOP（更新）
    # ============================================================
    @mcp.prompt()
    def full_footprint_check(folder_path: str) -> str:
        """端到端流程 SOP：告诉 AI 如何完成一个文件夹的完整封装检查。"""
        return f"""对文件夹 `{folder_path}` 执行完整的封装检查（6 大项）。

## 步骤

### 第 1 步：列文件夹
调用 `list_folder_files(folder_path="{folder_path}")`。
拿到 PDF / .dra / .pad 列表。缺任一则报告并停止。

### 第 2 步：定位 land pattern 页
调用 `locate_land_pattern_page(pdf_file_path=<pdf>)`。

### 第 3 步：AI 读图提取 datasheet 理论参数
应用 `extract_land_pattern(pdf_stem=<stem>, page=<page>)`，
按提示词读图并输出 JSON，然后调 `validate_land_pattern_json` 校验。

### 第 4 步：跑 .dra 数值规则检查
调用 `check_footprint_by_rules(dra_file_path=<dra>, pad_file_paths=<pads>)`。

这一步返回 17 项数值检查结果（6 大项）：
- 命名规范（Python 能做的部分）
- 焊盘尺寸
- 间距与原点
- Place_Bound
- Assembly / Silkscreen

### 第 5 步：AI 语义检查（3 个 prompt）

**5.1 命名语义**
应用 `check_naming_semantic(symbol_name=<name>, pdf_stem=<stem>, page=<page>)`

**5.2 pin number 语义**
应用 `check_pin_number_semantic(pdf_stem=<stem>, page=<page>, dra_pins_json=<pins JSON>)`
`.dra` 的 pin 列表来自第 4 步返回的 `footprint_data.pins`。

**5.3 极性标识**
应用 `check_polarity_marker(pdf_stem=<stem>, page=<page>, asm_silk_json=<layers JSON>)`
Assembly / Silkscreen 数据来自第 4 步返回的 `footprint_data.layers`。

### 第 6 步：合并报告

把第 4 步的数值检查和第 5 步的语义检查合并成一份报告。

**结论优先级**：
- 任一 FAIL → 整体 FAIL
- 有 NA → REVIEW_REQUIRED
- 只有 WARN → PASS（但要提醒）
- 全 PASS → PASS

### 第 7 步：保存 JSON 报告
调用 `save_checklist_report(checklist_result=<合并后的结果>, dra_file_path=<dra>)`。

## 输出格式

最终报告分 6 大项：

```
## 封装检查报告

**封装：** <name>
**路径：** <path>
**元件类型：** IC / chip / connector / unknown

### 1. 封装命名规范
- [PASS] 前缀 NB_
- [WARN] 前缀大小写
- [PASS] 类型与 datasheet 一致（SOD-323）
- ...

### 2. 焊盘尺寸
- [PASS] 焊盘尺寸与命名一致
- ...

### 3. 间距与原点
- ...

### 4. pin number
- ...

### 5. Place_Bound
- ...

### 6. Assembly 和 Silkscreen
- ...

### 汇总
- 总数：17 项（数值）+ 8 项（语义）
- PASS：N / FAIL：N / WARN：N / NA：N
- 结论：PASS / FAIL / REVIEW_REQUIRED

### 人工复核提醒
AI 视觉识别结果必须对照 PDF 原始图纸复核，不可直接信任。
```

## 注意事项

- **数值检查的 FAIL 优先信 Python**，AI 不要凭感觉推翻。
- **AI 语义检查如果和 Python 结果冲突，以 Python 为准**（Python 更精确）。
- **失败不中止**：任何一步失败，报告错误，继续其他步骤。
- **不要编造数据**：datasheet 里没有的信息，如实说"未提供"。
"""
