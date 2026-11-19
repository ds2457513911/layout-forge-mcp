---

name: allegro-footprint-check
description: |
  Checks PCB footprint pad parameters against datasheet land pattern specifications.
  Compares theoretical pad dimensions extracted from PDF datasheets with actual pad
  parameters read from Allegro .dra files. Covers 6 major categories of checks:
  naming convention, pad dimensions, spacing & origin, pin number, place-bound,
  assembly/silkscreen. Use when the user asks to verify footprint correctness,
  check pad dimensions, compare datasheet vs Allegro, or validate land pattern.
  Also use for golden-standard batch benchmarking / rule-library calibration
  ("金标准批量学习"、"批量对拍"、"校准规则库"), and whenever the user hands over a
  footprint folder and asks whether it was built right — even if they never say
  the word "check".
license: MIT
compatibility: >
  Requires Python 3.10+, fastmcp, skillbridge, PyMuPDF, pdfplumber, and the
  layout-forge-mcp MCP server (footprint build + check tools in one server).
  Vision extraction is performed by the client LLM.
metadata:
  author: layout-team
  version: "2.6.0"
  category: hardware-layout
  # ★ 三声明（Phase 3 §7）：部署前校验用，改动本文件时同步更新
  mcp_min_commit: "a5bd61f"
  verified_with: "layout-forge-mcp @ a5bd61f / 2026-10-15：eval/check_eval.py 五套全绿、behavior 5/5"
  eval_baseline: "eval/baselines/tool_contract.json + geometry_regression.json（239/222/35/253 件） + spec_reading.json（92.5%）"
requires-mcp-servers:
  - name: layout-forge-mcp
    package: "local"
    description: >
      Local MCP server exposing footprint build and check tools
      (all tools; full list in docs/tools-catalog.md). Same server used by the allegro-skillbridge-ops skill.
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

## references 索引（细节按需读，不要一次读完）

正文只放工作流主干和「违反了也看不出来」的判据；下面 6 份细节**按症状查**：

| 文件 | 什么时候读 | grep 关键词 |
|---|---|---|
| `references/padstack-conventions.md` | **建或检查 THT 件之前**（层清单 / drill 实义 / 命名语法） | `OVAL_SLOT` `no_check` `BPM` `环宽` |
| `references/image-reading-rules.md` | **每次要读规格书数字之前**（分辨率 / 交叉校验 / 公差 / schema） | `dpi` `render_pdf_region` `unconsumed` `SHAPE` |
| `references/naming-pitfalls.md` | **做第 5 步语义检查之前**（四个会得出相反结论的口径） | `中值` `Place_Bound` `TOP VIEW` `丝印间距` |
| `references/report-sample.md` | 收到 `markdown` 想核对结构是否完整时 | `6 大项` `⚠️` `结论行` |
| `references/golden-learning.md` | 只在做金标准批量对拍 / 规则库校准时（模式 C） | `四层漏斗` `extracta` `裁定` |
| `references/client-config.md` | 新机器首次接入 / 客户端"工具全都不见了" | `.venv` `mcpServers` `uv sync` |

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

### TH 件 padstack 层清单

建或检查**通孔插件（mount=tht）**封装时，padstack 层清单是生死线 ——
2026-10-12 LYDPJ 事故（零 drill 全链路绿灯被工程师否决）就出在这里。
双面铜箔 / 双面阻焊 / 钢网 / DRILL 实义四类静默陷阱 / 命名语法 / paste_bottom 键，
完整清单见 `references/padstack-conventions.md`，**建或检查 THT 件之前必读**。

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

**例外**：用户意图明确是"金标准批量学习 / 对拍 / 校准规则库"时，不走 A/B，
直接进**模式 C**（不需要 PDF，只对 .dra 全量跑数值检查）。

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

## 模式 C：金标准批量学习（四层漏斗）

**适用场景**：手里有一个"已知正确"的金标准封装库（如 `Data\PCB_Footprint`，几百个 .dra），
用它全量对拍检查器：① 挖掘构造规约（第 0 层）② 校准规则阈值
③ 沉淀库惯例裁定 ④ 验证两条数据源一致。
**不需要 PDF、不需要 AI 读图** —— 第 0/1 层数值收割与对拍全部 0 token。

四层漏斗的每一步命令、数据源坑（253 件实锤）、第 2 层裁定纪律、收尾强制工具化的对拍要求，
全在 `references/golden-learning.md`。**只在做金标准批量对拍/校准时读。**

---

## 单封装工作流程

