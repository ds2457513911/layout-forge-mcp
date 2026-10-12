# MCP 工具层（`layout-forge-mcp`）

> 收录：2026-10-09 启用 MCP 后新增的内容（SKILL.md 原 §3.1 / §3.2 / §3.4 的详细版）
>
> ★ 什么时候读这份：**要动手建封装 / 要归档 / 怀疑 MCP 工具与既有规则打架** 时才读。
> 平时 SKILL.md 第 3 节那几行够用。

## 这个文件是干什么的

`layout-forge-mcp` 是**服务层**（fastmcp / stdio / 28 个工具），本技能是**知识层**。
两者不是替代关系：MCP 负责"谁来调 Allegro"，本技能负责"调的时候别踩哪些坑"。

本文件记三件事：走 MCP 建封装的完整工具链、归档怎么落位、以及
**MCP 工具与既有 SKILL 规则的已知差异**（这块最容易踩，且会随代码变动）。

## 1. 建封装的完整工具链（★ 已实测 28/28 可用）

```
list_specs() / find_spec(...)          ← ★ 先查 spec 库，命 exact 就零读图（见 spec-library.md）
inspect_allegro_environment()          ← 先探，Allegro 不在会返回规范错误而非崩溃
locate_land_pattern_page(pdf)          → 定位规格书页码
                                         ★ 返回 page:null 时看它的 hint —— 扫描件走下面这条
render_pdf_contact_sheet(pdf)          → ★ 扫描件兜底：整份拼成一张缩略图，扫一眼定位页码
render_pdf_page(pdf, page)             → 渲染，供 AI 读图取参
  …（AI 读图，产出参数 JSON）
validate_land_pattern_json(raw_json)   → 守门员，防格式漂移  ※参数是 str 不是 dict
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
真源在 MCP 项目 `Data\specs\*.json`，用法与判据见 **`spec-library.md`** ——
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
| `check_footprint_by_rules` | `save_report` 默认 True → 会写报告文件到磁盘 | 只要结论时传 `save_report=False` |
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
