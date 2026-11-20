# 构造约定的演进流程（knowledge/conventions.json）

> 建立：2026-11-20 ｜ 触发时机：要改约定值、增减约定键、或怀疑约定库与代码不同步

一句话：约定库只按「收割 → 聚合 → 裁定 → 接线 → 三层对拍」升级；工具在干活时**不会**自己改它。

## 这份文档管什么

- 管**规则层** `knowledge/conventions.json` 的升级：从图纸事实到带支持度的构造约定。
- 不管**参数层** `knowledge/specs/`（建完封装随手存档）、**经验层** `knowledge/cases/`（人工裁定沉淀）、**阈值层** `knowledge/rules/checker_rules.json`（手改即生效）。
- 三处真源分工：机器真源 `knowledge/conventions.json`；人读报告 `docs/conventions_report.md`（脚本生成，别手改）；逐件事实 `Tools/log/conventions_padstacks.json`（`--reaggregate` 可秒级重算）。

## 为什么不能「边用边学」

1. **约定是建/检双向真源**：`apply_conventions`（正变换）与 checker 2.8/2.9/2.10（逆变换）读同一份，改错两侧一起错。
2. **单次运行只有一件料**（n=1）：现有约定靠 16~26 件支持度才成立，一件不能改约定。
3. **自证循环**：把「检查通过」当成「约定正确」，就会把错误封装学成规则。
4. **正在查的那件本身可能画错**：在线学等于让错误样本投票。

结论：运行中只允许沉淀案例（`save_case`，只预警），不允许改约定。

## 五步流程

### 1 收割（需要 Cadence 的 extracta）

```
.venv\Scripts\python.exe Tools\mine_conventions.py [语料目录]
```

- 默认语料 `Layout\Data\PCB_Footprint`（253 件金标准）。
- 逐件跑 extracta（cwd=专用临时目录，不污染项目根），原始 pads.txt 归档到 `Tools/log/conventions_raw/`；逐 padstack 事实写 `Tools/log/conventions_padstacks.json`。
- 解析只走 `services/extracta_reader._parse_pads_file`，不要在脚本里另写一份（2026-10-13 drillChar 断供的根因就是内联解析与 reader 漂移）。

### 2 聚合（离线，秒级）

```
.venv\Scripts\python.exe Tools\mine_conventions.py --reaggregate
```

不重跑 extracta，用已落盘事实重算，重写 `conventions.json` 与 `docs/conventions_report.md`，并做列守卫普查。先看报告里的支持度与例外，再看 json 的 `conventions` 块。

### 3 裁定（唯一需要人拍板的一步）

- **改哪里**：`Tools/mine_conventions.py:71` 的 `ADJUDICATED` 常量，不是 json。
- **为什么**：json 的 `adjudicated` 块由该常量聚合生成；手改 json 会在第 2 步被覆盖。
- **每条裁定要带**：值、支持度、例外上下文、`rule_level`（例：命名语法因 3 件遗留变体只给 WARN）。
- **实例**：环宽 standard 0.3（16 件）+ compact_allowed 0.2（6 件，高密度 type_c24 空间不足时收窄）。
- 改完必须回第 2 步重新聚合。

### 4 接线

- 新键只有数字还不够，必须有人读它。消费点三处：`services/convention_engine.py`（builder 正变换、出厂自检 `verify_built_footprint`）、`services/rule_checker.py`（2.8/2.9/2.10 逆变换）。
- 在 `Tools/selfcheck_conventions.py` 的 `WIRING` 里登记「键 → 源码消费点正则」；暂时接不了的进 `KNOWN_UNWIRED` 显式欠账。
- 不登记就是假学习：存了、没人用。

### 5 三层对拍

1. **逐件（金标准语料）**：`Tools/benchmark_golden.py` 全量，带 `--force-verify` 强制实跑 extracta。假绿 = 接受的是检查器的猜测而不是磁盘证据。
2. **例外/裁定（ADR 0009）**：每处语料偏离逐件说清，记入 ADR 例外表。
3. **实机抽样（不可省，需 Allegro + SkillBridge）**：从新语料抽 2–3 件，逐规则 diff 检查器输出 vs 图纸事实。2026-10 第 3 层曾证伪 26 件误裁——只跑前两层就交付，等于跳过唯一接触真实封装的环节。

验收判据：第 1 层 0 ERROR、0 假绿；第 3 层差异件件有解释。

## 生效与验收

- **生效**：json 改完即生效（`load_conventions` 按 mtime/size 失效重建缓存），不用重启 MCP；改判定口径才要改代码。
- **必跑**：`Tools/selfcheck_conventions.py`（A 解析契约 / B 消费接线 / C 裁定常量↔库一致）、`Tools/selfcheck_geometry.py`（239 项几何基线）、`eval/suites/architecture/check_architecture.py`（ERROR 0）。
- **C 段保证**：`ADJUDICATED` 与 `conventions.json` 的 `adjudicated` 键路径、值逐项一致；手改 json、或只改常量不重新聚合，都会红。
- **提交顺序**：消费点与 `WIRING`（代码）→ `ADJUDICATED`（常量）→ 重新聚合的 json 与报告，放同一个提交。

## 常见错误

| 现象 | 原因 | 处置 |
|--|--|--|
| C 段红：值/键不一致 | 手改了 conventions.json，或改了常量没聚合 | 改回常量，重新聚合 |
| B1 红：新键未登记 | 只聚合、没接线 | 补消费点并登记 WIRING，或显式进 KNOWN_UNWIRED |
| B2 红：清单正则没命中 | 消费点代码重构后正则漂移 | 更新正则，别删断言 |
| 检查端仍用旧值 | 缓存未失效 | 确认文件 mtime 变了；看 `load_conventions` 日志 |
| 只拿一件新料就改约定 | 样本不足 | 回到语料支持度，确认同族多次重现 |
| 用存量 spec 当真值 | 自证循环 | 读数真值必须人工签字（`eval/suites/spec_reading/README.md`） |

## 变更记录

- 2026-11-20 建立；同时给 `Tools/selfcheck_conventions.py` 加 [C] 段（裁定常量 ↔ 约定库一致性）。
