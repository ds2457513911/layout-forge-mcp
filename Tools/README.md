# Tools/ —— CLI 脚本 / 探针 / 自检 / 文档

> 最后更新：2026-10-09（新增 `debug/` 与 2 个迁入脚本）

## 这个目录是干什么的

**人要读、要跑的**东西放这里（源码业务逻辑在 `../services/`）。

四个子目录各有明确分工，**别混**：

| 子目录 | 放什么 |
|--|--|
| `test/` | ★ **测试图纸干活区**：`.dra` / `.pad` / `.psm` 产物（每个符号一个子目录，见 SKILL §3.2 归档口径） |
| `log/` | ★ **日志区**：Allegro 的 `.jrl` / `.log`、`master.tag`、探针输出、脚本日志 |
| `debug/` | ★ **调试排障脚本**（2026-10-09 从 `layout-check-mcp` 迁入，见下） |
| `__pycache__/` | Python 字节码（可随时删） |

## 文件清单

### ★ 自检 / 校准（最常跑的两个）

| 文件 | 功能 | 需要 Allegro |
|--|--|--|
| `selfcheck_geometry.py` | ★ **求解器离线自检 239 项**：命名↔数值、padstack、pin 坐标、外框丝印、拓扑、防错、**尺寸取值口径**、**extracta 视图防线**、**位号模板口径**、**同目录守卫**、`[9b]` **assembly 执行器 gap 回归（pin1_mark 一次成型 / drill_type 默认 OBLONG_DRILL）**、`[10]` **CSTJ-421 通孔槽孔件 / 三 padstack / body_center** | ❌ |
| `selfcheck_skill_syntax.py` | ★ **SKILL 表达式构造器自检 221 项**：括号平衡、API 名核对、参数形态、回复帧解码、目录约定、NOCONFIRM 变量族、`[14]` **auto extents 覆盖计算**（防"图元落在绘图范围外被静默丢弃"） | ❌ |
| `online_calibrate.py` | ★ **在线校准闭环**：新建图纸 → 建封装 → 编译 `.psm` → 回读 → 与人工金标准逐值对拍。`[A]` 形态必须一致 / `[B]` 已记录口径差异。带 `--install` 可顺带落影子库 | ✅（`--no-build` 时只回读） |
| `dump_golden_geom.py` | 把某张 `.dra` 的几何**全精度** dump 成 `log/golden_geom.json`（离线校准丝印形态用） | ❌ |
| `benchmark_golden.py` | ★ **金标准批量对拍（三层漏斗第 1 层，2026-10-12 新增）**：扫 `Data\PCB_Footprint` 全部 `.dra`，强制离线（`LAYOUT_CHECK_FORCE_SOURCE=extracta`，不碰 Allegro）逐件提取 + 跑规则检查，产出 `log/golden_benchmark/`（summary.csv / report.json / exceptions/）。**例外件 JSON 就是第 2 层 AI 复核的输入**；`--only` 单件过滤、`--limit` 冒烟。退出码：0 全过 / 1 有 FAIL / 2 有提取失败 | ❌ |
| `verify_e2e.py` | 端到端验证：新建图纸 → 建 padstack → 放 pin | ✅ |
| `e2e_3S48000163.py` | 3S48000163 的端到端实测脚本 | ✅ |
| `e2e_fpc0510_6p.py` | FPC 0.5-10P 软排线座端到端实测（SMD 件） | ✅ |
| `e2e_cstj421.py` | ★ **CSTJ-421-1010BG 音频插座端到端实测**（**第一个 THT 通孔件**：6 脚全槽孔、3 个 padstack、原点取"中心线×本体上边缘"） | ✅ |

### 探针（一次性/排障用）

| 文件 | 功能 |
|--|--|
| `probe_api_exists.py` | 探测某个 AXL/SKILL 函数**在运行时到底存不存在**（文档不可信） |
| `skill_api_probe.il` | 上面那个探针的 SKILL 侧脚本 |
| `probe_psm_onsave.py` | 验证「symbol editor 里保存 `.dra` 是否自动产出 `.psm`」 |
| `probe_save_recompile.py` | 验证「保存被改过的 `.dra` 是否自动重编译」 |

### 运维 / 生命周期

