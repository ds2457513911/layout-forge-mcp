# knowledge/rules/ —— 封装规则集（唯一真源）

> 建立：2026-10-12 ｜ 最后更新：2026-11-23（票 #21：规则集版本 + 覆盖率进报告；票 #19：三形态收成一个参数组）
> 维护者：`services/rule_engine.py`（加载 + 判定入口）

## 这个目录是什么

**封装知识（"这颗封装应该长什么样"）的唯一真源**。里面是四类对象，互相用稳定 id 引用：

| 文件 | 对象 | 一个对象回答什么 | 必填字段 |
|--|--|--|--|
| `forms.json` | form（形态） | 「哪些输入算同一类封装」——由谓词白名单组合（安装方式 / 孔形 / 层存在 / 层集包含 / 名字族 / 焊盘数） | `id`、`name`、`when` |
| `parameters.json` | parameter（参数） | 「这一类封装的某尺寸该是多少」——参数级写 `keys`/`allowed`/`eps`（所有形态共用），形态差异进 `groups[]`（每条 `form`/`value`/`support`/`source`），一个参数只在**一处**定义 | 分组形状：`id`、`keys`、`allowed`、`groups`；单形态也可写扁平形状 `id`、`form`、`value`、`support`、`source`。两种形状二选一，同 `(field, form)` 不许第二份拷贝 |
| `rules.json` | rule（规则） | 「拿哪个参数、比哪一侧、违反了算什么」——`when` 选 form、`use` 选参数、`assert` 选判法 | `id`、`name`、`use`、`assert`、`severity`、`source`、`since` |
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
  库里出现**未登记**的 `*.json` —— 一律抛 `RuleLibraryError`，错误里带 `file` / `path` / `message`。
  **不降级、不回退内置默认值**：一份坏库比没有库更危险。
- 判定/派生的三态：`PASS` / 违反（状态由 severity 映射给出）/ `SKIP`；
  SKIP 必须写明缺什么（`缺 form：` / `缺参数：` / `缺规则：` / `输入 UNKNOWN：`），不许拿 SKIP 冒充通过。

## 与 `conventions.json` 的分工

| | `rules/` 四类库 | `../conventions.json` |
|--|--|--|
| 内容 | 判定规则（形态怎么切、参数多少、违反算什么） | **构造约定**的裁定值（TH 环宽/钢网/命名语法/drill figure，带支持度） |
| 来源 | ADR-0002 + 253 件金标准对拍 + 裁定记录 | 2026-10-13 第 0 层挖掘 + 裁定 |
| 消费 | `rule_engine`（判定）→ `rule_checker`；后续派生侧 | `convention_engine`（builder 派生 + checker 2.7b/2.8/2.9/2.10 + 出厂自检） |

TH 构造类数值仍以 `conventions.json` 为准；四类库里凡引用它的参数把 `SRC-CONV-ADJUDICATED` 记在 `source` 上——**同一份约定不许有两个数字**。

### 阻焊外扩已收成一条参数（票 #19）

`PARAM-MASK-EXPAND` 是阻焊开窗单边外扩的**唯一参数定义**：

- 参数级：`field=mask_expand_side`、`keys=[expand_x, expand_y]`、`allowed=[0.05, 0.13]`、`eps=0.005`——三个形态**共用同一个允许区间**；
- 分组：`FORM-MASK-TH-OBLONG` 0.05（support 22）/ `FORM-MASK-TH-CIRCLE` 0.05（support 9）/ `FORM-MASK-SMD` 0.08（support 195），各带 `source`。

两侧现在读同一份数据：判定侧 `rule_checker` 的 2.2 走 `judge(lib, "RULE-MASK-EXPAND", …)`；派生侧
`convention_engine.th_oblong_params()` 通过 `rule_engine.resolve_parameter(param, form)` 取 TH 长圆孔那组的 `value`。
改 `allowed` → 两侧的允许区间同时变；改某组的 `value` → 该形态的派生尺寸同时变；**改一个值只改一处**。
`conventions.json` 的 `adjudicated.th_oblong_plated.mask_expand_side_mm` 只作为出处记录保留，代码不再从它取值。

> `geometry_solver.py` 里 SMD 派生仍用旧字面 0.05（不是 0.08），是为「改造不改变已建封装形状」；
> 已在迁移映射表里登记为 legacy，接线留给票 #20。

## `checker_rules.json`（已冻结）

2026-11-20 起只读：它是迁移期旧阈值文件。2.2 阻焊外扩已改由四类库判定，
其余规则的数字阈值暂时仍从这里读。**不许再往里加键**；新规则一律进四类库。
文件哈希记录在 `eval/baselines/rule_engine.json` 的 `legacy_rules_sha256`（R6 会比对）。

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
  基线夹具的 2.2 状态逐件相同。代码侧配套步骤（convention_engine 回读 `conventions.json`）不在配方里，
  完整回滚 = 执行配方 + `git revert` 票 #19 的提交，写在该条 `note` 里。

## 加一个对象的手续

1. 先确认 schema 已有该字段（`services/rule_engine.py` 的 `SCHEMA_FILES` + 校验分支）；
   没有就先改校验器与它的测试，**不许让库接受未声明的字段**。
2. 给 `source`：新数字必须有出处，没出处加载就被拒。
3. 改了规则集内容就升 `library.json` 的 `version`；加完跑
   `eval/suites/geometry_regression/check_rule_engine.py`（R6）；
   若改了行为，基线要另起一票重冻 —— `check_derive.py` 那份基线是故意不给 `--update` 的。

## 相关目录

- 加载/判定：`../../services/rule_engine.py`；消费：`../../services/rule_checker.py`
- 构造约定：`../conventions.json`；决策记录：`../../docs/adr/0002-建封装知识收成规则集.md`
- 行为基线：`../../eval/baselines/rule_engine.json`；判据：`../../eval/suites/geometry_regression/check_rule_engine.py`
