---
name: allegro-skillbridge-ops
metadata:
  # ★ 三声明（Phase 3 §7）：放在 front-matter 最前，确保进入部署校验的读取窗口
  mcp_min_commit: "a5bd61f"
  verified_with: "layout-forge-mcp @ a5bd61f / 2026-10-15：eval/check_eval.py 五套全绿、behavior 5/5"
  eval_baseline: "eval/baselines/tool_contract.json（工具契约快照）+ geometry_regression.json（239/222/35/253 件） + spec_reading.json（92.5%）"
description: |
  在 Cadence Allegro (SPB 17.2+) 上通过 SkillBridge 通道做自动化时的运维与排障技能。
  覆盖：AXL/SKILL 函数「文档里有但运行时有没有」、通道错位/卡死后的恢复、
  会弹模态框把通道搞死的 API 清单、Allegro 的启动与收尾生命周期（收尾必须
  关进程 + 收回后台 shell 两件都做）、产物落点错位与「编译成功只认磁盘证据」、
  不用 Allegro 也能读 .dra（extracta + 视图文件）、图元被 extents 静默丢弃、
  尺寸/位号/库落位口径，以及 layout-forge-mcp 的 MCP 工具怎么配合本技能（全表见 `docs/tools-catalog.md`）。
  只要任务涉及 Allegro 自动化、axl 函数、SkillBridge 通道、.dra/.psm 产物、
  启动/关闭 Allegro、建封装或归档封装，就先读本技能，即使对方没提 "SkillBridge"。

---

# Allegro SkillBridge 运维与 API 探测

> **★ 收尾不是可选项。** 收尾 = **A**（关掉 Allegro 进程）**+ B**（收回后台挂住的 shell），
> **两件都要做**。只做 A 会留下一条空转的后台 shell，WorkBuddy 界面一直挂着
> "1 个后台任务运行中" —— 用户看到的就是「活没干完」。见下面第 2 节。

本文件是**入口**：只放「必须知道的那几件事」+「去哪找」。细节全在 `references/` 的
9 个文件里，**按需再读**，不要一次读完。

## 0. 30 秒判断表

| 你要干什么 / 遇到什么症状 | 去读 |
|--|--|
| **要建封装**（走哪条路、调哪些工具） | 本文件第 3.1 节 + `references/mcp-tools.md` 第 1 节（完整清单） |
| **新料号要不要重读规格书**（先查库） | `references/spec-library.md` |
| `locate_land_pattern_page` 返回 `page: null` / 扫描件找不到尺寸页 | `references/spec-library.md` §4（缩略图拼版兜底） |
| MCP 报「连不上 Allegro」/ 想确认 MCP 层是不是活的 | 第 **3.0** 节 + `references/lifecycle.md` |
| 怀疑 MCP 工具参数 / 默认落点与既有规则冲突 | `references/mcp-tools.md` 第 3 节 |
| 图元没画出来；几十步里只挂 1 步、静默返回 `nil` | `references/extents.md` |
| 通道没响应 / `evaluating SKILL` 卡住 / 回复积压 / 服务挂了 | `references/lifecycle.md` |
| 要**启动**或**关闭** Allegro；收尾怎么收干净 | `references/lifecycle.md` |
| 拿不准某个 API 会不会**弹框** | `references/popup-ban.md` |
| 要新建 / 清空 / 重建 symbol 图纸 | `references/drawing-ops.md` |
| 尺寸取标注值还是查表；单位精度；位号怎么放；库怎么落位 | `references/drawing-ops.md` |
| 引脚号字号不一致 / 随脚号变大 | `references/drawing-ops.md` §5.4.2b |
| 出 `.psm` 了但不确定成没成；产物找不到；编译日志里的错误码 | `references/compile-psm.md` |
| 封装做完要**归档**（往哪建文件夹、怎么走 MCP） | 第 **3.2** 节 + `references/compile-psm.md` §13 |
| 某函数"文档里有但运行时 unbound"；版本与文档对不上 | `references/api-facts.md` |
| 要找官方手册 / 别人怎么写的 | `references/api-facts.md` |
| 要写 `allegro.ilinit` 或拼 SKILL 表达式 | `references/skill-syntax.md` |
| 想截图取证屏幕上的 Allegro 窗口 | `references/lifecycle.md` + `scripts/` |

## 1. 红线清单（做了就把通道搞死 / 丢产物）

