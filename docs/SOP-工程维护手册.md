# layout-forge-mcp 工程维护手册（部署 / 排障 / 变更）

> 最后更新：**2026-10-12**
>
> 定位：给**维护这个项目的人**看（部署、排障、改代码后该跑什么）。
> 给老板/使用者看的操作说明在 `docs/封装自动生成工具-SOP.md`，两份不重复。

---

## 1. 系统概览

### 1.1 三层分工

| 层 | 位置 | 职责 |
|---|---|---|
| Agent（客户端） | WorkBuddy / Trae / Claude Desktop 等 | 承载 Skill 流程、调用 MCP 工具、**负责语义判断**（读规格书提参数、命名含义、极性标识） |
| MCP 服务 | `mcp_server/` | 暴露 **31 个 tool** + resource + prompt，**负责数值计算**（几何求解、规则核验、PDF 渲染、归档） |
| 业务源码 | `services/` | 求解器、建造器、extracta / SkillBridge 读写、PDF 服务、spec 库、规则表 |

核心原则：**数值由代码算，语义由 AI 判断。**
凡能用数字精确算的（阻焊 +0.05、Place_Bound 外扩、原点居中、字块）走 `services/`；
凡是要"看懂图纸"的（这份规格书第几页、F 是中心距还是总长）走 AI。

### 1.2 与 layout-check-mcp 的关系（2026-10-09 核准）

`Layout\layout-check-mcp` 是**旧版**，本项目由它复制而来并**单向增强**：

| 判定 | 内容 |
|---|---|
| 逐字节相同 10 件 | `mcp_server/{config,prompts,resources,__init__}`、`services/{pdf_locate,rule_checker,skillbridge_reader}`、`services/{json_utils,validation}`（原 `shared/` 3 件，2026-10-12 并入 services） |
| 本项目单方增强 3 件 | `extracta_reader` 598→646（+REF DES 第 4 路视图）、`pdf_service` 136→264（+contact sheet）、`footprint_extractor` 128→182（+缺口检测） |
| 工具覆盖 | 本项目 31 个 ⊇ 旧版 7 个，**7/7 全覆盖** |

