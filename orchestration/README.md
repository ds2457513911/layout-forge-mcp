# orchestration/ —— L3 编排与控制层（规则装配 + 前置自检 + 任务状态机）

> 最后更新：2026-10-15（Phase 3：`playbook.json`（规则装配表，25 条 rule_id + 13 条 gate）与 `pipeline.py`（任务状态机）落地；`eval/suites/behavior` 加 B5 守这两份东西。此前 Phase 2-c：`preflight.py` 噪声规则扩到「Allegro 运行痕迹 + 历史检查报告」并公开 `is_noise()` 作为单一真源）

## 这个目录是干什么的

把「散在 SKILL 与 README 里的铁律 / S 系硬规则」变成**可执行、可索引、可自检**的东西，
让 AI 只负责**语义裁定**（这个料号该用哪份规格书、这个例外该不该放行）。

**要解决的现状**：

| 现状问题 | 证据 |
|--|--|
| 规则散在 4 处，改一处不知道别处是否过期 | 根 `README.md`（883 行）/ `allegro-footprint-check/SKILL.md`（铁律 + S1–S4）/ `allegro-skillbridge-ops` 红线清单 / `docs/` |
| 规则违反了也看不出来 | S1「目录里多个 PDF/.dra 必须先确认」——而 `services/allegro_service.py::list_folder_files` 只回**单个** `pdf` 字段，多份 PDF 在返回值里根本看不见 |
| 「有没有自动检测」这一列已经存在，但没人知道在哪 | 239 项几何自检 + 222 项 SKILL 语法自检 + `Tools/deploy/scripts/preflight_check.py` |

## 文件清单

| 文件 | 功能 | 阶段 | 影响 / 依赖 |
|--|--|--|--|
| `__init__.py` | 分层声明：本层只下沉三类规则、只允许 stdlib + `services` | ✅ 已实现 | 被 `check_architecture.py` 的层方向断言覆盖 |
| `preflight.py` | ★ **前置自检（已实现）**：`preflight(target, recursive=False)` / `classify_folder(folder)` / **`is_noise(name)`**（噪声判定唯一入口）；纯 stdlib、**零副作用**（不打开 PDF、不起 Allegro） | ✅ 已实现 | 被 `mcp_server/tools.py` 的 `folder_preflight` 工具调用；噪声文件（`AUTOSAVE.dra`、`~$*`、`*.bak`、`*.log`、`checklist_report_*.json`、`allegro.jrl`、`master.tag`…）先剔除再判歧义；`eval/suites/geometry_regression` 的 G4 与 `eval/suites/behavior` 的 B1/B3 都调它 |
| `playbook.json` | ★ **规则装配表（已实现）**：25 条 `rule_id` + 13 条 `G-*` 门禁，每条都有 检测方式 / 后果类别（`deny`·`silent`·`recoverable`）/ 证据字段；同 id 的多个调用点收在 `variants[]`（带行号），并点名 `stale_rule_ids` | ✅ 已实现 | 与 `knowledge/cases/` 的教训条目用 `rule_id` 互指；**它是索引不是新真源**（id 的产出者仍是 `services/rule_checker.py`）；生成的底稿脚本在缓存区（见「怎么改」） |
| `pipeline.py` | ★ **任务状态机（已实现）**：`preflight → env-check → extract → check → report`，每步带前置/后置断言（=playbook 的 gate id）；`step` 名与 `observability` 的埋点字段**同名** | ✅ 已实现 | 只 import 标准库 + `services`；只**产出**事件字典，落盘由 `mcp_server/middleware.py` 做；`eval/suites/behavior` 的 B5 守它的放行/拦停判定 |
| `README.md` | 本文件 | — | — |

### `preflight.py` 的实际契约（2026-10-14 落地）

```
classify_folder(folder) -> {folder, verdict, action, ok, ambiguous,
                            pdf_count, dra_count, pdf_files, dra_files, noise_files, hint}
verdict ∈ ok | ambiguous_pdf | ambiguous_dra | missing_pdf | missing_dra | empty | not_found | not_a_dir
action  ∈ proceed | confirm | stop          # ok 只表示「预检本身跑成功」，不表示目录合格
preflight(target, recursive=False) -> 单目录结果 + mode:"single"
                                   | mode:"batch" + units[]/unit_count/suspects[]/noisy[]，verdict=batch_ok|batch_suspects
CLI: python -m orchestration.preflight <目录> [--recursive] [--json]
```

