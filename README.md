# layout-forge-mcp

Allegro 封装**生成** MCP —— 通过 SKILL 在 Allegro 里建 padstack / pin / 丝印 / Place_Bound，产出 `.dra` / `.pad` / `.psm`。

与 `layout-check-mcp`（检查器，只读）互补：这个是**生成器**（写入）。

---

## 定位：为什么不走 KiCad 转 Allegro

| 环境 | 谁做 | 用什么 |
|--|--|--|
| 封装数据准备 | AI + 本 MCP | 解析规格书 → 算坐标 → 直接生成 `.dra`/`.pad` |
| 设计本体 | Allegro 工程师 | Allegro 里做 layout、规则设置 |
| Gerber 生成 | Allegro 本身 | Artwork / NC Drill |
| Gerber 验证 / DFM | AI + KiCad 生态 | 检查 Allegro 导出的 Gerber |

KiCad 10 的 Allegro 导入器是**单向**的（只能导入 `.brd`，不能导出 `.dra`），
且 Allegro 的二进制格式不可直写。所以 Allegro 侧的"直写"等价物 = **用 SKILL 原生生成**，
不需要任何转换层。

---

## 架构

```
mcp_server/tools.py           MCP 工具定义（薄层，只做参数校验 + 转发）
      │
      ├── services/geometry_solver.py    ★ 参数 → 几何（纯函数、零依赖 Allegro）
      │      规格书参数 ──> padstack / pins / silk / body / place_bound / refdes + 两个名字
      │
services/footprint_builder.py 编排层（顺序、依赖、单位换算、结果归一化）
      │
      ├── services/skill_snippets.py     SKILL 表达式构造器（纯函数，可单测）
      └── services/skillbridge_writer.py SKILL 执行通道（连 Allegro、发命令）
```

**分层原则**：构造表达式的代码（`skill_snippets`）与执行代码（`skillbridge_writer`）严格分离，
前者是纯函数、不需要 Allegro 就能跑完整测试 —— 这是 `tools/selfcheck_skill_syntax.py` 能存在的前提。
`geometry_solver` 更进一步：**连 Allegro 都不碰**，纯数学，可离线逐值对拍金标准。

**单位约定**：所有 MCP 工具**对外统一用 mm**，内部按当前 drawing 的 design units 自动换算。
调用方（AI）永远不需要关心图纸是 mm 制还是 mil 制。

---

## ★ 目录约定（单一真源：`services/paths.py`）

```
<PROJECT_ROOT>/
├── services/  mcp_server/          源码（json_utils / validation 并入 services，无 shared）
├── knowledge/                      ★ 热更新数据层：specs / cases / rules（改 JSON 不用重连 MCP）
├── docs/                           SOP 维护手册 + 使用者 SOP + SKILL API 核实报告
├── .trash/                         隔离区（**可恢复**，不删东西进去就完事）
└── Tools/
    ├── *.py  *.il  *.md            CLI 脚本 + 文档
    ├── test/        ★ 图纸干活区：每个封装一个子目录 + golden_3S48000163.dra
    ├── deploy/      ★ 一键部署（deploy.bat + setup_agent.py + skills 副本）
    └── log/         ★ 日志：Allegro 的 .jrl/.log、master.tag、checklist_report、archive/
```

### 目录导航（每个目录都有自己的 `README.md`）

> ★ 规则（用户 2026-09-30 下达，**所有项目通用**）：**每个目录都要有一份
> markdown 说明，且改完代码必须同步更新** —— 目的是用文档代替反复遍历目录。
> 动手前先读对应目录的 README，别靠 `ls` + 读源码去猜。

| 目录 | 一句话 | 说明文件 |
|--|--|--|
| `services/` | 生成侧核心：求解器 / 执行器 / 读取层 / SKILL 代码片段生成 | `services/README.md` |
| `mcp_server/` | MCP 工具层（对外暴露的那个门面） | `mcp_server/README.md` |
| `Tools/` | CLI 脚本 + 探针 + 自检 + 文档 | `Tools/README.md` |
| `Tools/test/` | ★ 测试图纸干活区（.dra / .pad / .psm） | `Tools/test/README.md` |
| `Tools/log/` | ★ 运行痕迹归集区 | `Tools/log/README.md` |
| `Tools/debug/` | 排障脚本（2026-10-09 从 layout-check-mcp 迁入）。★ 项目根是**上三级** | `Tools/debug/README.md` |
| `knowledge/specs/` | ★ **spec 库**（2026-10-09 新增）：已解析器件的参数 JSON，同封装零读图复用 | `knowledge/specs/README.md` |
| `knowledge/cases/` | ★ **检测案例库**（2026-10-12 新增）：指纹 + 报告摘要 + 人工裁定 + 教训，检查前 `find_case` 同族预警 | `knowledge/README.md` |
| `knowledge/rules/` | ★ **规则阈值外置**（2026-10-12 新增）：`checker_rules.json`，改数字立即生效 | `knowledge/README.md` |
| `docs/` | ★ **作业流程 SOP** + 调研与攻坚清单（决策依据都在这） | `docs/README.md` |
| `Tools/deploy/` | ★ **一键部署**（新机器双击 `deploy.bat` 即可：装环境 / 注册 MCP / 装 Skill / 配 SkillBridge / 验收） | `Tools/deploy/README.md` |
| `.trash/` | 隔离区（可恢复，**不要当垃圾桶删**） | `.trash/README.md` |
| `<Layout>/Data/footprint/` | ★ 人工模板库 = **金标准**，默认不覆盖 | `Data/footprint/README.md` |

**为什么非得分开**：Allegro 和探针脚本会把日志**甩在"图纸所在目录"**，
不是某个固定位置。跑几次就能把干活目录搅成一锅粥 —— 而且
`new_symbol_drawing()` 的退路模板扫描会扫**工作目录下的 `*.dra`**，
图纸和脚本混在一起时特别容易把探针脏图当模板。

