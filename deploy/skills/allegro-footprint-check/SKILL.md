---

name: allegro-footprint-check
description: |
  Checks PCB footprint pad parameters against datasheet land pattern specifications.
  Compares theoretical pad dimensions extracted from PDF datasheets with actual pad
  parameters read from Allegro .dra files. Covers 6 major categories of checks:
  naming convention, pad dimensions, spacing & origin, pin number, place-bound,
  assembly/silkscreen. Use when the user asks to verify footprint correctness,
  check pad dimensions, compare datasheet vs Allegro, or validate land pattern.
license: MIT
compatibility: >
  Requires Python 3.10+, fastmcp, skillbridge, PyMuPDF, pdfplumber, and the
  layout-forge-mcp MCP server (footprint build + check tools in one server).
  Vision extraction is performed by the client LLM.
metadata:
  author: layout-team
  version: "2.3.0"
  category: hardware-layout
requires-mcp-servers:
  - name: layout-forge-mcp
    package: "local"
    description: >
      Local MCP server exposing footprint build and check tools
      (28 tools). Same server used by the allegro-skillbridge-ops skill.
    command: python
    args:
      - "-c"
      - "import sys; sys.path.insert(0, r'{{PROJECT_ROOT}}'); from mcp_server.server import main; main()"
    parameters:
      PROJECT_ROOT:
        description: "Absolute path to the layout-forge-mcp project root"
        required: true
        default: "."

---

# Footprint Tolerance Check

检查 PCB 封装是否符合设计规范（datasheet 与 Allegro 封装是否一致）。

## 触发条件

当用户提到以下意图时启用：

- “检查封装”、“验证 footprint”、“对比 datasheet 和 Allegro”
- “焊盘尺寸对不对”、“land pattern 公差”
- 给出一个封装文件夹路径，要求检查

## 核心原则

**数值由 MCP 计算，语义由 AI 判断。**

| 类型 | 谁负责 | 覆盖范围 |
|---|---|---|
| **数值** | MCP（Python） | 焊盘尺寸、pitch、间距、原点、外框尺寸、重叠检测 |
| **语义** | AI | 命名含义、pin 功能对应、极性标识识别、元件类型判断 |

**分界线**：凡是能用数字精确算的，交给 MCP；凡是需要"看懂图纸"的，交给 AI。

### 焊盘尺寸比对的数据源（重要）

比对"焊盘尺寸 vs datasheet 理论值"时，**必须使用 ETCH/TOP 层的铜箔尺寸**。

`.dra` 里同一焊盘有多层尺寸：
- **ETCH/TOP** = 铜箔焊盘（**用这个和 datasheet 比**）
- **PIN/SOLDERMASK_TOP** = 阻焊开窗（通常比铜箔单边大 0.05mm，**不参与理论比对**）
- **PIN/PASTEMASK_TOP** = 钢网（通常与铜箔等大）

`read_allegro_footprint` 返回的是 `pin.b_box`（整层最大 bbox），
**通常是阻焊开窗尺寸**，比铜箔大 0.1mm，会导致误判。
因此**禁止使用 `read_allegro_footprint`**，必须用 `check_footprint_by_rules`。

### 报告渲染（重要）

最终报告由 `check_footprint_by_rules` 内部的 `render_markdown` 生成，
AI 拿到返回后**原样贴出 `markdown` 字段**，禁止改写、补充、解释。
格式完全由 Python 控制。

## 前置条件

1. **MCP 服务器**：`layout-forge-mcp` 已连接（建封装与检查封装共用同一个服务）。
2. **目标文件夹**：包含 PDF 规格书、`.dra` 封装、可选的 `.pad`。
3. **路径要求**：**封装文件夹路径里不能有中文**。Allegro 不支持非 ASCII 路径。

## 工作模式判定

**第一步：判断用户给的是单封装还是批量。**

1. 调用 `list_folder_files(folder_path="<用户给的路径>")`。
2. 如果 `pdf` 或 `dra_files` 有内容 → **模式 A（单封装）**。
3. 如果都为空但文件夹下有子文件夹 → **模式 B（批量）**。
4. 无法判断 → 直接问用户。

---

## 模式 A：单封装检查

按下方“单封装工作流程”执行。

## 模式 B：批量检查

批量任务分 4 阶段：**侦察 → 规划 → 执行 → 汇总**。

### B0. 侦察

