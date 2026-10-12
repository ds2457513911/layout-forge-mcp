# services/ —— 生成侧核心（编译器 + 执行器 + 读取层）

> 最后更新：2026-09-30

## 这个目录是干什么的

**规格书参数 → Allegro 封装（.dra / .psm）** 的全部业务逻辑都在这。
按职责分四层，**不要跨层混用**：

| 层 | 文件 | 职责 | 是否需要 Allegro |
|--|--|--|--|
| **求解层（编译器）** | `geometry_solver.py` | 参数 → 几何（padstack/pins/丝印/本体/PB/位号），纯函数、可单测 | ❌ 不需要 |
| **执行层** | `footprint_builder.py` | 把算好的几何**在 Allegro 里真正建出来**，并发 SKILL 表达式 | ✅ 需要 |
| **读取层** | `footprint_extractor.py` → `extracta_reader.py` / `skillbridge_reader.py` | 读 `.dra` 回来做校验/对拍 | extracta ❌ / skillbridge ✅ |
| **代码生成** | `skill_snippets.py` | 只负责**拼 SKILL 表达式字符串**，不连 Allegro | ❌ 不需要 |

其余是支撑件（目录约定、CDSROOT 定位、PDF 解析、规则表、报告渲染、库落位）。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `paths.py` | ★ **目录约定的单一真源**（`PROJECT_ROOT` / `TOOLS_DIR` / `TEST_DIR` / `LOG_DIR` / `TRASH_DIR`）+ 运行痕迹归集 `collect_run_logs()` | **几乎所有模块都 import 它**；改目录结构先改这里 |
| `geometry_solver.py` | ★ **尺寸求解器**。含 `resolve_dim()`（尺寸来源口径：图上标注→取标注值 / 查表→取 MAX）、`solve_padstack/pins/outlines/silkscreen/refdes`、`solve()` 总入口、`to_build_args()`、`SPEC_3S48000163` 金标准样例 | 纯函数，7 条 doctest；**改它必须同步 `Tools/selfcheck_geometry.py`** |
| `footprint_builder.py` | ★ **执行器 + 顶层接口**。`new_symbol_drawing()`（含切 mm / 工作目录断言 / 污染校验）、`build_footprint()`、`export_psm()`（**磁盘证据制成功判据**）、`add_*()` 单步动作、`_quarantine_file()` 隔离。<br>★ `_compute_auto_extents()` / `_outline_xy_points()`：**绘图范围必须覆盖全部将画的几何**（引脚+丝印+本体+Place_Bound）—— 越界的图元会被 Allegro **静默丢弃**（返回 nil 不报错）。<br>★ `change_extents()`：**先精确设定，失败才并集**（空图纸的 `->bBox` 是"模板页框"不是内容框） | **最大文件**；在线建封装的唯一入口 |
| `skill_snippets.py` | SKILL 表达式构造器：`build_text` / `build_text_orientation` / `build_polyline` / `build_rect_polyline` / `build_change_units` / `build_extent_set` / `build_zoom_points` / `build_zoom_bbox` … | 只拼字符串；`Tools/selfcheck_skill_syntax.py` 对它做 212 项离线自检 |
| `skillbridge_writer.py` | SkillBridge 通道客户端：`AllegroSession`、`eval_skill()`、`units()`、`MIL_TO_MM` | ✅ 需要 Allegro 在跑；通道错位时看它 |
| `extracta_reader.py` | ★ **extracta 读取**（**不需要 Allegro**，~1.5s 读完一张图）。**4 路视图**：pins / pads / PACKAGE GEOMETRY / **REF DES** | `CLASS = "..."` 是**过滤器**，位号必须单独一路，否则**静默读不到** |
| `extracta_views/footprint_views.txt` | 上面 4 路视图的**定义文件**（一视图 → 一输出文件） | 删掉第 4 路 = 位号静默缺口回归（有自检守） |
| `skillbridge_reader.py` | SkillBridge 版读取（保底路径） | ✅ 需要 Allegro；与 extracta 输出结构**必须一致** |
| `footprint_extractor.py` | 读取**分发器**：extracta 优先 → SkillBridge 回退；`_extracta_gaps()` 显式上报缺口 | 缺口**不阻断**几何判据（缺位号回退也白搭） |
| `library_install.py` | ★ **影子库落位**：把产物按约定拷进 `..\Data\footprint\<PN>\`；`verify_colocation()` 同目录守卫；自动扫 .pad 时**按 .dra 实际引用过滤**（`_pad_referenced_by_dra`，2026-10-12 加，XTAL/FPC 误卷根治） | `overwrite` **默认 False**（库里可能是人工金标准！）；**不碰 padpath** |
| `rule_checker.py` | 19 项封装规则复核（从 layout-check 继承） | 与 `Data/checklist/PCB_Footprint建库规范.xlsx` 同源 |
| `report_renderer.py` | 把检查结果渲染成报告 | 只读输入，不写 `.dra` |
| `cdsroot_locator.py` | 定位 Cadence `CDSROOT` / `extracta.exe` | extracta 与 Allegro 都要它 |
| `allegro_service.py` | Allegro 进程/环境相关小服务 | — |
| `pdf_locate.py` / `pdf_renderer.py` / `pdf_service.py` | 规格书 PDF 定位 / 渲染 / 解析入口 | 给"读图纸"阶段用 |
| `__init__.py` | 包标记 | — |

## 关键约定与坑

1. **`paths.py` 是唯一真源** —— 别在别的文件里硬编码 `Tools/test` 这类路径。
2. **共享层与 `layout-check-mcp` 逐字节相同**（`footprint_extractor` /
   `extracta_reader` / `skillbridge_reader` / `cdsroot_locator` /
   `allegro_service` / `rule_checker` / `report_renderer` / 3 个 `pdf_*`）。
   **改这些文件时要意识到 check 项目那边会分叉**。
   （2026-09-29 起 `extracta_reader.py` / `footprint_extractor.py` /
   `extracta_views/footprint_views.txt` 三处**已分叉** —— check 没有位号那一路。）
3. **绝不 `os.remove` 产物** —— 覆盖保护一律走 `.trash/` 隔离（`_quarantine_file`）。
   Allegro 碰到已存在文件会弹「File Exists」确认框 = 通道自杀。
4. **`axlCompileSymbol` 恒返回 nil** —— 成功判据看**磁盘 `.psm` 快照差异**，别看返回值。
5. **`axlSaveDesign` / `create symbol` 写的是 Allegro 工作目录**，不是 `.dra` 所在目录。
   `getWorkingDir()` 读、`axlShell("cd <dir>")` 改（唯一方法）。
6. **位号默认口径**（2026-09-30）：`#REF` @ `REF DES/ASSEMBLY_TOP`、居中于本体、LEFT 对齐。

## 相关目录

- 对外门面：`../mcp_server/`
- 离线自检与在线对拍脚本：`../Tools/`
- 人工模板库（金标准）：`../../Data/footprint/`