> ★ **我自己的缓存不进工作区**：临时脚本 / 探针 / `*.log` / 中间产物一律放
> `C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\`（含说明文件）。
> 工作区只留项目自己的东西。

| 规则 | 说明 |
|--|--|
| 新建 symbol 图纸 | **相对名默认落 `Tools/test/`**；给绝对路径则尊重你指定的位置 |
| 日志归集 | `new_symbol_drawing` / `export_psm` / `build_footprint` 跑完**自动**把运行痕迹搬进 `Tools/log` |
| 手动归集 | `./.venv/Scripts/python.exe Tools/collect_logs.py`（`--dry-run` 可先预览）|
| 归集失败 | **永不拖累主流程**；`allegro.jrl` 被运行中的 Allegro 攥着搬不动是**正常现象**，会记进 `skipped` |

> ★ 想收当前会话的 `allegro.jrl`，等 Allegro 退出后再跑一次 `collect_logs.py`。

---

## 工具清单

### 生成类（本 MCP 新增，共 27 个）

| 工具 | 作用 |
|--|--|
| `inspect_allegro_environment` | **体检。所有生成操作前先调这个。** 返回连通性 / 单位 / 是否 Package symbol / 警告 |
| **`new_symbol_drawing`** | ★★★ **全自动新建 Package symbol 图纸**（当前版本模板复制法，零弹框）——**根治图元叠加污染** |
| `new_footprint_drawing` | 新建空图纸（调 `axlKillDesign()`，★ 产出的是 **BOARD** 类型，非 SYMBOL） |
| **`clear_footprint_drawing`** | ★ **清空图纸图元**（保留图纸本体）—— 另一种解决图元叠加污染的方式 |
| `change_drawing_extents` | 设置 drawing 有效范围。**放 pin 前必调**。默认做**并集扩展**（`union_with_current=True`），只扩不缩 |
| **`frame_drawing_view`** | ★ **图纸取景**：把「图纸范围 + 视图窗口」都摆成**以原点为中心** → 打开就居中，不再缩在画布左下角。`build_footprint_from_params` 默认已自动调用 |
| `create_padstack` | 建焊盘（多层 + 可选钻孔），落盘为 `.pad`。带**三层重名探测** + `on_exists` 策略（`error`/`skip`/`build`） |
| `add_footprint_pin` | 放单个引脚 |
| `add_footprint_outline` | 画矩形轮廓（silkscreen / assembly / place_bound） |
| `add_footprint_text` | 加文字。target 支持 `silkscreen` / `assembly` / **`refdes`**（位号必须用 refdes，否则 Create Symbol 报 SPMHCS-1） |
| `save_footprint_drawing` | 保存 drawing |
| **`export_psm`** | ★ **出 `.psm`**（调 `axlCompileSymbol`，= 界面 `File → Create Symbol`）**全自动无 UI**。成功判据＝**证据**（`.psm` 快照差异 / 日志完成记录），不认返回值——`axlCompileSymbol` 实测**恒返 nil** |
| **`install_shadow_library`** | ★ **影子库落位**（纯文件操作，不占通道）：把 `.dra`/`.psm`/`.pad` 装进 `..\Data\footprint\<PN>\`，拷完校验 size+sha1。**默认 `overwrite=False`**——影子库里可能是人工金标准 |
| `list_shadow_library` | 列出影子库已归档的料号与产物（只读核对） |
| **`solve_footprint_geometry`** | ★ **参数 → 几何**（纯离线）：把器件参数算成 padstack/pins/silk/body/place_bound/refdes + 两个名字，带 `derivations` 审计。★ **尺寸取值口径**：图上标注了具体值 → 取标注值；图上没标需查表 → 取表 MAX（无 MAX 用 TYP + 告警）。命名永远用中值。`build_footprint_from_params` 的**前置编译器** |
| `build_footprint_from_params` | **顶层接口**：一次调用建出完整封装。★ 支持 `fresh_symbol=True` **先自动新建干净图纸**；`silk`/`body`/`place_bound` 接受**列表**（多段轮廓） |
| `render_pdf_contact_sheet` | ★ **整份 PDF 拼一张缩略图**（2026-10-09 新增）。`locate_land_pattern_page` 在**扫描件上会失配**（13 份实测只强命中 4 份），用它一次读图定位尺寸页 |
| `pdf_probe` | ★ **判 PDF 形态**（2026-10-13 新增）：返回 `form` = `cad_vector` / `printed` / `scan` / `thin_text` + 建议读法。零成本（只读元数据），**每次读规格书第一步** |
| **`render_pdf_region`** | ★ **区域裁剪 + 高 dpi 重渲染**（2026-10-13 新增）—— **读尺寸数字的主力**。缩的是*视野*不是*像素密度*，600–1200 dpi；`render_pdf_page` 固定 150 dpi 做不到 |
| `measure_region` | ★ **矢量量距**（2026-10-13 新增）：返回区域内图元包围盒（pt），与读数做**交叉校验**。非主读数（焊盘与本体轮廓常粘连） |
| `save_spec` | ★ **spec 库**：把本次解析出的 spec 存档到 `knowledge/specs/<符号名>.json`（含 family / source_pdf / notes 方法论） |
| `load_spec` | 按名字取回 spec —— **同封装不同料号可零读图复用** |
| `find_spec` | 按几何指纹（mount / pin_count / pitch / body / pad，容差 0.05）找同族 spec，返回 `exact` / `family` / `keyword` 三档 |
| `list_specs` | 列出库里所有 spec 索引条目 |
| **`save_case`** | ★ **案例库**（2026-10-12 新增）：检测裁定后存档（指纹 + 报告摘要 + findings 裁定 + lessons 教训） |
| **`find_case`** | ★ 检查**前**先查：同封装/同族案例召回（exact/family/keyword 三档，复用 spec 指纹） |
| `list_cases` | 列出案例库全部案例 |

### 检查类（继承自 layout-check-mcp，共 7 个）

`list_subfolders` / `list_folder_files` / `locate_land_pattern_page` / `render_pdf_page` /
`read_full_footprint` / `check_footprint_by_rules` / `validate_land_pattern_json`

### 运维工具（命令行，非 MCP）

| 脚本 | 作用 |
|--|--|
| `tools/probe_api_exists.py` | ★ 探测 AXL/SKILL 函数「文档里有、运行时有吗」 |
| `tools/restart_skillbridge.py` | ★ 让 Allegro 自己重启 SkillBridge 服务（fire-and-forget）|
| `tools/selfcheck_skill_syntax.py` | SKILL 表达式构造器的离线自检（不需要 Allegro）|
| `tools/selfcheck_geometry.py` | ★ **参数→几何**求解器的离线自检（**239 项**，不需要 Allegro）|
| `tools/dump_all_footprints.py` | 批量扫 `Data/footprint` 下所有 `.dra` 出命名规则分析（2026-10-09 迁入；本项目根下无该目录时自动退回上一级真实库）|

> ★ **工程维护手册（部署 / 排障 / 变更）**：`docs/SOP-工程维护手册.md`。
> 给使用者/汇报看的操作说明：`docs/封装自动生成工具-SOP.md`。两份定位不同。
| `tools/online_calibrate.py` | ★★ **在线校准闭环**：建 → 编译 → 回读 → 与金标准 `.dra` 逐值对拍。分 `[A] 形态/结构`（必须一致）与 `[B] 尺寸口径`（已记录差异）两类分开报；`--install` 可顺带跑影子库落位 |
| `tools/e2e_3S48000163.py` | 端到端实测脚本（`--dry-run` 只体检 / `--keep-pad` 拷 .pad）|
| `tools/verify_e2e.py` | 通道恢复后的快速验证（`--probe` 只探活）|
| `tools/install_skillbridge.py` | ★ SkillBridge 自启动安装（**生产验证版，别改**）|
| `tools/print_restart_cmds.py` | 打印完整恢复命令（绝对路径太长，别从聊天记录复制）|
| `tools/collect_logs.py` | ★ 把运行痕迹（.jrl/.log/master.tag）归集到 `Tools/log` |

---

## 环境准备

### 1. 一键部署（推荐）

```bat
Tools\deploy\deploy.bat                          :: 新机器双击即可
Tools\deploy\deploy.bat /dir "D:\tools\lfm"      :: 指定安装目录
Tools\deploy\deploy.bat /pcbenv "D:\...\pcbenv"  :: 手工给 pcbenv
Tools\deploy\deploy.bat /dry                     :: 只预览不改文件
```

跑完就全配好了（MCP 注册 + Skill 安装 + Allegro 自动加载 + 冒烟验收）。
逐步中文对照与排障表见 `Tools/deploy/README.md`。

### 1.1 手工装依赖（脚本失败时的退路）

```bash
.venv\Scripts\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

