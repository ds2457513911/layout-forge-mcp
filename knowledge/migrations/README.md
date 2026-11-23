# knowledge/migrations/ —— 规则迁移映射表

> 建立：2026-11-23（票 #19）｜ 最后更新：2026-11-23
> 维护者：`knowledge/migrations/rules_v1.json`（数据）+ `Tools/render_migration_table.py`（渲染）

## 这个目录是干什么的

规则集从「旧阈值文件 + 旧检查器内联规则」迁到 `knowledge/rules/` 时，
**每个旧阈值键、每条旧检查器规则都必须有且只有一个归属**，不许丢也不许一物两主。
这份映射就是这个承诺的台账：新克隆或回滚时，靠它回答「某个旧数字去哪了、为什么」。

| 文件 | 是什么 |
|--|--|
| `rules_v1.json` | 机器可读台账（schema `layout-forge/rules/migration@1`）：`sources` 盘点旧阈值/旧规则，`entries` 逐条写归属（新参数 / 新规则 / 保留 legacy + 原因 + 回滚配方） |

## 人读版与闸门

- 人读版 `docs/迁移映射表-规则集v1.md` **是生成物，不要手改**：改台账后跑
  `python Tools/render_migration_table.py` 重渲染。
- `eval/suites/geometry_regression/check_rule_engine.py` 的 **R6.13** 逐字节比对台账渲染结果与
  docs 里的文档，并核对旧阈值文件的每个叶子键、旧检查器的每条规则都有且只有一个归属；
  **R6.14** 照 `rollback_recipe` 把库展开回旧形态，用同一批夹具重判，结果必须与旧 2.2 状态一致。

## 目录约定

- 只放台账与它的说明，不放脚本（渲染器在 `Tools/`）。
- 台账 schema 变更要同时改 `Tools/render_migration_table.py` 与 R6.13/R6.14 的判据。
