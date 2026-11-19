# layout-forge-mcp 工程维护手册（部署 / 排障 / 变更）

> 最后更新：**2026-10-16**（★ 重建环境实测：`uv sync --frozen` 本身只要 **9.5 秒**，但「建好」≠「能用」——
> 同轮补齐 §3.5 **三处外部引用清单**（`~/.workbuddy/mcp.json` / `allegro.ilinit` / `cordis.patch.yml`）、
> `--venv-python` 参数、沙箱解释器**写不了** `%USERPROFILE%\.workbuddy\*\*` 的坑（§3.6）、
> 以及「收尾没关 Allegro → 它锁着 `allegro.jrl` 挡隔离」（§6）；顺手把 §1.1/§4.3/§9.1 里过期的 **31/28 工具数**对齐到 **36**。
> 此前：★ 新增排障铁律：**先看退出码** —— GBK 控制台下 `print("✓")` 会让「五套全绿」反而 exit 1
> （红分支打 ASCII 所以正常），六个入口已加 `_force_utf8_stdout()`；此前：Phase 3：`orchestration/playbook.json`（规则装配表）+ `pipeline.py`（状态机）落地，`behavior` 加 **B5** 守这两份东西（夹具 5/5），§7.2 补「改 playbook / pipeline 必须复跑 behavior」；此前：Phase 2-d：发布检查收成**一条命令** `python eval\check_eval.py`（五套断言 + 元判据 E1–E4）；此前：七项检查 + `behavior` 行为夹具（S1/S2/S4 可执行化）+ `preflight` 噪声规则扩到「运行痕迹 + 历史报告」→ G4 现形 145 个噪声文件（属真发现），**当日全部隔离进 `.trash/` 后复绿、判据未放宽**；再此前：六项检查 + `spec_reading` 读图准确率 92.5% + SKILL 三处落点 + `.venv`/解释器坑 + 沙箱解释器会让回归评分假红）
>
> 定位：给**维护这个项目的人**看（部署、排障、改代码后该跑什么）。
> 给老板/使用者看的操作说明在 `docs/封装自动生成工具-SOP.md`，两份不重复。

---

## 1. 系统概览

### 1.1 三层分工

| 层 | 位置 | 职责 |
|---|---|---|
| Agent（客户端） | WorkBuddy / Trae / Claude Desktop 等 | 承载 Skill 流程、调用 MCP 工具、**负责语义判断**（读规格书提参数、命名含义、极性标识） |
| MCP 服务 | `mcp_server/` | 暴露 MCP tool + resource + prompt，**负责数值计算**（几何求解、规则核验、PDF 渲染、归档） |
| 业务源码 | `services/` | 求解器、建造器、extracta / SkillBridge 读写、PDF 服务、spec 库、规则表 |

核心原则：**数值由代码算，语义由 AI 判断。**
凡能用数字精确算的（阻焊 +0.05、Place_Bound 外扩、原点居中、字块）走 `services/`；
凡是要"看懂图纸"的（这份规格书第几页、F 是中心距还是总长）走 AI。

### 1.2 与 layout-check-mcp 的关系（2026-10-09 核准）

`Layout\layout-check-mcp` 是**旧版**，本项目由它复制而来并**单向增强**：

