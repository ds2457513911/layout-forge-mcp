# MCP 工具层（`layout-forge-mcp`）

> 收录：2026-10-09 启用 MCP 后新增的内容（SKILL.md 原 §3.1 / §3.2 / §3.4 的详细版）
>
> ★ 什么时候读这份：**要动手建封装 / 要归档 / 怀疑 MCP 工具与既有规则打架** 时才读。
> 平时 SKILL.md 第 3 节那几行够用。

## 这个文件是干什么的

`layout-forge-mcp` 是**服务层**（fastmcp / stdio），本技能是**知识层**。
两者不是替代关系：MCP 负责"谁来调 Allegro"，本技能负责"调的时候别踩哪些坑"。

本文件记三件事：走 MCP 建封装的完整工具链、归档怎么落位、以及
**MCP 工具与既有 SKILL 规则的已知差异**（这块最容易踩，且会随代码变动）。

## 1. 建封装的完整工具链（★ 2026-10-14：新增 `folder_preflight` / `get_run_stats`；本节只讲调用顺序，工具全表见 `docs/tools-catalog.md`）

```
list_specs() / find_spec(...)          ← ★ 先查 spec 库，命 exact 就零读图（见 spec-library.md）
inspect_allegro_environment()          ← 先探，Allegro 不在会返回规范错误而非崩溃
# ── 读规格书：三组工具，不是一条链（读数守 SKILL.md 第 3 步的六条不变式）──
#   定位  pdf_probe → locate_land_pattern_page → render_pdf_contact_sheet（扫描件 / 失配时）
#         render_pdf_page 与 MCP resource 的整页图只定位，不读数
#   读数  render_pdf_region（★ 主力，缩视野不缩密度）/ render_focus_regions（概览 + 焦点区）
#   校验  extract_page_numbers · reconstruct_pad_array · measure_region → AI 读图
#         → validate_land_pattern_json 守门  ※参数是 str 不是 dict
#   完整工具表见 docs/tools-catalog.md，本文件不抄。
solve_footprint_geometry(...)          → 算几何（焊盘/本体/丝印/位号）
new_symbol_drawing(target_name)        → 建图纸
create_padstack(name, layers, drill)   → 建焊盘
add_footprint_pin(padstack_name, number, x, y)
add_footprint_outline(...) / add_footprint_text(...)
change_drawing_extents(...)            → ★ 必须先扩，否则图元被静默丢弃（见 extents.md）
frame_drawing_view(save=True)
save_footprint_drawing()
export_psm(symbol_name)                → 出 .psm
read_full_footprint(dra) / check_footprint_by_rules(dra)   → 回读核验
save_spec(name, spec_json, family, source_pdf, notes)      → ★ 建完存档（notes 写踩过的坑）
```

**spec 库四件套**（`save_spec` / `load_spec` / `find_spec` / `list_specs`）：
真源在 MCP 项目 `knowledge\specs\*.json`，用法与判据见 **`spec-library.md`** ——
一句话：**建之前先查（能省读图），建之后必存（下次才有得省）**。

想一次成型就调 `build_footprint_from_params(padstack, pins, silk, body,
place_bound, refdes, ..., assembly)`（内含 auto_extent + frame_view）。

★ **`assembly` 参数（2026-10-12 加）**：把 `solve_footprint_geometry` 返回里的
`assembly` 字段（本体矩形 + 1 脚三角标）**原样**传进来，执行器逐项画到
PACKAGE GEOMETRY/ASSEMBLY_TOP。**不要**手工改传 `body` 字段 —— 那会丢掉
1 脚三角标，规则检查"Assembly 有 1 脚标识"必 FAIL（FPC/LYDPJ 两次应验，
当时只能 CLI 补画）。`geometry_solver.to_build_args(solve(...))` 已默认携带。

**★ 别越过服务层**：建封装不直接跑 `Tools/e2e_*.py`。
只有启停 / 离线自检 / 归档 / 收尾这四件走 CLI（理由见 SKILL.md §3.3）。

## 2. 归档落位（用户 2026-10-09 口径）

