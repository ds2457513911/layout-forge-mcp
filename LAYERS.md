# LAYERS.md —— 五层能力 ↔ 模块映射（**唯一权威**）

> 最后更新：2026-11-20（票 #16 / ADR-0002 D5·D8：`services/padstack_derive.py` 进 L1 内核 —— padstack 形态决策入口与 `create_padstack` 的实例化拆开；L5 geometry 判据扩到 R1–R5）。此前：2026-10-15（Phase 0 建立；Phase 1：L4 遥测接线 + L3 `preflight` 工具化 + L2 契约套件；Phase 2-a：L5 几何回归评分套件 + 冻结基线；Phase 2-b：L5 `spec_reading` 骨架 + 10 例真值签字 + **S8 定位判据与 `pdf_locate` 双证据重写**；Phase 2-c：L3 **`behavior` 四个行为夹具**（S1/S2/S4 变成可执行断言）+ `preflight` 噪声规则扩到「运行痕迹 + 历史报告」并公开 `is_noise()` → **G4 现形 145 个噪声文件，当日隔离清理后复绿**；Phase 2-d：**验收收成一条命令 `eval/check_eval.py`**（五套断言汇总 + 元判据 E1–E4）；Phase 3：**`playbook.json` 规则装配表 + `pipeline.py` 任务状态机落地**（25 条 `rule_id` + 13 条 `G-*` 门禁，每条都带「检测方式 + 后果类别」），behavior 加 B5 守这两份东西）
> 分期清单与重构计划在**缓存区**（不进仓库）；本文件是仓库内分层与依赖方向的唯一权威。

## 为什么有这份文件

项目按「LLM 工程手段五层」体检过一遍（2026-10-14）：

| 层 | 体检结论 |
|--|--|
| 1 确定性内核 | **最扎实** |
| 2 契约与工具层 | **已具备** |
| 3 编排与控制 | 部分 → **骨架齐全**（Phase 1 把 `preflight` 工具化；Phase 3 补上规则装配表 `playbook.json` + 任务状态机 `pipeline.py`，并被 behavior 的 B5 守住） |
| 4 可观测性 | 较弱 → **已接线**（Phase 1：每次工具调用落 JSONL，`stats` 可读）；仍缺「规则 FAIL 排行进 SKILL」 |
| 5 度量与评测 | **待补（空白）** → 骨架已起（architecture + tool_contract + **geometry_regression** 三套断言 + 冻结基线）；**spec_reading 真值仍空白** |

本文件把「层 ↔ 目录 ↔ 防线 ↔ 谁在守」钉死。**改结构先改这里**，再改代码。

## 层 ↔ 目录 ↔ 防线

| 层 | 目录 / 文件 | 已有什么 | 还缺什么 | 谁来守 |
|--|--|--|--|--|
| **1 确定性内核** | `services/` 的纯函数群：`geometry_solver.py` / `skill_snippets.py` / `rule_checker.py` / `convention_engine.py` / `padstack_derive.py` / `spec_library.py` / `case_library.py` / `validation.py` / `json_utils.py` / `report_renderer.py` / `paths.py` | 零 Allegro 依赖；239 项几何自检 + 222 项 SKILL 语法自检 + 253 件金标准对拍 | —（只防退化） | `eval/suites/architecture`（A3）+ `eval/suites/geometry_regression` |
| **2 契约与工具层** | `mcp_server/`（工具清单见 `docs/tools-catalog.md`，`tools.py` 内 docstring 即契约；`middleware.py` 统一埋点） | 证据制判据（`axlCompileSymbol` 恒 nil → 看磁盘快照）；静默失败清零 | 统一错误码 `errors.py`；返回信封（`run_id` / `error_code`）；`list_folder_files` 的**歧义可见化**（Phase 1 已用 `folder_preflight` 从外部补齐，工具本身不动 —— 它与 `layout-check-mcp` 逐字节共享） | `eval/suites/tool_contract`（✅ 已就位：数量与名字 / docstring 参数 / 遥测口径 / 中间件接线 + 冻结基线 + 分组完整性 C7 + 目录过期 C8） |
| **3 编排与控制** | `orchestration/`（`preflight.py` 已实现，`is_noise()` 为噪声判定单一真源；**`playbook.json` 规则装配表 + `pipeline.py` 状态机已落地**）+ 两份 SKILL | 铁律 / S1–S4 / 红线清单；部署侧 `Tools/deploy/scripts/preflight_check.py`；`playbook.json` 25 条 `rule_id` + 13 条 `G-*` 门禁（每条带检测方式与后果类别 `deny`/`silent`/`recoverable`，并把已删除仍在流通的 `6.5` 记进 `stale_rule_ids`）；`pipeline.py` 五步状态机 + 失败码 `CHANNEL_NOT_READY`/`CHANNEL_BUSY`/`AMBIGUOUS_INPUT`/`NO_CANDIDATES`/`UNITS_UNKNOWN`/`NO_DATA`；`services/channel_guard.py` 通道锁 + 建图租约（撞锁报 `CHANNEL_BUSY` 带持有人） | SKILL 侧尚未按计划书 §7 改写（S1 改读 `ambiguous` 字段、S4 改按失败码分支、新增「工具已保证」小节、头部三声明 `mcp_min_commit`/`verified_with`/`eval_baseline`）；**SKILL 待记条数仍 >5** | `eval/suites/behavior`（✅ 已就位：B1 S1 歧义 / B2 S2 旧报告不算证据 / B3 噪声剔除 / B4 S4 先探通道 / **B5 L3 状态机 + playbook 硬约定** / **B6 D4 通道锁 + 建图租约**）+ A1 |
| **4 可观测性** | `observability/`（`telemetry.py` / `runstore.py` / `stats.py`）+ `eval/runs/` | 有产物：`Tools/log/` 1,242 个文件、`knowledge/cases/` 10 条裁定；**每次工具调用已落 JSONL**（`ts/run_id/tool/ok/ms/error_code/args_keys/args_hash`，只记参数名与 hash） | token 数属**客户端**指标（不进本仓）；缺口清单由 `stats.summarize()["gaps"]` 如实喊 | A2 + A6 + `eval/suites/tool_contract` C5/C6 |
| **5 度量与评测** | `eval/` | `suites/architecture`（A1–A10）+ `suites/tool_contract`（C1–C6）+ `suites/geometry_regression`（G1–G6 + R1–R5：239 / 222 / 39 / 253 件 + extracta 被调方探针 + padstack 决策入口对拍）+ `suites/spec_reading`（S1–S8 + 8 字段四分类 + **真值人工签字闸门** + **S8 定位不撒谎**）+ `suites/behavior`（B1–B5）均已就位；`baselines/tool_contract.json`、`baselines/geometry_regression.json`、`baselines/spec_reading.json`（92.5%）冻结 | `run_eval.py` 汇总入口；扩充 spec_reading 用例（现 10 例） | 本层自己（唯一没被守的层要自守） |