调用 `list_subfolders(parent_path)` 列出所有子文件夹，对每个子文件夹调用 `list_folder_files` 记录 PDF/.dra/.pad 情况。输出侦察清单（哪些可检查、哪些跳过）。

### B1. 规划

根据可检查封装数分批：

| 封装数 | 策略 |
|---|---|
| 1~3 | 一次性完成 |
| 4~8 | 一次性完成，每个封装独立报告 |
| 9~15 | 分 2~3 批，每批 5 个，批间暂停等用户确认 |
| 16+ | 必须分批，每批 5 个 |

### B2. 执行

- 维护进度表（✅ 完成 / ⏳ 进行中 / ⏸️ 待处理 / ❌ 失败）
- 每个封装完成后**立即输出报告**，不要攒到最后
- 失败不阻断：记录原因，继续下一个
- **每完成 5 个主动汇报进度**

### B3. 汇总

所有封装完成后，输出：
- 结论分布表（PASS / FAIL / REVIEW_REQUIRED / FAILED / SKIPPED）
- 失败明细
- 跳过项及原因
- 建议下一步

---

## 单封装工作流程

### 第 1 步：列出文件夹内容

调用 `list_folder_files(folder_path="<封装文件夹>")`。

从返回里提取：
- `pdf`：PDF 完整路径
- `pdf_stem`：PDF 文件名去掉 `.pdf`
- `dra_files`：所有 .dra
- `pad_files`：所有 .pad

如果 `pdf` 为 null 或 `dra_files` 为空，报告并停止。

### 第 2 步：定位 land pattern 页

调用 `locate_land_pattern_page(pdf_file_path="<pdf>")`。

根据 `method` 决定：
- `primary` / `fallback` → 使用返回的 `page`
- `no_match` → 尝试渲染所有页自己找；找不到就报告，用第 1 页兜底

### 第 3 步：AI 读图提取 datasheet 理论参数

1. 读 resource `datasheet://<pdf_stem>/page/<page>` 拿到 PDF 该页图片
2. 按下方"提取规则"读图，输出 JSON
3. 调用 `validate_land_pattern_json` 校验
4. 校验通过 → 保留为 `theoretical_payload`（第 6 步要用）
5. 校验失败 → 根据 errors 修正后重试，最多 2 次

**如果客户端不支持读 MCP resource**：调用 `render_pdf_page(pdf_file_path=<pdf>, page=<page>)` 拿到图片路径，再读图。

#### 提取规则

- **★ 分辨率铁律（2026-09-29 实测踩中）**：**禁止用整页低分辨率图读数字**。
  实测同一份规格书（3S48000163 p5 LAND PATTERN）：
  - 200 dpi 整页渲染 → 把纵向间隙 `0.5` 读成了 `0.7`（**读错**）
  - 450 dpi **区域裁剪放大** → 读对 `1.2 | 0.5 | 1.2`
  正确做法：先整页定位区域 → 用 `render_pdf_page` 或 PyMuPDF `clip` **裁到该区域 + 提高 dpi（≥400）** → 再读数。
- **★ 交叉校验（读完之后必做，至少两条）**：
  1. **图纸内自洽**：相邻标注求和是否等于总尺寸（如 `1.2 + 0.5 + 1.2` 应等于标注的总高）
  2. **与既有产物比对**：同名件若已有 `.dra` / 历史 `checklist_report_*.json`，用它的实测值反查本次读数
  3. 只有"数值范围守门"（0.2~5 mm）**挡不住** 0.5↔0.7 这类量级相近的误读，别拿它当保险
- 图片可能旋转 90 度，先在心里旋转再读数
- 只读 **land pattern / recommended pad / 推荐焊盘** 区域，不读器件外形尺寸
- 区分"器件尺寸"（3.2×2.5 是器件本体）和"焊盘尺寸"（1.4×1.2 是铜箔）——**只要焊盘尺寸**
- pin 号必须是整数，不要输出 "1" 或 1.0
- 单位统一 mm；mil 换算：1 mil = 0.0254 mm
- spacing_x / spacing_y 是焊盘中心间距，无标注填 null
- 数值合理性自检：单个焊盘通常 0.2~5mm；小于 0.1 或大于 10 肯定误读
- 图纸模糊 → note 里说明，不编造

#### 公差提取规则