### 第 1 步：列出文件夹内容（先预检，再取文件清单）

★ **先调 `folder_preflight(folder_path="<封装文件夹>")`** —— 它把 S1/S2 的判定一次做完：

- `ambiguous=false` + `action=proceed` → 目录里恰有一对 PDF/.dra，继续第 2 步；
- `ambiguous=true`（`verdict=ambiguous_pdf|ambiguous_dra`）→ **把 `pdf_files` / `dra_files` 候选列给用户确认**，
  禁止默认选第一个；
- `verdict=empty|missing_pdf|missing_dra` 或 `action=stop` → 缺料，如实报告并停止（`noise_files` 里的东西
  不算候选：`AUTOSAVE.dra`、`allegro.jrl`、`master.tag`、`*.log`、历史 `checklist_report_*.json`）。

确认目标后，再调用 `list_folder_files(folder_path="<封装文件夹>")`。

从返回里提取：
- `pdf`：PDF 完整路径
- `pdf_stem`：PDF 文件名去掉 `.pdf`
- `dra_files`：所有 .dra（**与预检的 `dra_files` 不一致时以预检为准**）
- `pad_files`：所有 .pad

如果 `pdf` 为 null 或 `dra_files` 为空，报告并停止。

### 第 2 步：定位 land pattern 页

调用 `locate_land_pattern_page(pdf_file_path="<pdf>")`。

根据 `method` 决定：
- `primary` / `fallback` → 使用返回的 `page`
- `no_match` → 尝试渲染所有页自己找；找不到就报告，用第 1 页兜底

### 第 3 步：AI 读图提取 datasheet 理论参数

> ★ **先判形态**（2026-10-13 起）：调 `pdf_probe(pdf_file_path)` 一次拿到
> `form` ∈ {`cad_vector` | `printed` | `scan` | `thin_text`} 与 `read_hint`，按提示选读法：
> - `cad_vector`（如 AutoCAD 出图，尺寸是字形轮廓、文本层无数字）→ **跳过定位，直接 `render_pdf_region`**
> - `printed` → 可提文本层表格；land pattern 用 `render_pdf_region`
> - `scan` → `render_pdf_contact_sheet` 定位页 → `render_pdf_region` 读数
> - `thin_text` → 关键词定位，失配则走拼版
>
> `form` 是启发式、可能误判；**它只决定"先试哪条路"**，不影响结果（区域裁剪对任何 PDF 都可用）。

1. 读 resource `datasheet://<pdf_stem>/page/<page>` 拿到 PDF 该页图片
2. 按下方"提取规则"读图，输出 JSON
3. 调用 `validate_land_pattern_json` 校验
4. 校验通过 → 保留为 `theoretical_payload`（第 6 步要用）
5. 校验失败 → 根据 errors 修正后重试，最多 2 次

**如果客户端不支持读 MCP resource**：调用 `render_pdf_page(pdf_file_path=<pdf>, page=<page>)` 拿到整页图片。
**整页图只用来定位（找 land pattern 在哪、判断哪块要细看），不得用它读数字**——它固定 150 dpi，
模型还会把长边压到 1600 px（A4 等效约 190 dpi），正好落在「0.5 读成 0.7」的误差带里。
读数一律走 `render_focus_regions`（概览 + 焦点区 ≥600 dpi，不够就切块）或 `render_pdf_region`（指定 rect）。

#### 提取规则

**分辨率铁律**：禁止用整页低分辨率图读数字 —— 200 dpi 整页曾把纵向间隙 `0.5` 读成 `0.7`，
450 dpi 区域裁剪才对。正确做法：先定位区域 → `render_pdf_region(rect, dpi≥400)` → 再读数。

读完之后**必须**做至少两条交叉校验（图纸内自洽 / 与既有产物比对），
并遵守公差提取与输出 schema 的完整规则（含 `unconsumed` spec 守卫、
SHAPE 型自绘轮廓焊盘的 0.2 WARN 口径）—— 见 `references/image-reading-rules.md`。

**输出格式**（`theoretical_payload` 的 schema，`validate_land_pattern_json` 会校验）：

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
      "unconsumed": [],
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

**★ 四个易误判的口径（实测实证，务必先读）**