★ **层是能力，不是目录名。** 已有能力不靠改名变强，靠把文档里的规则变成**可执行断言**（这就是本文件与 `check_architecture.py` 的用处）。

## 依赖方向（单向，越往下越确定）

```
mcp_server/        工具层（对外门面）
    ↓
orchestration/     编排与控制（规则装配、前置自检、状态机）
    ↓
services/          确定性内核 + 读取层 + 执行层
    ↓
Data/  knowledge/  纯数据（不是代码层，谁都能读）
```

| 目录 | 允许 import | 禁止 import |
|--|--|--|
| `services/` | 标准库、`services.*` | `mcp_server`、`orchestration`（例外见「已知偏差」） |
| `orchestration/` | 标准库、`services.*` | `mcp_server`、`observability`（埋点由 `mcp_server/middleware.py` 统一做，本层自己写日志只会得到第二种口径） |
| `observability/` | 标准库、`services.paths`（取目录真源） | `mcp_server`、`orchestration`、`services` 业务模块 |
| `mcp_server/` | `services`、`orchestration`、`observability` | —（它是顶层门面） |
| `eval/` | 任何项目模块 | **绝不被项目源码 import**（生产代码依赖测试 = 方向反了） |

**为什么不搬迁目录**（2026-10-14 决定）：`services/` / `knowledge/` / `Tools/` / `docs/` / `Data/`
一动就要改全部 MCP 工具 + 27 个 Tools 脚本 + 两份 SKILL + 与 `layout-check-mcp` 逐字节共享的 8 个文件。
层能力靠**新增层目录 + 断言**补齐，不靠改名。

## 登记手续：新增目录 / 新增模块怎么做

> 哪类事实归哪一层（工具 / 读数 / 通道 / 部署 / 文档指针）见 [`docs/边界契约.md`](docs/边界契约.md) 的「事实归属」表。
> 本节只留 LAYERS 自己的登记动作；新增 / 改名工具的手续在契约「三条改动手续」第 1 条，这里不再重复。

| 要做的事 | 必须同步改的地方 |
|--|--|
| 建一个新目录 | ① 写该目录 `README.md`（定位 / 最后更新 / 文件清单 / 约定与坑 / 相关目录）② 加进根 `README.md` 的目录导航表 ③ 加进 `eval/suites/architecture/check_architecture.py::REGISTERED_DIRS` ④ 若是源码包，加进 `pyproject.toml` 的 `packages.find.include` |
| 加一个确定性内核模块 | 加进 `check_architecture.py::CORE_MODULES`（自动获得「禁止连 Allegro」保护） |
| 加一个目录约定 | 只加在 `services/paths.py`（**唯一真源**），别在别处硬编码路径 |

## 已知偏差（声明制：仍然打印，但不算 ERROR）

| 位置 | 偏差 | 清偿方向 | 期限 |
|--|--|--|--|
| `services/skillbridge_writer.py:195` | `AllegroSession.__init__` 里 `from mcp_server import config` 取 `SKILLBRIDGE_WORKSPACE_ID` —— 服务层反向依赖工具层 | 默认值下沉到 `services`（`paths.py` 或新的 services 配置模块），或改由调用方传入 | Phase 1 |