| ❌ 不要 | 为什么 | 正解 |
|--|--|--|
| 建封装时直接跑 `Tools/e2e_*.py` **绕过 MCP** | 服务层被架空，CLI 与 MCP 两套能力各自演化、迟早不同步 | 走 MCP 工具链（第 3.1 节）；仅启停 / 自检 / 归档 / 收尾走 CLI |
| 调会弹**模态对话框**的 API | 弹框 = 通道彻底堵死，且外面看不出来 | 用 `NOCONFIRM` 变量族压住；清单见 `popup-ban.md` |
| 用 `os.remove` 做覆盖保护 | 真实事故，直接丢产物 | 改走隔离 / 改名 |
| 设 `no_symbol_onsave` | 设了就退回"必须手工 Create Symbol" | 别设 |
| 拿 `axlCompileSymbol` 的返回值判成功 | **恒返 `nil`**，会误判成失败 | 只认磁盘证据（三条） |
| 直接比较 `tasklist` 的原始输出 | 输出是 **GBK** | 先转码再比 |
| 用落在 `drawing extents` 之外的坐标画图元 | **静默丢弃**：返回 `nil`、不报错不弹框 | 先扩 extents |
| 空图纸时把 `axlDBGetDesign()->bBox` 当"内容框" | 它给的是**模板页框**（实测 1117.6×863.6 mm） | 按实际图元算 |
| 同一台机**同时跑两个建图任务** | `AllegroSession` 无锁，SkillBridge 只有一条 7777 通道、`Channel.send()` 是「发一收一」原子操作；两个任务同时发，回复队列错位后只能重启服务清 | **一次只跑一个**：等前一个 `export_psm` + 回读核验结束，再开下一个 |
| 命令结尾接 `\| tail` | 会把命令搞**挂死** | 重定向到文件再读 |

## 2. ★ 收尾铁律：A + B，缺一不可

| | 事 | 手段 | 只做它的后果 |
|--|--|--|--|
| **A** | 关掉 Allegro 进程 | `Tools/stop_allegro.py`（默认）/ `axlShell("exit")` / `taskkill` | Allegro 没了，**后台 shell 还在空转** |
| **B** | 收回后台挂住的 shell | `TaskStop <task_id>` | shell 没了，Allegro 可能还活着 |

**★ 用 A 的方式关掉 Allegro，不等于 B 被处理了。A、B 都必须做。**

用「后台挂住」启动时（`cmd //c start_allegro.cmd` 配 `while true; do sleep 20; done`），
**启动那一刻就把 task_id 存下来**，别靠记性：

```bash
# 启动（run_in_background=true）→ 工具会返回 task_id
# ★ 立刻存盘，收尾时读它：
echo "<task_id>" > "<缓存区>/allegro_task_id.txt"

# 收尾（顺序不能换）
1) ./.venv/Scripts/python.exe Tools/stop_allegro.py        # A 关进程
2) cat "<缓存区>/allegro_task_id.txt" → TaskStop(该 id)     # B 收回后台会话
3) python scripts/preflight_check.py                        # 复查（端口/进程/锁/污染）
4) ./.venv/Scripts/python.exe Tools/collect_logs.py         # 归集 allegro.jrl，必须最后
```

**★ 复查口诀**（后台任务列表只能这样探）：

```
TaskStop(task_id="__list__")        # 传一个不存在的 ID
→ There are no running background tasks.     ← 干净
→ Running tasks: ON26rs (Bash: ...)          ← 还有活的，继续收
```

另注：后台命令若自带 `> /tmp/x.txt 2>&1`，**输出文件在 `TaskStop` 之前可能是空的**
（缓冲没刷），别用"输出是空的"判断任务没在跑。

**为什么必须收干净**：残留 CWD 会**静默**改变下一次 `axlSaveDesign` / `create symbol`
的落点（"产物凭空消失"的根因）；孤儿 `.dra.lck` 会让下次启动弹「File Exists」框，
又堵死通道。**不要"留着下次用"，每次都关。**

## 3. 标准流程

项目根（本技能只服务这一个项目）：

```
C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp
```

### 3.0 ★ 启动 Allegro：MCP 替代不了，「后台挂住」照旧

**两层生命周期，别混为一谈：**

| 层 | 谁拉起 | 谁维持 | 要不要后台挂住 |
|--|--|--|--|
| MCP server（stdio 子进程） | 客户端（WorkBuddy） | 客户端 | **不需要** |
| Allegro（GUI 进程） | bash 命令 | 那条 bash 命令 | **需要** |

MCP 只接管了「谁来调 Allegro」，**接管不了「Allegro 这个进程怎么活下来」** ——
MCP 工具里**没有任何启停 Allegro 的能力**。bash 命令一结束，
它拉起的 GUI 子进程会被命令边界回收（沙箱内外一样，见 `lifecycle.md`）。

