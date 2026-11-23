# knowledge/ —— 项目知识库（会越用越厚的部分）

> 最后更新：2026-10-13（SHAPE 型焊盘建模：PADSHAPENAME 透传 → checker 0.2 显名；PADFLASH 全语料 0 行；builder spec 未知键守卫）

## 这个目录是干什么的

机器读写的**知识真源**，随代码一起入库/搬走。五个子目录/文件各管一层：

- **conventions.json** —— **构造约定的历史来源（2026-10-13 第 0 层）**：由
  `Tools/mine_conventions.py` 对 253 件金标准全量收割 + AI 裁定产生，
  带**支持度计数**可审计。票 #22 起**运行时无消费方**：数字已收进
  `rules/`（判定与派生都读它），本文件只保留出处与支持度（`SRC-CONV-ADJUDICATED`）。
  `services/convention_engine.py` 与 `legacy_judge.py` 已删除。人读版 `docs/conventions_report.md`；
  逐件事实 `Tools/log/conventions_padstacks.json`（reaggregate 可秒级重算；
  原始 dump 归档 `Tools/log/conventions_raw/`）。**升级流程**（收割/聚合/裁定/接线/
  三层对拍、裁定只改常量）见 `docs/conventions-evolution.md`，常量与库的一致性由
  `Tools/selfcheck_conventions.py` 的 [C] 段断言。
  ★ **列守卫**（2026-10-13）：`column_guard` 块记录 pads.txt 27 列普查结果，
  "未消费且未豁免"的非空列必须显名——新字段/schema 漂移不可能静默丢失；
  `th_*.drill_char` 为 report-only（设计级钻头表字符，随图纸变，不强制）。
  ★ **SHAPE 型焊盘**（2026-10-13 建模裁定）：LAYER 行 PADSHAPENAME（r12）
  非 0 = 自绘轮廓焊盘（语料 106 行/38 个 padstack，如 JOPEN25X30_S-L-P），
  reader 透传 `shape_name` → miner facts → checker **0.2 WARN** 显名
  （轮廓几何 extracta 不给顶点，形状类目测须人工复核）；真 flash 引用
  （PADFLASH r11）全语料 **0 行**。
- **specs/** —— 参数层：每颗料解析出的 spec（喂 `solve_footprint_geometry` 复用，同封装零读图）。用法见 `specs/README.md` 与 `services/spec_library.py`。
- **cases/** —— 经验层：每次检测的**人工裁定**沉淀（指纹 + 报告摘要 + findings + lessons）。检查前 `find_case` 按几何指纹召回同封装/同族案例做预警——裁定一次、全族受益。代码在 `services/case_library.py`。
- **rules/** —— **规则集真源**（ADR-0002）：形态 `forms.json` / 参数 `parameters.json` / 规则 `rules.json` / 出处 `sources.json` + 容差 `tolerances.json` + 版本 `library.json`。判定与派生**都只读这一份**；旧 `checker_rules.json` 已冻结为历史出处（无代码打开）。改数字改 `parameters.json` / `tolerances.json`，改判定逻辑改 `rules.json`。
- **tool_groups.json** —— **MCP 工具分组映射（2026-11-19 第 0 层）**：工具名 → 六组（图纸/器件写侧、几何求解与库、spec/案例库、读 PDF/目录、检查、编排/观测）的唯一人工维护处。
  名字与数量不在这里抄——名字来自 `mcp_server/tools.py` 的 `@mcp.tool`（AST 解析），本文件只回答「它属于哪组」。人读目录 `docs/tools-catalog.md` 由它与 AST 生成；
  漏给新工具分组由 `eval/suites/tool_contract` **C7** 报红，目录忘了重生成由 **C8** 报红。

## 文件清单

| 文件/目录 | 功能 | 影响 / 依赖 |
|--|--|--|
| `conventions.json` | 构造约定的历史来源（TH 环宽/mask/paste/drill figure/命名语法，含裁定块 `adjudicated`） | **票 #22 起无运行时消费方**，数字真源在 `rules/`；`Tools/mine_conventions.py` 再生，`selfcheck_conventions.py` [C] 段守常量↔库一致 |
| `specs/*.json` + `_index.json` | spec 库 | `spec_library.py` / MCP `save_spec` 等四工具 |
| `cases/*.json` + `_index.json` | 案例库（含 2026-10-13 CONV-2.7 钻孔实义、CONV-MINE-1 构造约定裁定） | `case_library.py` / MCP `save_case` 等三工具 |
| `rules/tolerances.json` | 判定容差 19 条（naming/pad/origin/place_bound/silkscreen，`TOL-*`） | `rule_checker.py::load_rules()`（每次现读规则库，坏库即 `RuleLibraryError`）；旧 `checker_rules.json` 只作 sha256 锚点，**无代码读取** |
| `tool_groups.json` | MCP 工具 → 六组的映射（**唯一人工维护的工具分类**） | `eval/suites/tool_contract/tool_catalog.py` 读它生成 `docs/tools-catalog.md`；C7 校验无漏分组/无幽灵名字 |

## 关键约定与坑

- 指纹口径**只有一套**：`(mount, pin_count, pitch_x, pitch_y, body_x, body_y, pad_x, pad_y)`，两库共用 `spec_library.fingerprint` —— 别在案例库再造一份。
- 手改 `specs/*.json` 或 `cases/*.json` 后指纹索引**不会**自动重建，需调对应库的 `rebuild_index()`（spec 库有；案例库索引随 save 更新，手改 JSON 后需手工同步 `_index.json`）。
- 规则库写错一个字节就**整库拒绝加载**（`RuleLibraryError` 带 file/path/message），不会带病运行也不会静默回退默认值——改完跑 `Tools/selfcheck_geometry.py`（[11] 段断言）与 R6/R7 套件。
- 案例文件名 = `<符号名slug>__<标签slug>.json`，标签里的中文会转成 `_`（slug 规则与 Allegro 落盘一致），正文里的 name/tag 保留原文。

## 相关目录

- 上游：`Tools/test/`（检测产物）、`Layout/Data/footprint_test/`（同事工作材料）
- 下游：MCP 工具层 `mcp_server/tools.py`（Tool 29~31 案例库）
- 同级：`Tools/`（脚本与自检）、`docs/`（SOP 与决策依据）
