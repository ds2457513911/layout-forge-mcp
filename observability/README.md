# observability/ —— L4 可观测性层（遥测 / 时间线 / 统计）

> 最后更新：2026-10-15（补 `step` 合法取值 = `orchestration/pipeline.py::STEP_NAMES`（preflight / env-check / extract / check / report）+ `record(tool="")` 落盘口径；此前 2026-10-14（Phase 1 完成：`telemetry.py` / `runstore.py` / `stats.py` 落地，**MCP 中间件已接线**，实测每次工具调用都落盘；新增 `StepRecord.fail()` 记录"没抛异常但返回 ok:false"的失败）

## 这个目录是干什么的

让「一次任务花了多少、慢在哪一步、哪条规则最常挂」变成**一条命令就能答出来**的问题。

**要解决的现状**：项目**有产物、没视图**。

| 已经有的 | 缺的 |
|--|--|
| `Tools/log/` **1,265 个文件**（`.txt` 508 / `.json` 240 / `.jrl` 208 / `.log` 114 …） | 之前没人能回答「最常 FAIL 的是哪条规则」（现在 `stats.py` 能了） |
| `knowledge/cases/` 10 条裁定（含 findings / verdict） | 没有耗时、没有步骤、没有 `run_id` |
| 239 + 222 项离线自检 | 自检只知成败，不知哪一项最常红 |

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `__init__.py` | 分层声明（本层是跨切面叶子；遥测绝不抛异常；只落字段名 + hash） | — |
| `telemetry.py` | ★ **写侧**：`track(tool, step, args)` 上下文管理器（成功/失败/异常都落一条）、`StepRecord.degrade(code, detail)` 登记**静默降级**、★ `StepRecord.fail(code, detail)` 登记**没抛异常但 `ok:false` 的业务失败**（中间件用它把 `{"ok": false, "error_code": ...}` 变成遥测）、`record()` / `log_degraded()` 单发、`hash_args()` 脱敏、`classify()` 异常→稳定错误码、`new_run_id()` / `set_run_id()` | 只 import `services.paths`；由 `mcp_server/middleware.py` 的 `on_call_tool` 中间件调用 |
| `runstore.py` | ★ **读侧**：`list_runs(days)` / `timeline(run_id)` / `latest_run()` / `health()` / `parse_errors()`（坏行必须可见） | 只读 `eval/runs/*.jsonl`；纯 stdlib |
| `stats.py` | ★ **聚合**（`python -m observability.stats`）：工具调用/失败/降级/耗时排行、**规则 FAIL 排行**、知识库分布、**缺口清单** | 吃 `eval/runs/*.jsonl` + `Tools/log/checklist_report_*.json` + `knowledge/**/_index.json` |

★ **与 Phase 0 计划的偏差（已改正）**：计划里写的是「`@observed` 装饰器由 `mcp_server/tools/registry.py` 统一套用」。实际实现改为 **`track()` 上下文管理器 + FastMCP `on_call_tool` 中间件**：中间件必须放 `mcp_server/`（它要 import `fastmcp`），这样本层才能保持**纯 stdlib**。装饰器方案还有个副作用 —— 会改写工具签名/docstring，而 docstring 就是本项目的接口文档。

## 用法（2026-10-14 实测通过）

```bash
# 人看（默认 7 天窗口）
python -m observability.stats
python -m observability.stats --days 1

# 机器读（MCP 工具 get_run_stats 走的就是 summarize()）
python -m observability.stats --json
```

实测输出（2026-10-14，**接线后**的真实结果；接线前的空数据作为对照保留在下一行）：

| 段落 | 接线后 | 接线前 |
|--|--|--|
| 遥测 | **run 1 次 / 事件 3 条 / 坏行 0 行**；工具调用 3 次（失败 1 / 降级 0）；`preflight_check 2/1/0 p50=2 p95=42`、`get_run_stats 1/0/0 p50=p95=38`（★ 这是改名前的实测记录：该工具 2026-10-14 已由 `preflight_check` 改名为 **`folder_preflight`**，改名前的历史事件仍留在 `runs/` 里、按当时名字统计，不做回填） | run 0 / 事件 0 —— 缺口清单如实喊「没有遥测」，不装成有数据 |
| 规则 | 报告 5 份 / 条目 85 条：`PASS=82 WARN=2 FAIL=1`；最常 FAIL = **`5.2` Place_Bound 外扩量** | 同 |
| 知识库 | cases 10 条（`false_positive=3 pass_with_notes=2 convention=1 real_defect_rule_gap=1 mixed=1 review_note=1 keep_fail=1`）/ specs 5 份 | 同 |
| 缺口 | **1 项**：`eval/baselines/` 冻结基线（现已由 `tool_contract` 补上第一个） | 2 项：遥测未接线 + 无冻结基线 |