命名用中值 / 实体用最大值 → 比 assembly 实体时差一个公差是**正常**的；
`Place_Bound` 外扩基准是**器件本体**不是焊盘阵列，连接器类的非对称外扩必须显式传 rect；
丝印 0.06 vs 规范 0.20 的落差遇 WARN 不必当缺陷；
pin 编号方向必须看 datasheet TOP VIEW（凭直觉会把反了的封装判成正确 → 贴片反向、整批报废）。
四条**完整推论与实测数值**见 `references/naming-pitfalls.md`。

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
- `output_dir` = 不传（默认 `None`）。★ 2026-10-16 约定：**报告直接落 `<封装目录>/check_log/`**，
  工具内部自动建目录、自动命名，AI **不需要也不能**自己传 `output_dir` 把报告写去别处；
  确有归档到指定位置的需要时才显式传（如批量迁移历史报告）。
- `output_dir` 想覆盖时才传；`report_path` 返回值可核对落点。

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

流程结束前，若本次检查有**人工裁定**（误报判定 / 阈值修正 / 规则确认）：

### 第 8 步：案例沉淀（有人工裁定时做，没有就跳过）

调用 `save_case` 把裁定写入案例库（`knowledge/cases/`，JSON 热更新，不用重连 MCP）：
- `tag`：封装名（如 `NB_WSON8_8P`）
- `fingerprint`：指纹八元组（mount/pin_count/pitch_x/pitch_y/body_x/body_y/pad_x/pad_y）
- `findings`：命中的误报项 + 原因；`verdict`：`PASS/FAIL/WARN/NA`；`lessons`：教训一句话

**下次检查同类封装前，先 `find_case`（按 keyword / fingerprint_query）做同族预警**，
命中案例里判过的误报项直接引用案例结论降级，不再重复误报。

流程结束。

---

## 报告格式

报告由 `check_footprint_by_rules` 内部的 `render_markdown` 生成，AI **原样贴出 markdown 字段**，
禁止改写、补充、解释。完整渲染示例（19 项 / 6 大项 / PASS+WARN 混排）见
`references/report-sample.md` —— 用来判断"收到的报告结构是不是和往常一样"。

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
   或通过 `render_pdf_page` / `render_pdf_region` / `render_focus_regions` tool 渲染后读图，
   数字候选与矢量阵列只能来自 `extract_page_numbers` / `reconstruct_pad_array`。
   **判文件形态**用 MCP 工具 `pdf_probe`（内部复用同一套判据，是合规出口），
   不要为了"看看是不是扫描件/CAD 图"自己写 Python 解析 PDF 结构。
   `pdf_probe` 返回 `ok:false` 时**回退旧流程**，不要中断整个检查（见本条第 4 款的例外）。

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

> ★ 第 9/10 条与下文 **S3**（禁推理文字泄漏）及「工具已保证」表的最后一行是**同一批规矩**，
> 记在铁律区是因为历史位置；**"AI 待记条数"统计时不重复计入**（见文末维护提示第 8 条）。

### S 系硬规则（2026-10-12 金标准批量实锤后补充；2026-10-15 按 Phase 3 §7 改为「由工具保证 / AI 只记必要」

> ★ **读法**：下面第 1 节是**工具已经保证**的事（AI 不必背，但必须**读工具返回的字段**来决定下一步）；
> 第 2 节才是 AI 仍需自己记的硬规则（**去重后共 4 条**：S1–S4，加 S5 的入口条件；Phase 3 验收口径见文末维护提示第 8 条）。

#### 工具已保证（AI 不用记，只要读字段）

| 原来靠 AI 记的 | 现在由谁保证 | AI 该读什么 |
|---|---|---|
| S1 目录里多份 `.dra`/PDF 不许瞎猜 | `folder_preflight` 工具（内部 `orchestration/preflight.py`） | `ambiguous` 字段（布尔）+ `verdict ∈ ambiguous_pdf|ambiguous_dra` + `pdf_files`/`dra_files` 候选清单 + `action`（`confirm` 就是"去问用户"） |
| S2 历史 `checklist_report_*.json` 不算本次证据 | 同一工具（`orchestration.preflight.is_noise` / `is_noise_path`） | `noise_files` 字段 —— 落在这里的都不是候选、也不是证据；**`check_log/` 里的报告是合法归档，不算噪声**（2026-10-16 约定）；`verdict=empty` + `action=stop` 就是"缺料，别再往下走" |
| S4 触 Alegro 前先探通道 | `inspect_allegro_environment` + `orchestration/pipeline.py` 的 `env-check` 步 | `connected` / `error`（`connected=false` 时 `error` 必非空）；`pipeline` 会给出失败码 `CHANNEL_NOT_READY` |
| 数值 vs 语义分工、NA 不参与比对 | `rule_checker.py` 判据 | 每个 `_item` 的 `status ∈ PASS|FAIL|WARN|NA`（**NA 不是 FAIL**，报告里已分类） |
| 报告不得二次加工 | `check_footprint_by_rules` 的返回 | `markdown` / `report_path` 字段本体（原样贴出） |