- 图纸写 `0.30±0.03` → tolerance = `{"min": -0.03, "max": 0.03}`
- 图纸写 `0.30 +0.03/-0.01` → tolerance = `{"min": -0.01, "max": 0.03}`
- 图纸只写名义值（如 `1.4`）→ 该维度 tolerance 填 null
- 公差区域模糊 → 填 null，note 里说明
- **图纸没标公差 ≠ 数据不可用**，正常提取名义值

#### 输出格式

    {
      "unit": "mm",
      "pads": [{"pin": 1, "width": 1.4, "height": 1.2}],
      "spacing_x": 2.2,
      "spacing_y": 1.7,
      "tolerance": {
        "width":     {"min": -0.03, "max": 0.03},
        "height":    {"min": -0.03, "max": 0.03},
        "spacing_x": {"min": -0.05, "max": 0.05},
        "spacing_y": null
      },
      "note": ""
    }

### 第 4 步：提取 .dra 原始数据（供语义检查用）

调用 `read_full_footprint(dra_file_path=<dra>, pad_file_paths=<pads>)`。

返回里提取：
- `symbol_name`：封装名（第 5.1 步语义检查用）
- `pins`：所有 pin 的 number / xy / bbox / pads（第 5.2 步语义检查用）
- `layers`：assembly_top / silkscreen_top / place_bound_top 等（第 5.3 步语义检查用）

**如果返回 `error`，停下来报告**，不得跳过或自己写 Python 替代。

### 第 5 步：AI 语义检查（AI 负责）

用第 4 步的 `pins` / `layers` + datasheet 图片，做 3 组语义判断，
最终产出一个 `semantic_result` JSON（第 6 步要用）。

#### 5.1 命名规范语义检查

**输入**：
- 封装名字：`read_full_footprint` 返回的 `symbol_name`
- datasheet 图片：`datasheet://<pdf_stem>/page/<page>`（或 `render_pdf_page`）

**检查项**：

1. **类型对比**：名字里的封装类型标识（如 SOD-323、QFN48、HDMI19）与 datasheet 实际类型是否一致？
2. **引脚数对比**：名字里的数字是否包含 datasheet 的引脚数？
3. **尺寸对比**：名字里的尺寸（如 2D7X1D4X0D75 = 2.7×1.4×0.75 mm）与 datasheet 外形尺寸是否一致？
4. **pitch 对比**：名字里的 pitch（如 0D5 = 0.5mm）与 datasheet 是否一致？

**输出**（放入 `semantic_result.naming`）：

    {
      "type_match": true,
      "pin_count_match": true,
      "dimension_match": true,
      "pitch_match": true
    }

**注意**：datasheet 没写的维度填 null，不要编造。

**★ 四个易误判的口径（2026-09-29 实测实证，务必先读）**

1. **命名用「中值」、画实体用「最大值」** —— 两者**允许差一个公差**。

   | 项 | 命名里写的 | 实体 / assembly 实测 |
   |---|---|---|
   | 3S48000163 本体 | `3D2X2D5X0D7` = 3.2 × 2.5 × 0.7（**中值**）| 3.2 × 2.5（该件金标准也用了中值）|
   | 若严格按规范画实体 | 同上（中值）| 应为 **3.3 × 2.6**（= 3.2+0.1 / 2.5+0.1）|

   → 拿命名值去比 assembly 实体，遇到本体公差 ±0.1 的件会**误判为不一致**。
     正确口径：**命名 ↔ 中值**、**实体 ↔ 最大值**，分别比。

2. **`Place_Bound` 的外扩基准是「器件本体」，不是「焊盘阵列」。**
   3S48000163：本体 3.2×2.5 + 单边 0.35 → **3.9 × 3.2** ✓
   若误按焊盘阵列（含阻焊 3.7×3.0）算会得 4.4 × 3.8 ✗。

3. **丝印间距：规范 vs 实物有落差。**
   [规范] 要求丝印与焊盘间距 ≥ 0.20 mm，但金标准实物只有 **0.06 mm**
   （检查器 6.6 只卡 `> 0`）。遇到 WARN 而非 FAIL 时**不必当缺陷** ——
   这是"本体小于焊盘跨距"的封装（本体 3.2×2.5 vs 焊盘跨距 3.7×3.0）的必然结果：
   丝印只能贴焊盘外缘画。

