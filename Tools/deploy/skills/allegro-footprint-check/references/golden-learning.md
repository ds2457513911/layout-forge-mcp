# 模式 C：金标准批量学习（四层漏斗）

> 从 SKILL.md 剥出（原「模式 C」整节，2026-10-13 扩容为四层）。
> **只在做金标准批量对拍 / 规则库校准时读**；日常单封装检查（模式 A/B）不需要。

## 模式 C：金标准批量学习（四层漏斗，2026-10-13 扩容）

**适用场景**：手里有一个"已知正确"的金标准封装库（如 `Data\PCB_Footprint`，几百个 .dra），
用它全量对拍检查器，达到四个目的：① **挖掘构造规约（第 0 层）** ② 校准规则阈值
③ 沉淀库惯例裁定 ④ 验证两条数据源一致。
**不需要 PDF、不需要 AI 读图**——第 0/1 层数值收割与对拍全部 0 token。

> ★ 为什么扩成四层：三层漏斗的学习对象是"规则检查输出"，规则没覆盖的构造事实
> （drill 实义、环宽、命名、paste）等于不存在——LYDPJ 零 drill 全链路绿灯被
> 工程师否决的根因。第 0 层直接从图纸事实挖约定，check/create 共用一份
> `knowledge/conventions.json`（单一真源，2026-10-13 架构裁定）。

### C0 第 0 层：构造规约挖掘（先于一切对拍）

| 步骤 | 做什么 | 产出 |
|---|---|---|
| 收割 | `Tools/mine_conventions.py`（全量 extracta，cwd=临时目录不污染根；原始 pads.txt 逐件归档 `Tools/log/conventions_raw/`） | `Tools/log/conventions_padstacks.json` 逐件事实 + 原始 dump 归档 |
| 聚合 | `Tools/mine_conventions.py --reaggregate`（调语法/阈值后秒级重算，不必重跑收割） | `knowledge/conventions.json`（带支持度）+ `docs/conventions_report.md` |
| 列守卫 | 聚合时对归档 dump 做 27 列全普查（2026-10-13 起） | "未消费且未豁免"的非空列显名进报告——新字段/schema 漂移必须人工裁定，不可能静默丢失 |
| 裁定 | 支持度不收敛的参数分组裁定（环宽 0.3 标准/0.2 紧凑、NP 豁免、命名变体） | 裁定写进 miner 的 `ADJUDICATED` 常量 + `save_case` 案例 |
| 接线 | 派生侧 `padstack_derive.derive_padstacks` / 判定侧 checker 2.7-2.10（经 `rule_engine` 读规则库）/ 出厂自检 `footprint_selfcheck.verify_built_footprint` | 数字只存在 `knowledge/rules/` 一处，判定与派生同源（drillChar 例外：设计级钻头表字符，report-only 不强制） |

### C1 数值对拍漏斗（严格按层推进，不得跳层）

| 层 | 做什么 | 怎么做 | 产出 |
|---|---|---|---|
| **第 1 层** | 离线全量对拍 | `Tools/benchmark_golden.py`（参数 `--src/--out/--only/--limit`），内部强制 `LAYOUT_CHECK_FORCE_SOURCE=extracta` 单源，不碰 Allegro | `Tools/log/golden_benchmark/`：`benchmark_report.json` + `exceptions/` 逐件例外；退出码 0=全过 / 1=参数错 / 2=有例外 |
| **第 2 层** | AI 例外裁定 | 只读 `exceptions/`，按「规则×原因」**分组**裁定（见 C3） | 机器可修的改代码/阈值；惯例类写 `save_case` 分组案例 |
| **第 3 层** | 实机抽样对拍 | 启动 Allegro（`Tools/start_allegro.cmd`），强制 `LAYOUT_CHECK_FORCE_SOURCE=skillbridge` 抽 2~3 件，与第 1 层 extracta 结果**逐规则 diff** | 两路一致性结论；发现不一致 = 单路伪影，修 reader 后全库重跑 |

**第 3 层不可省**：2026-10-12 实锤——extracta 路径不处理 pin 旋转，侧边 pin bbox 宽高反 90°，
第 1/2 层把 26 件重叠**误裁成"真实设计"**，第 3 层实机对拍才证伪。单靠离线全量对拍发现不了单路伪影。

### C2 数据源坑（253 件实锤，改 reader 前先对照）

- **图纸单位可能是 mil**：extracta / skillbridge 两路都必须归一 mm
  （`_normalize_result_units`），`units_raw` 保留原值。
- **pin bbox 必须 etch 铜箔优先**：阻焊/钢网比铜箔大，拿它们推 bbox 会虚胖，
  3.3 重叠检查成片假 FAIL。
- **extracta 必须处理 pin 旋转**：视图加 `PIN_ROTATION` 字段（实测合法），
  90°/270° 时宽高互换，否则侧边 pin bbox 反 90°。
- **阈值判定加容差**：范围判定加 eps=0.005 消浮点压线；重叠判定加 0.05mm²
  消 bbox 擦边；覆盖检查加 0.1mm 内缩。

### C3 第 2 层裁定纪律

1. **先分"机器可修"与"惯例类"**：
   - 机器可修（改 `rule_checker.py` / `knowledge/rules/` 的参数与容差）：规则逻辑错、
     缺 NA 口径（如 chip/≤3 脚无丝印→NA、npth/pth 机械件无 assembly→NA、
     place_bound 零面积点→NA）、阈值需容差。
   - 惯例类（不改代码，写案例）：库历史命名/命名不编码 etch/外扩离散等，
     用 `save_case` 按「规则×原因」**分组**写 `GOLDEN-CONV-*` 案例
     （`verdict`: false_positive / review_note / keep_fail / mixed），
     **不逐件写**，每组给 affected 数量 + 例举件名 + 证据数值。
2. **裁定必须给证据**：件名 + 实测数值 + 原因，禁止"看起来合理"式裁定。
3. **每改一轮必须重跑全量对拍验证**：警惕 PASS 不升反降——NA 口径放宽松会让
   REVIEW_REQUIRED 激增，逐层排查后再收。
4. **裁定可能被第 3 层证伪**：发现单路伪影后，回头修正已入库的案例
   （改 findings.reason / affected / verdict 并注明修正依据）。
5. **金标准库的三条已知惯例**（新库对拍前先想到）：padstack 命名不编码 etch 尺寸、
   阻焊外扩惯例 0.08mm 但离散（0~0.076 均有）、chip 类不画丝印、
   assembly 只画外框（min_elements=1）。

### C4 收尾

- 重跑 `Tools/selfcheck_geometry.py`（239 项）+ `Tools/selfcheck_skill_syntax.py`（222 项）
  + `Tools/selfcheck_conventions.py`（约定链路：数据管道契约 + 消费覆盖率）回归。
- **验收对拍强制工具化**：声明「与金标准一致」必须附
  `Tools/compare_extracta.py <金标准dump目录> <重建dump目录> --ignore-col 13`
  的完整输出（逐列 diff，非零退出码即未通过）；**禁止肉眼比对后手写"一致"**
  （holeType 差异曾在输出里被肉眼放过）。忽略列须逐列说明理由。
- commit 顺序：代码修复 → 案例库 → （第 3 层）案例修正，分开提交便于回溯。
- **Allegro 用完必须按 `allegro-skillbridge-ops` 规矩关闭**（`Tools/stop_allegro.py`）。