→ **旧版项目不再维护**，保留在 `Layout\` 下仅作历史参考。
→ 唯一遗留风险已消除：`rule_checker.py`（1114 行）曾在两边各存一份，改规则时"改哪边"不确定；现在只认**本项目这一份**。

### 1.3 两个 Skill 共用一个 MCP 服务

| Skill | 用途 | 服务 |
|---|---|---|
| `allegro-skillbridge-ops` | **建封装**（读规格书 → 算几何 → 出国纸 → 归档） | `layout-forge-mcp` |
| `allegro-footprint-check` | **查封装**（已有 `.dra` 与 datasheet 对拍 → 出报告） | `layout-forge-mcp` |

两者**不合并**，因为知识集几乎不重叠（见 §7.3）。

### 1.4 数据源

| 数据源 | 是否需要 Allegro 运行 | 说明 |
|---|---|---|
| **SkillBridge（7777）** | **需要** | 建封装必经；`pcbenv\allegro.ilinit` 已注入启动段 |
| **extracta.exe（读 `.dra`）** | **不需要** | Cadence 自带，从 CDSROOT 自动定位。**回读/检查走这条** |
| PyMuPDF / pdfplumber | 不需要 | PDF 渲染（给 AI 读图）与文本层定位 |

回退逻辑（`services/footprint_extractor.py`）：extracta 失败或数据不完整 → 自动回退 SkillBridge。
调试开关：`LAYOUT_CHECK_FORCE_SOURCE=extracta|skillbridge`。

---

## 2. 术语与结论口径

| 术语 | 含义 |
|---|---|
| **spec** | 一颗料的**纯参数清单**（JSON）。只描述"这是什么器件"，不含任何"怎么画"的规则 |
| **solver** | `services/geometry_solver.py`，把 spec 翻译成成品几何的规则引擎 |
| **rule_checker** | `services/rule_checker.py`，独立跑 17 项规则核验（判 PASS/FAIL，不参与生成） |
| **padstack** | 焊盘栈：TOP 铜箔 / SOLDERMASK_TOP 阻焊（单边 +0.05）/ PASTEMASK_TOP 钢网（等宽） |
| **extents** | 图纸绘图范围。**硬边界**，图元越界会被静默丢弃（不报错） |
| **contact sheet** | 整份 PDF 拼成一张缩略图，用于"文本层定位不到尺寸页"时一次读图定位 |
| **影子库** | 归档落点。锚点 = **规格书所在目录**，其下建与符号同名的子目录 |

**尺寸取值口径**：焊盘布局图给的是标注值 → 直接用；本体尺寸查 MIN/MAX 表 → **取 MAX**。
（这套口径在 `selfcheck_geometry.py` 里有专项断言，改代码别绕过自检。）

---

## 3. 部署 SOP

### 3.1 前置条件

| 项 | 要求 |
|---|---|
| Python | ≥ 3.10（实测 3.10 与 3.13 均可；`pyproject.toml` 已放开 `<3.13` 的历史上限） |
| Allegro | SPB 17.2+ |
| SkillBridge | Python 侧 `skillbridge` 包 + Allegro 侧 `allegro.ilinit` 注入 |
| 依赖 | `fastmcp` / `skillbridge` / `PyMuPDF` / `pdfplumber` |

### 3.2 ★ 唯一的硬性坑：解释器必须选对

**`skillbridge` 只装在项目 venv 里。** 实测系统 Python 与 WorkBuddy 托管 Python
都 `import skillbridge` 失败。MCP 注册的 `command` 必须指向：

```
C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp/.venv/Scripts/python.exe
```

### 3.3 一键部署（新机器走这条）

```bat
Tools\deploy\deploy.bat                          :: 双击即可
Tools\deploy\deploy.bat /dir "D:\tools\lfm"      :: 指定安装目录
Tools\deploy\deploy.bat /pcbenv "D:\...\pcbenv"  :: 手工给 pcbenv
Tools\deploy\deploy.bat /dry                     :: 只预览不改文件
```

脚本干六件事：定位项目 → 查/装 uv·git → 取代码（local 原地 / clone 模式）
→ 建 `.venv` 装依赖 → 注册 MCP + 装 Skill + 配 SkillBridge → 冒烟验收。
逐步中文对照见 `Tools\deploy\README.md`。

只跑 Agent 侧配置（环境已建好时）：

```bash
.venv\Scripts\python.exe Tools\deploy\setup_agent.py --dry-run
```

### 3.3.1 手工建环境与装依赖（脚本失败时的退路）

```bash
cd C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp

# 建 venv（任选其一）
.venv\Scripts\python.exe -m venv .venv          # 已有则跳过
uv venv                                          # 用 uv 也行

# 装依赖
.venv\Scripts\python.exe -m pip install -e .     # 按 pyproject.toml
```

SkillBridge 自动加载由 `Tools\deploy\setup_agent.py` 的 C 步负责，
等价于旧版的 `deploy_skillbridge.bat`（已移到 `Tools\deploy\_legacy\`）。

### 3.4 部署后验收清单

```bash
# ① 离线自检（不需要 Allegro）—— 必须全绿
.venv\Scripts\python.exe Tools\selfcheck_geometry.py       # 239/239
.venv\Scripts\python.exe Tools\selfcheck_skill_syntax.py    # 222/222