★ 解释器必须是项目 `.venv` 里的 python —— `skillbridge` 只装在这里，
系统 Python 和托管 Python 都 import 不到。

### 2. 在 Allegro 里开 SkillBridge

SkillBridge 通过 workspace 端口通信，本项目默认 `workspace_id = "7777"`
（见 `mcp_server/config.py`）。

Allegro 侧由 `Tools\deploy\setup_agent.py` 的 C 步自动配置：往 `pcbenv\allegro.ilinit`
写自动加载块（`load python_server.il` + `pyStartServer ?id "7777"`）。
★ **它会持久修改 Allegro 配置**（开机自动启用），不想要就在部署时加 `/nobridge`。
旧版的 `deploy_skillbridge.bat` 已移到 `Tools\deploy\_legacy\`，别再跑。

#### ★ 什么时候需要重启服务

Allegro 侧 `ipcWriteProcess` 的回复走一条**固定深度约 15 帧的管道**。
只要出现过「发了命令但没把回复读走」（异常中断、多行代码、调试脚本被 Ctrl-C），
这些回复就会滞留在管道里，此后**所有调用都会读到错位的结果**：

```
调 axlCurrentDesign()  ->  返回上一次 axlGetDrawingName() 的值
调 axlDBGetDesignUnits() -> 返回上一次 axlCurrentDesign() 的值
```

客户端 `Channel.send()` 是「发一条 + 收一条」的原子操作，队列长度
（已发 − 已收）**守恒**，所以**靠客户端自己无法排空**。唯一办法是重启服务：

```skill
;; 在 Allegro CIW 里执行
pyKillServer()
load("<skillbridge>/server/python_server.il")   ; 路径按实际安装位置填
pyStartServer ?id "7777"
```

`AllegroSession.open()` 里带了 `_handshake()` 自检：队列干净时第 1 轮就通过；
对不齐会抛带上面指引的错误，不会让你在静默错位中继续跑。

### 3. ★ 新建一张 Package symbol 图纸（**全自动化，无需人工**）

> ★★★ **结论（2026-09-29 实测，两次修正后的最终版）**
> 这一步**不需要人工**，但也不是靠"改类型"API 实现的 ——
> **AXL 根本没有"建 symbol 图纸"的 API**（详见下表）。
> 正确做法是 **【当前版本模板复制法】**，见 3.2。

#### 3.1 实测得到的硬边界（务必知道）

| 尝试 | 实测结果 |
|--|--|
| `axlKillDesign()` | ✅ 零弹窗，但建出的是 **`BOARD`** 类型（`axlDesignType(t)` = `"BOARD"`） |
| `axlSetSymbolType("package")` | ❌ 拒绝：*"Procedure can only be applied to symbol drawings"* |
| `axlSetDesignType` | ❌ **`undefined function`** —— **函数不存在**（见下方「文档自身的错误」） |
| `axlDBCreateSymbolSkeleton` | ❌ 文档明确 *"Do not use this function in the symbol editor"*（它是往 layout 放 symbol 实例的） |

**早期两次结论的修正记录**：
- ~~"必须人工建图纸"~~ → ❌ 错。成因：裸符号探测法把 `axlKillDesign` 等全部误判成 `unbound`
- ~~"`axlKillDesign()` 就能全自动建 symbol 图纸"~~ → ❌ 也不对。它只能建 `BOARD`

#### 3.2 ✅ 正确解法：官方模板复制法

`new_symbol_drawing(target_name)` 内部流程：

```
1. 选模板：① Cadence 官方 sym_template.dra（首选，必是 SYMBOL 类型）
           ② 退路：当前图纸所在目录下的 *.dra，且 dra_major()==17
2. os.remove 目标 .dra + 目标 .dra.lck      ← 防 File Exists 弹框
   （★ 目标是我们即将重建的副本，删掉无妨；有价值产物必须走 `_quarantine_file` 隔离）
3. shutil.copyfile(模板, 目标)               ← 纯文件操作，零 UI
4. axlOpenDesign(?design 目标 ?mode "wf")    ← 类型由模板文件本身决定 → 天然 SYMBOL
5. 校验 axlDesignType(nil) == "SYMBOL"
6. ★ 污染校验：数 pins / text / padstacks    ← 退路模板非空就拒绝
7. 归集运行日志 -> Tools/log
```

★ **目标路径**：相对名默认落 **`Tools/test/`**（目录约定见上）；给绝对路径则尊重你指定的位置。

**★ 第 6 步：模板污染校验（2026-09-29 新增）**

图元创建是**叠加**不是替换 —— 模板里已有的 pin 会跟新建的 pin 撞号，
最终 `Duplicate pin number` → `Create symbol has been aborted.`。
已知风险源：`Tools/test/` 下的 `probe_test.dra` / `X.dra` 这类**探针脏图**
（这正是把它们从 `Tools/` 挪进 `Tools/test/` 的原因之一）。

| 模板来源 | 计数非空时的处置 |
|--|--|
| 官方 `sym_template.dra` | 只记 warning，**不阻断**（保住首选路径）|
| 退路（扫描图纸目录得到的）| **拒绝**，副本进 `.trash/`，报错并让你换模板 |

计数的可靠属性只有 `pins` / `text` / `padstacks` ——
`lines` / `shapes` / `rectangles` **不在 `axlDBGetDesign()` 属性表里**（读恒为 0，不能当判据）。
实现：`services/skill_snippets.py::build_content_count()`（有离线自检覆盖）。

**官方模板路径**：
```
<CDS_INST_DIR>/share/pcb/pcb_lib/symbols/template/sym_template.dra
```
Cadence 17.2 自带 —— 类型保证 SYMBOL、版本保证当前、内容干净。
比"扫工作目录找 .dra 赌它是 symbol"可靠得多。

**为什么成立**：图纸类型**内嵌在 .dra 文件里** —— 复制一个 symbol 模板，
打开后就是 symbol，**绕开了"没有改类型 API"这个死结**。

```python
new_symbol_drawing("NB_XTAL4_3D2X2D5X0D7")
# → {"success": true, "design": "NB_XTAL4_3D2X2D5X0D7",
#    "dra_path": ".../NB_XTAL4_3D2X2D5X0D7.dra",
#    "template": ".../pcb_lib/symbols/template/sym_template.dra", "steps": [...]}
```

> ★ **第 5 步校验不能省**：若模板本身是 BOARD，复制出来还是 BOARD。
> `axlDesignType(nil)` 必须返回 `"SYMBOL"`，否则流程判失败。
> 也可用 `template_path=` 显式指定模板。

#### 3.3 ★★★ `.dra` 版本标识（离线预判会不会弹升级框）

版本标识串固定在**文件 offset 248**，读 64 字节即可判定。

**★ 三种真实格式（务必都认）**：

| 样本 | offset 248 内容 | 大版本 |
|--|--|--|
| 17.2 自建（API 建的） | `all375891/28/...` | **17** |
| 17.2 官方符号库（3277 个） | `allv17-08/24/.apdv16-02/22/...` | **17** |
| 17.2 官方模板 `sym_template.dra` | `qvuv17-28/6/2dbdv16-512/5/...` | **17** |
| 16.0 老版本 | `qvuv16-04/12` | 16 ⚠️ |
| 15.7 老版本 | `dbdv15-71/3` | 15 ⚠️ |

**★ 判定方法：解析主版本号，不要精确匹配字符串。**

标识串是**以 `/` 分隔的版本链**，每段形如 `<prefix><major>-<rest>`。
取第一段提取主版本号；`all375891` 是特例（无 `-`，375 即 17.2 的内部编号）。

> ⚠️ **踩坑记录**：早期版本把判据写成 `tag == "all375891"`（精确匹配），
> 导致 **Cadence 官方符号库 3277 个 17.2 符号全被判为"老版本"** ——
> 它们是 `allv17-08/...`，同为 17.2 只是串写法不同。
> 教训：**版本串格式多样，必须解析主版本号。**

本工具用 `_dra_major_version()` 统一归一化，`== 17` 才放行，
老模板直接报错拒绝，绝不冒险。

#### 3.4 其它两条可用路径（需注意副作用）

```skill
;; 开一张新的无名图纸（★ 类型是 BOARD，不是 SYMBOL）
axlKillDesign()

