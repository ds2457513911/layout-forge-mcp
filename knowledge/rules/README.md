# knowledge/rules/ —— 封装规则集（唯一真源）

> 建立：2026-10-12 ｜ 最后更新：2026-11-23（票 #22：旧阈值文件收编为 `tolerances.json`、旧判定开关与 `convention_engine` 删除、检查与建封装只读本库；票 #20：派生侧接入——规则里可写 `derive`，引擎正向产出 padstack 的层集/各层尺寸/命名/旋转，逐值带决策记录，无派生规则报 SKIP；求解器不再内联阻焊/钢网/命名的约定值。票 #18：2.8/2.9/2.10 三条判定也迁到本目录；命名走尺寸反推，环宽有 warn 带，规则可声明 report_id / exempt。票 #21：规则集版本 + 覆盖率进报告；票 #19：三形态收成一个参数组）
> 维护者：`services/rule_engine.py`（加载 + 判定入口）

## 这个目录是什么

**封装知识（"这颗封装应该长什么样"）的唯一真源**。里面是四类对象，互相用稳定 id 引用：

| 文件 | 对象 | 一个对象回答什么 | 必填字段 |
|--|--|--|--|
| `forms.json` | form（形态） | 「哪些输入算同一类封装」——由谓词白名单组合（安装方式 / 孔形 / 层存在 / 层集包含 / 名字族 / 焊盘数） | `id`、`name`、`when` |
| `parameters.json` | parameter（参数） | 「这一类封装的某尺寸该是多少」——参数级写 `keys`/`allowed`/`eps`（所有形态共用），形态差异进 `groups[]`（每条 `form`/`value`/`support`/`source`，可选 `derive_value` = 派生口径值，缺省回落 `value`），一个参数只在**一处**定义；参数级 `value` 是挖掘出的语料典型值（展示/支撑口径），`derive_value` 才是派生侧真正乘进去的数（ADR-0002 D4）；数值参数可带 `warn`（警告带），命名参数用 `kind: naming` + `grammar` + `tol` | 分组形状：`id`、`keys`、`allowed`、`groups`；单形态也可写扁平形状 `id`、`form`、`value`、`support`、`source`；命名参数是扁平形状特例（无 value/allowed）。两种形状二选一，同 `(field, form)` 不许第二份拷贝 |
| `rules.json` | rule（规则） | 「拿哪个参数、比哪一侧、违反了算什么」——`when` 选 form、`use` 选参数、`assert` 选判法；反向/正向两侧：`derive` 用参数把读数展开成 padstack 定义（层集/各层尺寸/命名/旋转） | `id`、`name`、`use`、`severity`、`source`、`since`，且 `assert` 与 `derive` **至少一侧**（只有 assert 的规则在派生模式报 SKIP「缺派生规则」）；可带 `report_id`（历史报告编号）与 `exempt`（整类豁免） |
| `sources.json` | source（出处） | 这个数字/这条规则**凭什么**——裁定记录、金标准对拍、ADR | `id`、`kind`、`ref` |
| `library.json` | 规则集元信息 | 「这一整库是哪一版」——语义版本 + 发布日期；改任何对象都应升版本 | `schema`、`version`、`released_at` |
| `_severity.json` | severity 映射 | 违反之后报多重 —— `severity = f(consequence, evidence)`，映射表单独一份，不散在代码里 | `id`、`severity`、`consequence`、`evidence` |

整库有一个内容哈希（`RuleLibrary.fingerprint`），覆盖包括 `library.json` 在内的全部文件：
同一份输入两次加载必须逐字节相同；改**任何**参数、规则或版本号，哈希都会变。
finding 里带 `rule_set_hash` 与 `rule_set_version`，报告首屏同时打印版本与短哈希，
任何一条判定都能回溯到具体版本。

## 版本与覆盖率（票 #21）

- 版本在 `library.json`：`version` 是语义版本，`released_at` 是发布日期。
  **改参数值、加规则、改严重度都要升版本并重跑 R6**（R6.9 守「改任一处哈希必变」）。