#### AI 仍需自己记的硬规则（4 条）

- **S1 报歧义不许猜**：先调 `folder_preflight`。返回 `ambiguous=false` 才可静默继续；
  `ambiguous=true`（或 `action=confirm`）时**必须**把 `pdf_files` / `dra_files` 候选列给用户确认，
  禁止默认拿第一个就跑。检查完用报告头 `symbol_name` 与文件名核对，防止张冠李戴。
- **S2 只看本次结果**：结论只能来自本次 `check_footprint_by_rules` 返回的 `report_path` / `markdown`。
  读历史报告仅限第 3 步交叉校验理论值，且必须先核对报告内 `symbol_name` 与本次目标一致。
  历史报告的归档位是 **`<封装目录>/check_log/`**（2026-10-16 起）；散放在封装目录根下的
  旧报告会被 `folder_preflight` 判为噪声 —— 遇到就提醒用户搬进 `check_log/`，别拿它当证据。
- **S3 禁推理文字泄漏**：最终输出只允许报告 `markdown` 字段本体。AI 自己的读图过程、中间 JSON、
  tool 参数、复核思路一律不出现在回复里（报告内部的 blockquote 提示行属报告内容，可保留）。
- **S4 通道失败码要按码处置**：`inspect_allegro_environment` 返回 `connected=false`
  （或 `pipeline` 报 `CHANNEL_NOT_READY`）时，如实报"Allegro 未连接（SkillBridge 7777 不通）"并停；
  禁止盲调业务工具后把超时误读成"工具挂了"、更禁止据此跳过步骤。
  读取图类工具不需要通道（PDF/.dra 解析不走 Allegro）。

★ **S5 改读图策略前先量分（禁止凭手感调 dpi / 换模型 / 改提取提示词）**：
凡是要改「读图策略 / dpi / 模型 / 提取提示词」之前，**先跑**
`python eval/suites/spec_reading/check_spec_reading.py`（可加 `--with-locate`），
并把结果与 `eval/baselines/spec_reading.json` 对比；**掉分就是回退，不许上线**。
调完再跑一次，分数须 ≥ 基线（当前 92.5%）。这条是"感觉更清楚了"式改动的唯一刹车。

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
| **`folder_preflight`** | ★ **第 1 步：目标消歧 + 缺料判定 + 噪声剔除**（S1/S2 由它保证） | `folder_path`, `recursive`（批量） |
| `list_folder_files` | 列出 PDF / .dra / .pad | `folder_path` |
| `list_subfolders` | 列出父文件夹的子文件夹（批量模式） | `parent_path` |
| `locate_land_pattern_page` | 定位 land pattern 页 | `pdf_file_path` |
| `render_pdf_page` | 渲染 PDF 页为图片（客户端不支持 resource 时用；**固定 150 dpi 整页**） | `pdf_file_path`, `page` |
| `pdf_probe` | ★ 开跑前判 PDF 形态（CAD 矢量 / 印刷 / 扫描件），决定走哪条读法 | `pdf_file_path` |
| `render_pdf_region` | ★ **区域裁剪 + 高 dpi 重渲染** —— 读尺寸数字的主力（缩视野、不缩密度） | `pdf_file_path`, `page`, `rect`, `dpi`（默认 600，上限 1200） |
| `measure_region` | ★ 矢量量距（图元包围盒），与读数做**交叉校验**；非主读数 | `pdf_file_path`, `page`, `rect` |
| `validate_land_pattern_json` | 校验 AI 输出的 JSON | `raw_json` |
| `read_full_footprint` | 提取 .dra 原始数据（pins/layers）供语义检查 | `dra_file_path` |
| **`check_footprint_by_rules`** | **跑 6 大项数值检查 + 存档 + 渲染 markdown** | `dra_file_path`, `theoretical_payload`, `semantic_result`, `save_report`, `compact`（默认 True，精简返回）, `output_dir`（**默认不传**：报告自动落 `<封装目录>/check_log/`，2026-10-16 约定） |

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

本技能与 `allegro-skillbridge-ops` 共用同一个 MCP 服务 `layout-forge-mcp`（工具全集见 `docs/tools-catalog.md`）。
已经注册过就不用再配。**换机器 / 给同事**时的 JSON 写法、`command` 必须指向 `.venv` 的理由、
`.venv` 不存在时怎么重建，见 `references/client-config.md`。