★ **噪声剔除是硬需求**，不是美化：金标准库 `Data/footprint` 曾有 5 个子目录躺着 `AUTOSAVE.dra`
（2026-10-14 已隔离清理，见 `../../Data/README.md`），而 `AUTOSAVE` 按名排序**排在 `nb_*` 前面**
—— 任何「取目录里第一个 `.dra`」的逻辑都会静默取到垃圾。★ 清理只是治症状：**只要还有人在库目录里
直接开工，`AUTOSAVE.dra` 就会再长出来**，所以噪声剔除逻辑永久保留。

### 噪声规则的现状（2026-10-15 扩展）

```
NOISE_NAMES    = autosave.dra, autosave.brd, master.tag, allegro.jrl
NOISE_PREFIXES = ~$, autosave, .~, allegro.jrl
NOISE_SUFFIXES = .bak, .tmp, .lck, .jrl, .run
NOISE_GLOBS    = checklist_report_*.json, signoise.log*, dbdoctor.log*, *.log
is_noise(name) -> bool        # ★ 唯一入口；任何地方判「算不算噪声」都必须调它
```

- **两类噪声**：① **可能被误当产物/证据**的（`AUTOSAVE.dra` 会被当成候选 `.dra`；历史
  `checklist_report_*.json` 正是 S2 要防的「拿旧报告当本次结论」）；② **运行痕迹**（`allegro.jrl` /
  `master.tag` / `*.log` / `signoise.run/`）——不会冒充产物，但证明**有人把库目录当工作目录开了**。
- 扩展后 `geometry_regression` 的 **G4 立刻转红**（★ 当日已清理复绿：145 个全部走 `_quarantine_file()` 进 `.trash/`，**名单与判据未放宽**）：三个库共 **145 个**噪声文件（110 `*.log` +
  9 `checklist_report_*.json` + 26 运行痕迹），其中 `Data/footprint/3S48000163/` 一个目录就躺着
  7 份 2026-09-17 的历史检查报告。**这是真发现**：S2 的风险在库里早有物证，只是以前预检不报。
- ★ **单一真源教训**：`check_geometry_regression.py` 曾自己拼 `NOISE_NAMES/PREFIXES/SUFFIXES` 三个常量，
  本次新增 `NOISE_GLOBS` 后它看不见新规则 —— 同一批目录 G4 报 26、preflight 报 145。
  现在统一调 `is_noise()`。**判定逻辑要调同一个函数，不要抄常量。**

### `playbook.json` 的实际契约（2026-10-15 落地）

```
schema = layout-forge-playbook/1
rules[]  : {id, title, consequence ∈ deny|silent|recoverable, detect{kind ∈ rule_id|tool_field|function|suite, ref},
            evidence[], cases[], variants[{line, status, rule, expected?, source?}]}
gates[]  : {id: "G-*", title, consequence, on_fail_action?, detect, evidence, enforced_by, guarded_by[]}
stale_rule_ids[]  # 案例还在引用、代码里已经没有的 rule_id（当前 1 条：6.5）
cases_index       # 案例名 → rule_id（只做互指，不复制案例内容）
```

- **后果类别口径**（写在 `_meta.consequence_taxonomy`，别再另起一套）：`deny` = 违反了就不能再给结论
  （无对象 / 无数据 / 证据源不可信）；`silent` = 结论会错但看不出来（数值 / 命名 / 几何 / 真缺陷被无声放过）；
  `recoverable` = 只是提示或口径问题，可继续但报告需注明。
- ★ **不引用自检项号**：`Tools/selfcheck_geometry.py` / `selfcheck_skill_syntax.py` **没有稳定项号**
  （只有分组横幅 `hr("[1] …")` 与运行时断言名，且只在失败时打印）；机器可读的只有总数
  （`eval/baselines/geometry_regression.json` 的 239 / 222 / 35）。能引用的 id 空间只有
  `services/rule_checker.py` 的 `rule_id`。
- ★ `stale_rule_ids` 是给「6.5 事件」留的座位：`services/rule_checker.py:1329` 注明
  「6.5（旧『Silkscreen 复杂 path 数』）已于 2026-10-12 删除：自造概念 + 金标准 55 件假阳性」，
  但 `services/report_renderer.py:214` 还留着死分支、`knowledge/cases/nb_rj45_15d95x9d8x3d6_1d02__warn_.json`
  的 finding 仍写 `rule_id: "6.5"`。`pipeline.summarize_findings()` 会把这类 finding **单独点名**，
  不许静默计入统计（B5 守这条）。要动它：改案例或迁移 finding，**不要**为了「有实现」把规则恢复回来。