;; 打开指定路径（★ 路径须已存在，否则弹 Drawing Parameters 框）
axlOpenDesign(?design "X.dra" ?mode "wf")

;; batch 版（不改命令/菜单；★ 位置参数，不是 ?design）
axlOpenDesignForBatch("X.dra" "wf")
```

**★ `?mode "wf"` 的语义边界**（易误解）：
- ✅ `f` = 丢弃当前设计的未保存编辑**不弹确认框**
- ❌ **不是**"覆盖目标文件不确认" —— 目标已存在照样弹 `File Exists. Overwrite?`
- → 对策：调用前先 `os.remove` 目标 `.dra` 和 `.dra.lck`

> ★ 注意：`axlNewDrawing` 这个函数确实**不存在**（全文档 0 命中），
> 但真正卡住自动化的不是它，而是**没有"建 symbol 类型"的 API**。

#### 3.5 ★ 文档自身的错误：`axlSetDesignType`

`14dsnctl.html` 在 `axlOpenDesign` / `axlOpenDesignForBatch` 的说明里都写了：

> *"The designType of the new design is set the same as the current design.
>   It can be changed using **axlSetDesignType**."*

但实测 `axlSetDesignType(nil)` 报 `*Error* eval: undefined function`，
**全文档也 0 命中该函数**。→ **文档前后矛盾时，以实测为准。**

---

## ★ 图纸污染与清空（重要）

**Allegro 的图元创建是「叠加」不是「替换」。** 同一张图纸反复跑生成流程，
pin 会累积成 2 份、3 份…… 最终 `export_psm` 报：

```
WARNING(SPMHA1-298): duplicate pin numbers
ERROR(SPMHA1-291): aborted
```

**两种解法**：

```python
# 解法 1（彻底）：换一张新图纸
new_footprint_drawing()

# 解法 2（保守）：图纸不动，只清内容
clear_footprint_drawing()                        # 默认清 pin + 文字 + 几何
clear_footprint_drawing(include_text=False)      # 想保留文字时
```

`clear_footprint_drawing` 内部用两层 API 互补：

| 目标 | API | 说明 |
|--|--|--|
| 几何（线/矩形/形状，按层） | `axlDeleteByLayer` | 高效；★ **官方明确不删 pin/via**（`PIN`/`VIA_CLASS`/`DRC_ERROR_CLASS`/`ROUTER_PLAN`/`CAVITY` 被忽略）|
| pin / text | `axlDeleteObject` | 补上面的盲区；★ **先 `mapcar` 拍快照再删**，否则边遍历边改集合会让游标失效 |

> ⚠️ `axlEraseObject` 只是**临时隐藏**，不是删除（`AXL shell` 退出就恢复），别用它。

---

## 典型用法

### 路线 A：分开调用（可控性强，便于排错）

```python
# 0) 体检
inspect_allegro_environment()
#    → {"success": true, "units": "mm", "is_package_symbol": true, "warnings": []}

# 0.5) ★★★ 全自动新建干净 Package symbol 图纸（零弹框）
new_symbol_drawing("NB_XTAL4_3D2X2D5X0D7")
#    → {"success": true, "design": "NB_XTAL4_3D2X2D5X0D7",
#       "dra_path": "...", "template": "...", "steps": [...]}
#
#    ★ 若图纸已有内容、想保留图纸本体只清内容：
#      clear_footprint_drawing()
#    ★ 或直接让顶层工具一步到位（内部自动先新建）：
#      build_footprint_from_params(..., fresh_symbol=True,
#                                  symbol_name="NB_XTAL4_3D2X2D5X0D7")

# 1) 扩范围（不调这个 pin 会报 SPMHA1-70）
change_drawing_extents(-10, -10, 10, 10)

# 2) 建 padstack
create_padstack(
    name="R1D4X1D2",
    layers=[
        {"layer": "TOP",            "figure": "RECTANGLE", "size": [1.4, 1.2]},
        {"layer": "SOLDERMASK_TOP", "figure": "RECTANGLE", "size": [1.5, 1.3]},
        {"layer": "PASTEMASK_TOP",  "figure": "RECTANGLE", "size": [1.4, 1.2]},
    ],
)
#    → {"success": true, "pad_file": "...\\r1d4x1d2.pad"}

# 3) 放 4 个 pin（SMD X'TAL 3.2x2.5，中心距 2.2 x 1.7mm）
for num, (x, y) in {"1": (-1.10, 0.85), "2": (1.10, 0.85),
                    "3": (-1.10, -0.85), "4": (1.10, -0.85)}.items():
    add_footprint_pin(padstack_name="R1D4X1D2", number=num, x=x, y=y)