```bash
cd Tools && cmd //c start_allegro.cmd > /tmp/al_bg.txt 2>&1; \
echo "=== 挂住中 ==="; while true; do sleep 20; done     # run_in_background=true
# ★ 启动那一刻就把 task_id 存盘：<缓存区>/allegro_task_id.txt
```

**2026-10-09 实测两条**（细节见 `lifecycle.md`）：① 后台挂住启动 → MCP
`inspect_allegro_environment` 返回 `connected: true`，所以这条**不是被取消，
而是 MCP 能工作的前提**；② ★ **`TaskStop` 掉后台任务，Allegro 不会被带走**
（`start ""` 已让它脱离父进程树）—— 后台挂住解决的是「**启动瞬间**被命令边界
回收」，Allegro 一旦起来就独立了。**别指望 `TaskStop` 顺带关掉它**，收尾 A 必须单独做。

### 3.1 主流程走 MCP 工具（★ 2026-11-19：当轮注册的工具全部冒烟通过）

**建封装一律走 MCP 工具，不要越过服务层去跑 `Tools/e2e_*.py`。**
主干见下（完整工具链见 **`references/mcp-tools.md` 第 1 节**）：

```
folder_preflight(folder)         → ★ 只给了目录时的第一步：先看有几份 PDF / 几颗 .dra
                                   （action="confirm" = 先问用户查哪一份，别自己挑；
                                     verdict="empty" + action="stop" = 缺料，别往下走）
list_specs() / find_spec(...)   → ★ 先查库：命 exact 就零读图（spec-library.md）
inspect_allegro_environment()   → ★ 通道自检（工具内建）：connected=false 时 error 必非空，
                                   按失败码 CHANNEL_NOT_READY 处置（见 3.1.1），别盲调业务工具
  ↓ pdf_probe 判形态（CAD/印刷/扫描件）→ locate_land_pattern_page 定页 → AI 读规格书取参
      · extract_page_numbers → render_focus_regions（概览 + 焦点 ≥600dpi，不够就切块）
      · reconstruct_pad_array 矢量对拍；指定区域才用 render_pdf_region
      · 定位不到页 → render_pdf_contact_sheet 拼图扫一眼（整页图只定位，不读数）
  ↓ validate_land_pattern_json(raw_json)   ※参数是 str 不是 dict
solve_footprint_geometry(...)   → 算几何；一次成型用 build_footprint_from_params(...)
  ↓ 建图纸 / 焊盘 / 图元 → ★ change_drawing_extents(...) 必须先扩，否则静默丢弃
save_footprint_drawing() → export_psm() → read_full_footprint() 回读核验
save_spec(name, spec_json, family, notes)  → ★ 建完存档（notes 写踩过的坑）
```

★ **为什么第一步是 `folder_preflight`**：S1 铁律要求「目录里多份 PDF/`.dra` 必须先确认」，
但 `list_folder_files` 的返回里只有**单个** `pdf` 字段 —— 歧义在返回值里不可见（会被静默吞掉）。
`folder_preflight` 把它变成显式字段，并**顺手剔除噪声**（`AUTOSAVE.dra` 按名排序会排在
真正的 `.dra` 前面，直接"取第一个"必然取到垃圾）。它只上报，不替你决定，也不拒绝执行。

### 3.1.1 ★ 通道自检与失败码处置（S4 的可执行版：工具内建，不用 AI 先去探）

**自检已经内建在工具链里**（`inspect_allegro_environment` + `orchestration/pipeline.py` 的 `env-check` 步），
AI 要做的是**按失败码分支，而不是自己判断"通道好像不行"**。失败码是稳定契约：

| 失败码 | 出现场合 | 正确动作 |
|---|---|---|
| `CHANNEL_NOT_READY` | `inspect_allegro_environment` 返回 `connected=false`（`error` 必非空）；`pipeline` 的 `env-check` 未通过 | **如实报**「Allegro 未连接（SkillBridge 7777 不通）」，停掉所有**触 Allegro 的工具**；若本次任务只需要读 PDF/.dra（`pdf_probe` / `read_full_footprint` / spec 库查询），**这些不走通道，可以继续** |
| `AMBIGUOUS_INPUT` | 目录里多份候选，`folder_preflight.ambiguous=true` | 把候选列给用户确认，**不许自己挑一个**，也不许"先跑一个试试" |
| `NO_CANDIDATES` | `verdict=empty` / 只有噪声文件 | 缺料，停；提示用户补 PDF 或 `.dra` |
| `UNITS_UNKNOWN` | `.dra` 单位既非 mm 也非 mil | 别按 mm 猜；用不带单位的几何描述，或先让用户确认单位 |
| `NO_DATA` | 取数返回空结构 | 如实说"该步骤未执行"，不补数据 |