- `rule_engine.coverage(lib, subjects)` 是纯函数，输出两件事：
  1. **form × 规则**表：每个形态本次命中多少、有哪些 assert/derive 规则、状态
     （`covered` / `assert_only` / `derive_only` / `uncovered`）、支撑焊盘数；
  2. **两类缺口分开列**：`forms_without_rule`（有形态但没规则，来自 form 表 × 规则表差集，
     静态就能算）与 `inputs_without_form`（输入连形态都匹配不上，只有跑输入才知道，
     带签名、原因、样例 pin）。
  「支撑（焊盘）」来自参数里的 `support`，是挖掘时的**焊盘样本数**（阻焊 22 / 9 / 195），
  与「多少件封装」（金标准 253 件：SMD 217 / TH-CIRCLE 23 / TH-OBLONG 15 / 无形态 19）
  是两个口径，报告里标单位，别混读；两者的对账由 R6.10 守。
- **未覆盖不判 PASS**：有形态无规则 → finding `status=SKIP`、`detail` 写「缺规则」；
  无形态 → `SKIP` 写「缺 form」。SKIP 计入报告，不会静默通过；Markdown 在表下列出
  「未判定（SKIP，不判 PASS）」及原因，读者不必去翻 JSON。
- 报告 JSON（`report_version` 3.1）新增 `rule_set` 与 `coverage` 段；Markdown 首屏
  打印「规则集 v… · hash …」与覆盖率表。缺这两段的旧 `numeric_result` 仍能渲染
  （整块跳过），`observability.stats` 用 `.get()` 容错，历史报告照常解析。

## 加载语义（硬要求，不是建议）

- 入口只有 `services/rule_engine.py::load_library()`。默认读项目根的 `knowledge/rules/`；
  环境变量 `LAYOUT_FORGE_RULES_DIR` 或显式参数可整体替换（影子比对、临时库都靠它）。
- **加载即校验**：未知谓词、未知断言种类、缺必填字段、id 重复、参数引用不存在的 form/source、
  规则引用不存在的参数、severity 引用不存在的映射、`value` 不在 `allowed` 内、`allowed` 不是 `[lo, hi]`、
  `warn` 不是 `[lo, hi]` 或不落在 `allowed` 内、命名参数 `grammar` 不在白名单、`report_id` 不是历史编号、
  `exempt` 字段越界、库里出现**未登记**的 `*.json` —— 一律抛 `RuleLibraryError`，错误里带 `file` / `path` / `message`。
  **不降级、不回退内置默认值**：一份坏库比没有库更危险。
- 判定/派生的三态：`PASS` / 违反（状态由 severity 映射给出）/ `SKIP`；
  SKIP 必须写明缺什么（`缺 form：` / `缺参数：` / `缺规则：` / `输入 UNKNOWN：`），不许拿 SKIP 冒充通过。

## 与 `conventions.json` 的关系（票 #22 收口）

| | `rules/` 四类库 + `tolerances.json` | `../conventions.json` |
|--|--|--|
| 角色 | **运行时唯一真源**：形态怎么切、参数多少、违反算什么、判定容差多少 | 挖掘与裁定的**历史来源**（TH 环宽/钢网/命名语法/drill figure，带支持度） |
| 来源 | ADR-0002 + 253 件金标准对拍 + 裁定记录 | 2026-10-13 第 0 层挖掘 + 裁定 |
| 消费 | 判定（assert）→ `rule_checker`；派生（derive）→ `padstack_derive`；中间态取阻焊/钢网值 → `geometry_solver` | **无运行时消费方**（票 #22 删除 `convention_engine` 后逐键核对源码） |

四类库里凡源自旧裁定的参数把 `SRC-CONV-ADJUDICATED` 记在 `source` 上——**同一份约定只留一个数字**：
数字进了 `rules/`，`conventions.json` 只保留出处与支持度，改数只改 `rules/`。

### 阻焊外扩已收成一条参数（票 #19）