# ② MCP 工具注册数（离线，不需要 Allegro）
#    期望 28
```

```bash
# ③ 在线探活（需要 Allegro 已启动）
#    走 MCP 调 inspect_allegro_environment() → connected: true
```

---

## 4. 配置 Agent

### 4.1 注册 MCP（`~/.workbuddy/mcp.json`）

```json
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
```

★ 用 `-c` 注入 `sys.path` 是必须的：客户端拉起 MCP 时 cwd 不确定，
不注入会 `ModuleNotFoundError: mcp_server`。

★ 这段配置**不需要手写**：`Tools\deploy\setup_agent.py` 会自动生成，
并且是「合并写入 + 先备份」，不会冲掉你已有的其它 server。
命令行等价物：`.venv\Scripts\python.exe Tools\deploy\setup_agent.py --dry-run`。

### 4.2 挂载 Skill

两个 Skill 都在 `C:\Users\ds245\.workbuddy\skills\` 下：

- `allegro-skillbridge-ops`（建封装）
- `allegro-footprint-check`（查封装，2026-10-09 由 `footprint-tolerance-check` 改名而来）

### 4.3 ★ 改代码后必须重连

MCP server 由客户端**拉起并长驻**，进程内缓存着启动时的代码。
改了任何 `services/` 或 `mcp_server/` 的文件后，**重启客户端 / 重连该连接器**才生效。

> 与旧版不同：本项目 `mcp_server/config.py` **没有 `MCP_VERSION` 字段**，
> 无法靠版本号判断有没有吃到新代码。判据是**重连后看工具总数是否为 28**，
> 或直接调一个新加的工具（如 `list_specs`）看是否报"未知工具"。

---

## 5. 日常使用 SOP

### 5.1 建封装（走 MCP 工具链，不要跑 `Tools/e2e_*.py` 绕过）

```
inspect_allegro_environment()          ← 先探
locate_land_pattern_page(pdf)          → 定位尺寸页（失配时用 render_pdf_contact_sheet）
render_pdf_page(pdf, page)             → 渲染供 AI 读图
  … AI 读图产出参数 …
load_spec / find_spec                  → ★ 先查库，同封装零读图
solve_footprint_geometry(...) 或 build_footprint_from_params(...)
  ↓ ★ change_drawing_extents 必须先扩，否则图元静默丢弃