4. **pin 编号方向必须看 datasheet 的 TOP VIEW。**
   4-pad 方阵常见排布是**逆时针、`#1` 在左下**
   （3S48000163 规格书实测：#1 左下 / #2 右下 / #3 右上 / #4 左上）。
   凭"左上角是 1 脚"的直觉去比，会**把方向反了的封装判成正确** ——
   这类错误会直接导致贴片反向、整批报废。

#### 5.2 pin number 语义检查

**输入**：
- datasheet 图片：`datasheet://<pdf_stem>/page/<page>`
- `.dra` 的 pins：`read_full_footprint` 返回的 `pins`

**检查项**：

1. **pin 数量**：datasheet 声明的 pin 数与 `.dra` 是否一致？
2. **pin 编号**：每个 pin 编号是否一致？缺号/重号/多余？
3. **pin 功能**：如果 datasheet 有 pin function 表，顺序是否对应正确？

**输出**（放入 `semantic_result.pin_number`）：

    {
      "dra_pin_count": 4,
      "datasheet_pin_count": 4,
      "count_match": true,
      "pin_numbers_match": true
    }

#### 5.3 极性标识检查

**输入**：
- datasheet 图片：`datasheet://<pdf_stem>/page/<page>`
- Assembly / Silkscreen 层元素：`read_full_footprint` 返回的 `layers`

**检查项**：

1. **1 脚标识**：Assembly 和 Silkscreen 是否都有？（三角形、圆圈、斜杠、数字 "1" 等）
2. **极性标识**（有极性元件：二极管、电解电容）：
   - 二极管：竖线端为负极（K），或有 +/- 标识
   - 电解电容：+ 号标识
3. **层一致性**：Assembly 和 Silkscreen 的标识方向一致吗？

**输出**（放入 `semantic_result.polarity`）：

    {
      "required": false,
      "pin1_marker_assembly": true,
      "pin1_marker_silkscreen": true
    }

**注意**：
- 无极性元件（电阻、普通电容、晶振）→ `required = false`
- 有极性元件但 `.dra` 没标识 → `pin1_marker_* = false`

#### 5.4 组装 semantic_result

把 3 组结果合成一个对象：

    {
      "naming": { ... 5.1 的输出 ... },
      "pin_number": { ... 5.2 的输出 ... },
      "polarity": { ... 5.3 的输出 ... }
    }

### 第 6 步：跑检查 + 存档 + 渲染报告

调用 `check_footprint_by_rules`：

- `dra_file_path` = `<dra>`
- `theoretical_payload` = 第 3 步的返回
- `pad_file_paths` = `<pads>`
- `semantic_result` = 第 5.4 步的 semantic_result
- `save_report` = `True`（默认，可不传）
- `compact` = `True`（默认，可不传）

**默认精简模式（compact=True）** 返回：
- `conclusion` / `component_type` / `summary`
- `markdown`：**最终报告**，AI 原样贴出
- `report_path`：JSON 报告路径（完整明细在此文件里）
- `save_error` / `render_error`：存档/渲染失败原因（成功时为空字符串）

**调试模式（compact=False）** 额外返回：`items` / `failed` / `warned` / `footprint_data`。
需要看逐项明细时，改传 `compact=False`；或直接读 `report_path` 的 JSON。

**如果 `markdown` 为空**（渲染失败），查看 `render_error`，
**如实报告"报告渲染失败：<原因>"**，不得自己拼表替代。

### 第 7 步：输出报告

**原样贴出第 6 步返回的 `markdown` 字段**。

禁止：
- 在 markdown 前后添加任何文字（包括"结论"、"建议"、"已保存到"）
- 改写表格中的任何字符
- 补充解释

流程结束。

---

## 报告格式

报告由 `check_footprint_by_rules` 内部的 `render_markdown` 生成，AI **原样贴出 markdown 字段**。