`PARAM-MASK-EXPAND` 是阻焊开窗单边外扩的**唯一参数定义**：

- 参数级：`field=mask_expand_side`、`keys=[expand_x, expand_y]`、`allowed=[0.05, 0.13]`、`eps=0.005`——三个形态**共用同一个允许区间**；
- 分组：`FORM-MASK-TH-OBLONG` 0.05（support 22）/ `FORM-MASK-TH-CIRCLE` 0.05（support 9）/ `FORM-MASK-SMD` 0.08（support 195），各带 `source`；三组各自另有 `derive_value: 0.05`（票 #20）。

两个数不是一回事，别混读（ADR-0002 D4）：

| 字段 | 回答什么 | 谁在消费 |
|--|--|--|
| `value` | 挖掘时语料里**最常见的取值**（展示与 `support` 的口径） | 覆盖率报告、R6.11 |
| `derive_value` | **建库时该乘进去的单值**；缺省回落 `value` | 派生侧（`rule_engine.resolve_derive`）与求解器的中间态（`geometry_solver.mask_expand_for_spec`） |

SMD 的 0.08 是语料典型值、0.05 是建库口径——改造前已建封装按 0.05 建，票 #20 的验收要求「派生输出与改造前逐字段相等」，
所以派生侧取 `derive_value`（0.05），`value` 保持 0.08 不动。**改前先确认改的是哪一个**：改 `value` 只影响展示/兼容视图，改 `derive_value` 才改派生尺寸。

`conventions.json` 的 `adjudicated.th_oblong_plated.mask_expand_side_mm` 只作为出处记录保留；票 #22 起**整个文件无代码读取**。
`geometry_solver.solve_padstack()` 的 `sm` 已改由 `mask_expand_for_spec()` 从本库取（按读数判形态 → `resolve_parameter`），
不再是 `rules["pad"]["soldermask_expand_mm"]` 的旧字面；`rules` 参数保留只为兼容历史调用方。

## 派生方向（票 #20）

规则里的 `derive` 是**正向**：拿读数（事实）+ 参数，展开成一颗 padstack 的定义。
入口是 `rule_engine.resolve_derive(lib, rule, form_id, facts)`，消费方是
`services/padstack_derive.py`（`derive_padstacks(reading)`）。

- **输入是事实，不是 `.dra`**（ADR-0002 D13）：`padstack_derive` 先从读数归一出一份 facts
  （`pad_x/pad_y`、`hole_x/hole_y`、`hole_long/hole_short`、`pad_long/pad_short`、
  `plated`、`offset_x/offset_y`、`paste_bottom`、`shape`、`figure`），再 `match_form` 选形态。
- **`derive` 对象的字段**（加载时校验，见 `_check_derive_object`）：
  `values`（名 → 表达式，可引用 facts / 参数键 / 之前声明的 values）、
  `layers[]`（`layer` / `figure`（字符串或 `{"from_fact": 名字}`）/ `size` 两个表达式 / 可选 `when: {fact, equals}`）、
  `drill`（`size` 两表达式、`drill_type`、`figure`、可选 `plated_from` / `offset`）、
  `name`（`{"style": "th_oblong", "template": "POB{孔}X{孔}HOB{盘}X{盘}"}` 或
  `{"style": "padstack_name", "shape": ..., "dims": [...]}`）、`rotation`、`notes[]`。
- **表达式白名单**：数字、fact/参数名、四则运算与括号；禁函数调用、属性、下标、比较、布尔、条件。
  判断在加载期一次做完，坏库整体拒绝。
- **命名模板在数据，格式化在代码**（ADR-0002 D6）：`services/padstack_naming.py::format_derive_name`
  才把模板渲染成名字；判定侧（2.9）仍用尺寸反推，不解析模板。
- **逐值决策记录**：`resolve_derive` 对每个 `values` / 层尺寸 / 钻孔尺寸产出一条
  `decisions`（`item` / `value` / `expression` / `rule_id` / `source` / `form` / `parameters[{id,key,value,source}]`），
  出问题能追到「哪条规则、哪个表达式、乘了哪个参数」。
