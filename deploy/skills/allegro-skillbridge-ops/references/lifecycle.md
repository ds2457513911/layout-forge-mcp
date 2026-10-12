# lifecycle —— Allegro 进程生命周期

> 本文件由 `SKILL.md` 按需指向，是 `allegro-skillbridge-ops` 技能的参考资料。
> 收录原 SKILL.md 的 **第二、十、十一、十四节**（章节号沿用原文，便于旧引用对上）。
>
> ★ 最要紧的一条：**收尾 = A（关进程）+ B（收回后台挂住的 shell），两件都要做。**
> 见下文「任务收尾就关掉 Allegro」与「A/B 双件事模型」。
>
> grep 关键词：`后台挂住`、`TaskStop`、`收尾`、`铁律`、`stop_allegro`、`lck`、`jrl`、`窗口`、`截图`

## 二、★★★ 让 Allegro「自己重启」SkillBridge 服务

### ★ 先搞清能力边界：谁能在什么时候重启服务

| 服务状态 | 谁可以启动 | 手段 |
|--|--|--|
| **活着** | Python 侧可以 | 发 `prog1(pyKillServer() load(..) pyStartServer ..)` fire-and-forget |
| **已死** | **只有 Allegro CIW** | 人工在 CIW 里 `load` + `pyStartServer` |

**为什么服务死后 Python 无能为力**：Python 客户端是通过 socket 连到
**Allegro 进程内部那个 Python 服务**的。服务一死 → socket 断 →
**再也没有通道把命令送进 Allegro**。端口会变成无监听
（`netstat` 看不到 7777，`connect` 报 `ConnectionRefusedError 10061`）。
唯一入口是 Allegro 自己的 CIW 命令窗口（进程内的 REPL）。

> ★ **根治办法：配 Allegro 启动自启（见 2.5）**，这样基本不会遇到"要手工 load"。
> ★ 服务死后别反复重试 Python 侧连接，除了刷屏没任何用。

### 2.5 ★★★ 配 Allegro 启动自启（推荐，一劳永逸）

**★ 核心原则：用项目里已验证过的 `Tools/install_skillbridge.py`，不要自己另写。**

```bash
uv run --directory <项目> python Tools/install_skillbridge.py --dry-run   # 预览
uv run --directory <项目> python Tools/install_skillbridge.py             # 真装
uv run --directory <项目> python Tools/install_skillbridge.py --uninstall # 卸载
```

`layout-check-mcp` / `layout-forge-mcp` 两份 `install_skillbridge.py`
**MD5 一致**（`085c1e4048b62addd3df0636dc89aeb0`，16387 字节），是生产验证版。
`deploy/deploy_skillbridge.bat` 第 203 行就是调它。

**它写出的正确块（这就是标准答案，不要改动）**：

```skill
;; ==== SkillBridge Auto-Load BEGIN (do not edit) ====
load("<...>/.venv/Lib/site-packages/skillbridge/server/python_server.il")
pyKillServer
pyStartServer ?id "7777" ?python "<...>/.venv/Scripts/pythonw.exe"
;; ==== SkillBridge Auto-Load END ====
```

> ★★★ **三条平铺的独立顶层语句**。这是全部要点。

**逐条解释（含反面教训）**：

| 元素 | 正确做法 | 我曾经的错误改法 | 为什么错 |
|---|---|---|---|
| **结构** | **平铺裸语句**，无 `when`/`isFile` 包裹 | 套了 `(when ...)` + `(getd ...)` | 一个报错点就让整块中断 |
| **`load`** | 裸调用 | ✔ 没改 | — |
| **`pyKillServer`** | **保留，且不带括号** | 删掉了 | 裸词只是"引用函数名"不调用，是无害占位；保留它即可与生产版一致 |
| **`pyStartServer`** | **不加 `?forceTcp`** | 硬加了 `?forceTcp t` | 改变了启动模式（强制 TCP vs 默认 IPC）|
| **幂等** | 交给 `pyStartServer` 自己 | 自造 `getd` 守卫 | 见下 |

**★★★ 为什么自造守卫是灾难（真实事故，2026-09-29）**：

```skill
;; ❌ 灾难写法 —— 会导致自启动完全失效
(unless (getd 'pyStartServer.ipc) (pyStartServer ...))
```

