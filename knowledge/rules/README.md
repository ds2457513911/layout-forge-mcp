# knowledge/rules/ —— 规则阈值外置真源

> 建立：2026-10-12 ｜ 最后更新：2026-10-13（口径说明：与 conventions.json 的分工）
> 维护者：`services/rule_checker.py::load_external_rules()`

## 这个目录是干什么的

`checker_rules.json` 存放 rule_checker 的**数字阈值与开关**
（naming/pad/origin/place_bound/silkscreen/assembly 各组）。
**改数字立即生效，不用重启 MCP**；判定逻辑（怎么比）仍在
`services/rule_checker.py`，改逻辑必须改代码。

## 与 conventions.json 的分工（2026-10-13 起）

| | `rules/checker_rules.json` | `../conventions.json` |
|--|--|--|
| 内容 | 通用检查阈值（容差、外扩范围、间距下限） | **构造约定**（TH 环宽/钢网/命名语法/drill figure，带支持度） |
| 来源 | 2026-10-12 金标准对拍校准 | 2026-10-13 第 0 层挖掘 + 裁定 |
| 消费 | rule_checker 各数值规则 | convention_engine（builder 派生 + checker 2.7b/2.8/2.9/2.10 + 出厂自检） |

TH 构造类判定（环宽、命名、paste、drill 实义）的阈值走 conventions.json，
不进本文件——**同一份约定不能有两个数字**。

## 关键约定与坑

- 顶层 `_meta` 是注释，不参与合并。
- 写错 JSON **不会带病运行**（自动回退内置 DEFAULT_RULES 并打日志），
  但改动会静默失效——改完跑一次 `Tools/selfcheck_geometry.py`（[11] 段有加载断言）。
- 阻焊外扩口径：本文件的 `soldermask_expand_*` 是**单边值**；与
  conventions.json 的 `mask_expand_side`（TH 件单边 0.05）并存，
  TH 件以 conventions.json 为准。

## 相关目录

- 上游：`../conventions.json`（构造约定）、`../../services/rule_checker.py`（消费方）