- **形态命中但没有派生规则 → SKIP，不许静默产出**（ADR-0002 D2/D3）：当前
  `FORM-MASK-TH-OBLONG`（`RULE-DERIVE-TH-OBLONG`）与 `FORM-MASK-SMD`（`RULE-DERIVE-SMD`）有派生规则；
  `FORM-MASK-TH-CIRCLE`、`FORM-TH-DRILL-OTHER` 只有判定侧，派生时进 `skips` 并写明
  「形态 X 尚无派生规则（缺派生规则）」。连形态都匹配不上的写「缺 form」。
- **层集 / 尺寸 / 命名 / 旋转归 derive**；pin 坐标、阵列、原点、本体、Place_Bound、丝印、位号仍归
  `geometry_solver`（ADR-0002 D5）。旋转由 derive 决定（TH 长圆 90°：本地竖孔 → 板面横槽），
  施加在求解器/执行器；判定读实际值。
- 每次派生结果里的 `decisions` / `skips` 是**纯数据**，可离线对拍（R5 基线与
  `eval/baselines/padstack_derive.json`）。

## 已迁移的判定（票 #18）

| 历史编号 | 规则 / 参数 | 形态 | 判法 |
|--|--|--|--|
| 2.8 环宽 | `RULE-ANNULAR-RING` / `PARAM-ANNULAR-RING` | TH 长圆孔 | `allowed=[0.15,0.4]`，`warn=[0.2,0.3]`：warn 内 PASS，allowed 内 warn 外 WARN，allowed 外 FAIL；NP 前缀按 `exempt` 直接 PASS |
| 2.9 命名 | `RULE-PADSTACK-NAMING` / `PARAM-NAMING-TH-OBLONG` + `PARAM-NAMING-TH-CIRCLE` | TH 长圆/圆孔 | `kind: naming` 按实测孔/盘尺寸反推名字里的数字（tol 0.011），**不解析模板**；NP、盘段省略、`OS` 后缀等旧变体保留 |
| 2.10 钢网 | `RULE-PASTE-TOP-PRESENT` / `RULE-PASTE-TOP-EQ-PAD` / `RULE-PASTE-BOTTOM-ABSENT` | TH 长圆孔 | 顶面钢网必须存在且与焊盘等大（0.01），底面不得有；NP 按 `exempt` 放行 |

`report_id` 把新规则映射回历史报告的 2.8/2.9/2.10，旧报告仍可读；finding 带
`direction: assert`，只有 assert 侧（没有派生规则）的规则才标方向。

**输入 UNKNOWN 一律 SKIP**：judge 缺观测时返回 `SKIP`，检查器把该条记成 `NA`，
不判 PASS。票 #22 起**没有旧判定回退路径**（`LAYOUT_FORGE_LEGACY_JUDGE` 与
`services/legacy_judge.py` 已删除）：规则缺了只能 SKIP，绝不 PASS。

影子闸门有两层，差异必须为 0：
- 判定平移（票 #18）：`eval/baselines/shadow_2_8_10_253.json`，旧侧 `(id,status)` 在
  `eval/baselines/shadow_2_8_10_old_d87e172.json`，重跑用 `make_shadow_2_8_10.py`；
- **全量**（票 #22）：`eval/baselines/shadow_full_253.json` 存整份 `items` 的
  `(id,status)` 序列，旧侧 `shadow_full_253_old_fa92057.json`，重跑用
  `make_shadow_full_253.py --baseline <旧快照>`；`check_rule_shift.py` R7.9 守它。
  253 件里 235 件可比差异 0；18 件 extracta 不可读（`errors_detail` 逐条写原因，
  属既有语料缺陷，不计入 judged）。R7.9 还会用当前代码实跑一遍再比。

## `checker_rules.json`（历史出处，无代码读取）