> 声明不等于豁免：`check_architecture.py` 会把每一条打成 `W3` 打印出来。**清偿后从 `DECLARED_EXCEPTIONS` 删掉**。

## 与旧文档的分工（避免第四份真源）

| 文档 | 讲什么 | 与本文的关系 |
|--|--|--|
| 根 `README.md` | 给人看的入口：定位 / 架构 / 目录导航 / 快速开始 | 目录清单以它为准，**分层与依赖以本文为准** |
| `services/README.md` | `services/` **内部**五层（求解 / 决策 / 执行 / 读取 / 代码生成） | 是本文件第 1 层的下钻 |
| 两份 SKILL | AI 的**语义裁定**与操作纪律 | 能被代码保证的规则已按计划书 §7 逐条移交 `orchestration/`（2026-10-15）：「AI 待记」去重后 **4 条**（S1–S4），其余改为「读工具返回的哪些字段」 |
| `knowledge/cases/` | 某次裁定的**证据与教训** | 与 `orchestration/playbook.json` 用 `rule_id` 互指，不复制内容 |
| `eval/README.md` | 评测层细则（真值与基线纪律） | 本文件只登记「层在哪、谁守」 |

## 验收（一条命令 / 五套断言）

```bash
# ★ 日常就一条（Phase 2-d）：五套断言全跑 + 汇总；缺依赖记 ERROR、不静默跳过
python eval/check_eval.py            # 含 253 件对拍，约 2 分钟
python eval/check_eval.py --fast     # 冒烟：geometry 跳过 253 件对拍
python eval/check_eval.py --json     # 机器可读（CI）
```

排障时可单独跑（判据仍写在各自套件里，本脚本只汇总）：

```bash
python eval/suites/architecture/check_architecture.py      # 层方向 / 登记 / README 覆盖 / 可解析 / 契约指针（A1–A10）
python eval/suites/tool_contract/check_tool_contract.py    # 工具数量 / docstring 参数 / 遥测口径 / 中间件（C1–C6）
python eval/suites/geometry_regression/check_geometry_regression.py   # 239/222/39 项 + 253 件对拍 + extracta 探针（G1–G6/R1–R5）
python eval/suites/spec_reading/check_spec_reading.py      # 读图准确率（S1–S8）：真值须人工签字，未签字时加 --allow-unsigned 自检
python eval/suites/behavior/check_behavior.py              # S1/S2/S4 硬规则 + L3 状态机可执行性（B1–B5，断言套件无基线）
```

`eval/check_eval.py` 只做**跑全 + 计数 + 汇总**：判据不复制、阈值不复制；它自己的元判据是
E1（五套都要跑到，缺依赖记 ERROR）/ E2（看退出码，不解析人话）/ E3（`--json` 解析失败也算 ERROR）/
E4（实跑行数必须等于计划行数）。**汇总漏一行却不报错，就是把闸门拆了一角。**

- **现状（2026-10-15 Phase 3 完成）**：`python eval/check_eval.py` **五套全绿 / 退出码 0**（architecture 只剩 1 条已声明偏差 W3 = `skillbridge_writer.py:195`）；`spec_reading` 10 例真值已签字、基线 **92.5%**；★ **G4 当日红过又转绿**：噪声名单扩展到「运行痕迹 + 历史报告」后，三个库现形 **145 个**噪声文件（`allegro.jrl` / `master.tag` / `nb_*.log` / 历史 `checklist_report_*.json`）—— 这是**真发现**（有人把库目录当 Allegro 工作目录用过；旧报告正是 S2 要防的）。已用 `_quarantine_file()` 全部隔离进 `.trash/`（绝不 `os.remove`），复跑 **G4 = 噪声 0**；**判据与名单一个字没放宽**。
- **Phase 3 的验收两项（2026-10-15）**：① `eval/suites/behavior` **5/5 通过**（B5 新守 `orchestration/playbook.json` + `pipeline.py`）；② SKILL「AI 待记」**去重后 4 条 ≤ 5**（S1–S4；铁律第 9/10 条与 S3 是同一批规矩，不重复计数）。另加部署侧**防静默回退**：`Tools/deploy/setup_agent.py` 的 `.deploy_manifest.json` + `--verify-skills` / `--sync-skills`（当天实测抓到 `allegro-skillbridge-ops/references/mcp-tools.md` 三落点陈旧并复绿）。
- **几何回归套件必须用「工作区外」的解释器**：被 DSH 沙箱限制的解释器会让 extracta 在第 2 路崩掉、把 R4 整片染红（详见该套件文件头「沙箱坑」）
- **规则**：新增层目录 / 新模块 / 新工具后必须复跑；红了先修事实与基线，**不许把阈值调松**；判定逻辑要**调同一个函数**（如 `preflight.is_noise`），不要抄常量（抄常量迟早分叉）

