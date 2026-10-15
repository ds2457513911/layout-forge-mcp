# knowledge/cases/ —— 案例库（人工裁定与教训的沉淀处）

> 建立：2026-10-12 ｜ 最后更新：2026-10-15（补 `rule_id` 与 `orchestration/playbook.json` 的互指分工 + `6.5` 陈旧规则的点名；此前 2026-10-13（+CONV-2.7 钻孔实义、CONV-MINE-1 构造约定）
> 维护者：`services/case_library.py`（读写都走它；手改 JSON 后需同步 `_index.json`）

## 这个目录是干什么的

每次检测的**人工裁定**按「指纹 + findings + lessons」落盘。检查前
`find_case` 按几何指纹召回同封装/同族案例做预警——**裁定一次、全族受益**。
与 `../conventions.json`（构造约定，机器自动消费）的区别：这里是
**需要 AI/人理解的教训**，检查时作为上下文注入，不直接参与规则判定。

## 文件清单

| 文件 | 内容 |
|--|--|
| `_index.json` | 案例索引（save 时自动更新；手改 JSON 需手工同步） |
| `nb_wson8_8p___pitch_.json` | WSON8 散热盘 pitch 裁定（同事 A 日志种子案例） |
| `nb_rj45_15d95x9d8x3d6_1d02__warn_.json` | RJ45 WARN 裁定（同事 B 日志种子案例） |
| `golden-conv-1_1-name-prefix.json` | 金标准 1.1 前缀小写（223 件，false_positive） |
| `golden-conv-2_1-padstack-etch.json` | 金标准 2.1 命名不编码 etch 尺寸（72 件） |
| `golden-conv-2_2-mask-expand.json` | 金标准 2.2 阻焊外扩离散（17 件） |
| `golden-conv-3_3-pad-overlap.json` | 金标准 3.3 旋转伪影（extracta 修正后重裁） |
| `golden-conv-5_2-expand-discrete.json` | 金标准 5.2 外扩离散（70 件 review_note） |
| `golden-conv-6_6-silk-in-pad.json` | 金标准 6.6 真缺陷保留（3 件） |
| `golden-conv-2_7-th-drill-integrity.json` | ★ 2026-10-13：TH 钻孔实义规则缺口（LYDPJ 二次事故）+ OBLONG_Y 口径 |
| `conv-mine-1-th-construction.json` | ★ 2026-10-13：第 0 层构造约定裁定（环宽/命名/paste/NP 豁免） |

## 关键约定与坑

- 案例文件名 = `<符号名slug>__<标签slug>.json`（slug 规则与 Allegro 落盘一致），正文 name/tag 保留原文。
- `verdict` 取值：`false_positive` / `review_note` / `keep_fail` / `mixed` / `convention` / `real_defect_rule_gap`。
- **裁定可能被后续证伪**（3.3 曾因 extracta 旋转伪影误裁"真实设计"）——修正时改
  findings.reason/verdict 并注明依据，不删案例。
- 分组裁定写一条带 affected 数量的案例，**不逐件写**。
- ★ **`rule_id` 与 `orchestration/playbook.json` 互指，不复制内容**：本目录存「某次裁定的证据与教训」，
  playbook 存「这条规则怎么检测、违规后果属于哪类」。playbook 的 `cases_index` 会把这里的案例名映射到
  rule_id，`stale_rule_ids` 会点名「案例还在引用、代码里已经没有」的规则 —— 当前 1 条：**`6.5`**
  （`services/rule_checker.py:1329` 注明 2026-10-12 已删除；本目录
  `nb_rj45_15d95x9d8x3d6_1d02__warn_.json` 的 finding 与 `services/report_renderer.py:214` 的死分支
   还留着它）。处置方向是改案例或迁移 finding，**不要**为了「有实现」把规则恢复回来。

## 相关目录

- 上游事实：`../conventions.json`（构造约定真源）、`../../Tools/log/golden_benchmark/`（对拍例外）
- 下游索引：`../../orchestration/playbook.json`（rule_id → 检测方式 / 后果类别 / 证据字段；`cases_index` 指回本目录）
- 代码：`../../services/case_library.py`、MCP 工具 `save_case` / `find_case` / `list_cases`