# 4) 丝印 / 本体 / Place_Bound
add_footprint_outline(-1.7, -1.35,  1.7,  1.35, target="silkscreen", width=0.15)
add_footprint_outline(-1.6, -1.25,  1.6,  1.25, target="assembly",   width=0.10)
add_footprint_outline(-1.7, -1.35,  1.7,  1.35, target="place_bound", filled=True)

# 5) 位号（必做！★ 必须落在 REF DES/* 层；2026-09-30 起默认目标层 = 装配层）
add_footprint_text("#REF", -0.15, 0.04, target="refdes_assembly")   # 对齐人工模板库

# 6) 存盘
save_footprint_drawing()

# 7) 出 .psm（★ 全自动）
export_psm(symbol_name="NB_XTAL4_3D2X2D5X0D7", symbol_type="package")
```

### 路线 B：一次搞定

```python
build_footprint_from_params(
    padstack={
        "name": "R1D4X1D2",
        "layers": [
            {"layer": "TOP",            "figure": "RECTANGLE", "size": [1.4, 1.2]},
            {"layer": "SOLDERMASK_TOP", "figure": "RECTANGLE", "size": [1.5, 1.3]},
            {"layer": "PASTEMASK_TOP",  "figure": "RECTANGLE", "size": [1.4, 1.2]},
        ],
    },
    pins=[
        {"number": "1", "x": -1.10, "y":  0.85},
        {"number": "2", "x":  1.10, "y":  0.85},
        {"number": "3", "x": -1.10, "y": -0.85},
        {"number": "4", "x":  1.10, "y": -0.85},
    ],
    silk={"x1": -1.7, "y1": -1.35, "x2": 1.7, "y2": 1.35, "width": 0.15},
    body={"x1": -1.6, "y1": -1.25, "x2": 1.6, "y2": 1.25, "width": 0.10},
    place_bound={"x1": -1.7, "y1": -1.35, "x2": 1.7, "y2": 1.35},
    refdes={"text": "U*", "x": 0.0, "y": -2.0, "text_block": "1"},
)
#    → {success, padstack_name, pad_file, unit, pin_count, created_pins, warnings, steps}
#
# ★ 有 solver 输出时（2026-10-12 加）：把 solve_footprint_geometry 返回里的
#   `assembly` 字段（本体框 + 1 脚三角标）**原样**传进来 ——
#   build_footprint_from_params(..., assembly=geo["assembly"])
#   不要手工改传 body —— 会丢 1 脚三角标，规则检查"Assembly 有 1 脚标识"必 FAIL。
```

### 7) 出 `.psm`（★ 已可全自动）

```python
export_psm(symbol_name="NB_XTAL4_3D2X2D5X0D7", symbol_type="package")
#    → {success, symbol, psm_path, steps, error}
```

内部调用 `axlCompileSymbol` —— **这就是界面里 `File → Create Symbol` 的 API**。

> **★ 2026-09-29 重大更正**：早期"AXL 出不了 .psm"的结论是**错的**。
> `axlCompCreate` / `axlSaveSymbol` / `axlDBSaveSymbol` 确实不存在，
> 但 `axlCompileSymbol` 存在且可用（实测绑定成功）。

**文档原文**（`14dsnctl.html` 第 2341 行）：
```
axlCompileSymbol( ?symbol t_name ?type t_type ) ⇒ t_symbolName /nil
  "Compiles and edit checks the current (symbol) design and saves the
   compiled version on disk under the name t_name.
   If t_name is not provided, then t_name is the current drawing name.
   ★ If t_name is provided as nil, you are prompted for the name. ★"
```

★★★ **致命陷阱**：

| 写法 / 情况 | 行为 |
|--|--|
| `?symbol nil` | ❌ **弹「另存为」对话框 → 通道卡死** |
| `?symbol "名字"` | ✅ **无 UI，直接编译落盘** ← 正路 |
| 不传 `?symbol` | 用当前图纸名（安全）|
| **目标 `.psm` 已存在** | ❌ **弹「File Exists. Overwrite?」→ 通道卡死** |

★ `axlCompileSymbol` **只有 `?symbol` / `?type` 两个参数，没有 `?noConfirm`**，
无法从 API 层抑制覆盖确认框。`export_psm()` 的做法是**编译前先把旧 `.psm` 移入 `.trash/` 隔离区**
（★ **不是 `os.remove`** —— 见下方"产物保护"）。

★★★ **产物保护（2026-09-29 修复）**：
直接 `os.remove` 掉旧 `.psm` 会带来灾难 —— 编译一旦失败（图纸污染 / 缺位号），
上一次的成功产物就**永久没了**。真实踩过：8640 B 的 `.psm` 就是这么丢的。
现在统一改成"搬走 + 记住路径"：

| 编译结果 | 隔离件的去向 |
|--|--|
| 成功 | 留作备份，路径见返回值的 `previous_psm_backup` |
| 失败 | **自动原样搬回原位**，绝不让产物凭空消失 |
| 无法隔离 | **中止编译**（宁可这次不编译，也不冒丢产物的险）|

实现：`services/footprint_builder.py::_quarantine_file()` / `_restore_quarantined()`。

**前置条件**（Allegro 会做 edit check，不满足返 nil）：
1. 当前必须是 Package symbol 图纸（`axlDesignType(nil)` == `"SYMBOL"`）
2. 图纸里**必须有位号文字，且位于 `REF DES/*` 层** ← 见下方"位号层"
3. 图纸里**不能有重复 pin 编号**
4. 图纸最好已保存过

**成功判据（★ 2026-09-29 更正：返回值不可信）**：
`axlCompileSymbol` **恒返回 nil**（字符串形式与 SKILL 符号形式都试过），
但编译其实成功。**绝不能看返回值** —— 成功判据必须用**磁盘证据**：
① 编译前后 `.psm` 快照出现新文件/更新（最硬）；② 日志含
`Create symbol completed` 且无 ERROR。（见 `export_psm` 的 `evidence` 字段。）

### ★★ 位号必须放 `REF DES/*` 层

逐条 `axlLayerGet` 实测（2026-09-29）：

| 层名 | 有效 |
|--|--|
| `REF DES/ASSEMBLY_TOP` | ✅ ← `add_text(target="refdes_assembly")`，**2026-09-30 起默认** |
| `REF DES/SILKSCREEN_TOP` | ✅ ← `add_text(target="refdes")`（可用，非默认）|
| `PACKAGE GEOMETRY/PLACE_TOP` | ❌ 返 None |
| `PACKAGE GEOMETRY/SILKSCREEN_TOP` | ✅（但放这里**不认**是 refdes）|

放错层的后果：`ERROR(SPMHCS-1): Symbol is missing a refdes.`
—— **即使图纸里确实有 `U*` 文字**，只要不在 `REF DES/*` 层就不算。

**★ 文本与位置口径（2026-09-30，以人工模板库为准）**：
扫 `Data/footprint/*/` 下 14 个人工模板，**14/14** 都是
`#REF` @ `REF DES/ASSEMBLY_TOP`、居中于本体、LEFT 对齐
→ 本项目默认已改为同一口径（旧默认「丝印层 `U*` 放本体外侧」已废，
但 `refdes_place="outside"` 仍保留可用）。
`3S48000163` 逐值对齐人工件：`(-0.15, 0.04)`。