- `pyStartServer.ipc` 是**带点的名字**，`'` 引号把它读成 **dotted pair 而非 symbol**
  → `getd` 立刻报错：
  `*Error* getd: argument #1 should be a symbol (type template = "s") - (pyStartServer.ipc)`
  （文档 `funcprog.html`：`getd( s_functionName )`，参数必须是 symbol）
- **报错点极险恶**：在 `load` **之后**、`pyStartServer` **之前**
  → `when` 块中断 → **服务根本没启动**，CIW 里只留一行红字。
- 现象：`Available commands:` 正常列出（load 成功），但端口没监听。

**★ `pyStartServer` 自身幂等**（`python_server.il` 第 199 行）：
```skill
defun(pyStartServer (@key ...)
    if(pyStartServer.ipc then
        fprintf(stderr, "server is already running")
        nil
    else ... ipcBeginProcess(...) ...)
```
→ **重复调用无害**，只打印 "server is already running"。所以压根不需要外部守卫。

> **设计原则**：**优先复用项目里已验证的脚本，不要自己"改进"。**
> 每加一层自以为聪明的守卫，就多一个让自启动静默失效的机会。

**安装后必须重启 Allegro 才生效**。验证：`netstat -ano | findstr 7777`。

**★★★ 重启 Allegro 只能由用户手工做**（重大教训）：
`allegro.exe` 是 GUI 程序，**必须在真实桌面会话启动**。用工具在 Bash/
沙箱里 `allegro.exe &`、`cmd /c start` 等**都会被回收** —— 进程可能闪现几秒
（能 `tasklist` 看到、甚至生成 `allegro.jrl`），随后消失，端口永不监听。
→ **别在这上面反复试，直接请用户双击启动。**

**★ 强制终止的正确姿势**：
```bash
taskkill /F /PID <pid>     # 必须带 /F
```
不带 `/F` 只是发关闭请求，GUI 会拦下来弹「是否保存」，反而制造新弹窗。
强杀后记得清理残留锁文件：`rm -f <dir>/*.dra.lck`（内容含已死 PID，
不清理会导致下次打开报"图纸被占用"）。

### 2.6 恢复命令**别从聊天记录复制**

绝对路径长约 110 字符，从聊天里复制**极易被截断**，典型报错：

```
*WARNING* (reader): string at line 1 was not terminated on EOF, '"' added
*Error* load: can't access file - ".../python_s"     ← 断在 python_s
```

→ 用工具打印完整命令（**带存在性验证**）：

```bash
python tools/print_restart_cmds.py
```

### 机制（读 `skillbridge/server/python_server.il` 源码）

```skill
defun(pyKillServer ()    ; 杀服务，pyStartServer.ipc = nil
defun(pyReloadScript ()  ; = pyKillServer() + load(python_server.il)
                         ;   ★ 只重载定义，**不启动**！
defun(pyStartServer (@key (id "default") (logLevel "INFO") (singleMode nil)
                          (timeout nil) (python "python") (forceTcp nil) "ttggtg")
    ; ★ ?python 默认 "python"，PATH 里未必有 -> 必须显式传绝对路径
pyReloadServer.filename = _filename   ; 自动记住脚本路径
```

### ★★★★ 核心矛盾：这是「自杀式闭环」

服务一被杀，Python 侧 socket 就断了 → **再也没法用 Python 发命令启动它**。

> **硬前提：必须在服务还活着的时候，把重启命令一次性发出去。**

### 正确做法：单行复合表达式 + fire-and-forget

```skill
prog1(pyKillServer() load("<python_server.il>") pyStartServer ?id "7777" ?python "<pythonw.exe>" ?forceTcp t)
```

- `prog1` 顺序求值、返回最后一值，把「杀 → 重载 → 启动」串成一条
- **必须单行**（SkillBridge 的 `eval_skill` 按 `\n` 逐行求值，多行会回复错位）
- ★ 这条命令的**回复永远收不到**（服务已被杀）→ 属**预期行为**，不是失败
- 发完立刻 `close()` 旧连接，然后**轮询重连**直到成功

### 工具

```bash
python tools/restart_skillbridge.py            # 只推断路径 + 预览表达式
python tools/restart_skillbridge.py --execute  # 真重启
```

### ★ 路径推断的两个坑

