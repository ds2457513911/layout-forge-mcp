# knowledge/ —— 项目知识库（会越用越厚的部分）

> 最后更新：2026-10-13（新增 conventions.json 构造约定真源，第 0 层落地）

## 这个目录是干什么的

机器读写的**知识真源**，随代码一起入库/搬走。四个子目录/文件各管一层：

- **conventions.json** —— **构造约定真源（2026-10-13 第 0 层）**：由
  `Tools/mine_conventions.py` 对 253 件金标准全量收割 + AI 裁定产生，
  带**支持度计数**可审计。**check 与 create 读同一份**（用户架构裁定）：
  builder `services/convention_engine.py::apply_conventions` 按它派生 TH padstack
  （spec 只声明孔位/pin），checker 规则 2.7b/2.8/2.9/2.10 按它校验，
  出厂自检 `verify_built_footprint` 按它放行。人读版 `docs/conventions_report.md`；
  逐件事实 `Tools/log/conventions_padstacks.json`（reaggregate 可秒级重算）。
- **specs/** —— 参数层：每颗料解析出的 spec（喂 `solve_footprint_geometry` 复用，同封装零读图）。用法见 `specs/README.md` 与 `services/spec_library.py`。
- **cases/** —— 经验层：每次检测的**人工裁定**沉淀（指纹 + 报告摘要 + findings + lessons）。检查前 `find_case` 按几何指纹召回同封装/同族案例做预警——裁定一次、全族受益。代码在 `services/case_library.py`。
- **rules/** —— 阈值层：`rule_checker` 的阈值外置真源（`checker_rules.json`）。**改数字立即生效，不用重启 MCP**；判定逻辑（怎么比）仍在 `services/rule_checker.py`，改逻辑必须改代码。

## 文件清单

| 文件/目录 | 功能 | 影响 / 依赖 |
|--|--|--|
| `conventions.json` | 构造约定（TH 环宽/mask/paste/drill figure/命名语法，含裁定块 `adjudicated`） | `convention_engine.py`（builder 派生 + checker 2.8/2.9/2.10 + 出厂自检）；`Tools/mine_conventions.py` 再生 |
| `specs/*.json` + `_index.json` | spec 库 | `spec_library.py` / MCP `save_spec` 等四工具 |
| `cases/*.json` + `_index.json` | 案例库（含 2026-10-13 CONV-2.7 钻孔实义、CONV-MINE-1 构造约定裁定） | `case_library.py` / MCP `save_case` 等三工具 |
| `rules/checker_rules.json` | 规则阈值（naming/pad/origin/place_bound/silkscreen/assembly） | `rule_checker.py::load_external_rules()`；文件缺失/损坏自动回退内置 DEFAULT_RULES |

## 关键约定与坑

- 指纹口径**只有一套**：`(mount, pin_count, pitch_x, pitch_y, body_x, body_y, pad_x, pad_y)`，两库共用 `spec_library.fingerprint` —— 别在案例库再造一份。
- 手改 `specs/*.json` 或 `cases/*.json` 后指纹索引**不会**自动重建，需调对应库的 `rebuild_index()`（spec 库有；案例库索引随 save 更新，手改 JSON 后需手工同步 `_index.json`）。
- `rules/checker_rules.json` 顶层 `_meta` 是注释，不参与合并；写错 JSON 不会带病运行（自动回退内置默认并打日志），但**改动会静默失效**——改完跑一次 `Tools/selfcheck_geometry.py`（[11] 段有外置加载断言）。
- 案例文件名 = `<符号名slug>__<标签slug>.json`，标签里的中文会转成 `_`（slug 规则与 Allegro 落盘一致），正文里的 name/tag 保留原文。

## 相关目录

- 上游：`Tools/test/`（检测产物）、`Layout/Data/footprint_test/`（同事工作材料）
- 下游：MCP 工具层 `mcp_server/tools.py`（Tool 29~31 案例库）
- 同级：`Tools/`（脚本与自检）、`docs/`（SOP 与决策依据）
