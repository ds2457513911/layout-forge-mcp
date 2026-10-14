# deploy/ —— 一键部署

> 最后更新：2026-10-14（v7.1：消灭空电脑手工环节——客户端没装过也直接自动写配置装 Skill）

## 这个目录是干什么的

让**一台全新的 Windows 电脑**双击一个脚本就能得到可用的 `layout-forge-mcp` 服务：
装环境、注册 MCP、装 Skill、配 Allegro SkillBridge 自动加载、跑验收。

纯 Windows 批处理 + 一个 Python 配置器。脚本内的输出是**英文**（CMD 编码坑多，
中文容易乱码），本文件给出逐条中文对照。

## 用法

```
deploy.bat                          双击即可，默认装到 %USERPROFILE%\layout-forge-mcp
deploy.bat /dir "D:\tools\lfm"      指定安装目录
deploy.bat /pcbenv "D:\...\pcbenv"  手工指定 Allegro 的 pcbenv，跳过自动搜索
deploy.bat /nobridge                不要动 allegro.ilinit
deploy.bat /dry                     只打印要做什么，一个文件都不写
```

也可以只跑 Agent 侧配置（环境已经建好时）：

```
.venv\Scripts\python.exe deploy\setup_agent.py --dry-run
.venv\Scripts\python.exe deploy\setup_agent.py --no-skillbridge
```

## 两种模式