- 判据来源：`orchestration/pipeline.py` 的 `ERR_CHANNEL_NOT_READY` 等常量 + `eval/suites/behavior`
  的 **B4/B5** 夹具（通道 `connected=false` 时 `error` 必须非空、歧义必须 `confirm` 且停住）。
- ★ **工具挂了 ≠ 通道没开**：探针失败时报"通道未连接"；业务工具超时才是"工具挂了"。
  两者都不许据此跳过步骤或编数据（红线清单第 4 条）。
- ★ 通道的 **15 帧滞后**是已知语义（见第 4 节），不是 `CHANNEL_NOT_READY`；别把"读到上上次的值"
  当成连接失败 —— 那种情况的唯一解法是**重启 Allegro 侧的 pythonw 服务**。

想知道"最近哪条规则老挂 / 哪一步最慢"：`get_run_stats(days=7)`
（数据源：`eval/runs/*.jsonl` 逐次工具调用 + `Tools/log/checklist_report_*.json`）。

### 3.2 ★ 归档：走 MCP（用户 2026-10-09 口径）

**锚点 = 规格书所在目录**，其下建**与符号同名的文件夹**放生成件。
`install_shadow_library` 只需传对这两个参数就符合口径：

```
install_shadow_library(symbol_name="<符号名>", pn="<符号名>",
                       lib_root="<规格书所在目录>",   # ★ 不传会落到 Data\footprint\ 下
                       overwrite=False, dry_run=True) # 先 dry_run 看会装什么
```

它还**不做**的两件事（需手工补）：① 规格书 PDF 移入该目录；② 写 `README.md`。
细节见 `references/mcp-tools.md` 第 2 节 + `compile-psm.md` 第十三节。

**★ check_log 归档（2026-10-16 约定）**：每个封装目录各建一个 `<封装目录>/check_log/`，
之后的检查报告 JSON（`checklist_report_*.json`）由 `check_footprint_by_rules`
**自动写进这里**（不用传 `output_dir`）；Allegro 运行日志（`.jrl`/`.log`/`master.tag`）
不要留在里面，用 `Tools/collect_logs.py` 收进项目 `Tools/log/`。
预检与 G4 噪声扫描都**豁免** `check_log/` 里的报告（合法归档位）；
散放在封装目录根下的旧报告仍是噪声 —— 遇到就搬进 `check_log/`。

### 3.3 以下三件不走 MCP（理由各异，不是"MCP 万能"）

```bash
# ① 启动 Allegro        —— MCP 无启停能力，见 3.0
# ② 离线自检（改过代码必跑，★ 只能用项目 venv）
./.venv/Scripts/python.exe Tools/selfcheck_geometry.py
./.venv/Scripts/python.exe Tools/selfcheck_skill_syntax.py
# ③ 收尾：A 关进程 + B 收回后台 shell + 复查      ★ 见上面第 2 节
./.venv/Scripts/python.exe Tools/stop_allegro.py
```

### 3.4 MCP 工具与既有规则的已知差异

**★ 一句话**：Allegro 侧的坑（extents 静默丢弃、弹框禁令、Edit Check 门槛、
通道 15 帧滞后）**与调用方式无关，MCP 下一条不少**；差异只出在
**参数透传**和**默认落点**这两类。

⚠️ 动手前**读 `references/mcp-tools.md` 第 3 节的差异表**（6 项逐项实测）：
`install_shadow_library` / `new_symbol_drawing` 的**默认落点**会坑人，
`change_drawing_extents` 的 **docstring 是旧的**（行为已修，信行为不信文档）。

> 2026-10-09 已修：`solve_footprint_geometry` 原先不透传
> `body_center` / `place_bound_expand_mm` / `pins_override` / `extra_padstacks`
> 等连接器专用字段，现已全部补齐，并加了 `extra_spec` 逃生舱。

## 4. 环境前提与两条认知

