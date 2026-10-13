# Tools/testdata/ —— 自检固定夹具

> 最后更新：2026-10-13

## 这个目录是干什么的

约定链路自检（`Tools/selfcheck_conventions.py`）的离线夹具。内容是
从金标准 `nb_audio5_13d05x6d3x4d2`（LYDPJ-H23CB1Y-053N 官方版）
extracta 输出截取的真实片段，用于断言解析器不丢受管字段
（如 DRILL 行 holeType 列）——解析器改动后跑自检，丢列即爆红。

| 文件 | 用途 |
|--|--|
| `extracta_fixture_pads.txt` | pads.txt 真实片段（含 ~DRILL 行 OVAL SLOT 口径），`selfcheck_conventions.py [A]` 节消费 |

## 关键约定与坑

- 夹具内容是**金标准的真值**，更新解析器视图列位时须同步核对 r[19] 等索引。
- 不要用探针/实验输出覆盖夹具。