### 渲染结果示例

    ## nb_xtal4_3d2x2d5x0d7 — ✅ PASS

    数值 19（18✅ 1⚠️，单位 mm）｜报告 checklist_report_20260917_110517.json

    | 大项 | 检查项 | 实测 | 要求 | 状态 |
    |---|---|---|---|---|
    | **1 命名规范** | 前缀 | nb_ | 前缀 = NB_ | ⚠️ |
    | | 小数点 | 无 | 不含 '.' | ✅ |
    | | 含数字 | 4, 3, 2 | 含数字 | ✅ |
    | | 命名语义（类型/引脚数/尺寸/pitch） | 全部一致 | 一致 | ✅ |
    | **2 焊盘尺寸** | 焊盘尺寸与命名一致性 | 一致 | = padstack 命名值 ±0.05 | ✅ |
    | | 阻焊开窗外扩 | x 0.05 / y 0.05 | 单边 0.05 ± 0.02 | ✅ |
    | | 钢网与焊盘等大 | 等大 | 钢网 = 焊盘 | ✅ |
    | **3 间距与原点** | Pin pitch | x 2.2 / y 1.7 | — | ✅ |
    | | pitch 与 datasheet 一致 (x) | 2.2 = 2.2 | = 2.2 ± 0.05 | ✅ |
    | | pitch 与 datasheet 一致 (y) | 1.7 = 1.7 | = 1.7 ± 0.05 | ✅ |
    | | 焊盘不重叠 | 最小间距 0.4 | 间距 > 0 | ✅ |
    | | 原点在封装中心 | [0, 0] | ≤ 0.05 | ✅ |
    | **4 pin number** | pin 数量 | 4 = 4 | 一致 | ✅ |
    | | pin 编号与位置 | 一一对应 | 一致 | ✅ |
    | **5 Place_Bound** | Place_Bound_Top 存在 | 有 | 必须存在 | ✅ |
    | | Place_Bound 外扩量（ic） | x 0.35 / y 0.35 | 单边 0.35 ± 0.05 | ✅ |
    | **6 Assembly / Silkscreen** | Assembly_Top 存在 | 2 元素 | 必须存在 | ✅ |
    | | Assembly 有内容 | 2 元素 | ≥ 2 元素 | ✅ |
    | | Assembly 有 1 脚标识 | 2 个元素 | 有 1 脚标识 | ✅ |
    | | Silkscreen_Top 存在 | 3 元素 | 必须存在 | ✅ |
    | | Silkscreen 有 1 脚标识 | 1 个复杂 path | 有 1 脚标识 | ✅ |
    | | Silkscreen 不与焊盘重叠 | 最小间隙 0.06 | 间距 > 0 | ✅ |

    > 理论值由 AI 读图获取，建议对照 PDF 原图复核。

---

## 注意事项

### 铁律（违反将导致任务失败）

1. **所有数值必须来自 MCP tool 的实际返回**。
   不得自己写 Python 替代 MCP tool，不得根据历史记忆推断 tool 会失败。

2. **每次调用 tool 后必须读取返回内容**。
   不得说"工具又挂了"而不贴原始返回。如果 tool 返回 error，
   **如实贴出 error 字符串**。

3. **不得凭历史记忆跳过任何 tool**。
   上一轮失败不代表这一轮失败，环境可能已变。必须实际调用确认。

4. **MCP tool 返回 error 时，停止当前封装检查，报错给用户**。
   不得继续走替代流程，不得编造数据。

5. **禁止用 Python 直接解析 PDF 内部结构**（如 `pg.get_images()`、
   `get_image_info()`、`page.get_drawings()`）。
   PDF 图纸必须通过 MCP resource `datasheet://<pdf_stem>/page/<page>` 读图，
   或通过 `render_pdf_page` tool 渲染后读图。

6. **禁止调用 `read_allegro_footprint` + `check_land_pattern_tolerance`**。
   这两个 tool 是旧版流程，已废弃，会导致焊盘尺寸误判（阻焊开窗 vs 铜箔）。

7. **不得根据理论值 + 公差推断"实测值"**。
   不得用"推算"、"大概"、"应该"等词语生成任何数据。
   只能报告 MCP tool 实际返回的值。

8. **如果某个 tool 挂了，如实说明"该步骤未执行"，不补充任何数据**。
   不得编造"看起来合理"的结果。

9. **禁止输出过程性文字**。
   不得输出"让我思考"、"工具返回"、"第 N 步"、"新版流程"、"读图过程"、
   "复核提醒"等叙述性文字。最终报告必须来自 `check_footprint_by_rules` 的
   `markdown` 字段，**原样贴出**，不得改写、补充、解释。

10. **禁止对报告内容做任何二次加工**。
    不得在 markdown 前后添加"结论"、"建议"、"已保存到"、"需要注意"等文字。
    不得改动表格中的任何一个字符。

### 常规注意事项