票 #22 起它是纯历史文件：24 个叶子键里 19 个有消费方的收编进 `tolerances.json`（
`TOL-*`，逐值相同），3 个阻焊键由 `PARAM-MASK-EXPAND` 接管，`pad.min_spacing_mm`
无消费方。**运行时代码（`services/`、`mcp_server/`）不再打开它**（R6.6 用 AST 断言
open/Path/read 调用），也不许再往里加键。文件 sha256 记录在 `eval/baselines/rule_engine.json`
的 `legacy_rules_sha256`（R6 比对当前锚点；票 #22 只改 `_meta` 标注冻结，24 个阈值数值一个未改，其中 4 处文本形态规范化为最小写法 `0.2` / `0.1`，数值相等）。

容差表 `tolerances.json` 的形状：`{id: TOL-<SECTION>-<KEY>, section, key, value, kind, source, since}`，
加载后摊成 `{section: {key: value}}`（与旧文件同形），消费方通过 `rule_checker.load_rules()` 取用。
改阈值 = 改 `tolerances.json` + 跑 R6/R7；**不再有「改数字不改代码」的第二个入口**。唯一留在代码里的判定常量是 5.2 的覆盖容差 `_COVERAGE_TOLERANCE_MM = 0.1`——旧阈值表没有这个键，票 #22 不凭空新增容差条目，故按构造常量保留并登记在迁移台账 `DEC-22-06`。

## 迁移映射表（票 #19）

旧阈值文件与旧代码规则必须能逐条说清「搬没搬、搬到哪、怎么退」：

- 机器真源：`knowledge/migrations/rules_v1.json`（schema `layout-forge/rules/migration@1`）。
  每个条目登记一条旧键或一条旧规则，`target` 指向新参数 / 新规则，或标 `legacy`（必须写 `reason` 与 `rollback`）。
  **不放进 `knowledge/rules/`**——加载器会把那里未登记的 `*.json` 当坏库拒掉。
- 可读版：`docs/迁移映射表-规则集v1.md`，由 `Tools/render_migration_table.py` 从上面的 JSON 渲染。
  R6.13 断言「文档与渲染结果逐字节一致」+「旧阈值文件每个叶子键、旧代码每条规则都有且仅有一个归属」，
  所以改 JSON 必须重渲染，改文档等于没改。
- 回滚配方：JSON 顶层的 `rollback_recipe` 可执行——把 `PARAM-MASK-EXPAND` 反向展开回三个扁平参数并把
  `RULE-MASK-EXPAND.use` 改回旧 id。R6.14 会真跑一遍，确认回滚后三形态视图与合并库逐字段相等、
  基线夹具的 2.2 状态逐件相同。票 #20 的两条 `RULE-DERIVE-*` 也引用这个合并参数，**回滚时必须一并删除**
  （配方里已登记；否则参数展开后规则指向不存在的参数，整库被拒）。派生侧配套步骤
  （`padstack_derive` 读派生值）不在配方里，
  完整回滚 = 执行配方 + `git revert` 票 #19 / #20 的提交，写在该条 `note` 里。

## 加一个对象的手续

1. 先确认 schema 已有该字段（`services/rule_engine.py` 的 `SCHEMA_FILES` + 校验分支）；
   没有就先改校验器与它的测试，**不许让库接受未声明的字段**。
2. 给 `source`：新数字必须有出处，没出处加载就被拒。
3. 改了规则集内容就升 `library.json` 的 `version`；加完跑 `eval/suites/geometry_regression/check_rule_engine.py`（R6）与 `check_rule_shift.py`（R7）；若改了行为，基线要另起一票重冻 —— `check_derive.py` 那份基线是故意不给 `--update` 的。

## 相关目录

- 加载/判定：`../../services/rule_engine.py`；消费：`../../services/rule_checker.py`
- 构造约定：`../conventions.json`；决策记录：`../../docs/adr/0002-建封装知识收成规则集.md`
- 行为基线：`../../eval/baselines/rule_engine.json`；判据：`../../eval/suites/geometry_regression/check_rule_engine.py`