## 落盘位置与格式

- 路径走 `services/paths.py::RUNS_DIR` = `eval/runs/<YYYY-MM-DD>.jsonl`（**不进 git**，见 `.gitignore`）
- JSONL **逐行追加**，每行一条事件（`schema=1`）：

```
schema / ts / run_id / step / tool / ok / ms
/ error_code / error_type / error_msg / error_source / args_keys / args_hash / degraded[]
```

- `error_source` 区分两种失败：`"exception"`（工具抛异常，由 `track()` 记）/ `"returned"`（工具返回 `ok:false`，由中间件的 `StepRecord.fail()` 记）。**两种都要在聚合里出现**，否则"返回失败"这类最常见的失败会从统计里消失。

- `error_code` 是**稳定枚举**（`NOT_FOUND` / `PERMISSION` / `TIMEOUT` / `DEP_MISSING` / `CHANNEL_DOWN` / `IO_ERROR` / `BAD_INPUT` / `INVARIANT` / `INTERNAL` / `CANCELLED`），聚合与 `tool_contract` 基线都按这个字符串比。
- `degraded[]` = `[{"code": "...", "detail": "..."}]`，**没抛异常但结果不可信**的情况必须登记在这里。

## 关键约定与坑

1. **`args` 只落字段名 + hash**，绝不落明文料号、文件名与绝对路径（客户资料 + 体积）。已实测：事件里出现 `folder_path` 字段名，但**参数值 `"x"` 不出现**。
2. **遥测永不影响主流程**：`record_event()` 内部吞掉一切异常，写盘失败只返回 `False` —— 同 `paths.collect_run_logs()` 的三条硬约束。
3. **降级必须显式**：`except` 里默默 `continue`、`or ""`、`or []` 这类回落，一律补 `rec.degrade("CODE", "...")`；否则「静默失败」永远看不见（这是坑清单里 extents 丢弃 / holeType 回落 / CLASS 缺口三类事故的共同形状）。
4. **token 数不进本层**：它是**客户端**（会话侧）指标，本项目拿不到也不该伪造。
5. **本层是跨切面叶子**：只 import 标准库（`services.paths` 取目录真源是唯一例外）；**禁止** import `mcp_server` / `orchestration` / `services` 的业务模块 —— 否则埋点会变成依赖环。
6. **旧产物不回填**：`stats.py` 同时读两类源，但报告里必须**如实标注** —— 新 JSONL 有耗时/步骤维度，`Tools/log` 与 `knowledge/cases` 的旧数据没有。
7. `step` 命名与 `orchestration/pipeline.py` **保持一致**，否则两边对不上。合法取值就是
   `orchestration/pipeline.py::STEP_NAMES` = **`preflight` / `env-check` / `extract` / `check` / `report`**
   （另有一个历史字面量 `degraded`，来自 `telemetry.log_degraded()`）。★ 落盘时要
   `telemetry.record(step=<名字>, tool="")` —— `stats.py::step_stats()` 会**丢弃带 `tool` 的事件**
   （`if not step or ev.get("tool"): continue`），所以「既有工具名又想进步骤统计」是做不到的，
   别在 `pipeline.py` 里试图两全。

## 相关目录

| 目录 | 关系 |
|--|--|
| `../eval/runs/` | 遥测落盘处（gitignore） |
| `../orchestration/` | `step` 命名同源；`preflight` 也产出遥测 |
| `../Tools/log/` | 旧产物源（`checklist_report_*.json`，现存 5 份） |
| `../knowledge/cases/` | 裁定分布的数据源 |
| `../eval/suites/architecture/check_architecture.py` | 断言第 5 条的 import 边界 |