| 文件 | 功能 |
|--|--|
| `start_allegro.cmd` | ★ **启动 Allegro**（设 HOME/CDS_LIC_FILE/CDSROOT，cwd 切到 `test/`，先归集上次日志）。**必须由桌面会话启动**，从 Bash/脚本里拉会被回收 |
| `stop_allegro.py` | ★ **任务收尾关闭 Allegro**（三阶段：优雅退出 → `taskkill /F /T` 兜底 → 孤儿 `.lck` 入 `.trash` + `allegro.jrl` 归集）。`--check` / `--force` |
| `collect_logs.py` | 把 `test/` + `Tools/` 里的运行痕迹搬进 `log/`（`--dry-run` 可预览） |
| `restart_skillbridge.py` | 让 Allegro **自己重启** SkillBridge 服务 |
| `install_skillbridge.py` | 给 Allegro 配置 SkillBridge 自动加载。**一般由 `Tools\deploy\setup_agent.py` 调用**，加 `--non-interactive`（多 pcbenv 候选时不再弹选择，直接取第一个，供一键部署用）、`--pcbenv`、`--dry-run`、`--uninstall` |
| `print_restart_cmds.py` | 打印**可直接粘贴**的 SkillBridge 恢复命令（含路径存在性预检） |

### 库分析 / 批量扫描（2026-10-09 从 layout-check-mcp 迁入）

| 文件 | 功能 | 需要 Allegro |
|--|--|--|
| `dump_all_footprints.py` | ★ 批量扫 `Data/footprint` 下所有 `.dra`，输出 pin 名 / pad 层名 / 尺寸 / 同目录 `.pad`，**供分析 padstack 命名规则**。产物 `Tools/footprint_dump.txt` | ❌ |
| `cleanup_redundant.py` | ⚠️ **只读参考，别在本项目直接跑** —— 它是当年整理 `layout-check-mcp` 的一次性清理脚本（备份到 `.cleanup_backup/` 再删冗余），内含旧版工具快照。留档用于参考写法 | ❌ |

> ★ `dump_all_footprints.py` 迁入后改过路径逻辑：
> 本项目根下**没有** `Data/footprint`（真实封装库在上一级 `Layout\Data\footprint`），
> 脚本会自动退回上一级；也可把目录作为第一个参数传入。

### 文档

| 文件 | 内容 |
|--|--|
| `ALLEGRO_SKILL_API_核实报告.md` | AXL/SKILL API 的存在性与签名核实结果（含官方文档出处） |
| `探针运行指引.md` | 怎么跑探针、怎么看结果 |
| `debug/README.md` | `debug/` 子目录说明（含"项目根是上三级"的坑） |

## 关键约定与坑

1. **`tasklist` / `taskkill` / `netstat` / `wmic` 的输出是 OEM 代码页（中文 Windows = GBK）**，
   用 `subprocess.run(..., text=True)` 会抛 `UnicodeDecodeError` → 拿 bytes 再 `decode("gbk")`。
2. **Bash 里跑控制台命令不要接管道**（会挂死）→ 重定向到文件再读。
3. **GUI 程序（allegro.exe）不能从命令边界外驻留** → 必须由真实桌面会话启动。
4. **收尾必须关 Allegro**（见 `../README.md` 与技能 `allegro-skillbridge-ops`）：
   `getWorkingDir()` 跨会话保持，残留 CWD 会静默改变下次 `axlSaveDesign` 的落点。
5. **每个脚本的「运行痕迹」都该落到 `log/`**，别留在 `Tools/` 根或项目根。
6. ★ **我的一次性探针脚本请写到 WorkBuddy 缓存区**
   （`C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\scratch\`），
   **不要**新建在这个目录里 —— 这里只放**要长期维护**的脚本。
7. ★ **`debug/` 下的脚本定位项目根要用「上三级」**（`Tools/debug` → `Tools` → 项目根）。
   2026-10-09 迁入时修过两个脚本的 `parent.parent`（少一层，会 import 失败）。
   以后在 `debug/` 里新加脚本照抄这个写法。

## 常见命令

```bash
# 离线自检（最常用）
.venv\Scripts\python.exe Tools\selfcheck_geometry.py
.venv\Scripts\python.exe Tools\selfcheck_skill_syntax.py

# 在线校准（需要 Allegro 已在跑）
.venv\Scripts\python.exe Tools\online_calibrate.py
.venv\Scripts\python.exe Tools\online_calibrate.py --no-build      # 只回读对拍
.venv\Scripts\python.exe Tools\online_calibrate.py --install --pn 3S48000163

# 收尾
.venv\Scripts\python.exe Tools\stop_allegro.py --check
.venv\Scripts\python.exe Tools\stop_allegro.py
```

## 相关目录

- 业务源码：`../services/`
- 产物：`./test/`；日志：`./log/`
- 隔离区：`../.trash/`