### ★★ 图纸污染会导致编译失败

图元创建是**叠加**不是替换。同一张图纸反复跑会在里面累积重复图元，最终：
```
WARNING(SPMHA1-298): Create symbol duplicate pin numbers.
ERROR(SPMHA1-291): Create symbol has been aborted.
```

`build_footprint` 现在会**提前检测**：生成前先数 `pins` / `text`，
非零就发 warning 提醒「会叠加，建议用干净图纸」。

---

---

### ★★ 打开时封装缩在画布左下角？—— `.dra` **会存"上一帧视图"**（2026-09-30）

**症状**：每次生成的封装配，打开后都缩在整个画布的**左下角**，几何本身没错。

**根因**（实测，不是猜的）：`.dra` 会把**当前视图窗口**一起存盘。
官方 `sym_template.dra` 自带的视图就是"原点落在左下角"；流程是
「复制模板 → 打开 → 画几何 → 存盘」，视图原样跟着走 —— 于是**每份产出**都左下角。

**两条被实测推翻的直觉**：

| 直觉 | 实测结果 |
|--|--|
| ✗「改 `extents` 就能改视图」 | `axlDBChangeDesignExtents` **只在缩小时夹一下视图**；范围变大**不放宽**视图。视图 ±4 时把范围设成 ±20，视图读数仍是 ±4 |
| ✗「`axlZoomCenter` 能兜底居中」 | 视图已包含原点时它返回 `t` 但**什么都不改** |

**正确做法**（`services/footprint_builder.py::frame_drawing()`，自动跑）：

```python
axlDBChangeDesignExtents(list(list(-5.2 -5.2) list(5.2 5.2)))   ; 范围 → 以原点对称
axlZoomPoints(nil list(4 4) list(-4 -4))                        ; 视图 → 以原点对称
axlZoomBbox(nil)                                                ; ★ 读回真实窗口
; → 底左锚定 + 按窗口宽高比外扩 ⇒ 中心偏到 +0.76
; → 把请求矩形反向平移同一偏移量，再设一次（迭代至中心误差 < 5µm）
```

**API 速查**（文档 `08intprm.html`）：

| API | 作用 | 坑 |
|--|--|--|
| `axlZoomBbox(nil)` | **读**当前视图窗口 | ★ 唯一能读回视图的 API。**别和 `axlExtentDB()` 混** |
| `axlZoomPoints(nil UL LR)` | **设**视图窗口 | 底左锚定 + 按宽高比外扩，不是"照抄矩形" |
| `axlZoomCenter(nil xy)` | 挪视图中心 | 视图已含原点时**空转** |
| `axlExtentDB()` | **读**几何外包框 | **无参**！多传会被当"未知选项"忽略并告警 |
| `axlWindowFit()` | = `zoom fit` | 符号模式下是"**占满**窗口"，不是"居中留白" |

> **为什么必须在存盘之前做**：视图是**存进文件的状态**，不重设就一路继承。
> `build_footprint` 的 `frame_view=True`（默认）在 `save_drawing` **之前**调用
> `frame_drawing()`；`online_calibrate.py` 的 `2.5) 取景` 段会在线校验
> 「视图中心 ≈ 原点」「几何完全落在视图内」「四周留白 ≥0.5mm」。

---

## 收尾：离线自检（不需要 Allegro）

```bash
python tools/selfcheck_skill_syntax.py        # SKILL 表达式构造器：212 项
python tools/selfcheck_skill_syntax.py -v     # 顺便打印每条表达式
python tools/selfcheck_geometry.py            # 参数→几何求解器：180 项（含金标准逐值对拍 + 尺寸口径 + 视图防线）
```

`selfcheck_geometry.py` 的基准全部可追溯：[规范] 建库规范 xlsx、[规则] `DEFAULT_RULES`、
[金标] `Tools\log\checklist_report_*.json` 实测值、[规格] 规格书 p5。
改求解器后**必须跑它** —— 它会挡住这三类曾真实发生过的回归：
"Place_Bound 按焊盘算""丝印按本体算""pin 编号方向反了"。

它检查：括号平衡、字符串闭合、纯 ASCII、AXL 函数白名单、**关键函数的参数顺序**。

参数顺序专项是重点 —— 这几个顺序写反了 Allegro **不会报错**，只会静默产出错误结果：

| 函数 | 正确顺序 | 写反的后果 |
|--|--|--|
| `axlDBCreateLine` | `(points width layer)` | 层名被当成宽度，线宽错乱 |
| `axlDBCreateRectangle` | `(bBox fill layer)` | fill 被当成层名 |
| `axlDBCreatePadStack` | `(name drill padList nocheck)` | 参数错位 |

---

## 踩过的坑（防复发清单）

这些是 probe 阶段实测确认的，全部写进了代码或自检脚本：

| 问题 | 真相 |
|--|--|
| `axlExtentSet` | **不存在**。正确是 `axlDBChangeDesignExtents(l_bBox)` |
| `axlNewDrawing` | **不存在**。新建 .dra 只能在界面里手工做 |
| `make_axlTextBlock` | **不存在**。text block 用名字字符串引用，如 `"1"` |
| `make_axlTextOrientation` 有坐标参数 | **错**。只有 `?textBlock/?rotation/?mirrored/?justify`，位置由调用方的 anchorPoint 给 |
| `make_axlPinText` 的 `?number` 传数字 | **错**。必须字符串，且不能含 `!` 等非法字符 |
| `make_axlPinText` 的 `?offset` 传标量 | **错**。必须是坐标对 `x:y` |
| pin 报 `SPMHA1-70: Pin is outside of the extents` | 不是 drawing 类型问题，是 extents 不含原点。先扩 extents |
| `axlPadstackToDisk` 落盘文件名 | Allegro 会**转小写**：`R1D4X1D2` → `r1d4x1d2.pad` |
| Allegro 路径含中文 | 不可靠。生成文件的操作走临时 ASCII 目录再拷回 |
| **`(axlFoo args)` 形式** | **必报 `eval: not a function`**。SKILL 里 `f(a)` 与 `(f a)` 是两种语义，AXL 函数不能当值取出。发代码必须用 `f(a)` |
| `ws["skill"]` | **不存在**。求值入口是私有的 `ws._channel.send()` |
| `_channel.send()` 只吃**单行** | Allegro 侧 `__pyOnData` 按 `\n` 逐行求值、每行回一条；多行会造成回复积压错位 |
| **`axlSaveDesign()` 不带 `?noConfirm t`** | 会弹交互框，把 SkillBridge 连接卡死（`ConnectionAbortedError 10053`） |
| `errset` 失败时 | 返回 `(nil)` —— 经 skillbridge 翻译后是 `[None]`，**不带错误信息**，错误定位困难 |
| **Allegro 回复管道有约 15 帧滞后** | 发第 k 条命令，读回的是第 (k-15) 条的结果。`Channel.send()` 是「发一收一」原子操作，队列长度守恒，**客户端无法自行排空** → 连续调试后出现"调用 A 返回上一次 B 的结果"时，**必须 `pyKillServer()` 重启服务** |