1. **必须纠正 Windows 路径大小写**：Python 的 `skillbridge.__file__` 常给
   `.../lib/site-packages/...`（小写），而磁盘真实是 `Lib`。
   Windows 文件系统不敏感所以 `os.path.exists` 通过，但**传给 SKILL `load()` 会失败**。
   解法：逐级 `os.listdir` 找回真实名字。
2. **`?python` 必须给绝对路径**，默认值 `"python"` 大概率找不到。

### 服务已死时的恢复（只能人工）

服务 DOWN 后自动化无能为力，必须在 **Allegro CIW** 里手工执行：

```skill
load("<...>/skillbridge/server/python_server.il")
pyStartServer ?id "7777" ?python "<...>/.venv/Scripts/pythonw.exe"
```
（`pyKillServer` 此时会报 "server was not running"，可跳过）

---


## 十、★ 运行痕迹会散落一地（`.jrl` / `.log` 的落盘位置）

**结论：Allegro 和 SKILL 探针把日志写在"图纸所在目录"，不是某个固定位置。**

| 文件 | 谁写的 | 落在哪 |
|--|--|--|
| `allegro.jrl`（+ 轮转 `,1`…） | Allegro 会话日志 | **图纸目录** |
| `<symbol>.log`（+ `,1` `,2`…） | `axlCompileSymbol` 的编译日志 | **输出 `.psm` 的目录** |
| `master.tag` | Cadence 生成的图纸索引 | 图纸目录 |
| `batch_drc.log` | `axlRunBatchDBProgram` 跑 DRC | 图纸目录 |
| `probe_result.txt` | `outfile("probe_result.txt" "a")` | **Allegro 的当前工作目录**（`pwd` 才知道） |
| `<drawing>.dra.lck` | Allegro 的**建议锁**（照抄所引 `.dra` 名） | 图纸目录 |

→ 跑几次就能把干活目录搅成粥。**`outfile()` 一律写绝对路径**，别写相对名。

**★★ `.lck` 不是日志，但同样会"留下来害人"** —— 它记录的是
"这张 `.dra` 正被 `PID=xxx` 打开"。进程**非正常结束**（崩溃 / `taskkill //F` /
作业对象回收）时它**不会被清理**，下次打开该图纸就命中一条指向死进程的锁 →
弹确认框 → 又一次堵死通道。**详见第十一节「收回窗口 = 关闭 Allegro」。**

**★★ 归集日志的两个硬约束（踩过）**

1. **归集动作绝不能拖累主流程** —— 搬日志失败不能把建模/编译搞挂。
   包一层 `try/except` 全部吞掉，把结果记进 `steps` 就算完。
2. **★ Allegro 运行中一直攥着 `allegro.jrl`** → Windows 共享冲突，
   `shutil.move` 报 `PermissionError`。**这是正常现象，不是 bug** ——
   跳过、记进 `skipped` 就行。
   想收当前会话的 jrl，**只能等 Allegro 退出后**再跑一次归集。

**★ 重名处理别用 `os.path.splitext`**：Allegro 的轮转名长这样 `x.log,1`，
`splitext` 会把 `.log,1` 整个当扩展名，时间戳插到末尾变成 `x_2026...`，
把 `,1` 的可读性弄丢。**插在第一个点之前**：`x.log,1` → `x_20260929-150000.log,1`。

**★ 目录要分家（否则会踩模板污染）**
日志和图纸混在一个目录时，`new_symbol_drawing` 的**退路模板扫描**
（扫工作目录下的 `*.dra`）很容易把**探针脏图**当模板 →
模板自带的 pin 跟新建的 pin 撞号 → `duplicate pin numbers` → 编译中止。
→ 图纸归 `test/`、日志归 `log/`，别混。
（本项目实现在 `services/paths.py`，含可离线自检的归集逻辑）

---


## 十一、★ 从命令行 / 自动化环境启动 Allegro

### 17.0+ 起启动依赖极小

`<install>\tools\bin\allegro_cmd.bat` 头部注释，官方原文：

> Starting with the Cadence Allegro 17.0 release, we no longer require either
> the Window's PATH variable or the Cadence CDSROOT variable be set to run
> Cadence Allegro tools.

→ **`allegro.exe` 是自足的**。PATH 里一个 Cadence 路径都没有也能跑，
别被"双击能起、命令行不行"的假象骗了。
（`CDSROOT` / `PATH` 加不加都行，加只是让 skill / java 等子工具行为一致。）