| 模式 | 触发条件 | 行为 |
|--|--|--|
| **local** | `deploy\` 的上一级有 `pyproject.toml` | 原地部署这份代码，**不 clone**，也不需要 git |
| **clone** | 上一级没有 `pyproject.toml` | 从 GitHub clone 到 `/dir` 指定目录（默认 `%USERPROFILE%\layout-forge-mcp`） |

分发给别人时，通常是把 `deploy.bat` 单独发给对方 → 走 clone 模式。
在本机开发目录里跑 → 走 local 模式。

## 部署做了什么（脚本里的 6 步）

| 步骤 | 脚本输出 | 做什么 |
|--|--|--|
| 0 | `[0/6] Locate project` | 判定 local / clone 模式，确定项目根 |
| 1 | `[1/6] Check winget` | 查 winget 在不在（没有也能跑，只要 uv/git 已有） |
| 2 | `[2/6] Check uv / python` | 找 uv；没有就找 python；都没有才用 winget 装 uv |
| 3 | `[3/6] Check git` | 仅 clone 模式需要 |
| 4 | `[4/6] Fetch project` | clone 或 pull |
| 5 | `[5/6] Create .venv and install dependencies` | `uv sync`，或 `python -m venv` + `pip install -e .` |
| 6 | `[6/6] Register MCP, install Skills, config SkillBridge` | 调 `setup_agent.py`，见下 |

`setup_agent.py` 的四段：

- **A 注册 MCP** —— 自动找客户端配置并**合并写入**（优先级：已存在的
  `~/.workbuddy/mcp.json` → Claude Desktop → Cursor → Trae；
  ★ 全都不存在（客户端还没装过）→ 直接在 `~/.workbuddy/mcp.json` **新建**，
  目录自动创建，客户端装好后启动即生效，**没有任何手工环节**）
- **B 装 Skill** —— `deploy\skills\*` → `%USERPROFILE%\.workbuddy\skills\`
  （★ `.workbuddy` 目录不存在就自动创建，不再跳过）
- **C 配 SkillBridge** —— 调 `Tools\install_skillbridge.py --non-interactive`，
  往 `allegro.ilinit` 写自动加载块，起 7777 端口
- **D 冒烟验收** —— import 四个依赖、读 MCP 工具总数（期望 **34**）、
  跑两个离线自检（239/239、222/222）

## 文件清单

| 文件 | 功能 | 影响 |
|--|--|--|
| `deploy.bat` | 一键部署入口：自举环境（winget/uv/git/venv/依赖） | 会装软件、建 `.venv` |
| `setup_agent.py` | Agent 侧配置：MCP / Skill / SkillBridge / 验收 | **会写用户级配置和 Allegro 的 `allegro.ilinit`** |
| `skills/` | 两个 Skill 的**发布副本**（`allegro-skillbridge-ops`、`allegro-footprint-check`） | 被复制到用户 skills 目录 |
| `_legacy/deploy_skillbridge.bat` | 旧版（layout-check-mcp v5）遗留，功能已被 C 步覆盖 | 别再跑 |

## 关键约定与坑

1. ★ **MCP 的 command 必须是项目 `.venv` 里的 python.exe**。
   `skillbridge` 只装在项目 venv 里，系统 Python / 托管 Python 都 import 不到。
   脚本第 5 步末尾有硬校验：`import skillbridge` 失败直接 fail，不往下走。
2. ★ **args 必须用 `-c` 注入 `sys.path`**。客户端拉起 MCP 时 cwd 不确定，
   不注入会 `ModuleNotFoundError: mcp_server`。所以**不用 `uv run`**。
3. ★ **写 `mcp.json` 是合并不是覆盖**：已有的其他 server 全部保留，
   写之前备份成 `<原名>.bak_<时间戳>`。同名 `layout-forge-mcp` 条目会被替换。
4. ★ **全程非交互**：任何 `input()` 都会把双击出来的窗口卡死。
   多个 pcbenv 候选时非交互默认选第一个（用 `/pcbenv` 可以精确指定）。
5. ★ **`Tools/` 在 `.gitignore` 里是被忽略的**，但 `install_skillbridge.py`
   和两个 `selfcheck_*.py` 单独放行了（一键部署要用）。
   → 别把它们挪走，也别把 `Tools/*` 改回 `Tools/`（目录级忽略会让 `!` 例外全部失效）。
6. ★ **走 clone 模式前，先把本地改动 commit + push**。
   仓库目前只有一次初始提交，spec 库、新工具等大量改动还没提交，
   clone 下来会拿到旧代码（工具数会少于 `setup_agent.py` 的 `EXPECTED_TOOLS`）。
7. **新电脑从没启动过 Allegro → 没有 pcbenv**：C 步会失败并打印指引，
   但**不算部署失败**（MCP 本身已可用）。启动一次 Allegro PCB Editor
   生成 pcbenv 后，重跑一次脚本即可。
8. **空电脑（Agent 客户端还没装过）也能一键到底**（2026-10-14 v7.1 起）：
   A 步直接新建 `~/.workbuddy/mcp.json`、B 步自动创建 skills 目录，
   **不再退化为生成 mcp-config.json 让用户手工合并**（旧行为已删）。
   客户端后装也生效——WorkBuddy 的配置/skill 路径是固定的，
   装好后启动即读取。验证方式：临时改 `USERPROFILE` 指向空目录跑
   `--dry-run`，应看到「未发现既有配置，将在 WorkBuddy 下新建」+
   两个 skill「将复制」。
9. Skill 目标已存在同名目录 → 先备份成 `<名字>.bak_<时间戳>` 再覆盖。
   ★ **`_user_meta.json` 必须保留**：那是客户端给 skill 写的安装元数据
   （`installedAt` / `source`），仓库副本里故意不带它（含本机时间戳）。
   覆盖时丢了这个文件，客户端就认不出它是已安装的 skill。脚本已处理：
   覆盖前取出、复制后写回，备份目录里也留一份。
10. 部署完**必须重启 Agent 客户端**：MCP 是长驻进程，改配置不重启不生效。
11. 与 `Tools/start_allegro.cmd` 的分工：那个只管**启动一次** Allegro，
    这里管**持久化配置 + 环境**。
12. ★ **同一份 SKILL 有两个副本，改的时候别只改一边**（2026-10-13 定）：
    - **真源 = 本目录 `deploy/skills/*`**（在 git 里、受版本控制）
    - **部署目标 = `%USERPROFILE%\.workbuddy\skills\*`**（客户端实际加载的那份）

    `setup_agent.py::install_skills` 是 **单向 `copytree`（deploy → 用户目录）**。
    所以**只改用户目录那份、不更新 deploy 副本 → 下次跑部署会被静默回退**。
    正确做法：**改 `deploy/skills/*` → 跑部署**（或两边同步改）。
    ★ 另注意 `deploy.bat` **同时会重写 `mcp.json`**（合并、先备份），
    只是同步 SKILL 时不要跑整个 deploy.bat，直接复制文件即可。
    （2026-10-13 实测过一次双向陈旧：`mcp-tools.md` / `spec-library.md` 里
    `Data\specs` 与 `knowledge\specs` **两侧各对一半**，已统一。）

## 常见报错对照

| 脚本输出 | 含义 / 处理 |
|--|--|
| `[X] skillbridge is NOT importable from the project venv` | 装错解释器了。删 `.venv` 重跑；确认 `pyproject.toml` 的 `requires-python` 没被改窄 |
| `[X] uv sync failed` | 网络 / 代理 / Python 版本问题。看上面的输出；可改用系统 python 模式（把 uv 从 PATH 摘掉再跑） |
| `[!] SkillBridge 自动加载未配成` | 通常是没 pcbenv。启动一次 Allegro 再重跑，或用 `/pcbenv` 指定 |
| 工具数与 `setup_agent.py` 的 `EXPECTED_TOOLS`（现 34）不符 | 少于期望 → 多半 clone 到了旧提交，`git pull` 后重启客户端；多于期望 → 本地加了新工具，去更新 `EXPECTED_TOOLS` |
| `[X] git clone failed` | GitHub 不通。配代理，或手工把整个项目拷到 `/dir` 指定的目录再跑 |

## 相关目录

> deploy 现位于 `Tools\deploy\`（项目根的下一级 Tools 里），以下均为相对本文件。

- 服务源码：`../../mcp_server/`、`../../services/`
- 自检 / SkillBridge 安装器：`../../Tools/selfcheck_*.py`、`../../Tools/install_skillbridge.py`
- 维护手册：`../../docs/SOP-工程维护手册.md`
- 知识库（热更新数据层）：`../../knowledge/`