### ★ 2026-09-29 重大更正（推翻了此前几条错误结论）

| 此前的错误结论 | 更正后的真相（实测证据） |
|--|--|
| "AXL 没有列出 padstack 的 API"；`axlDBGetPadstacks()` 不存在 | **错**。`axlDBGetDesign()->padstacks` 完全可用：`length(...)` → `7`。函数名是 `axlDBGetPadstacks`（复数 s），且它是**宏**，只能内联用（`length(axlDBGetDesign()->padstacks)`），顶层裸调用才报 `eval: unbound variable` |
| "eval 通道里 `->` 属性穿透取不到值" | **错**。`->` 完全可用：`car(axlDBGetDesign()->pins)->number` → `'1'`，`->xy` → `[-1.1, 0.85]`。当初 `->drawing_type` 取不到值是因为**那个属性名不存在**（正确 API 是 `axlDesignType()`），不能推广成"属性访问一律失效" |
| `_channel.send()` 返回带 `success `/`failure ` 前缀的原始帧 | **错**。`TcpChannel.send()` → `_receive_only()` → **`decode_response()` 已剥前缀并在失败时抛异常**。所以 `send()` 返回的是**纯载荷字符串**：`'2\n'` / `'None\n'` / `'"probe_test"\n'` / `'Remote("__py_dbid_...")\n'` |
| 重名检测只看磁盘 `.pad` 文件 | **不够**。内存可持有磁盘上没有的 padstack（实测：磁盘 `set()` 为空，内存有 7 个含 `R1D4X1D2`）。**已改为三层探测**（见下） |
| `axlLayerGet` 不存在 / 层参数传对象 | 层名参数一律传**字符串**（如 `"PACKAGE GEOMETRY/SILKSCREEN_TOP"`）；`axlLayerGet("...")` 本身可用，返回层对象 |
| 图元创建成功可以 `if raw:` 判断 | **危险**。返回结构是 `((dbid) drc_flag)`，`if raw:` 在结构变化后会静默失效。已改用 `_is_creation_ok()` 按结构判定 |

### ★ padstack 存在性判定：三层探测

`footprint_builder._padstack_exists()` 按顺序短路：

1. **内存全量名单**（最可信）—— `mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)`
   一次往返拿到全部名字，同时服务重名检测与枚举报告。
2. **单名内存探测** —— `axlLoadPadstack("<name>")` → 存在返 remote object，不存在返 `nil`。
   **大小写不敏感**（实测 `R1D4X1D2` 与 `r1d4x1d2` 返回同一对象）。
3. **磁盘 `.pad` 文件**（双保险）—— 只能作为"存在"的**正向**证据；
   磁盘没有**不能**推出内存没有。

> `lambda` 写法注意：`lambda((ps) ...)` —— 形参表要再包一层括号；
> 形参名避开 `t` / `lst`（报 `lambda: t is reserved and cannot be used`）。

### ★ 图元创建（丝印 / Assembly / Place_Bound）失败排查

`axlDBCreateLine` / `axlDBCreateRectangle` **失败时不报错，只返回 nil**。按顺序排查：

1. **先 `change_drawing_extents`** —— extents 过小/未初始化是**最常见**原因。
   本工具已默认做「并集扩展」（见下），一般不会再踩。
2. 确认坐标落在 extents 内。
3. 若之前有操作**中途失败**，drawing 可能处于异常状态（连合法图元也建不出来）→
   `pyKillServer()` + `pyStartServer` 重启服务，把 drawing 恢复到干净状态再重试。

> **不要**归因于层名写错。实测 `PACKAGE GEOMETRY/SILKSCREEN_TOP`、
> `.../ASSEMBLY_TOP`、`.../PLACE_BOUND_TOP` 在本机均有效（`axlLayerGet` 都能取到对象）。

### ★ extents 必须"只扩不缩"

`axlDBChangeDesignExtents` 文档 + 实测：**新范围比现有内容小就返回 nil 失败**。

因此 `change_drawing_extents` 默认 `union_with_current=True`：
取「请求范围 ∪ 当前内容范围 + 2mm 余量」，保证幂等、只扩不缩、不会因
drawing 里已有图元而失败。需要精确设定（含收缩）时显式传 `False`。

> 当前内容范围读 `axlDBGetDesign()->bBox`（返回如 `[[-3.1,-2.85],[3.1,2.85]]`）。
> 注意 `->extents` 在本机**返回 None** —— 该属性名不在 `axlDBGetDesign` 的属性表里。

### 图形对象的统计盲区

`axlDBGetDesign()` 的属性表里**没有** `lines` / `shapes` / `rectangles`
（读它们恒为 `0`，**不代表没建成**）。可用属性只有：
`nets` `padstacks` `pins` `symbols` `symdefs` `text` `bus` `components`
`drcs` `groups` `designOutline` 等。

所以**验收图元是否落库**，用 `pins` / `text` / `padstacks` / `bBox` 作为间接证据，
不要依赖 `length(axlDBGetDesign()->lines)`。

---

## ★ API 存在性探测（不要相信文档）

### 为什么需要

Cadence 手册里有、但运行时**未绑定**的函数真实存在。走错一步就是几个小时的排查。

| 函数 | 文档 | 运行时（SPB 17.2-2016 实测）|
|--|--|--|
| `axlOpenDesign` / `axlOpenDesignForBatch` | ✅ 详细 | ❌ unbound |
| `axlKillDesign` | ✅ 有 | ❌ unbound |
| `axlSetDesignType` | 被提及 | ❌ unbound |
| `axlSaveDesign` | ✅ 有 | ✅ 可用（需 `?noConfirm t`）|
| **`axlCompileSymbol`** | ✅ 有 | ✅ **可用**（出 `.psm` 的正路）|
| `axlDBCreateSymbol*` / `axlWriteDeviceFile` | ✅ 有 | ✅ 可用 |
| `axlCompCreate` / `axlSaveSymbol` / `axlDBSaveSymbol` | ❌ 无 | ❌ unbound |

### ★ 弹框地雷清单（会让通道卡死）

| 调用 | 弹出的对话框 |
|--|--|
| `axlOpenDesign(?design "<不存在的路径>")` | Drawing Parameters |
| `axlSaveDesign()` 不带 `?noConfirm t` | 保存确认 |
| **`axlCompileSymbol(?symbol nil ...)`** | **「另存为」** |

**规律**：文档里写 *"you are prompted for the name"* 的，传 `nil` 必弹框。
**凡是能显式传名字/加 `?noConfirm t` 的，一律显式传。**

### ★ 探测方法（三轮踩坑才定论）