真正必须的只有两个，都可在 `HKCU\Environment` 查到：

| 变量 | 作用 | 本项目值 |
|--|--|--|
| `HOME` | 定位 `pcbenv`（`allegro.ilinit` / `.mru` / `.ini` / 日志） | `D:\Dev_tools\Cadence_17.2\Cadence\SPB_Data` |
| `CDS_LIC_FILE` | license 文件 | `...\LicenseManager\license.dat` |

最小命令（cmd 里就这一行）：

```
start "" "D:\Dev_tools\Cadence_17.2\Cadence\Cadence_SPB_17.2-2016\tools\bin\allegro.exe"
```

### ★★★ 管道陷阱：`| tail` 会把命令搞"挂死"

**症状极具误导性，2026-09-29 真踩**：

```bash
cmd //c start_allegro.cmd | tail -4     # ← 挂住，最终 SIGTERM，连 echo 输出都没吐
```

原因：`start` 出来的 `allegro.exe` **继承了管道写端**。cmd 早就退出了，
但 GUI 子进程一直攥着写端 → 下游 `tail` 永远等不到 EOF → 整条命令被杀。

★ 误判风险极高：现象像"沙箱/自动化环境禁止启动 GUI"，实际与沙箱毫无关系。
**对策：重定向到文件，绝不接管道。**

```bash
cmd //c start_allegro.cmd > /tmp/out.txt 2>&1   # ✓ 正常返回
```

### 命令边界会回收 GUI 进程（沙箱内外一样）

Bash / PowerShell 工具执行的**一条命令 = 一个作业**，命令一结束，
它派生的 GUI 进程就被回收（`dangerouslyDisableSandbox` 也一样）。
实测：命令内等 45 秒 → `allegro.exe` 存活、7777 `LISTENING`；
下一条命令再看 → 进程已消失、端口已释放。

→ 能"启动 + 验证"，但**做不到"长期驻留"**。
（想用 WMI `Win32_Process.Create` 逃逸作业对象 → 被安全策略直接拦。）

### ★★★ 破局：用「后台挂住」让 Allegro 跨命令存活（2026-09-29 实测可用）

上面那条限制看着死路一条，但**回收的触发条件是「命令结束」** ——
所以只要让那条启动命令**永远不返回**，作业就不关闭，Allegro 就不会被回收。

**做法**：用 `run_in_background` 起一条**永不退出**的命令：

```bash
cd Tools && cmd //c start_allegro.cmd > /tmp/al_bg.txt 2>&1; \
  cat /tmp/al_bg.txt; while true; do sleep 20; done
```

- 命令自身"挂住" → 作业不关闭 → Allegro 稳定存活
- 之后**另开命令**（独立作业）连 7777 跑测试，完全不受影响
- 实测：跨两条命令后 Allegro 仍在（PID 432 持续存活）、7777 持续 `LISTENING`；
  期间跑通了 `inspect_environment()` 和 `new_symbol_drawing()`（都是首次运行）
- 测完 `TaskStop` 掉那条后台命令 → 作业关闭 → **Allegro 顺带被回收**，
  正好做到"测完即关"，不留残留进程
  （2026-09-29 实测证实，并附赠一条副作用 —— 见下）

> ★★★ **2026-10-09 实测修正：上面这条不成立（至少不必然成立）** ★★★
>
> `start_allegro.cmd` 用的是 `start "" allegro.exe`，Allegro **一启动就脱离了
> 父进程树**。实测：`TaskStop` 掉后台挂住的命令后 ——
> `allegro.exe` **PID 仍在**、7777 **仍 LISTENING**、MCP 仍 `connected: true`。
>
> 正确理解：
> - 后台挂住解决的是「**启动瞬间**被命令边界回收」；
> - Allegro 一旦起来就**独立存活**，`TaskStop` 带不走它；
> - 所以**不能指望 TaskStop 顺带关掉 Allegro** —— 收尾 A 必须单独做。
>
> 09-29 那次看到"顺带被回收"，可能是当时 Allegro 尚未完全脱离，
> 或是观察时机/其它因素。**以 10-09 这次为准：A 和 B 是两件独立的事，
> 谁也替不了谁。**

**★ 三个必须注意的点**

