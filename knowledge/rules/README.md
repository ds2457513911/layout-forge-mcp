# knowledge/rules/ —— 封装规则集（唯一真源）

> 建立：2026-10-12 ｜ 最后更新：2026-11-20（票 #17 / ADR-0002：本目录升级为**规则集**，四类对象进 JSON；`checker_rules.json` 冻结只读）
> 维护者：`services/rule_engine.py`（加载 + 判定入口）

## 这个目录是什么

**封装知识（"这颗封装应该长什么样"）的唯一真源**。里面是四类对象，互相用稳定 id 引用：

| 文件 | 对象 | 一个对象回答什么 | 必填字段 |
|--|--|--|--|
| `forms.json` | form（形态） | 「哪些输入算同一类封装」——由谓词白名单组合（安装方式 / 孔形 / 层存在 / 层集包含 / 名字族 / 焊盘数） | `id`、`predicates` |
| `parameters.json` | parameter（参数） | 「这一类封装的某尺寸该是多少」——按 form 分组，一个参数只在**一处**定义 | `id`、`form`、`keys`、`value`、`allowed`、`support`、`source` |
| `rules.json` | rule（规则） | 「拿哪个参数、比哪一侧、违反了算什么」——`when` 选 form、`use` 选参数、`assert` 选判法 | `id`、`name`、`use`、`assert`、`severity`、`source`、`since` |
| `sources.json` | source（出处） | 这个数字/这条规则**凭什么**——裁定记录、金标准对拍、ADR | `id`、`kind`、`ref` |
| `_severity.json` | severity 映射 | 违反之后报多重 —— `severity = f(consequence, evidence)`，映射表单独一份，不散在代码里 | `id`、`level`、`consequence`、`evidence` |

整库有一个内容哈希（`RuleLibrary.fingerprint`）：同一份输入两次加载必须逐字节相同。
finding 里会带 `rule_set_hash`，报告可回溯「这条判定出自哪一版规则集」。

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

TH 构造类数值仍以 `conventions.json` 为准，四类库里的 `PARAM-MASK-EXPAND-TH-*` 引用它作为出处
（`source: SRC-CONV-ADJUDICATED`）——**同一份约定不许有两个数字**。

## `checker_rules.json`（已冻结）

2026-11-20 起只读：它是迁移期旧阈值文件。2.2 阻焊外扩已改由四类库判定，
其余规则的数字阈值暂时仍从这里读。**不许再往里加键**；新规则一律进四类库。
文件哈希记录在 `eval/baselines/rule_engine.json` 的 `legacy_rules_sha256`（R6 会比对）。

## 加一个对象的手续

1. 先确认 schema 已有该字段（`services/rule_engine.py` 的 `SCHEMA_FILES` + 校验分支）；
   没有就先改校验器与它的测试，**不许让库接受未声明的字段**。
2. 给 `source`：新数字必须有出处，没出处加载就被拒。
3. 加完跑 `eval/suites/geometry_regression/check_rule_engine.py`（R6）；
   若改了行为，基线要另起一票重冻 —— `check_derive.py` 那份基线是故意不给 `--update` 的。

## 相关目录

- 加载/判定：`../../services/rule_engine.py`；消费：`../../services/rule_checker.py`
- 构造约定：`../conventions.json`；决策记录：`../../docs/adr/0002-建封装知识收成规则集.md`
- 行为基线：`../../eval/baselines/rule_engine.json`；判据：`../../eval/suites/geometry_regression/check_rule_engine.py`