save_footprint_drawing() → export_psm() → check_footprint_by_rules()
save_spec(...)                         → ★ 建完存档
install_shadow_library(...)            → 归档（锚点 = 规格书所在目录）
```

### 5.2 收尾铁律（A + B，两件独立的事）

| | 动作 | 说明 |
|---|---|---|
| **A** | 关 Allegro 进程 | `.venv\Scripts\python.exe Tools\stop_allegro.py` |
| **B** | 收回后台挂住的 shell | 启动 Allegro 的那条后台任务要单独停 |

★ 实测修正：**`TaskStop` 掉后台任务不会带走 Allegro**（`start_allegro.cmd` 用
`start "" allegro.exe`，进程已脱离父树）。所以 A 必须单独做，别指望 B 顺带完成。

### 5.3 查封装

走 `allegro-footprint-check` Skill。两条硬口径：

1. 与 datasheet 比对必须用 **ETCH/TOP 铜箔**尺寸，不能用阻焊开窗（后者单边大 0.05mm 会误判）；
2. `check_footprint_by_rules` 返回的 **`markdown` 字段原样贴出**，禁止改写、加结论、加前后缀。

### 5.4 产物说明

```
Tools\test\<符号名>\
├── <规格书>.pdf
├── <符号名>.dra
├── <符号名>.psm（小写落盘）
├── *.pad
└── README.md（清单 + md5 + 校验记录）
```

根目录残留经 sha1 核对后移入 `.trash\`，不直接删。

---

## 6. 排障手册

| 现象 | 原因 | 处理 |
|---|---|---|
| MCP 报「连不上 Allegro」 | Allegro 没起 / 不是由桌面会话启动的 | 用 `Tools\start_allegro.cmd` **后台挂住**启动；再 `inspect_allegro_environment()` |
| 图元画了但图纸上没有 | **extents 硬边界**，越界静默丢弃 | 画之前先 `change_drawing_extents` 扩范围 |
| 读回来的值总是"上上次"的 | 通道固定约 15 帧滞后（认知问题，非故障） | 重启 SkillBridge 服务 |
| 引脚号字号随脚号变大 | `make_axlPinText` 简化写法 `?text "N"` 被当成字块号 | 已修：强制结构体形式 `textBlock "1"`；自检有回归项 |
| `locate_land_pattern_page` 返回 null | 扫描件无文本层（实测 13 份规格书只有 4 份强命中） | 调 `render_pdf_contact_sheet` 一次读图定位 |
| 改了代码没生效 | MCP 进程缓存旧代码 | 重连 MCP（见 §4.3） |
| 归档落在 `Data\footprint\` | `install_shadow_library` 的 `lib_root` 默认值 | 显式传 `lib_root=<规格书所在目录>` |
| 路径含中文导致异常 | Allegro 不支持非 ASCII 路径 | 换英文路径 |

诊断工具：

```bash
.venv\Scripts\python.exe Tools\debug\test_skillbridge.py   # 通道体检
.venv\Scripts\python.exe Tools\dump_all_footprints.py      # 批量扫库分析命名规则
```

---

## 7. 维护与变更 SOP

### 7.1 改哪里 → 影响什么

| 改这个文件 | 影响 | 改完必须跑 |
|---|---|---|
| `services/geometry_solver.py` | 所有器件的几何（焊盘/本体/丝印/外扩/原点） | `selfcheck_geometry.py`（239 项，含 3 份内置 spec 基线） |
| `services/rule_checker.py` | 17 项规则判定 | `selfcheck_geometry.py` + 对一颗已知件重跑 `check_footprint_by_rules` |
| `services/skill_snippets.py` | 发给 Allegro 的 SKILL 表达式 | `selfcheck_skill_syntax.py`（222 项）+ **实机建一颗件** |
| `services/footprint_builder.py` | 建封装执行流程 | 实机 e2e（如 `Tools\e2e_cstj421.py`） |
| `services/extracta_views/*.txt` | 回读视图（增删视图会改变输出文件对应关系） | 回读一颗已知 `.dra` 对拍 |
| `services/spec_library.py` | spec 库读写 / 指纹匹配 | 冒烟：`save` → `load` → `find` round-trip |
| `mcp_server/tools.py` | MCP 工具签名与参数透传 | 离线注册检查（工具总数）+ 改完**重连 MCP** |
| `knowledge/specs/*.json` | 已存档器件的解析成果 | 对应器件的 `solve` 结果不应变化 |

### 7.2 发布流程

1. 改代码 → 2. 跑两项自检（必须全绿）→ 3. 实机建一颗件验证 → 4. 更新相关 `README.md` → 5. 重连 MCP 验证工具数 28。

### 7.3 两个 Skill 为什么不合并

判据不是"能力是否被覆盖"，而是**「会不会一起被读」**：

| | allegro-skillbridge-ops（建） | allegro-footprint-check（查） |
|---|---|---|
| 独占知识 | extents 静默丢弃、Allegro 启停与通道滞后、归档口径、字块坑、spec 库 | ETCH 铜箔 vs 阻焊开窗口径、报告原样贴出契约、AI 语义分工、防编造铁律 |
| 输出 | `.dra`/`.psm`/`.pad` + 归档 | 一份给用户的 markdown 报告 |

合并会导致：触发词打架、每次触发都载入双方知识、body 膨胀。
**建完自检 ≠ 独立验收**，两者是同一个工具（`check_footprint_by_rules`）的两种用法。

### 7.4 已知文档偏差（维护时一并修）

- `mcp_server/config.py` **无 `MCP_VERSION`**（旧版项目有）。别照抄旧版 SOP 里"更新版本号"那一步。
- `config.py` 里 PDF 渲染输出目录变量叫 `RENDER_OUTPUT_ROOT`，落盘目录为 `Tools/log/render/`（历史名 `marker_out`，2026-10-12 迁入 Tools/log）。
- `Tools/debug/` 下脚本定位项目根要用**上三级**（`parent.parent.parent`），迁入时修过。

---

## 8. 硬约束（执行者必须遵守）

1. **建封装一律走 MCP 工具**，禁止越过服务层直接跑 `Tools/e2e_*.py`。
2. **收尾必须 A+B 都做**（关进程 + 收后台），二者独立，不能互相替代。
3. **禁止调用会弹模态对话框的 API** —— 会把 SkillBridge 通道彻底搞死。
4. **画任何图元之前先扩 extents**，否则静默丢弃且不报错。
5. **编译成功判据以磁盘产物为准**（`axlCompileSymbol` 实测恒返回 nil，不能当判据）。
6. **禁止用 `os.remove` 覆盖产物**，走 `.trash` 隔离。
7. **数值必须来自工具实际返回**，不得凭历史记忆推断、不得由理论值反推"实测值"。
8. **检查报告原样贴出 `markdown` 字段**，不得改写或加前后缀。
9. **路径含中文提醒用户换英文路径。**

---

## 9. 附录

### 9.1 MCP 工具速查（31 个）

**建封装（22）**：`inspect_allegro_environment`、`new_footprint_drawing`、`new_symbol_drawing`、
`clear_footprint_drawing`、`create_padstack`、`add_footprint_pin`、`add_footprint_outline`、
`add_footprint_text`、`change_drawing_extents`、`frame_drawing_view`、`save_footprint_drawing`、
`export_psm`、`build_footprint_from_params`、`solve_footprint_geometry`、
`validate_land_pattern_json`、`install_shadow_library`、`list_shadow_library`、
`render_pdf_contact_sheet`

**spec / 案例库（7）**：`save_spec`、`load_spec`、`find_spec`、`list_specs`、
`save_case`、`find_case`、`list_cases`

**读 PDF / 目录（4）**：`locate_land_pattern_page`、`render_pdf_page`、`list_subfolders`、`list_folder_files`

**检查（2）**：`read_full_footprint`、`check_footprint_by_rules`

> 18 + 7 + 4 + 2 = 31。分类是人为划分：`list_folder_files`、`render_pdf_page` 等
> 在建、查两条链路都会用到。
>
> ★ 阈值类参数外置在 `knowledge/rules/checker_rules.json`（2026-10-12 起），
> 改 JSON 立即生效、不用重启 MCP；判定逻辑仍在 `rule_checker.py`。

### 9.2 目录说明

| 目录 | 内容 |
|---|---|
| `knowledge/specs/` | ★ spec 库（`*.json` + `_index.json` + `README.md`） |
| `knowledge/cases/` | ★ 检测案例库（指纹 + 报告摘要 + **人工裁定** + 教训），同族件自动召回 |
| `knowledge/rules/` | ★ 规则阈值外置（`checker_rules.json`），改数字不改代码 |
| `mcp_server/` | MCP 服务（tools / config / resources / prompts / server） |
| `services/` | 业务源码（solver / builder / rule_checker / extracta / skillbridge / pdf / spec_library / case_library / json_utils / validation） |
| `Tools/` | CLI 脚本（自检 / e2e / 启停 / 归集 / 归档） |
| `Tools/test/` | 图纸干活区，每个符号一个子目录 |
| `Tools/log/` | 日志归集区 |
| `Tools/debug/` | 排障脚本（项目根是上三级） |
| `Tools/log/archive/` | 历史渲染输出归档（原 `marker_out/`，2026-10-12 迁入；`RENDER_OUTPUT_ROOT` 见 9.3） |
| `.trash/` | 隔离区（不直接删） |
| `Tools/deploy/` | 部署脚本（2026-10-12 从根 `deploy/` 迁入） |

### 9.3 关键环境变量

| 变量 | 作用 |
|---|---|
| `LAYOUT_CHECK_FORCE_SOURCE` | `extracta` / `skillbridge` —— 强制回读数据源 |
| `LAYOUT_CHECK_EXTRACTA_STRICT` | `=1` 时 extracta 的任何缺口都算不完整 → 回退（默认关） |
| `LAYOUT_CHECK_CDSROOT_DEBUG` | CDSROOT 定位调试 |
| `FORGE_NOCONFIRM` | 建封装时抑制交互式确认 |
| `CDS_INST_DIR` / `HOME` | Allegro 安装目录 / 用户 Home（CDSROOT 反推用） |