1. 启动那步**仍然不能接管道**（`| tail` 会挂死，见上）；重定向到文件。
2. 末尾的 `while true; do sleep N; done` 是**必需的**，一旦返回就前功尽弃。
3. 这样启的 Allegro 窗口是**真实显示在桌面上的**（Console 会话 1），不是隐藏进程 ——
   会打扰用户，用之前最好说一声。用户也可能正需要这个窗口来看现象。

→ 这是"AI 侧也能独立跑端到端测试"的唯一途径，
  也是**验证任何 SkillBridge 代码的第一选择**：不必再让用户手工启动。

### ★★★ 收回窗口 = 关闭 Allegro（2026-09-29 实测证实）

「后台挂住」的另一半价值：**它是一个"程序化关闭 Allegro"的手段** ——
不需要 `taskkill`，不需要点窗口右上角。

> ★ 这条**不是唯一手段**（2026-09-29 修正）：`axlShell("exit")` 与
> `taskkill` 同样能程序化关闭，且不要求 Allegro 由本会话后台命令启动。
> 完整四条路径见本节末尾「★ 铁律：任务结束就关掉 Allegro」。

**实测证据链**（起手：`allegro.exe` PID 432，7777 由 `pythonw.exe` PID 21680 监听）

| 时刻 | 动作 | `allegro.exe` | 7777 |
|--|--|--|--|
| T-0 | 后台任务 `03CKRP` 已挂住 9 分 13 秒 | **PID 432 存活**（442 MB）| LISTENING |
| T-0 | 对 `03CKRP` 执行 `TaskStop` | — | — |
| T+8s | 复检 | **已消失** | **已释放** |
| T+23s | 再复检（确认不复活）| 仍无 | 仍空闲 |

→ Bash 工具的**作业对象会连带回收整棵进程树**：`allegro.exe`、它 `load` 出来的
`pythonw.exe`（SkillBridge 服务）**一起走**。返回值是
`Cleanup completed for task "03CKRP"` + `Status: killed`。

**★ 但这是"硬杀"，不是正常退出 —— 代价是一条孤儿锁**

作业对象杀进程走不到 Allegro 的退出清理。实测残留：

```
Tools\NB_XTAL4_3D2X2D5X0D7.dra.lck     120 字节
# Allegro advisory lock file
DATE=Tue Sep 29 15:28:11 2026
USER=ds245
HOST=BMORN_COMPUTER
PROGRAM=allegro
PID=432                                 ← ★ 正是刚被杀掉的那个 PID
```

**这条 `.lck` 是真风险**：它是"图纸正被 PID 432 打开"的凭证，而 432 已经死了。
下次再开这张 `.dra`，Allegro 读到指向**死进程**的锁 → 触发
"drawing is in use / 只读打开？/ File Exists" 之类确认框 →
**又是一条能把自动化通道堵死的弹框来源**（第三节那类坑的新入口）。

**对策**：`TaskStop` 之后**顺手清掉该图纸的 `.lck`**，或至少在下一次
`new_symbol_drawing()` / `axlOpenDesign` 前清。
★ 本项目 `new_symbol_drawing()` 复制模板前已有 `os.remove(目标 .dra.lck)`，
所以**走它建的图不受影响**；但**直接 `axlOpenDesign` 打开已有 `.dra` 时没有这层保护**。

**★ 推荐收尾顺序（顺序不能换）**

```
0. 若跑了 stop_allegro.py，完成 A（关进程）后**必须再做 B**（TaskStop 后台会话）
1. TaskStop 掉后台挂住的命令     → 收回 shell（★ 2026-10-09 实测：
                                    **不会**顺带回收 Allegro，见上文修正）
2. 等 5~10s，确认 allegro.exe / pythonw.exe 都没了、7777 已释放
   （若第 1 步后 Allegro 仍在 —— 正常，须先跑 stop_allegro.py 走 A）
3. 清掉相关 .dra.lck（不想要就从 .trash 走，别 rm）
4. 跑 Tools/collect_logs.py      → 归集 allegro.jrl
5. ★ 复查：`TaskStop(task_id="__list__")` → 必须回 "There are no running background tasks."
```

第 4 步必须放最后：**Allegro 活着时 `allegro.jrl` 搬不动**
（进程攥着文件句柄，Windows 共享冲突，见第十节）。