**锚点 = 规格书所在目录**，在其下建**与符号同名的文件夹**，把生成件放进去。
`install_shadow_library` 的落点是 `lib_root/<pn>/`，**传对参数即符合口径**：

```
install_shadow_library(
    symbol_name = "<符号名>",
    pn          = "<符号名>",          # 子目录名；不传则用 symbol_name
    lib_root    = "<规格书所在目录>",   # ★ 关键：不传会落到 Data\footprint\ 下
    overwrite   = False,               # 默认不覆盖，冲突进 collisions
    dry_run     = True,                # 先看会装什么，一个字节都不动
)
```

它**还做不到的两件事**（需手工 / CLI 补）：

1. 规格书 PDF **移入**该目录；
2. 写 `README.md`（文件清单 + md5 + 校验记录）。

详见 `compile-psm.md` 第十三节（§13.2 禁区、§13.4 MCP 归档路径）。

## 3. ★ MCP 工具与既有规则的已知差异（2026-10-09 逐项实测）

| 工具 | 差异 / 缺口 | 怎么应对 |
|--|--|--|
| `solve_footprint_geometry` | ✅ **2026-10-09 已补齐**：`body_center` / `place_bound_expand_mm` / `pins_override` / `pin_names` / `pin1_mark_axis` / `extra_padstacks` / `symbol_name` / `symbol_name_hint` 均已进签名；另加 `extra_spec` 逃生舱（dict 原样透传，显式参数优先）。已用 CSTJ-421 对拍：与 solver 直连结果**逐字段一致** | 连接器类件现在可走 MCP 直算。solver 以后新加字段，用 `extra_spec` 传即可，**不用改工具签名** |
| `add_footprint_pin` / `build_footprint_from_params` | ✅ **2026-10-09 已修（共享层 `skill_snippets.build_pin_text`）**：原实现用 `make_axlPinText` 简化写法 `?text "N"`，**数字串被 Allegro 当成字块号** → 引脚号字号随脚号递增（CSTJ-421 实测 1→block1 … 6→block6）。现强制结构体形式，默认 `textBlock "1"` + `justify "center"`（人工件口径） | 修复后 MCP server 进程若还缓存旧代码，需重启客户端连接器才生效；CLI 即时生效。回读校验走 extracta（`design->text` 看不到引脚号），标准：6 脚字块一致且与 #REF 同字块 |
| `install_shadow_library` | `lib_root` 默认 `Data\footprint\` → 落 `<料号>/` 根目录，**不符合归档口径**。✅ 2026-10-12 已修误卷：自动扫到的 .pad 现按 `.dra` 二进制里实际引用的 padstack 名过滤（XTAL/FPC 无关残留不再被卷入），`dry_run` 会输出核对提醒 | 显式传 `lib_root=<规格书所在目录>`、`pn=<符号名>`（见上面第 2 节）；`pad_names=` 仍是最可靠的显式指定方式 |
| `new_symbol_drawing` | 相对名**默认落 `Tools/test/`**；`overwrite` 默认 **True**（会 `os.remove` 目标） | 目标名别指向人工件 / 金标准；最终落点靠第 2 节归档 |
| `change_drawing_extents` | **docstring 是旧口径**（仍写"无条件并集"）；底层已修为「精确设定失败才并集」，行为正确。实测 extents 仍是硬边界：±2 范围内画 ±10 矩形 → 返回 nil | 信行为不信 docstring；**先扩范围再画图元**，与 CLI 完全一样 |
| `check_footprint_by_rules` | `save_report` 默认 True → 会写报告文件到磁盘；★ 2026-10-16 起报告默认落 `<封装目录>/check_log/`（可用 `output_dir` 覆盖，一般别传） | 只要结论时传 `save_report=False` |
| `export_psm` / `frame_drawing_view` | 与 SKILL 规则一致（CWD 断言 + 快照证据 / frame 须在 save 前） | 无冲突 |

**★ 一句话**：Allegro 侧的坑（extents 静默丢弃、弹框禁令、Edit Check 门槛、
通道 15 帧滞后）**与调用方式无关，MCP 下一条不少**；差异只出在
**参数透传**和**默认落点**这两类。

## 关键约定与坑

1. **工具函数定义在 `mcp_server/tools.py` 的 `register(mcp)` 内部**，
   模块级拿不到 `tools.solve_footprint_geometry`。要离线调它验证，得
   `FastMCP("x")` → `register()` → `asyncio.run(m.get_tool(name)).fn`。

2. **`solve_footprint_geometry` 是「编译器」，`build_footprint_from_params` 是「执行器」**：
   前者纯离线、不连 Allegro、不写文件，产出给 AI 复核；后者才动 Allegro。
   两者之间用 `geometry_solver.to_build_args(solve(...))` 转。

3. **改了 `tools.py` 之后**：离线自检必跑 `Tools/selfcheck_geometry.py`（239）
   与 `Tools/selfcheck_skill_syntax.py`（222），**只能用项目 `.venv`**。

4. **差异表要跟着代码更新**。本表 2026-10-09 实测；下次改 MCP 工具签名后
   要回来同步，否则 SKILL 会按旧参数指导出新错误。

## 案例库（2026-10-12 新增，Tool 29~31）

检测**前**先 `find_case(spec_json=...)`：同封装/同族案例自动召回（复用 spec 几何指纹，
exact/family/keyword 三档）——把案例里的 `lessons` / `findings` 作为预警上下文，
解读报告前先对照历史裁定，避免把已知误报再报一遍吓工程师。

检查**后**（人工确认完哪条是真问题、哪条是误报）`save_case(...)` 存档：
findings 每条 {"rule_id","issue","verdict":"false_positive|true_positive|needs_human","reason"}，
lessons 写教训。**裁定一次、全族受益**。真源 `knowledge/cases/`。

规则阈值外置 `knowledge/rules/checker_rules.json`：改数字立即生效（不用重启 MCP），
判定逻辑仍在 `rule_checker.py`。

## 读规格书的工具（2026-10-13 起，2026-11-19 扩到六件）

建封装的真实成本在「读规格书提参数」，这组工具把这一步从"靠眼力"变成"有梯子"。

| 工具 | 干什么 | 什么时候用 |
|--|--|--|
| `pdf_probe(pdf_file_path)` | 判 PDF 形态：`pages / bitmaps / text_chars / drawings` → `form` + `read_hint` | **每次读规格书的第一步**（零成本）。判错读法的代价不对称：对 CAD 图做文本提取、对扫描件提文本层都是纯白工 |
| `render_pdf_region(pdf_file_path, page, rect, dpi=600)` | 按 PDF 坐标裁一块区域、高 dpi 重渲染 | **读尺寸数字的主力**。缩的是**视野**不是**像素密度**（拼版把单页稀释到 72 dpi，本工具保住 600–1200 dpi） |
| `measure_region(pdf_file_path, page, rect)` | 量区域内矢量图元的包围盒（pt） | **交叉校验**：几何比值 vs 标注比值对拍。**非主读数**（焊盘与器件本体轮廓常粘连） |
| `extract_page_numbers(pdf_file_path, page, max_numbers=400)` | 抽文本层里「像尺寸」的数字，**带 bbox** | 读数前的**线索表**：只收不筛（除年份），归属交给看图那一步。★ 扫描件返回空表是正常形态 |
| `render_focus_regions(pdf_file_path, page, dpi=900)` | 概览图 + 数字密集焦点区（必要时 2×2 切块） | 不想手工算 rect / dpi 时用。**分辨率配额**：概览压到 1600 px，焦点区保住 ≥600 dpi，达不到就缩范围 |
| `reconstruct_pad_array(pdf_file_path, page)` | 从矢量图元还原焊盘阵列（pad / pitch / 行列 / 本体） | **零模型成本的交叉校验**：输出是候选（带 pt 与来源），pt→mm 靠图上数字标定；扫描件无矢量图元会如实告警 |

### ★ 坐标系陷阱（2026-10-13 实测踩中）

`page.get_pixmap(clip=...)` 吃的是**显示坐标**（含 rotation，与 `page.rect` 同系），
而 `page.get_drawings()` 返回的是**未旋转坐标**（与 `mediabox` 同系）。
旋转页（LYDPJ 样本 rotation=270：显示 842×595 vs mediabox 595×842）不做转换，
几何量距会与任何图元都不相交（实测 `n_in_rect = 0`）。
`measure_region` 内部已用 `derotation_matrix` / `rotation_matrix` 完成转换，
对外统一用**显示坐标**（与 `rect` 参数、`render_pdf_region` 同系）。

### 形态分类是启发式

实测 17 份真实规格书 → `printed` 12 / `cad_vector` 3 / `scan` 1（另有 1 份重复文件）。
阈值会误判（如 TF 卡座仅 325 字符被判 `printed`），所以 `form` 只作"先试哪条路"的建议 ——
区域裁剪对任何 PDF 都可用，是通用兜底，**误判只影响选路、不影响结果**。

### 动工约束（防副作用，务必遵守）

以后改这三个工具、或再加 PDF 工具时：

1. **不要动** `pdf_renderer.DEFAULT_PYMUPDF_DPI`（150）—— 改它会波及所有既有整页渲染与缓存；
2. 写盘必须走 `config.RENDER_OUTPUT_ROOT` 绝对路径；区域图落 `<stem>/regions/`，
   与既有 `<stem>/pymupdf_pages/` 分开，**避免缓存互相覆盖**；
3. 新工具**必须 try/except 返回 `ok:false`、绝不 raise** ——
   SKILL 规则「tool 报错即停止检查」会把一个 probe 的 bug 放大成"所有封装都检查不了"。

## 编排与统计（2026-10-14 新增）

> ★ **两个 "preflight" 不是一回事，别混**（2026-10-14 改名就是为这个）：
> - **`folder_preflight`** = MCP 工具，**开工前**查目录里有没有歧义（下表第 1 行）。
> - **`scripts/preflight_check.py`** = 本地脚本，**收尾后**复查端口/进程/孤儿锁/项目根污染（见 `scripts/README.md`）。
>
> 本工具 2026-10-14 之前叫 `preflight_check`，与上面那个脚本同名 —— 现已改名为 `folder_preflight`。
> 若在旧对话/旧文档里看到「跑 preflight_check」，先分清是**工具**还是**脚本**。

| 工具 | 干什么 | 什么时候用 |
|--|--|--|
| `folder_preflight(folder_path, recursive=False)` | 目录**输入预检**：数出这个目录里有几份 PDF、几颗 `.dra`，返回 `verdict` / `action` / `hint`；**噪声文件先剔除再判**并单独列在 `noise_files`（`AUTOSAVE.dra` / `~$*` / `*.bak/.tmp/.lck` / **`checklist_report_*.json` 旧报告** / **`allegro.jrl`·`master.tag`·`*.log` 运行痕迹**） | **拿到一个目录后、动手前的第一步**（尤其用户只说"检查一下这个料号"没给具体文件时）。`action="confirm"` = 先问用户查哪一份，别自己挑 |
| `get_run_stats(days=7)` | 运行统计：工具调用次数 / 失败 / 降级 / p50·p95 耗时；规则报告的 PASS·WARN·FAIL 排行；知识库规模；**缺口清单 `gaps[]`** | 想知道"最近哪条规则老挂""这个改动让哪步变慢了"时；也是**遥测是否真的在记**的自检入口 |

### 为什么需要 `folder_preflight`

S1 铁律要求「目录里有多个 PDF / `.dra` 时必须先与用户确认」，但底层
`services/allegro_service.py::list_folder_files` 的返回里只有**一个** `pdf` 字段 ——
多份 PDF 在返回值里根本不可见，AI 想遵守也无从遵守（歧义被静默吞掉）。
本工具把这件事**变成可见字段**。★ 它只**上报**歧义，不代替用户决定，也**不拒绝执行**：
批量模式 B（每个子目录各一对 PDF/`.dra`）下多文件完全合法。

### 为什么噪声剔除是硬需求

金标准库 `Data/footprint` 曾有 5 个子目录躺着 `AUTOSAVE.dra`（Allegro 自动保存产物），
而 `AUTOSAVE` 按名排序**排在 `nb_*` 前面** —— 任何「取目录里第一个 `.dra`」的逻辑都会先取到垃圾。
★ 那 5 个已于 2026-10-14 隔离到 `.trash/`（**绝不 `os.remove`**），但**剔除逻辑永久保留**：
只要还有人在库目录里直接开 Allegro 画图，`AUTOSAVE.dra` 就会再长出来 —— 症状清了，防线不能撤。
`noise_files` 非空时，先把噪声移出该目录再往下走。

★ 2026-10-15 扩了噪声名单，多认两类（因为库里真的躺着，而且它们会**误导结论**）：

| 类 | 例子 | 为什么算噪声 |
|--|--|--|
| **可能被误当证据** | `checklist_report_20260917_*.json` | 那是**上一次**检查的报告。S2 明文：本次结论只能来自本次运行 —— 拿旧报告当结果就是造假 |
| **运行痕迹** | `allegro.jrl` / `allegro.jrl,1` / `master.tag` / `padstack_editor.jrl` / `*.log` / `signoise.run/` | 不会冒充产物，但证明**有人把库目录当工作目录开过**（违反 `Data/README.md` 的既有约定）—— 见到就要警觉，别在这个目录里直接开工 |

扩展后实测：三个库共 **145 个**噪声文件（110 `*.log` + 9 个历史报告 + 26 个运行痕迹），
其中 `Data/footprint/3S48000163/` 一个目录就躺着 7 份 2026-09-17 的历史报告。
**这就是 `geometry_regression` 的 G4 转红的原因** —— 红的是现实，不是判据；★ 2026-10-15 当日已把这 145 个全部隔离进 `layout-forge-mcp/.trash/`（`_quarantine_file()`，可搬回），G4 复绿，**名单一个字没放宽**。

### ★ 失败码契约（2026-10-15 Phase 3：`orchestration/pipeline.py`）

判定"该不该继续"的**稳定契约是失败码，不是人话**。SKILL 侧按下表分支（源码常量在
`orchestration/pipeline.py`，行为由 `eval/suites/behavior` 的 B4/B5 夹具钉住）：

| 失败码 | 触发场合 | 正确动作 |
|---|---|---|
| `CHANNEL_NOT_READY` | `inspect_allegro_environment` 返回 `connected=false`（`error` 必非空）；`pipeline` 的 `env-check` 未过 | 如实报「Allegro 未连接（SkillBridge 7777 不通）」，停掉触 Allegro 的工具；**只读 PDF/.dra 的工具不受影响**（不走通道） |
| `AMBIGUOUS_INPUT` | 目录里多份候选（`folder_preflight.ambiguous=true` / `verdict=ambiguous_*`） | 把候选列给用户确认；**不许自己挑、不许"先跑一个试试"** |
| `NO_CANDIDATES` | `verdict=empty/missing_*`，或目录里只有噪声 | 缺料，停；提示用户补 PDF 或 `.dra` |
| `UNITS_UNKNOWN` | `.dra` 单位既非 mm 也非 mil | 别按 mm 猜；先让用户确认单位或用无名几何描述 |
| `NO_DATA` | 取数返回空结构 | 如实说「该步骤未执行」，**不补数据** |

★ 这五个码也决定了**动作**：`deny → stop` / `silent → confirm`（可继续但报告必须写明）/
`recoverable → proceed`；唯一的例外是 `G-ambiguous-src` 在 playbook 里显式写了
`on_fail_action: "confirm"` —— 「拿不准」的正确动作是**问用户**，不是拒绝执行。

### 遥测（`observability/`，2026-10-14 接线）

`mcp_server/middleware.py::TelemetryMiddleware` 包住**每一次**工具调用，落
`eval/runs/<date>.jsonl`：`ts / run_id / tool / ok / ms / error_code / args_keys / args_hash`。
★ **只记参数名与 hash，不记参数值**（避免把图纸路径、料号明文写进日志）。
`ok:false` 的业务失败也记（`error_source:"returned"`）—— 失败不再只存在于对话里。