## 维护提示

每次改了 MCP 代码后：
1. **重连 MCP / 重启客户端** —— 服务进程缓存启动时的代码，不重连不生效
   （本项目 `mcp_server/config.py` **没有** `MCP_VERSION` 字段，无法靠版本号判断）
   ★ **只有改 `services/`、`mcp_server/` 才需要重连**；改 `knowledge/`
   （rules/checker_rules.json、specs、cases）是**每次调用现读盘**，立即生效
2. 判据：重连后工具总数应与本机源码的 `@mcp.tool` 定义数一致（部署冒烟 D2 已同源比较，不再有硬编码期望值）；或直接调一个新加的工具（如 `find_case` / `get_run_stats`）
   看是否报"未知工具"
3. 改动较大时跑两套离线自检：`Tools/selfcheck_geometry.py`（239 项）+
   `Tools/selfcheck_skill_syntax.py`（222 项），**只能用项目 `.venv`**
4. ★ **加了 / 删了 / 改了 MCP 工具或参数**：同轮改本 SKILL 的工具数、
   `allegro-skillbridge-ops/references/mcp-tools.md`、`Tools/deploy/setup_agent.py::EXPECTED_TOOLS`，
   再跑 `python eval/suites/tool_contract/check_tool_contract.py --update` 冻结基线 ——
   漏任一步，工具契约断言或部署冒烟会红（这就是防"改了没人知道"的闸门）
5. ★ **动了目录结构 / 新增目录**：跑 `python eval/suites/architecture/check_architecture.py`（ERROR 必须为 0）
6. ★ **改本 SKILL 后必须打齐三处落点，否则模型读到的还是旧版**（静默回退）：
   `Tools/deploy/skills/` 是**唯一真源**，但本机有**三个** junction 落点
   （`~/.workbuddy/skills`、`~/.skills-manager/skills`、`~/.dsh/skills`）——
   直接改用户目录那份会被下次部署覆盖回去。正确做法：
   ```
   python Tools/deploy/setup_agent.py --sync-skills     # 写真身 + 记 .deploy_manifest.json
   python Tools/deploy/setup_agent.py --verify-skills   # 必须全部 MATCH，否则 exit 1
   ```
   并用 `Tools/deploy/setup_agent.py` 的解释器要求（工作区外、有依赖）执行。
7. ★ **改完同步头部三声明**：`mcp_min_commit`（当前 `a5bd61f`）、`verified_with`、
   `eval_baseline`。它们不是装饰：`--verify-skills` 会软校验（缺了只 WARN），
   部署漂移时靠它判断"这份 SKILL 是配哪个 commit 验过的"。
8. ★ **"AI 待记"条数 = 4**（S 系：S1 歧义 / S2 只看本次 / S3 不泄漏推理 / S4 按失败码处置；
   S5 是"改读图策略前先量分"的一个入口条件，不单独计）。
   ★ 与铁律第 9/10 条的关系：那两条（不输出过程性文字 / 不改写报告）**就是 S3 与
   「报告不得二次加工」的同一句话**，只是历史位置在铁律区，**不重复计数**（否则同一条规矩数两遍）。
   ★ **加规则前先问：能不能下沉成工具字段**（像 `ambiguous` / `noise_files` / `CHANNEL_NOT_READY`）？
   能下沉就不许写进 SKILL —— 这是 Phase 3 验收硬指标「SKILL 待记条数 ≤5」的口径：
   按去重后的实质条目算 **4 条 ≤ 5 ✓**（含铁律区重复表述则字面为 6，属同一批规矩的重复写法）。
9. ★ **正文与 `references/` 的分工**（2026-10-23 拆分）：正文留「每次都用的工作流 + 违反了也看不出来的判据」，
   `references/` 放「按症状查的细节」（THT padstack 层清单 / 读图规则 / 四个易误判口径 /
   报告示例 / 模式 C 四层漏斗 / 客户端配置）。两条纪律：
   - **新增一条硬规则时，先判断它属于"每次都用"还是"偶尔查"** —— 后者进 references，
     并在上方索引表和触发处都写指针；否则正文会重新膨胀回 800+ 行。
   - **`references/` 里的数值和命令与代码同源**（如 `conventions.json`、`checker_rules.json`）：
     改代码导致口径变化时，同轮改对应 reference，否则模型按旧口径办事而没人发现。
     `--verify-skills` 只比 hash 不读语义，救不了这种「三处一致但都过期」。