- **怎么改**：`playbook.json` 由缓存区脚本 `build_playbook.py`（ast 抽取 `_item(...)` 调用点，
  含 `for _rid, _title in (…)` 这种动态 id）+ 人工核对后果类别生成。改完 `rule_checker.py` 的规则要重跑它，
  **别手改 `variants[].line`** —— 那等于把行号变成第二份真源。

### `pipeline.py` 的实际契约（2026-10-15 落地）

```
STEPS     = preflight → env-check → extract → check → report
每个 Step = {name, title, pre[], post[], local, needs_channel}   # pre/post = playbook 的 gate id
STEP_NAMES = ("preflight", "env-check", "extract", "check", "report")   # 也是埋点 step 的取值

run(folder, recursive=False, env_probe=None, stop_after=None) -> PipelineResult
    .action ∈ proceed | confirm | stop      .next_step = 该由上层工具执行的那一步
    .steps[].status ∈ done | needs-confirm | failed | pending
    .events[] = {step, ok, action, error_code, degraded[]}      # 交给上层落盘，本层不写遥测
evaluate_gate(gate_id, ctx) / summarize_findings(findings) / validate_playbook()
CLI: python -m orchestration.pipeline <目录> [--recursive] [--until X] [--no-env] [--json]
     python -m orchestration.pipeline --validate-playbook
```

- **失败码**（SKILL 侧按码分支，不按人话分支）：`CHANNEL_NOT_READY` / `AMBIGUOUS_INPUT` /
  `NO_CANDIDATES` / `UNITS_UNKNOWN` / `NO_DATA`。
- ★ **动作由后果类别决定，但歧义是例外**：`deny → stop`、`silent → confirm`（可以继续，报告必须写明）、
  `recoverable → proceed`（只记提示）；而 `G-ambiguous-src` 在 playbook 里显式写了
  `on_fail_action: "confirm"` —— 「拿不准」的正确动作是**问用户**，不是拒绝执行（约定第 2 条）。
- ★ **`confirm` 是终点**：既然说了「先与用户确认」，就不许继续往下取数（B5 守这条）。
- ★ `step` 名要进 `observability/stats.py::step_stats()` 才有统计，而那个函数会**丢弃带 `tool` 的事件**
  （`if not step or ev.get("tool"): continue`）→ 落盘时必须 `telemetry.record(step=<名字>, tool="")`。
- 本层**不 import observability**：`run()` 只产出事件字典，落盘口径由 `mcp_server/middleware.py` 统一。

## 关键约定与坑1. **只下沉三类规则**（2026-10-14 与用户确认的边界）：歧义可见化 / 前置自检内建 / 输出不可伪造（报告带 nonce）。
   ★ **不做「铁律 1–10 全下沉」** —— 语义判断留在 SKILL，硬塞进代码只会变成第二份散文。
2. **不许「直接拒绝执行」**：S1 场景（一个目录里多份 PDF/.dra）在**批量模式 B**（每个子目录各一对 PDF/.dra）下完全合法。
   本层只**上报歧义**（`ambiguous` 字段），由 AI / 用户裁定。
3. **层方向**：`mcp_server → orchestration → services`。本层**禁止 import `mcp_server`**；
   本层**只允许标准库 + `services`**，**也禁止 import `observability`** —— 埋点由 `mcp_server/middleware.py`
   的 `on_call_tool` 中间件**统一**做（每次 `folder_preflight` 调用都会自动落一条遥测），
   本层再自己写日志只会得到第二种口径。
4. **每条规则必须同时有「检测方式」与「后果类别」**，否则不许进 `playbook.json` —— 防止它退化成又一份散文清单。
5. 与 `knowledge/cases/` 的分工：案例库存**某次裁定的证据与教训**；本层存**规则 → 检测 → 后果**的装配关系。两边用 `rule_id` 互指，不复制内容。

## 相关目录

| 目录 | 关系 |
|--|--|
| `../observability/` | `step` 字段同名；本层的每次 preflight 都产生一条遥测 |
| `../eval/suites/behavior/` | 三个真实场景夹具（多份 .dra / 通道未开 / 报告已存在）守本层规则 |
| `../knowledge/cases/` | 裁定与教训（`rule_id` 互指） |
| `../eval/suites/architecture/check_architecture.py` | 断言第 3 条的层方向 |
| `../docs/` | 人的 SOP；本层是「可执行的 SOP」 |