第 5 步是**交付前的最后一道闸**：收尾做不干净，用户界面上还挂着"后台任务运行中"，
等于告诉用户"没干完"。**2026-10-08 就是漏了这一步**（详见上面 A/B 小节）。

### ★★★ 铁律：任务收尾就关掉 Allegro（2026-09-29 定案）

**不要「留着下次用」。** 每次任务收尾都关，下次测试前重新启。

**为什么（三条都有实测证据）**

1. **CWD 跨会话保持** —— `getWorkingDir()` 在**另起的新进程**里读到的
   还是上次的值（实测仍是 `Tools/test`）。残留 CWD 会**静默**改变下一次
   `axlSaveDesign` / `create symbol` 的落点 —— 这正是"产物凭空消失"的
   根因（第六节）。**外面看不出来，最阴的一条。**
2. **文件句柄不放** —— Allegro 运行期间一直攥着 `allegro.jrl` 与
   `*.dra.lck`：日志搬不动（第十节）；锁会误导下次启动弹
   「File Exists」→ 又一条能把通道堵死的弹框。
3. **通道状态不可知** —— 跑过会弹框的 API（如早年的 `axlCompileSymbol`）
   之后，通道可能仍在**静默错位**状态（第二节），你无法从外面看出来。

> **一句话：进程干净 → 每次测试都从同一个已知状态出发。**

**★ 四条关闭路径（按推荐度）**

| # | 手段 | 适用 | 代价 |
|--|--|--|--|
| 1 | **`Tools/stop_allegro.py`** | ★ **默认就用这个**；不管 Allegro 是谁启动的都行 | — |
| 2 | `axlShell("exit")` via SkillBridge | 想让它自己走退出流程 | 本机走不完（疑似卡对话框），仍需兜底 |
| 3 | `taskkill /F /T /PID <pid>` | 确定、快 | 留孤儿 `.dra.lck` |
| 4 | `TaskStop` 掉后台挂住的命令 | **仅当** Allegro 由**本会话**后台命令启动 | 同上；对独立启动的进程完全无效 |

#### ★★★ 上表是"关进程"的四条路，**不是收尾的全部**（2026-10-08 实测踩坑）

**如果 Allegro 是用"后台挂住"启的**（`cmd //c start_allegro.cmd` + `while true; do sleep 20; done`，
见第 1364 行那套），那么收尾有**两件互相独立的事**：

| | 事 | 手段 | 只做它的后果 |
|--|--|--|--|
| **A** | 关掉 Allegro 进程 | `stop_allegro.py` / `axlShell("exit")` / `taskkill` | Allegro 没了，**但那条 shell 还在空转** |
| **B** | 收回后台挂住的 shell | `TaskStop <task_id>` | shell 没了，Allegro 可能还活着 |

**★ 用 A 的方式关掉 Allegro，不等于 B 也被处理了 —— A、B 必须都做。**
上表第 1 行"默认就用 `stop_allegro.py`"说的是**关进程**那一件事，
**不能**因此跳过 `TaskStop`。

**2026-10-08 实测后果**：手动跑了 `stop_allegro.py`，Allegro 进程确实干净了
（`tasklist` 无 allegro/pythonw、7777 已释放），**但后台挂住的那条 shell 继续跑了
29 分钟**，WorkBuddy 界面上一直挂着"1 个后台任务运行中"的提示 ——
用户看到的就是"活没干完 / 收尾漏了"。它会一直空转到进程被显式杀掉为止。

**★ 自查一句**：`TaskStop` 一个**不存在的 ID** 即可列出当前所有运行中的后台任务：

```
TaskStop(task_id="__list__")
→ No task found with ID: __list__. There are no running background tasks.
→ （若在跑，会原样列出 ID + 命令行，如 "Running tasks: ON26rs (Bash: ...)"）
```

★ 注意：后台挂住的命令若自身带 `> /tmp/xx.txt 2>&1`，**输出文件在 `TaskStop` 之前
可能一直是空的**（缓冲没刷）——`TaskStop` 之后反而能读到完整内容（本次实测如此）。
所以别用"输出文件是空的"来判断它没在跑。

**★ 本项目实现：`Tools/stop_allegro.py`** —— 三阶段、幂等、全程 ≤20s

