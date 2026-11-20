# services/ —— 生成侧核心（编译器 + 执行器 + 读取层）

> 最后更新：2026-11-20（票 #17 / ADR-0002：新增 `rule_engine.py` —— 规则库加载器 + 判定入口；2.2 阻焊判定改走 `knowledge/rules/` 新四类库，finding 带规则 id / 出处 / 支持件数 / 整库哈希，旧 `checker_rules.json` 对该条冻结只读。此前：2026-11-20（票 #16 / ADR-0002：新增 `padstack_derive.py` —— padstack 形态**决策**入口，从 `footprint_builder.build_footprint` 的 1a/1b 段搬出，与 `create_padstack` 的实例化彻底分开，判定口径一字未改）。此前：2026-11-19（新增 `channel_guard.py`：通道锁 + 建图租约，撞锁报 `CHANNEL_BUSY`；此前同一日新增 `pdf_reading.py` / `pdf_geometry.py` 与读数配额三件套 MCP 工具；`pdf_service.render_region` 的 dpi 改按单边 8192 反解。此前：2026-10-14 `pdf_locate.py` 重写为「文本 + 结构」双证据定位；`pdf_renderer.safe_stem()` 修尾空格坑；实测更正与 `layout-check-mcp` 的共享文件清单（只剩 4 件相同）。此前：Phase 0 登记 L3/L4/L5 目录常量、补齐漏登记的 4 个模块）

## 这个目录是干什么的

**规格书参数 → Allegro 封装（.dra / .psm）** 的全部业务逻辑都在这。
按职责分五层，**不要跨层混用**：

| 层 | 文件 | 职责 | 是否需要 Allegro |
|--|--|--|--|
| **求解层（编译器）** | `geometry_solver.py` | 参数 → 几何（padstack/pins/丝印/本体/PB/位号），纯函数、可单测 | ❌ 不需要 |
| **决策层（padstack 形态）** | `padstack_derive.py` | 决定「这颗 padstack 该长什么样」（层集 / 各层尺寸 / 命名 / 旋转），吃 dict 吐 dict | ❌ 不需要 |
| **执行层** | `footprint_builder.py` | 把算好的几何**在 Allegro 里真正建出来**，并发 SKILL 表达式 | ✅ 需要 |
| **读取层** | `footprint_extractor.py` → `extracta_reader.py` / `skillbridge_reader.py` | 读 `.dra` 回来做校验/对拍 | extracta ❌ / skillbridge ✅ |
| **代码生成** | `skill_snippets.py` | 只负责**拼 SKILL 表达式字符串**，不连 Allegro | ❌ 不需要 |

