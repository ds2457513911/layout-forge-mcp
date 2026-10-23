# deploy/ —— 一键部署

> 最后更新：2026-10-16（v7.5：`deploy.bat` 支持**本地离线部署** —— 三档定位 + 本地模式先
> `git pull`、pull 失败只 WARN 不阻断，新增 `/nopull`；「两种模式」升级为「三种定位」。
> 此前 v7.4：`setup_agent.py` 新增 `--venv-python`（缺省取项目 `.venv`，不再把调用者解释器
> 写进 `mcp.json`）+ `install_skillbridge.py` 强制 UTF-8 修 C 步中断；坑 14 重写为
> 「跑脚本的解释器 ≠ 写进配置的解释器」。此前 v7.3：加 `.deploy_manifest.json` +
> `--verify-skills` / `--sync-skills` 防静默回退；修 junction 被 `shutil.move` 移走导致三落点陈旧的旧 bug；
> SKILL 头部三声明软校验。此前 v7.2：工具数 36 + 实测补记「skills 目录是 Junction」的部署坑）

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
deploy.bat /nopull                  local 模式：连 git pull 也不试（纯离线）
deploy.bat /dry                     只打印要做什么，一个文件都不写
```

也可以只跑 Agent 侧配置（环境已经建好时）：

```
.venv\Scripts\python.exe deploy\setup_agent.py --dry-run
.venv\Scripts\python.exe deploy\setup_agent.py --no-skillbridge
```

★ **只同步 / 只校验 SKILL（改完 `deploy/skills/**` 后最常用，2026-10-15 新增）**：

```
python Tools\deploy\setup_agent.py --sync-skills      # 写真源 → 三落点 + 更新 .deploy_manifest.json
python Tools\deploy\setup_agent.py --verify-skills    # 只读校验：全部 MATCH 才 exit 0
```

这两个开关在 A–D 之前**提前返回**（只干这一件事）。`--verify-skills` 的四种判定
（MATCH / STALE / DIVERGED / MISSING）与「为什么需要它」见下面坑 15 ——
**改了 SKILL 却不 `--sync-skills`，模型读到的可能还是旧版，而且你不会收到任何报错。**

## 三种定位（v7.5 起）

脚本第 0 步按「由近及远」三档找项目根，**任何一档命中就不再访问 GitHub**：

| 档 | 触发条件 | 行为 |
|--|--|--|
| **(a) local** | `deploy.bat` 的上一级（`..\..`）有 `pyproject.toml`，即它正坐在 `<检出>\Tools\deploy\` 里 | 原地部署这份代码，**不 clone** |
| **(b) local** | `deploy.bat` 自己所在目录就有 `pyproject.toml`，即它被 `curl` 下载到了检出根目录 | 同上，锚定 `%~dp0` |
| **(c) clone** | 两处都没有 `pyproject.toml` | 从 GitHub clone 到 `/dir`（默认 `%USERPROFILE%\layout-forge-mcp`） |

★ 档位判定结果会打印在第 0 步（`MODE = local / clone`），**不会静默回退**。

**local 模式现在会先 `git pull`**（第 4 步），并且：

- `git` 缺失 → 只 WARN，pull 跳过，其余步骤照跑（代码已经在本地，local 模式本来就不需要 git）；
- `git pull` 失败（离线 / 代理 / 冲突）→ **只 WARN**，用当前检出原样继续部署，**不阻断后续任何环节**；
- 想完全跳过网络尝试 → 加 `/nopull`。

> 这正是文档开头那条 `curl -L -o deploy.bat <raw url> && deploy.bat` 的离线友好化：
> 如果你**已经**在项目里（或把 bat 下到了项目根），就一个字节都不下载。

## 部署做了什么（脚本里的 6 步）

| 步骤 | 脚本输出 | 做什么 |
|--|--|--|
| 0 | `[0/6] Locate project` | 三档定位（见上），确定项目根并打印 `MODE` |
| 1 | `[1/6] Check winget` | 查 winget 在不在（没有也能跑，只要 uv/git 已有） |
| 2 | `[2/6] Check uv / python` | 找 uv；没有就找 python；都没有才用 winget 装 uv |
| 3 | `[3/6] Check git` | clone 模式必需；local 模式**可选**（只用于第 4 步的 pull） |
| 4 | `[4/6] Fetch project` | clone 模式 = `git clone`；local 模式 = `git pull`（失败只 WARN，`/nopull` 可跳过） |
| 5 | `[5/6] Create .venv and install dependencies` | `uv sync`，或 `python -m venv` + `pip install -e .` |
| 6 | `[6/6] Register MCP, install Skills, config SkillBridge` | 调 `setup_agent.py`，见下 |

`setup_agent.py` 的五段：

- **A 注册 MCP** —— 自动找客户端配置并**合并写入**（优先级：已存在的
  `~/.workbuddy/mcp.json` → Claude Desktop → Cursor → Trae；
  ★ 全都不存在（客户端还没装过）→ 直接在 `~/.workbuddy/mcp.json` **新建**，
  目录自动创建，客户端装好后启动即生效，**没有任何手工环节**）
- **B 装 Skill** —— `deploy\skills\*` → **三个落点**（按 `realpath` 去重后写在真身上：
  `~/.workbuddy/skills`、`~/.skills-manager/skills`、`~/.dsh/skills`；逐文件覆盖、保留
  `_user_meta.json`）→ 然后写 `deploy\.deploy_manifest.json`
- **C 配 SkillBridge** —— 调 `Tools\install_skillbridge.py --non-interactive`，
  往 `allegro.ilinit` 写自动加载块，起 7777 端口
- **D 冒烟验收** —— import 四个依赖、读 MCP 工具总数（期望 **36**）、
  跑两个离线自检（239/239、222/222）
- **E 落点校验** —— `verify_skills()`：真源 vs 三落点，**不一致就整体 exit 1**
  （外加 `check_skill_declarations()` 的软 WARN：SKILL 头部三声明）

## 文件清单

| 文件 | 功能 | 影响 |
|--|--|--|
| `deploy.bat` | 一键部署入口：自举环境（winget/uv/git/venv/依赖） | 会装软件、建 `.venv` |
| `setup_agent.py` | Agent 侧配置：MCP / Skill（三落点）/ SkillBridge / 验收 / **落点校验** | **会写用户级配置和 Allegro 的 `allegro.ilinit`** |
| `.deploy_manifest.json` | ★ **部署记录（本机状态，被 .gitignore 忽略）**：真源 hash + 三落点的 files/junction/real + `deployed_at` | **不是真源**；`--verify-skills` 用它区分 STALE 与 DIVERGED |
| `skills_backup/` | ★ mirror 前把「真源没有、落点里却有」的文件备份到这里（`<name>.bak_<ts>/`） | 只在本机出现；确认无用后可删 |
| `skills/` | 两个 Skill 的**发布副本 = 唯一真源**（`allegro-skillbridge-ops`、`allegro-footprint-check`） | 被镜像到三个落点；**只改落点会被静默回退** |
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
13. ★★ **目标 skills 目录在本机是 Junction，SKILL 有三个落点（2026-10-14 实测；2026-10-15 已修复）**：
    `%USERPROFILE%\.workbuddy\skills\<skill>` 与 `%USERPROFILE%\.dsh\skills\<skill>`
    都是 **junction**，指向 `C:\Users\ds245\.skills-manager\skills\<skill>`
    （客户端 skills-manager 管理，同一个目标）。
    - ★★ **旧 `install_skills` 的 bug（2026-10-15 已改）**：它用 `shutil.move` 把 junction
      移成 `<name>.bak_<时间戳>`，再 `copytree` 建**真目录** → 部署后
      `.workbuddy\skills\<skill>` 不再是链接，**三个落点从此各说各话**（这正是「改了 SKILL
      但模型读到的还是旧的」的根因）。
      现在 `mirror_skill()` 改成**逐文件覆盖写在真身上**（sha 相同的跳过、保留 `_user_meta.json`、
      真源没有的额外文件先备份到 `Tools/deploy/skills_backup/<name>.bak_<ts>/`），
      junction 只被写穿一次、所有指向它的视图同时变新。
    - ★ **`.bak_<时间戳>` 里还留着 `_user_meta.json`，客户端可能把它当成第二个
      skill 列出来**（它的 SKILL.md 就在 junction 目标根上）→ 装完把 `.bak_*`
      **移出** skills 目录即可（移动链接本身，目标内容不受影响）。
      历史遗留已移到 `C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\scratch\skill_junction_backups\`。
    - 现在不必手工打三处：`setup_agent.py --sync-skills` 会按 `realpath` 去重后一次写完
      （`.dsh` 与 `.skills-manager` 常是同一真身）。
14. ★ **本机（DSH 会话）跑部署要用「不受沙箱限制的解释器」，且必须带 `--venv-python`**：
    - **症状**：用工作区内的解释器（`layout-forge-mcp\.venv\Scripts\python.exe` 或
      `layout-check-mcp\.venv\Scripts\python.exe`）跑 `setup_agent.py`，写 `%USERPROFILE%\.workbuddy`
      被拒 —— `PermissionError [Errno 13] Permission denied` / `[WinError 5] 拒绝访问`
      （`shutil.copy2` → `open(dst,'wb')` / `shutil.move` / `os.makedirs` 都失败；
      该解释器只有**工作区 + TEMP** 可写）。实测同一目录用工作区外解释器写 → OK。
    - **正确命令**（跑脚本与写进配置是两个解释器，别混）：
      ```
      "C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe" ^
        Tools\deploy\setup_agent.py ^
        --venv-python "<项目根>\.venv\Scripts\python.exe"
      ```
      （缓存区那份需备 fastmcp / skillbridge / PyMuPDF / pdfplumber；见 `cache\tools\venv-unconfined`。）
    - ★ **不传 `--venv-python` 会留下更隐蔽的坏现场**：2026-10-16 之前脚本用
      `Path(sys.executable)` 当 command，于是**谁跑脚本就把谁的路径写进 `mcp.json`** ——
      实测被写成缓存区临时解释器 `...\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe`。
      注册看着成功、客户端也能连，**但缓存区一清理 MCP 就静默消失**（不报错）。
      现已修：`--venv-python` 缺省取 `<项目根>\.venv`（`default_venv_python()`），
      运行解释器 ≠ 目标解释器时打 WARN，目标 `.venv` 不存在直接 ERR 退出。
    - 注：MCP 自身仍必须用项目 `.venv`（见坑 1），只有「写用户目录」的部署动作需要放宽。
    - 顺带（同日修）：`install_skillbridge.py` 的中文 OK/WARN/ERR 前缀是 `✅/⚠️/❌`，
      被 `setup_agent.py` 以管道调用时（无控制台）在 cp936 下抛
      `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'` → 一键部署断在 C 步。
      现已在入口 `reconfigure(encoding="utf-8", errors="replace")`。
15. ★ **防静默回退：`.deploy_manifest.json` + `--verify-skills` / `--sync-skills`（2026-10-15 新增）**：
    - 每次部署会写 `deploy/.deploy_manifest.json`（schema `layout-forge-deploy-manifest/1`）：
      记录真源 `source_hashes`（每份 skill 的 `{相对路径: sha256}`，**跳过 `_user_meta.json`**）、
      每个落点的 `{path, real, junction, files}` 与 `deployed_at`。
      ★ **这份记录不是真源**，真源永远是 `deploy/skills/**`；它放在 `Tools/`（被 .gitignore 忽略）= 本机状态。
    - `--verify-skills` 判定四种：**MATCH**（落点 == 仓库现状）/ **STALE**（落点 == 上次部署记录、
      但仓库已前进 → 重跑部署）/ **DIVERGED**（既非仓库现状也非上次记录 → **有人手改过**，最危险）/
      **MISSING**（落点根在、这份 skill 不在）；落点根整个不存在记 N/A 不算失败。**只有全 MATCH/N/A 才 exit 0。**
    - 实测价值（同日）：`--verify-skills` 当场抓到 `allegro-skillbridge-ops` 三落点
      **`references/mcp-tools.md` 陈旧**（客户端 14857 B / 仓库 16108 B）→ `--sync-skills` 复绿。
      没有这道闸门，那次陈旧会一直「看起来正常」。
    - ★ **部署后的 E 段**（`install_skills` 之后自动跑 `verify_skills()`）会把不一致算进
      `results["skills_sync"]` → **一键部署会因为落点不同步而整体失败**，而不是留个说不清的现场。
16. ★ **SKILL 头部三声明**（`mcp_min_commit` / `verified_with` / `eval_baseline`，Phase 3 §7）：
    `check_skill_declarations()` 只做**软校验（WARN，不影响退出码）** —— 声明是「这份 SKILL 配哪个 commit
    验过的」的溯源线索，不是运行期依赖。★ 校验**只读 front-matter 区块**（首个 `---` 到第二个 `---`），
    不要用固定字符窗口：`allegro-skillbridge-ops` 的 description 长达 2.5 KB，会把 `metadata` 挤到
    2000 字符之外 → 固定窗口会「明明声明了却报缺失」（2026-10-15 实测踩到后才改成按 front-matter 切）。
    改 SKILL 内容时同轮更新 `mcp_min_commit`（当前 `a5bd61f`）与 `verified_with`。

## 常见报错对照

| 脚本输出 | 含义 / 处理 |
|--|--|
| `[X] skillbridge is NOT importable from the project venv` | 装错解释器了。删 `.venv` 重跑；确认 `pyproject.toml` 的 `requires-python` 没被改窄 |
| `[X] uv sync failed` | 网络 / 代理 / Python 版本问题。看上面的输出；可改用系统 python 模式（把 uv 从 PATH 摘掉再跑） |
| `[!] SkillBridge 自动加载未配成` | 通常是没 pcbenv。启动一次 Allegro 再重跑，或用 `/pcbenv` 指定 |
| 工具数与 `setup_agent.py` 的 `EXPECTED_TOOLS`（现 36）不符 | 少于期望 → 多半 clone 到了旧提交，`git pull` 后重启客户端；多于期望 → 本地加了新工具，去更新 `EXPECTED_TOOLS` |
| `[X] git clone failed` | GitHub 不通。配代理，或手工把整个项目拷到 `/dir` 指定的目录再跑 |

## 相关目录

> deploy 现位于 `Tools\deploy\`（项目根的下一级 Tools 里），以下均为相对本文件。

- 服务源码：`../../mcp_server/`、`../../services/`
- 自检 / SkillBridge 安装器：`../../Tools/selfcheck_*.py`、`../../Tools/install_skillbridge.py`
- 维护手册：`../../docs/SOP-工程维护手册.md`
- 知识库（热更新数据层）：`../../knowledge/`