```
[0] 检测        tasklist /FI "IMAGENAME eq allegro.exe"
[1] 优雅退出    SkillBridge 发 axlShell("exit")，daemon 线程 + 12s 超时保护
[2] 兜底强杀    taskkill /F /T /PID
[3] 善后        孤儿 *.dra.lck → .trash/locks_<ts>/（★ 只挪不删）
[4] 归集        allegro.jrl → Tools/log/allegro_<ts>.jrl（含 ,1…,3 轮转档）
```

★ **只能用项目 `.venv` 的解释器**：系统 Python 3.10 / WorkBuddy 托管 Python 3.13
**都没装 `skillbridge`**，一 import 就 `ImportError: 请先安装 skillbridge`；
而且 `pip install skillbridge` 在本机装不上（PyPI 无可用版本）。
排查"脚本跑不起来"时先确认解释器是不是 `.venv/Scripts/python.exe`。

```bash
./.venv/Scripts/python.exe Tools/stop_allegro.py           # 默认：优雅→强杀→善后
./.venv/Scripts/python.exe Tools/stop_allegro.py --check   # 只看状态，不动手
./.venv/Scripts/python.exe Tools/stop_allegro.py --force   # 跳过优雅等待，直接强杀
```

实测一次真实收尾（PID 24524）：

```
[0] 当前进程 : [24524]
[1] 优雅退出 : expr=axlShell("exit")  →  通道被强行断开（10054）
    剩余进程 : [24524]        ← 12s 内没退完 → 强杀兜底是必需的
[2] 兜底强杀 : killed=[24524]  进程已清空 ✓
[3] 孤儿 .lck : 隔离 1 个 → .trash/locks_*/
[4] jrl 归集  : 2 份（Tools/log/ 与 Tools/test/ 各一）
  结论 : ✓ Allegro 已关闭，环境干净
```

**★★ 两个实测坑（第一次跑就都踩了）**

1. **`axlShell("exit")` 生效的铁证是 socket 被 reset。**
   `ConnectionResetError(10054)` / `OSError(10038)` **不是失败** ——
   恰恰说明 Allegro 收到 `exit` 并**开始关闭**（它主动断了 SkillBridge）。
   判成失败会白等一整个超时。
   ```python
   # ✓ 正确判据
   if any(k in str(e) for k in ("10054", "10038", "ConnectionReset",
                                "Connection", "socket")):
       sent = True     # 退出指令已生效
   ```

2. **`tasklist` / `taskkill` 输出是 OEM 代码页，中文 Windows = GBK/CP936。**
   用 `subprocess.run(..., text=True)`（默认 UTF-8）会抛
   `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb3 / 0xd0`。
   **正确姿势**：`capture_output=True` 拿 bytes，再按 GBK 解。
   ```python
   cp = subprocess.run([...], capture_output=True, timeout=20)   # ✗ 不要 text=True
   out = cp.stdout.decode("gbk", errors="replace")
   ```
   ★ 同一条坑也适用于任何解析 Windows 控制台命令输出的场合
   （`netstat` / `wmic` / `ipconfig` …）。

### ★ 通道错位的恢复（配合后台挂住一起用）

后台挂住让 Allegro 活着，但**通道本身仍可能错位/卡死**（尤其跑过会弹框的
API，如 `axlCompileSymbol`）。典型症状：

```
ConnectionError: SkillBridge 通道无法对齐：连续 24 轮发送的探针都读不回自己的结果
```

含义：Allegro 侧 `ipcWriteProcess` 队列里积压了大量陈旧回复。
恢复办法是让 Allegro 自己重启服务（`pyKillServer` → `load python_server.il`
→ `pyStartServer ?id "7777"`），详见第二节；
本项目实现为 `Tools/restart_skillbridge.py`（fire-and-forget，不需要读回复，
**通道错位时也能用**）。重启后队列清空，即可继续。

### `allegro.jrl` 落点由 `allegro.ini` 决定，**不是 cwd**

`<pcbenv>\allegro.ini`：

```ini
[allegro]
directory=C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp/Tools
```

`allegro.jrl` 就写在这个 `directory=` 指向的目录。
**启动前 `cd` 到哪都不改变 jrl 落点**（只有"新建图纸的默认目录"才看 cwd）。

→ 归集 jrl 只能事后做；**启动前**（Allegro 未运行时）是唯一能搬走旧 jrl 的安全时机，
可作为启动脚本的前置步骤。


---


## 十四、★★★ 定位并取证"屏幕上的 Allegro 窗口"（多显示器 / 后台任务，2026-09-30）

