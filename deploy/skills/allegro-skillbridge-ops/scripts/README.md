# scripts/ —— 技能自带脚本

> 最后更新：**2026-10-08**（新增 `preflight_check.py`）
>
> ★ 维护者文档，**不被 SKILL.md 引用** —— 它不会进入对话上下文。
> `SKILL.md` 里只写到"有哪些脚本 + 一行用法"，不指向本文件。

## 这个目录是干什么的

放**确定性的、可重复执行**的动作：取证、复查。凡是"每次都要现写一遍"的东西，
就固化到这里。

设计约束：**除了 `preflight_check.py`，其余两个只依赖标准库**
（`stop_allegro.py` 不在本目录 —— 它属于用户项目 `layout-forge-mcp\Tools\`，
因为它要 import 项目的 `skillbridge`）。

## 文件清单

| 脚本 | 功能 | 依赖 | 会被谁调用 |
|--|--|--|--|
| `preflight_check.py` | ★ **收尾复查**：一条命令查 ① 7777 端口是否释放 ② `allegro.exe`/`pythonw.exe` 是否残留 ③ 项目里有无孤儿 `*.lck` ④ 项目根有无 `*.log`/`*.out`/探针脚本 | 仅标准库，**任意 Python 3.8+** | `SKILL.md` 第 2 节收尾清单第 3 步 |
| `find_allegro_window.ps1` | 定位屏幕上的 Allegro 窗口，输出 `allegro_window.json`（屏幕布局 / 窗口几何 / 可见性）+ 全虚拟屏 PNG | PowerShell | `references/lifecycle.md` 第十四节 |
| `shot_stats.py` | 对截图做像素统计 —— **数值取证替代肉眼**："画布有没有渲染出来" | 仅标准库（可能需 PIL） | 同上 |

## 用法

### 收尾复查（最常用）

```bash
# 以"当前目录"当项目根
python scripts/preflight_check.py

# 指定项目根与端口
python scripts/preflight_check.py \
    --project "C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp" \
    --port 7777
```

退出码：**0 = OS 层干净**，**1 = 有残留**。适合串进收尾流程里当闸门。

输出分四段（进程 / 端口 / 孤儿锁 / 项目根污染），最后固定打印一句提醒：

```
★ 后台任务列表（本脚本查不到，必须你手动确认）
   TaskStop(task_id="__list__")     # 传一个不存在的 ID
```

### 窗口取证

```bash
# 1) 定位 + 截图（★ 必须传 OutDir 的 Windows 绝对路径）
powershell -NoProfile -ExecutionPolicy Bypass \
  -File scripts/find_allegro_window.ps1 -OutDir "<缓存区>\shot"

# 2) 数值取证
python scripts/shot_stats.py "<缓存区>\shot\allegro_window.json"
```

## 关键约定与坑

1. **`preflight_check.py` 查不到"后台任务列表"** —— 那是**宿主的**能力，
   不是 OS 层的进程。所以脚本只能提醒，真正确认要靠
   `TaskStop(task_id="<随便一个不存在的 ID>")`。
   **这是 2026-10-08 那次漏收的根源**：Allegro 进程干净了，
   但"后台挂住"的 shell 又空转了 29 分钟。

2. **`tasklist` 的输出是 GBK**。脚本里 `_decode()` 先试 GBK 再试 UTF-8 最后
   `replace` —— **不要**把 `subprocess` 拿到的 bytes 直接当 str 用（会乱码，
   然后 `in` 判断全 False，看上去"没有残留"，实际有）。

3. **判断端口用 socket connect，不要解析 `netstat`**：
   connect 成功 = 有监听，跨语言、跨 locale 都稳。

4. **遍历项目时剪掉 `.venv` / `.git` / `.trash` / `__pycache__` / `node_modules`**，
   否则又慢又假阳性（`_walk_pruned()`）。

5. **`preflight_check.py` 是只读的** —— 它只报告，不动手。
   清锁、清日志要不要做、怎么做，由人决定（按 `workspace-hygiene`：
   先列清单、确认后再动，走 `.trash` 或让用户自己删，别 `rm -rf`）。

6. **别在技能目录里跑 `py_compile` 之后忘了清 `__pycache__`**（2026-10-08 踩过）。

## 相关目录

- 上层：`../SKILL.md`、`../references/`
- 维护说明：`../README.md`
- 项目侧脚本（不在本目录，因为依赖项目环境）：
  `layout-forge-mcp\Tools\stop_allegro.py`、`start_allegro.cmd`、`collect_logs.py`、
  `selfcheck_geometry.py`、`selfcheck_skill_syntax.py`