| 判定 | 内容 |
|---|---|
| 逐字节相同 10 件 | `mcp_server/{config,prompts,resources,__init__}`、`services/{pdf_locate,rule_checker,skillbridge_reader}`、`services/{json_utils,validation}`（原 `shared/` 3 件，2026-10-12 并入 services） |
| ★ 更正（2026-10-14 实测） | 上表的「相同 10 件」是 **2026-10-13 的快照**，当天复测只剩 **4 件逐字节相同**：`pdf_locate.py`、`cdsroot_locator.py`、`allegro_service.py`、`report_renderer.py`；**6 件已分叉**：`pdf_service`（19.9 KB vs 4.2 KB）、`pdf_renderer`（4.6 vs 3.9）、`footprint_extractor`（7.6 vs 4.6）、`extracta_reader`（33.4 vs 21.0）、`skillbridge_reader`（14.7 vs 13.0）、`rule_checker`（60.9 vs 38.2）。改前 4 件仍要两仓同轮改，后 6 件只看本仓 |
| 本项目单方增强 3 件 | `extracta_reader` 598→646（+REF DES 第 4 路视图）、`pdf_service` 136→264（+contact sheet）、`footprint_extractor` 128→182（+缺口检测） |
| 工具覆盖 | 本项目全部工具 ⊇ 旧版 7 个，**7/7 全覆盖** |

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
Tools\deploy\deploy.bat /nopull                  :: local 模式：连 git pull 也不试（纯离线）
Tools\deploy\deploy.bat /dry                     :: 只预览不改文件
```

脚本干六件事：定位项目 → 查/装 uv·git → 取代码（local 原地 / clone 模式）
→ 建 `.venv` 装依赖 → 注册 MCP + 装 Skill + 配 SkillBridge → 冒烟验收。
逐步中文对照见 `Tools\deploy\README.md`。

★ **本地离线部署（v7.5 起）**：文档开头那条
`curl -L -o deploy.bat <raw url> && deploy.bat` 现在**在项目内不会下载任何东西**。
第 0 步按由近及远三档定位，**任一档命中就不访问 GitHub**：

| 档 | 条件 | 行为 |
|--|--|--|
| (a) | `..\..\pyproject.toml` 存在（bat 正坐在 `<检出>\Tools\deploy\`） | local：原地部署 |
| (b) | `%~dp0\pyproject.toml` 存在（bat 被 curl 下到检出根） | local：原地部署 |
| (c) | 两处都没有 | clone：从 GitHub 取到 `/dir` |

local 模式下第 4 步会尝试 `git pull`，但**git 缺失或 pull 失败都只 WARN，
不影响后面任何环节**（代码已在本地）；不想联网就加 `/nopull`。

★★ **第 5 步永不删除 `.venv`（v7.7 起，2026-10-27 真实事故后修）**：
旧脚本用 `python.exe --version` 探测版本，但该参数会被 argparse 直接吃掉、
输出为空 → 误判「Python 不可用」→ `rmdir /S /Q .venv` → 删完重建失败，
把一个健康环境（~9000 文件）打剩 35 个文件。现在：
- 探测改用 `-V`；
- 即使探测失败也**只隔离不删除** —— `.venv` 整体移入 `.trash/venv-broken-<时间戳>/`；
- 搬不动（被 Allegro 等占用）就**停下报错**，绝不留下半个环境。

踩坑详情与排查表见 `Tools\deploy\README.md` 坑 17。

只跑 Agent 侧配置（环境已建好时）：

```bash
.venv\Scripts\python.exe Tools\deploy\setup_agent.py --dry-run
```

### 3.3.1 手工建环境与装依赖（脚本失败时的退路）

```bash
cd C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp

# ★ 首选：按 uv.lock 精确复现（本项目有 uv.lock，2026-10-16 实测 9.5 秒、exit 0）
uv sync --frozen

# 退路（uv 不可用时，任选其一建 venv）
.venv\Scripts\python.exe -m venv .venv          # 已有则跳过
uv venv                                          # 用 uv 也行

# 退路装依赖
.venv\Scripts\python.exe -m pip install -e .     # 按 pyproject.toml
```

★ **建完不要直接收工** —— 还有 §3.5 的三处外部引用要跟着改。
★ `uv sync` 会装 `pymupdf / pdfplumber` 等 PDF 依赖；`fastmcp` 版本随锁文件（实测 4.0.10），
  与 MCP 工具注册 API 相关，**不要手工 `pip install -U fastmcp`**（会偏离锁文件）。

SkillBridge 自动加载由 `Tools\deploy\setup_agent.py` 的 C 步负责，
等价于旧版的 `deploy_skillbridge.bat`（已移到 `Tools\deploy\_legacy\`）。

### 3.4 部署后验收清单

```bash
# ① 离线自检（不需要 Allegro）—— 必须全绿
.venv\Scripts\python.exe Tools\selfcheck_geometry.py       # 239/239
.venv\Scripts\python.exe Tools\selfcheck_skill_syntax.py    # 222/222