### 14.1 症状

- 在**双屏**环境（Allegro 在副屏）下截图 / 观察 Allegro GUI 失败：
  拿到全黑、纯色、或**别人的窗口**；误判成"Allegro 没渲染 / 没启动"。
- 更隐蔽的连带症状：**`axlZoomPoints` 等视图 API 返回 `t` 但画面纹丝不动**。

### 14.2 三个根因（必须同时排掉）

| # | 根因 | 表现 | 对策 |
|--|--|--|--|
| 1 | **副屏坐标可能是负数** | `GetWindowRect` 给出 `T=-1087`；从 `(0,0)` 起抓 / 只抓 `Screen.PrimaryScreen` → 窗口整个在范围外 | 用 `SystemInformation.VirtualScreen`（可含负原点）做源矩形 |
| 2 | **`PrintWindow` 对 OpenGL 画布无效** | 拿到缓存帧 / 黑块（Allegro 不响应 `WM_PRINT`） | 改用 `CopyFromScreen`（抓 DWM 合成后的桌面） |
| 3 | **坐标不同源** | 抓到了但图错位/黑 | 图像坐标 = `窗口rect - VirtualScreen原点`，并**裁到图像范围内** |

★ 附带真相：**OpenGL 窗口被遮挡（或非前台）时不刷新后备缓冲** →
`CopyFromScreen` 抓到的会是遮挡物；此时视图 API 也失效。
**取证前先把窗口置前**（`ShowWindow(SW_MAXIMIZE)` + `SetForegroundWindow`）。

### 14.3 正确姿势（脚本已随技能附带）

```powershell
# 1) 定位 + 截图：输出 JSON（屏幕布局 / 窗口几何 / 可见性）+ 全虚拟屏 PNG
powershell -NoProfile -ExecutionPolicy Bypass -File `
  "<skill>\scripts\find_allegro_window.ps1" -OutDir "<缓存目录>"
#   → <OutDir>\allegro_window.json   <OutDir>\allegro_shot.png

# 2) 数值取证：像素统计，不靠肉眼
<项目venv>\python.exe "<skill>\scripts\shot_stats.py" "<OutDir>\allegro_window.json"
```

`find_allegro_window.ps1` 每条窗口记录的含义：

| 字段 | 含义 / 判读 |
|--|--|
| `left/top/right/bottom` | **虚拟桌面坐标**，可为负 |
| `onScreen` | 窗口中心落在哪块屏（`\\.\DISPLAYn`） |
| `minimized` | `IsIconic` —— 最小化时画布不渲染 |
| `visible` | `IsWindowVisible` |
| `dwmCloaked` | `DwmGetWindowAttribute(h, 14, …)`：`0`=正常显示；非 0 表示被 DWM 隐藏（虚拟桌面/遮挡剔除），**此时截图必然失败** |
| `isForeground` | 是否前台窗口；非前台 ≠ 抓不到，但**被遮挡的区域会是遮挡物** |

### 14.4 数值判据（替代"看截图"）

`shot_stats.py` 对窗口区域输出：量化唯一色数 `uniq`、`stddev`、各特征色占比
（`near_black` / `green` / `yellow` / `white`）。

**判定"画布已渲染并可截取"**（三者同时成立）：

```
uniq >= 20                      # 不是纯色/空白帧
max(stddev) > 20                # 有内容层次
near_black < 0.97               # 不是全黑
```

**再加画布特征**：`green > 0.0005`（焊盘）或 `yellow > 0.0005`（丝印/装配色）。
实测（FPC-0510 6P，副屏窗口 1933×1045）：`uniq=255`、`std=120.5`、
`near_black=0.52`、`green=0.45%`、`white=43.9%` → 判定通过。

### 14.5 纪律

1. **截图只是辅助证据**；能用可读回 API 拿到的数值（bbox / 视图窗口 / 单位 /
   元素计数）优先，截图用来解释"为什么数值与肉眼感觉不一致"。
2. 只要窗口在屏幕上可见（`visible=True` 且 `dwmCloaked=0`），
   **全虚拟屏 `CopyFromScreen` 就能抓到**——失败一定是上述 1~3 号根因或遮挡。
3. 抓完图**不要把图当结论**：先跑 `shot_stats.py` 拿数值，再决定要不要展示。

---