其余是支撑件（目录约定、CDSROOT 定位、PDF 解析、规则表、报告渲染、库落位）。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `paths.py` | ★ **目录约定的单一真源**（`PROJECT_ROOT` / `TOOLS_DIR` / `TEST_DIR` / `LOG_DIR` / `TRASH_DIR`；2026-10-14 增 `ORCHESTRATION_DIR` / `OBSERVABILITY_DIR` / `EVAL_DIR` / `RUNS_DIR` / `BASELINE_DIR`）+ 运行痕迹归集 `collect_run_logs()` | **几乎所有模块都 import 它**；改目录结构先改这里 |
| `geometry_solver.py` | ★ **尺寸求解器**。含 `resolve_dim()`（尺寸来源口径：图上标注→取标注值 / 查表→取 MAX）、`solve_padstack/pins/outlines/silkscreen/refdes`、`solve()` 总入口、`to_build_args()`、`SPEC_3S48000163` 金标准样例 | 纯函数，7 条 doctest；**改它必须同步 `Tools/selfcheck_geometry.py`** |
| `padstack_derive.py` | ★ **padstack 形态决策入口**（2026-11-20 票 #16 / ADR-0002 D5·D8）：`derive_padstacks(reading)` 吃一份 spec 形读数（mount / padstack / extra_padstacks / pins_override），吐纯数据计划（`padstack` / `extra_padstacks` / `pins_override` + `notes.conventions/tht_layers/errors`）；`ensure_tht_padstack_layers()` 通孔层兜底由本文件从执行器搬来。<br>★ 与 `create_padstack()` 的分工：**这里只决定形状，实例化归执行器** —— 拆开是为了让规则引擎（#17 起）能整体替换决策而不碰实例化 | 纯模块，登记在 `CORE_MODULES`（A3）：不 import 执行器 / SkillBridge / 通道，可离线调用；对拍基线 `eval/baselines/padstack_derive.json` + `eval/suites/geometry_regression/check_derive.py`（R5） |
| `rule_engine.py` | ★ **规则库加载器 + 判定入口**（2026-11-20 票 #17 / ADR-0002 D1–D3·D9）：`load_library()` 一次读 `knowledge/rules/` 的四类对象（form / parameter / rule / source，外加 `_severity.json`）并做引用完整性校验 —— 未知谓词、缺出处、参数/形态/严重度引用不存在、库里有未登记的 `*.json`，一律抛 `RuleLibraryError`（带 file/path/message，**不降级、不猜**）；`judge(lib, rule_id, subject, observed)` 出三态 finding（PASS / 违反=WARN / SKIP 且写明缺什么），finding 自带 rule_id / 出处 / 支持件数 / 整库哈希；`match_form()` 决定一条规则按哪个 form 取参数 | 纯模块，登记在 `CORE_MODULES`（A3）：零 Allegro、零网络；行为基线 `eval/baselines/rule_engine.json` + `eval/suites/geometry_regression/check_rule_engine.py`（R6）；环境变量 `LAYOUT_FORGE_RULES_DIR` 可整体替换规则库 |
| `footprint_builder.py` | ★ **执行器 + 顶层接口**。`new_symbol_drawing()`（含切 mm / 工作目录断言 / 污染校验）、`build_footprint()`、`export_psm()`（**磁盘证据制成功判据**）、`add_*()` 单步动作、`_quarantine_file()` 隔离。<br>★ `_compute_auto_extents()` / `_outline_xy_points()`：**绘图范围必须覆盖全部将画的几何**（引脚+丝印+本体+Place_Bound）—— 越界的图元会被 Allegro **静默丢弃**（返回 nil 不报错）。<br>★ `change_extents()`：**先精确设定，失败才并集**（空图纸的 `->bBox` 是"模板页框"不是内容框） | **最大文件**；在线建封装的唯一入口 |
| `skill_snippets.py` | SKILL 表达式构造器：`build_text` / `build_text_orientation` / `build_polyline` / `build_rect_polyline` / `build_change_units` / `build_extent_set` / `build_zoom_points` / `build_zoom_bbox` … | 只拼字符串；`Tools/selfcheck_skill_syntax.py` 对它做 212 项离线自检 |
| `skillbridge_writer.py` | SkillBridge 通道客户端：`AllegroSession`、`eval_skill()`、`units()`、`MIL_TO_MM` | ✅ 需要 Allegro 在跑；通道错位时看它 |
| `extracta_reader.py` | ★ **extracta 读取**（**不需要 Allegro**，~1.5s 读完一张图）。**4 路视图**：pins / pads / PACKAGE GEOMETRY / **REF DES** | `CLASS = "..."` 是**过滤器**，位号必须单独一路，否则**静默读不到** |
| `extracta_views/footprint_views.txt` | 上面 4 路视图的**定义文件**（一视图 → 一输出文件） | 删掉第 4 路 = 位号静默缺口回归（有自检守） |
| `skillbridge_reader.py` | SkillBridge 版读取（保底路径） | ✅ 需要 Allegro；与 extracta 输出结构**必须一致** |
| `footprint_extractor.py` | 读取**分发器**：extracta 优先 → SkillBridge 回退；`_extracta_gaps()` 显式上报缺口 | 缺口**不阻断**几何判据（缺位号回退也白搭） |
| `library_install.py` | ★ **影子库落位**：把产物按约定拷进 `..\Data\footprint\<PN>\`；`verify_colocation()` 同目录守卫；自动扫 .pad 时**按 .dra 实际引用过滤**（`_pad_referenced_by_dra`，2026-10-12 加，XTAL/FPC 误卷根治） | `overwrite` **默认 False**（库里可能是人工金标准！）；**不碰 padpath** |
| `rule_checker.py` | 19 项封装规则复核（从 layout-check 继承） | ★ 其余阈值的数字仍外置 `knowledge/rules/checker_rules.json`（改数字立即生效）；**2.2 阻焊已改走 `knowledge/rules/` 新四类库**（形态/参数/规则/出处，见 `rule_engine.py`），旧阈值文件对 2.2 冻结只读 |
| `case_library.py` | ★ **检测案例库**（2026-10-12 新增）：save/load/find/list_cases，指纹复用 spec_library | 检查前 `find_case` 同族预警；裁定一次全族受益 |
| `report_renderer.py` | 把检查结果渲染成报告 | 只读输入，不写 `.dra` |
| `cdsroot_locator.py` | 定位 Cadence `CDSROOT` / `extracta.exe` | extracta 与 Allegro 都要它 |
| `allegro_service.py` | Allegro 进程/环境相关小服务 | — |
| `pdf_locate.py` / `pdf_renderer.py` / `pdf_service.py` | 规格书 PDF 定位 / 渲染 / 解析入口。★ `pdf_locate.py` **2026-10-14 重写为「文本 + 结构」双证据定位**（`analyze_pdf` 逐页统计 text_chars/drawings/dim_signals → 纯函数 `_decide()` 出结论；① 强关键词唯一领先 → `primary`；② 无文本层矢量图纸页 → `drawing_only`；③ 稀薄文本图纸页 → `thin_drawing`；④ 并列即 `ambiguous` + `candidates`，**宁可不给页码也不瞎猜**），新增 `confidence`/`needs_visual`/`candidates`/`page_stats`/`reason`。`pdf_renderer.py` 的 `safe_stem()` 防「PDF 名的 stem 以空格结尾 → Windows 吃尾空格 → `mkdir` 抛 `FileNotFoundError`」。★ `pdf_service.py` 另有**读规格书三件套**（2026-10-13）：`probe_pdf`（判形态）/ `render_region`（区域高 dpi 重渲染）/ `measure_region`（矢量量距交叉校验） | 给"读图纸"阶段用；三件套均为**纯读**、try/except 返回 `ok:false`、绝不 raise；`needs_visual=True` 时读数值前必须渲染确认（判据 S8 会查这件事） |
| `pdf_reading.py` / `pdf_geometry.py` | ★ **读数配额两模块**（2026-11-19 从 layout-web 吸收）：`pdf_reading.extract_numbers`（文本层 span 抽「像尺寸」数字 + bbox，上限 400）/ `cluster_rect`（12×16 网格取最密 3×3，标注区定位）/ `render_focus_regions`（概览压到 1600px + 焦点区强制 ≥600dpi，达不到按 2×2 切块**不降 dpi**，符合单边 8192 上限）；`pdf_geometry.analyze_page`（从矢量矩形重建焊盘阵列：rows/cols/pitch/pad 尺寸 + mm 标定，零模型成本） | 纯读、try/except 返回 `ok:false`；对应 MCP 工具 `extract_page_numbers` / `render_focus_regions` / `reconstruct_pad_array`；dpi 太高会被模型压成 ~190dpi 等效反而不准，所以配额优先于分辨率 |
| `convention_engine.py` | ★ **构造约定引擎**（2026-10-13 第 0 层落地）：`check_*` 与 `create` 共用同一份「封装应该长什么样」的知识 —— `load_conventions` / `apply_conventions` / `derive_th_name` / `derive_th_oblong_padstack` / `check_pin_conventions` / `check_padstack_naming` / `verify_built_footprint` | 约定数据在 `knowledge/conventions.json`；`Tools/mine_conventions.py` 收割、`Tools/selfcheck_conventions.py` 守「学到 → 消费」链路 |
| `spec_library.py` | ★ **spec 库**（每颗料解析成果的沉淀处）：`fingerprint` 指纹复用 / `save_spec` / `load_spec` / `list_specs` / `find_spec` / `rebuild_index` | 落 `knowledge/specs/`；**同封装零读图复用 = 读图成本的主要节省点** |
| `validation.py` | Land pattern JSON schema 校验：`validate_land_pattern` → `(is_valid, errors, normalized)` | 输入是 AI 读图提取的 dict（下游兜底）；★ 文件头 docstring 仍写 `shared/validation.py` —— 2026-10-12 `shared/` 并入 `services/` 前的痕迹 |
| `json_utils.py` | JSON 清洗与数值转换：`extract_json_body`（从 LLM/视觉模型的自由文本里抠出合法 JSON）、`coerce_numeric` | 读图链路的下游清洗；同样保留 `shared/json_utils.py` 的旧 docstring |
| `channel_guard.py` | ★ **通道并发守卫**（2026-11-19，票 #10 / D4 定案）：`ChannelGuard`（进程内 `RLock` 可重入 + 跨进程 `msvcrt.locking` 文件锁，取不到 30s 后抛 `ChannelBusyError` 并附持有人 pid/任务/开始时间）、`TaskLease`（建图长任务租约，撞上**只报告不排队**）、`read_holder()` / `describe_holder()`、错误码 `CHANNEL_BUSY` / `CHANNEL_NOT_READY` | 纯标准库 + `services.paths`，**不依赖 Allegro 与 mcp_server**；`AllegroSession.open/close` 已内置这把锁；锁文件与租约落 `Tools/log/` |
| `__init__.py` | 包标记 | — |

## 关键约定与坑

1. **`paths.py` 是唯一真源** —— 别在别的文件里硬编码 `Tools/test` 这类路径。
2. **与 `layout-check-mcp` 的共享文件 —— 2026-10-14 实测，两边只同 4 个了**（此前的「10 件逐字节相同」说法已作废）：
   - **仍逐字节相同（4）**：`pdf_locate.py`、`cdsroot_locator.py`、`allegro_service.py`、`report_renderer.py`
     —— 改这 4 个仍然要**两仓同轮改**（`pdf_locate.py` 2026-10-14 的重写已同步镜像，两边哈希一致）。
   - **已分叉（6）**：`pdf_service.py`、`pdf_renderer.py`、`footprint_extractor.py`、`extracta_reader.py`、
     `skillbridge_reader.py`、`rule_checker.py` —— 数量级差异（如 `rule_checker.py` 60.9 KB vs 38.2 KB），
     说明 check 仓是**旧版快照**；改这几个**只看本仓**，别假设两边还同步。
   （2026-09-29 起 `extracta_reader.py` / `footprint_extractor.py` /
   `extracta_views/footprint_views.txt` 三处**已分叉** —— check 没有位号那一路。）
3. **绝不 `os.remove` 产物** —— 覆盖保护一律走 `.trash/` 隔离（`_quarantine_file`）。
   Allegro 碰到已存在文件会弹「File Exists」确认框 = 通道自杀。
4. **`axlCompileSymbol` 恒返回 nil** —— 成功判据看**磁盘 `.psm` 快照差异**，别看返回值。
5. **`axlSaveDesign` / `create symbol` 写的是 Allegro 工作目录**，不是 `.dra` 所在目录。
   `getWorkingDir()` 读、`axlShell("cd <dir>")` 改（唯一方法）。
6. **位号默认口径**（2026-09-30）：`#REF` @ `REF DES/ASSEMBLY_TOP`、居中于本体、LEFT 对齐。
7. **★ PDF 坐标系陷阱**（2026-10-13 实测）：`page.get_pixmap(clip=...)` 吃**显示坐标**
   （含 rotation，与 `page.rect` 同系），而 `page.get_drawings()` 返回**未旋转坐标**
   （与 `mediabox` 同系）。旋转页（LYDPJ：rotation=270，显示 842×595 vs mediabox 595×842）
   不做转换 → 几何量距与任何图元都不相交（实测 `n_in_rect = 0`）。
   `measure_region` 已用 `derotation_matrix` / `rotation_matrix` 转换，**对外统一显示坐标**。
8. **★ 不要动 `pdf_renderer.DEFAULT_PYMUPDF_DPI`（150）** —— 它是 `render_pdf_page` /
   `datasheet://` resource 的整页渲染默认值，改它会波及所有既有渲染与缓存。
   要高 dpi 请用 `render_region(dpi=...)`（参数级，上限 1200）。

## 相关目录

- 对外门面：`../mcp_server/`
- 离线自检与在线对拍脚本：`../Tools/`
- 人工模板库（金标准）：`../../Data/footprint/`