# ② MCP 工具注册数（离线，不需要 Allegro）
#    期望 36（2026-10-14 起；此前 34/31/28）
```

```bash
# ③ 在线探活（需要 Allegro 已启动）
#    走 MCP 调 inspect_allegro_environment() → connected: true
```

### 3.5 ★ 重建环境：`uv sync` 之后还必须做的三件事

**`uv sync` 只保证「包装在磁盘上」，不保证「客户端能用」。** 2026-10-16 全量重建实测：
`uv sync --frozen` 自身 **9.5 秒**（exit 0，`uv.lock` 不变），但同一个 16 分钟窗口里
974 秒只有 249 秒在跑工具 —— 其余是验证与修引用。**漏掉下面任一处，表现都是
「MCP 连上了但没有工具 / 工具报 Unknown tool」，看起来像代码坏了，其实是配置没跟着走。**

| # | 外部引用 | 位置 | 谁来写 | 漏掉的症状 |
|---|---|---|---|---|
| 1 | WorkBuddy MCP 注册 | `C:\Users\ds245\.workbuddy\mcp.json` | `Tools\deploy\setup_agent.py`（A 步，合并写入 + 先备份） | 客户端里整个 server 不见 |
| 2 | SkillBridge 自动加载 | `D:\Dev_tools\Cadence_17.2\Cadence\SPB_Data\pcbenv\allegro.ilinit` | 同上（C 步，调 `Tools\install_skillbridge.py`） | 建封装报「连不上 Allegro」 |
| 3 | DSH 侧连接器 | `C:\Users\ds245\.dsh\profiles\tauri\cordis.patch.yml`（`insert: - id: mcp-layout-forge-mcp`） | **手工**，非本仓库 | DSH 工具列表里没有本项目的工具 |

三处都指向**同一个**路径（`layout-forge-mcp\.venv\Scripts\python.exe`），重建后逐个对照：

```bash
# ① 注册是否正确（应看到项目 .venv 的绝对路径，不是临时解释器）
python -c "import json,os;d=json.load(open(os.path.expanduser('~/.workbuddy/mcp.json'),encoding='utf-8'));print(d['mcpServers']['layout-forge-mcp']['command'])"

# ② ilinit 是否正确（应看到 .venv\Scripts\pythonw.exe 与 python_server.il）
findstr /C:"Python:" /C:"SKILL:" "D:\Dev_tools\Cadence_17.2\Cadence\SPB_Data\pcbenv\allegro.ilinit"

# ③ 工具数（离线，正确应为 36）
.venv\Scripts\python.exe -c "import sys,asyncio;sys.path.insert(0,'.');from mcp_server.server import mcp;print(len(asyncio.run(mcp.list_tools())))"
```

★ **改 DSH 配置（第 3 处）后必须重启宿主 / 刷新页面才生效**；MCP 注册（第 1 处）改完要重连客户端（§4.3）。

### 3.6 ★ 沙箱里跑部署脚本：必须带 `--venv-python`

**两个约束互相打架，这是本机重建时最容易踩的坑：**

| 约束 | 事实 |
|---|---|
| 写 `%USERPROFILE%\.workbuddy\**` 的解释器 | **不能**是工作区内的 `.venv\Scripts\python.exe` —— 它被 DSH 沙箱限制，写用户目录抛 `PermissionError [Errno 13]`；只有**工作区外**的解释器能写 |
| 写进 `mcp.json` 的 `command` | **必须**是项目 `.venv\Scripts\python.exe` —— `skillbridge` 只装在那里（§3.2） |

所以**「跑脚本的解释器」≠「写进配置的解释器」**，必须显式分开：

```bash
# 跑脚本：工作区外、装了 fastmcp/skillbridge 的解释器（本项目缓存区那份）
"C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe" ^
  Tools\deploy\setup_agent.py ^
  --venv-python "C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp/.venv/Scripts/python.exe"