| 项 | 值 / 规则 |
|--|--|
| 项目根 | `C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp`（本技能只服务这一个项目） |
| 调用方式 | **MCP 为主**（已注册到 `~/.workbuddy/mcp.json`，当轮工具全量冒烟实测）+ **CLI 补位**（启停 / 自检 / 归档） |
| Python | 走 MCP 时**由 server 管**，调用方不碰解释器。★ 但跑 CLI 那三件事**只能用项目 `.venv/Scripts/python.exe`** —— 系统 Python 与托管 Python 都没装 `skillbridge`，一 import 就 ImportError |
| MCP 拉起方式 | 项目 `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, <项目根>); from mcp_server.server import main; main()"`。**不依赖 cwd**（`config.py` 的 `PROJECT_ROOT` 按 `__file__` 解析） |
| 服务 | SkillBridge 在 **7777** 端口；Allegro 侧的 pythonw 服务随 Allegro 生灭 |
| 自启动 | `...\SPB_Data\pcbenv\allegro.ilinit` 内有 `;; ==== SkillBridge Auto-Load BEGIN/END ====` 段 |
| 日志 | `allegro.jrl` 落点由 `allegro.ini` 决定，**不是 cwd**；Allegro 活着时搬不动 |

**通道「固定滞后」的真相**（认知，不是故障）：Allegro 侧到 Python 服务端之间
有**约 15 帧的管道缓冲** → `发第 k 条命令，读回的是第 (k-15) 条的结果`。
所以「`axlCurrentDesign()` 返回上上次的值」不是函数错了，**唯一解法是重启服务**。

**适用场景**：通过 SkillBridge 从 Python 驱动 Allegro（发 SKILL 表达式）时；
需要确认某个 `axlXXX()` 函数在当前版本里**是否真的可用**；
通道报「无法对齐」「回复积压」「attempting to reconnect」需要恢复；
想自动化重启 Allegro 侧的 Python 服务。

## 5. references 索引

| 文件 | 收录原章节 | 讲什么 | grep 关键词 |
|--|--|--|--|
| `references/lifecycle.md` | 二、十、十一、十四 | 启动 / 后台挂住 / **收尾 A+B** / 通道错位恢复 / 运行痕迹 / 窗口取证 | `TaskStop` `收尾` `stop_allegro` `lck` `jrl` |
| `references/popup-ban.md` | 三 | 弹框禁令全表 + `NOCONFIRM` 变量族 + 覆盖保护禁区 + `.dra` 升级框 | `弹框` `NOCONFIRM` `os.remove` `onsave` |
| `references/drawing-ops.md` | 五 | 新建 / 清空 / 重建图纸；单位精度、尺寸口径、位号、库落位 | `模板复制法` `单位` `dim_source` `padpath` |
| `references/compile-psm.md` | 七、十三 | 出 `.psm` + 证据制判据 + 落点错位 + 日志诊断 + 归档规范 | `axlCompileSymbol` `证据` `归档` `SPMH` |
| `references/api-facts.md` | 一、六、八、十二、**十三** | 函数存在性探测 / 版本差异 / 官方手册 / 外部资源 / **与 `eval/suites/tool_contract` 的挂钩（结论 ↔ 断言对照）** | `unbound` `手册` `extracta` `社区` `tool_contract` |
| `references/skill-syntax.md` | 九 | SKILL 语法陷阱（写 ilinit 或拼表达式时） | `带点的名字` `let` `when` |
| `references/extents.md` | 十五 | 图元被静默丢弃：extents 是硬边界 | `extents` `静默丢弃` `页框` `bBox` |
| `references/mcp-tools.md` | （2026-10-09 新增） | MCP 工具链完整清单 / 归档落位 / **工具与规则的差异表** | `MCP` `install_shadow_library` `extra_spec` `透传` |
| `references/spec-library.md` | （2026-10-09 新增） | ★ spec 库：同封装零读图复用 / 建完存档 / 扫描件定位兜底 | `spec` `save_spec` `load_spec` `find_spec` `contact sheet` |

> 章节号**沿用拆分前的原文编号**。正文里的「见第十一节」指的是**那一节现在所在的文件**，
> 速查：`一/六/八/十二`→api-facts、`二/十/十一/十四`→lifecycle、`三`→popup-ban、
> `五`→drawing-ops、`七/十三`→compile-psm、`九`→skill-syntax、`十五`→extents、`四`→本文件第 4 节。
>
> `scripts/` 下另有两个取证脚本：`find_allegro_window.ps1`（定位窗口 + 截图，
> 输出 JSON + PNG）、`shot_stats.py`（对截图做像素统计，用数值代替肉眼）；
> 用法见 `references/lifecycle.md` 第十四节。收尾复查脚本是 `scripts/preflight_check.py`
> （见上面第 2 节清单第 3 步）。