**错误做法**（都会给出错误结论）：

| 做法 | 问题 |
|--|--|
| `errset(axlFoo)` 裸符号 | **恒报** `unbound variable` —— 连 `axlSaveDesign`（能用）也报 |
| 发裸名字 `axlFoo` | 同上。Allegro 侧 `evalstring` 把裸符号当**变量**求值，必 unbound |

**✓ 唯一可靠**：真调用 `axlFoo(<无害参数>)`，按错误类型判定：

```
报 "undefined function"   -> 不存在
报 "unbound variable"     -> 不存在
报其它任何错误            -> ★存在（已绑定，只是参数/环境不对）
正常返回                  -> 存在
```

★ **必须区分连接层错误**：`TimeoutError` / 无法连接 → **不能判定存在性**，且应
立即停止后续探测（否则全是噪音）。

### 工具

```bash
python tools/probe_api_exists.py axlCurrentDesign          # 单个
python tools/probe_api_exists.py --suite symbol            # 内置套件
python tools/probe_api_exists.py --suite server            # 服务控制类
python tools/probe_api_exists.py --file candidates.txt     # 批量
```
套件：`calib`（校准）/ `symbol` / `design` / `server` / `server-danger`（危险）

**惯例**：先跑 `--suite calib` 做对照，确认探测法本身有效再测目标。

---

## ★ SkillBridge 服务重启（fire-and-forget）

### 核心矛盾

服务一被杀，Python 侧 socket 就断了 → **再也没法用 Python 发命令启动它**。

> **硬前提：必须在服务还活着的时候，把重启命令一次性发出去。**

### 正确做法

```skill
prog1(pyKillServer() load("<python_server.il>") pyStartServer ?id "7777" ?python "<pythonw.exe>" ?forceTcp t)
```

- `prog1` 顺序求值，把「杀 → 重载 → 启动」串成一条（`pyReloadScript` 只重载不启动）
- **必须单行**（SkillBridge 按 `\n` 逐行求值，多行会回复错位）
- ★ 回复**永远收不到**（服务已被杀）→ 属预期，不是失败
- 发完立刻重连轮询

```bash
python tools/restart_skillbridge.py            # 预览
python tools/restart_skillbridge.py --execute  # 真重启
```

### 服务已死时（自动化无能为力）

只能在 **Allegro CIW** 里手工恢复：

```skill
load("<...>/skillbridge/server/python_server.il")
pyStartServer ?id "7777" ?python "<...>/.venv/Scripts/pythonw.exe"
```

### ★ 弹框 API 禁令

**任何可能弹模态对话框的 AXL 调用，在自动化通道里都等于自杀** ——
调用永不返回，回复队列彻底错位，之后所有调用报「无法对齐」。

| 地雷 | 后果 |
|--|--|
| `axlOpenDesign(?design "<不存在的路径>")` | 弹 Drawing Parameters 框，通道卡死 |
| 不带 `?noConfirm t` 的 `axlSaveDesign()` | 弹确认框，报 `ConnectionAbortedError(10053)` |

### ★ 通道固定滞后

Allegro 侧 `ipcWriteProcess` 到 Python 服务端 `stdin` 之间有**固定约 15 帧**的管道缓冲：
发第 k 条命令，读回来的是第 (k-15) 条的结果。
客户端 `Channel.send()` 是「发一收一」原子操作，队列长度守恒 ——
**任何发送组合都无法缩短滞后，唯一解法是重启服务**。

---

## SKILL 语法速查

```skill
;; 符号 vs 字符串 —— 大部分 ?type / ?figure 参数要符号
make_axlPadStackPad(?layer "TOP" ?type 'REGULAR ?figure 'RECTANGLE ?figureSize 1.4:1.2)
;;                                                                            ^^^^^^^^^^^ 坐标对用冒号

;; 可选参数用 ?key value，没有 end 关键字，块结束用右括号
;; ?figureSize 的数值单位 = 当前 drawing 的 design units（mm/mil 差 25.4 倍）

;; ★ 属性穿透 -> 与 mapcar/lambda 在 eval 通道里都可用
car(axlDBGetDesign()->pins)->number                          ;; -> '1'
car(axlDBGetDesign()->pins)->xy                               ;; -> [-1.1, 0.85]
mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)     ;; -> ['R1D4X1D2', ...]
;;      ^^^^^^^^^^ 形参表再包一层括号；形参名避开 t / lst

;; errset 语义（注意：经 skillbridge 翻译后失败是 [None]，不带错误信息）：
;;   成功 -> (结果)          -> Python 里是 [结果]
;;   失败 -> (nil 错误信息)   -> Python 里常见 [None]
errset(axlDBCreatePin("R1D4X1D2" -1.1:0.85 ...))

;; ★ 图元创建返回结构（不要用 if raw: 判断）
;;   axlDBCreateLine    -> ((dbid) drc_flag)   -> Python [[obj], None]
;;   axlDBCreateRectangle -> (dbid drc_flag)   -> Python [obj, None]
axlDBCreateLine(list(list(-1 -1) list(1 -1)) 0.15 "PACKAGE GEOMETRY/SILKSCREEN_TOP")
axlDBCreateRectangle(list(list(-1 -1) list(1 1)) t "PACKAGE GEOMETRY/PLACE_BOUND_TOP")

;; ★ 函数调用用 f(a)；括在括号里的 (f a) 会报 eval: not a function
axlDBGetDesignUnits()      ;; ✓
(axlDBGetDesignUnits())    ;; ✗ eval: not a function
```

SKILL 文档位置：`<Cadence>\doc\algroskill\`（如 `03dbcre8.html` 图元创建、`02dbdesc.html` 设计属性表、`14dsnctl.html` drawing 控制）
官方范例：`<Cadence>\share\pcb\examples\skill\dbcreate\`（`axldbctpin.il` 是 pin/文字的金标准）

---

## 已知限制

1. **新建 .dra 需人工** —— 见上文（无 `axlNewDrawing`）。
2. **异形焊盘（SHAPE）需预置 `.ssm`** —— `figure="SHAPE"` 时要先有 shape symbol 文件。
3. **`.psm` 需在 Allegro 里 Create Symbol** —— 本 MCP 只负责画图纸内容。
4. **只支持 Package symbol 图纸** —— `axlDBCreatePin` 在 board 上无效。
5. **未做 DFM 规则校验** —— 生成是生成，规则检查走 `layout-check-mcp` 的 `check_footprint_by_rules`。
6. **无法枚举图元** —— `axlDBGetDesign()` 不暴露 lines/shapes/rectangles，
   只能通过 `pins`/`text`/`padstacks`/`bBox` 间接验收。
7. **不覆盖已有对象** —— `axlDBCreatePadStack` 遇重名静默失败（已加三层探测）。
   图元（线/矩形/文字）**重复调用会叠加**，不是替换；反复迭代会产生重复图形，
   建议在干净图纸上一次性生成，或在 Allegro 界面里手工清理后重跑。