- **数值 vs 语义分工清楚**：数值问题让 Python 判定，语义问题让 AI 判断
- **不要给没看到 ± 的尺寸编造公差**
- **单位统一 mm**
- **spacing 为 null 时不参与比对**（NA，不算 FAIL）
- **批量任务中每个封装独立报告**，不要交叉引用其他封装的细节
- **不要无限重试**：同一操作失败 2 次就跳过
- **路径含中文时提醒用户**：Allegro 不支持非 ASCII 路径，封装文件应放在英文路径下

## 依赖的 MCP 工具一览

### 主用工具（按工作流顺序）

| Tool | 用途 | 关键参数 |
|---|---|---|
| `list_folder_files` | 列出 PDF / .dra / .pad | `folder_path` |
| `list_subfolders` | 列出父文件夹的子文件夹（批量模式） | `parent_path` |
| `locate_land_pattern_page` | 定位 land pattern 页 | `pdf_file_path` |
| `render_pdf_page` | 渲染 PDF 页为图片（客户端不支持 resource 时用） | `pdf_file_path`, `page` |
| `validate_land_pattern_json` | 校验 AI 输出的 JSON | `raw_json` |
| `read_full_footprint` | 提取 .dra 原始数据（pins/layers）供语义检查 | `dra_file_path` |
| **`check_footprint_by_rules`** | **跑 6 大项数值检查 + 存档 + 渲染 markdown** | `dra_file_path`, `theoretical_payload`, `semantic_result`, `save_report`, `compact`（默认 True，精简返回） |

### 废弃工具（禁止使用）

以下 tool 是旧版流程，会导致焊盘尺寸误判（阻焊开窗 vs 铜箔），**禁止调用**：

| Tool | 废弃原因 |
|---|---|
| `read_allegro_footprint` | 返回 `pin.b_box`（阻焊开窗），不是铜箔 ETCH/TOP |
| `check_land_pattern_tolerance` | 依赖上一条 tool 的数据，会与铜箔理论值产生假 delta |
| `save_tolerance_report` | 旧版报告格式，功能已被 `check_footprint_by_rules` 内部存档取代 |

## 依赖的 MCP 资源一览

| Resource URI | 用途 |
|---|---|
| `datasheet://{pdf_stem}/page/{page}` | PDF 整页图片（PyMuPDF PNG） |
| `datasheet://{pdf_stem}/info` | PDF 元信息（JSON 字符串） |

## 依赖的 MCP 提示一览

| Prompt | 用途 |
|---|---|
| `extract_land_pattern` | 单页 datasheet 提取参数 |
| `check_naming_semantic` | 命名规范语义检查 |
| `check_pin_number_semantic` | pin number 对照检查 |
| `check_polarity_marker` | 极性标识检查 |
| `full_footprint_check` | 端到端流程 SOP |

**注意**：如果你的客户端（如 Trae）不支持调用 MCP prompt，直接按本文档"第 5 步"里的语义检查内容执行即可，无需调用 prompt。

---

## 客户端配置示例

本技能与 `allegro-skillbridge-ops` 共用同一个 MCP 服务 `layout-forge-mcp`
（28 个工具：建封装 22 + 读 PDF/目录 4 + 检查 2）。已经注册过就不用再配。

下面给的是**换机器 / 给同事**时的写法。

### 推荐：直接指向项目 venv 的 Python（改代码即生效，无缓存）

    {
      "mcpServers": {
        "layout-forge-mcp": {
          "command": "C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp/.venv/Scripts/python.exe",
          "args": [
            "-c",
            "import sys; sys.path.insert(0, r'C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp'); from mcp_server.server import main; main()"
          ]
        }
      }
    }

★ **`command` 必须指向装了 fastmcp / skillbridge 的那个 Python。**
本项目是 `.venv/Scripts/python.exe` —— 实测系统 Python 和 WorkBuddy 托管
Python 都 `import skillbridge` 失败，用错解释器会直接连不上 Allegro。

---

## 维护提示

每次改了 MCP 代码后：
1. **重连 MCP / 重启客户端** —— 服务进程缓存启动时的代码，不重连不生效
   （本项目 `mcp_server/config.py` **没有** `MCP_VERSION` 字段，无法靠版本号判断）
2. 判据：重连后工具总数应为 **28**；或直接调一个新加的工具（如 `list_specs`）
   看是否报"未知工具"
3. 改动较大时跑两套离线自检：`Tools/selfcheck_geometry.py`（231 项）+
   `Tools/selfcheck_skill_syntax.py`（222 项），**只能用项目 `.venv`**