```

- `--venv-python` 默认取**项目 `.venv`**（`default_venv_python()`），不再是 `sys.executable`。
  ★ 2026-10-16 之前是 `Path(sys.executable)` —— 换个解释器跑，`mcp.json` 就被写成了那个解释器的路径（实测踩到）。
- 不传该参数且**运行解释器 ≠ 项目 `.venv`** 时，脚本打 WARN 提示；`.venv` 不存在则直接 ERR 退出。
- 顺带：`install_skillbridge.py` 的中文输出已强制 UTF-8，cp936 控制台下不再 `UnicodeEncodeError` 崩掉 C 步。

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
> 无法靠版本号判断有没有吃到新代码。判据是**重连后看工具总数是否为 36**，
> 或直接调一个新加的工具（如 `folder_preflight` / `get_run_stats`）看是否报"未知工具"。

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
| `locate_land_pattern_page` 返回 null | 2026-10-14 起分三种：`scan_only`（纯位图扫描件）/ `ambiguous`（证据并列，**故意不猜**）/ `no_match` | 看返回的 `candidates` 与 `hint`：`ambiguous` 就按候选页逐页看，或 `render_pdf_contact_sheet` 拼版一次定位 |
| `locate_land_pattern_page` 给了页码但仍要确认 | 定位结论是**结构推定**（`needs_visual=true`），不是强关键词实锤 | 读尺寸前 `render_pdf_page` / `render_pdf_region` 渲染确认；判据 S8 会查「有没有撒谎」 |
| 改了代码没生效 | MCP 进程缓存旧代码 | 重连 MCP（见 §4.3） |
| 归档落在 `Data\footprint\` | `install_shadow_library` 的 `lib_root` 默认值 | 显式传 `lib_root=<规格书所在目录>` |
| 路径含中文导致异常 | Allegro 不支持非 ASCII 路径 | 换英文路径 |
| 隔离文件报 `[WinError 32] 另一个程序正在使用此文件` | **Allegro 进程还开着**，它持有工作目录里的 `allegro.jrl`（日志文件，0 字节也会被独占） | 先按 §5.2 的 **A** 关掉 Allegro（`Tools\stop_allegro.py`）再隔离。★ 2026-10-16 实测：`setup_agent.py` 跑完 C 步后可能留下一个 `allegro.exe`（父进程已退出，属孤儿），它会锁住 `Data\footprint\<料号>\allegro.jrl` |
| 写 `%USERPROFILE%\.workbuddy\**` 报 `PermissionError [Errno 13]` / `[WinError 5]` | 用的解释器**在工作区内**（被 DSH 沙箱限制） | 换工作区外解释器跑部署脚本，并用 `--venv-python` 指定项目 `.venv`（§3.6） |
| `check_eval` 里 geometry 套件假红（PASS 0 / FAIL 236） | 评分用了**工作区内**解释器，extracta 第 2 路崩（`rc=3221226505`） | 改用工作区外解释器（§7.2 的 ★★ 条目），**别去调 R4 阈值** |

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

1. 改代码 → 2. 跑**一条命令**（五套断言全跑，见下；必须全绿）→ 3. 实机建一颗件验证 → 4. 更新相关 `README.md` → 5. 重连 MCP 验证工具数与本机源码的 `@mcp.tool` 定义数一致（部署冒烟 D2 已是同源比较）。

```bash
# ★ 一条命令跑完五套断言（Phase 2-d；缺依赖记 ERROR、不静默跳过）
python eval\check_eval.py            # 含 253 件对拍，约 2 分钟
python eval\check_eval.py --fast     # 冒烟：geometry 跳过 253 件对拍
python eval\check_eval.py --json     # 机器可读汇总（CI 用）
python eval\check_eval.py --show-fail  # 红了就附打该套件输出尾部
```

需要单独看某一套（排障时）：

```bash
# ① 两项离线自检（要装了依赖的解释器，即 .venv）
python Tools\selfcheck_geometry.py          # 239 项
python Tools\selfcheck_skill_syntax.py      # 222 项
# ② 四套断言（纯 stdlib，任何 3.10+ 都能跑）
python eval\suites\architecture\check_architecture.py      # 层方向 / 目录登记 / README 覆盖，ERROR 必须 0
python eval\suites\tool_contract\check_tool_contract.py    # 数量与名字 / docstring / 遥测口径 / 分组 C7 / 目录过期 C8
python eval\suites\spec_reading\check_spec_reading.py      # 读图准确率（真值人工签字；基线 92.5%）
python eval\suites\behavior\check_behavior.py              # S1/S2/S4 硬规则 + L3 状态机可执行性（B1–B5）
# ③ 几何回归评分（把 239/222/35 与 253 件对拍变成分数；必须用「工作区外」的解释器，见下）
python eval\suites\geometry_regression\check_geometry_regression.py
```

★ `eval/check_eval.py` 只做**跑全 + 计数 + 汇总**，判据仍在各自套件里（A1–A7 / C1–C6 / B1–B5 / S1–S8 / G1–G6 + R1–R4）；
它自己的元判据是 E1（五套都要跑到，缺依赖记 ERROR）、E2（看退出码，不解析人话）、E3（`--json` 解析失败也算 ERROR）、
E4（实跑行数必须等于计划行数）。**汇总漏一行却不报错，就是把闸门拆了一角。**

★ **排障时先看退出码，别只看最后几行输出**：2026-10-15 踩到「五套全绿，但 `check_eval.py` 自己 exit 1」——
GBK 控制台下汇总行打印 `[✓]`（U+2713）抛 `UnicodeEncodeError`，而红色分支打的是 ASCII `[X]`，
于是**红了正常退出、全绿反而崩**。现在 `check_eval.py` 与五个套件都有 `_force_utf8_stdout()`
（自己进程 `reconfigure(encoding="utf-8")`）；给子进程注入 `PYTHONIOENCODING` **不能替代**这一步。
**看到「全绿但命令失败 / 输出缺尾巴」，先怀疑编码，再看业务。**

★ **行为夹具是「文档里的规则真的落到工具层了吗」的守门人**：`eval/suites/behavior/` 现场造临时目录，
逼工具层对「目录里多份 `.dra`」（必须报歧义不许猜）、「只有历史 `checklist_report_*.json`」（不算本次证据）、
「通道没开」（必须如实说未连接，不许沉默）表态。改 `orchestration/preflight.py` 或 S 系规则后必须复跑；
噪声判定要调 `orchestration.preflight.is_noise()`（**单一真源，别抄常量**）。

★ **改 `orchestration/playbook.json` / `pipeline.py` 必须复跑 `behavior`（B5 守它们）**：B5 会检查
① 每条规则都同时有「检测方式」与「后果类别」（`deny` / `silent` / `recoverable`）——
两者缺一不许进表；② `pipeline.run()` 的放行与拦停正确：正常一对 PDF/.dra → `proceed`；
**目录里多份候选 → 必须 `confirm` 且停在 `preflight`（后续步骤一律 pending，不许继续取数）**；
通道不可用 → `stop` 且错误码 `CHANNEL_NOT_READY`；③ `stale_rule_ids` 里点到的已删除规则
（当前 1 条：`6.5`）在 `summarize_findings()` 里必须被**单独点名**，不许静默计入统计。
`playbook.json` 由缓存区脚本从 `services/rule_checker.py` 的 `_item(...)` 调用点生成，
**别手改 `variants[].line` 与后果类别**（改代码后重跑脚本，否则行号会变成第二份真源）。

★ **读图准确率只在签字真值上算分**：`eval/suites/spec_reading/` 的真值必须人工签字（`signed_by`/`signed_at`），
未签字真值只能 `--allow-unsigned` 自检、**不得冻结基线**；加 `--with-locate` 会多跑一条 S8「定位器不撒谎」
（需带 `pdfplumber` 的解释器）。改读图策略/换模型/调 dpi 前后都要对比这条分数。

★★ **回归评分必须用「工作区外」的解释器**（2026-10-14 定位）：被 DSH 沙箱限制的解释器
（工作区里的 `.venv`）会让 extracta 在第 2 路崩掉 → 253 件里 PASS 0 / FAIL 236，全是**环境分数**；
工作区外的解释器得到 PASS 7 / FAIL 81。套件的 **G6** 会先把这件事红出来，别去调 R4 阈值。

★ **改了 MCP 工具或参数**：同轮改 `Tools/deploy/setup_agent.py::EXPECTED_TOOLS`、
两份 SKILL 的清单（含 `references/mcp-tools.md`），再 `check_tool_contract.py --update` 冻结基线。
★ **发了 SKILL 就得打齐三处落点**（2026-10-14 实测；详见 `Tools/deploy/README.md` 坑 13）：
真源 `Tools/deploy/skills/*` → `%USERPROFILE%\.workbuddy\skills\*`（跑 `setup_agent.py`）→
`C:\Users\ds245\.skills-manager\skills\*`（`Copy-Item -Recurse -Force`，`.dsh\skills` 经 junction 随即变新）。
装完顺手把 `.workbuddy\skills\*.bak_*` 移出该目录（带 `_user_meta.json` 的备份会被客户端当成第二个 skill）。
★ **本机部署要用不受沙箱限制的解释器，并且必须带 `--venv-python`**：工作区内的 venv python 写 `%USERPROFILE%` 会
`PermissionError`，改用工作区外的解释器跑脚本；同一命令里用 `--venv-python` 把**写进配置**的路径钉在项目 `.venv`
（两者不能混为一谈，详见 §3.6 与 `Tools/deploy/README.md` 坑 14）。
★ **`.venv` 缺失时先按 3.3.1 重建，再按 §3.5 核三处引用**（2026-10-14 / 2026-10-16 两次实测本机都缺 `.venv`，
而 MCP 配置还指向它 —— 表现为「所有工具都不见了」，不是代码问题）。

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
- 历史快照文档里的工具数（如 `docs/规格书读取方案.md` 的「31 / 34 个工具」）是该文写作时的快照，不是现状声明；
  **现状工具数只认 `docs/tools-catalog.md`（生成物）与 `eval/suites/tool_contract` 基线**。改工具集时**不必**回改这些正文，
  但新写的文档别再写数量、也别抄清单。★ 2026-11-19：15 份文档里 29 处声明数字已清空，改由 C7（分组漏补）/C8（目录过期）守门。

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

### 9.1 MCP 工具速查

★ **工具全表不在这里**。手抄的清单守不住：2026-11-19 之前这一节抄着 36 个名字与分组，同一天
同仓另一处 `references/client-config.md` 抄的是另一套分组，两套都自称 36。

全仓唯一的工具清单是**生成的** `docs/tools-catalog.md`（按 6 组，每个工具名 + docstring 首句）。
它由 `mcp_server/tools.py` 的 `@mcp.tool` 定义与 `knowledge/tool_groups.json`（**唯一人工维护的分组映射**）
生成，过期由契约套件 **C8** 报红；分组漏补由 **C7** 报红。

- 想知道「有没有某个能力」→ 看 `docs/tools-catalog.md`，或直接看 MCP 客户端里的工具列表（运行期下发的就是真表）
- 想知道「常用工具怎么配合」→ 见 `references/mcp-tools.md`（调用顺序工作流，不抄清单）
- 门禁：`eval/suites/tool_contract/check_tool_contract.py`（C1/C2 冻结基线、C3/C4 docstring、
  C5/C6 遥测、**C7 分组完整性**、**C8 目录过期**）

> ★ 阈值类参数外置在 `knowledge/rules/checker_rules.json`（2026-10-12 起），
> 改 JSON 立即生效、不用重启 MCP；判定逻辑仍在 `rule_checker.py`。
> ★ 改工具集后的三步：① 在 `knowledge/tool_groups.json` 里给新工具补分组
> ② `python eval/suites/tool_contract/check_tool_contract.py --update` 重冻结基线
> ③ 同一脚本加 `--write-catalog` 重生成 `docs/tools-catalog.md`。
> **文档里不要再抄工具名或数量** —— 抄了就会成为第二份、第三份真源。

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
| `orchestration/` | **L3 编排层**：`preflight.py`（目录预检）、`playbook.json`（规则装配表）、`pipeline.py`（五步状态机） |
| `observability/` | **L4 观测层**：`telemetry.py`（工具调用落盘 `eval/runs/*.jsonl`）、`runstore.py`（读侧）、`stats.py`（统计，即 `get_run_stats` 后端） |
| `eval/` | **L5 评测层**：`suites/*`（五套断言）、`baselines/*.json`（进 git 的基线）、`runs/`（遥测落盘，**不进 git**）；入口 `check_eval.py` |
| `LAYERS.md` | 五层 ↔ 目录 ↔ 防线 ↔ 责任人对照表（改配置结构先读它） |

| `data`-**side** `Data/` | 数据源（在工作区根，不在本项目内）：`Data/footprint/`（有规格书的封装，每料号一目录）、`Data/PCB_Footprint/`（无法规书的大封装库）、`Data/footprint_test/`、`Data/checklist/` |
| `.deploy_manifest.json` | 部署指纹：两个 Skill × 三落点的 sha 记录（写在 `%USERPROFILE%\.workbuddy\`），`setup_agent.py --verify-skills` 据此判 MATCH / STALE / DIVERGED |

### 9.3 关键环境变量

| 变量 | 作用 |
|---|---|
| `LAYOUT_CHECK_FORCE_SOURCE` | `extracta` / `skillbridge` —— 强制回读数据源 |
| `LAYOUT_CHECK_EXTRACTA_STRICT` | `=1` 时 extracta 的任何缺口都算不完整 → 回退（默认关） |
| `LAYOUT_CHECK_CDSROOT_DEBUG` | CDSROOT 定位调试 |
| `FORGE_NOCONFIRM` | 建封装时抑制交互式确认 |
| `CDS_INST_DIR` / `HOME` | Allegro 安装目录 / 用户 Home（CDSROOT 反推用） |
