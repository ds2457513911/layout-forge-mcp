# Data/specs/ —— spec 库（每颗料解析成果的沉淀处）

> 建立：2026-10-09 ｜ 最后更新：2026-10-09
> 维护者：`services/spec_library.py`（读写都走它，别手改 JSON 后忘记重建索引）

## 这个目录是干什么的

建一颗封装的真正成本在「**读规格书提参数**」——这步只能 AI 读图，无法预写。
以前解析出的 spec 是一次性消耗品（用完即弃，返工时还得手工重拼 JSON）。
本目录把 spec **落盘存档**，于是：

- **同封装不同料号**（LMBR120/140/160 同为 SOD-323HE）→ `load_spec` 零读图复用
- **同族改脚数**（FPC 0.5-10P → 30P）→ 只读差异部分
- **返工重建**（改原点、改外扩）→ 改两个字段即可，不用重新读图
- **交接 / 换机**：这颗料的参数有机器可读存档，对话历史零依赖

## 文件清单

| 文件 | 器件 | 族 | 说明 |
|--|--|--|--|
| `_index.json` | — | — | ★ 反查表：名字 / 族 / 来源 PDF / 指纹 / 存档时间。**由代码维护** |
| `nb_xtal4_3d2x2d5x0d7.json` | 3S48000163 晶振 3.2×2.5 | SMD-Crystal-3225 | ★ 所有几何自检的**基线样例**（改它 = 改 231 项断言的基准） |
| `nb_fpc0510_6p_4d77x3d6.json` | FPC 0.5 间距 6+2 脚 | FPC-0.5 | 非对称引脚 + 双 padstack 的写法先例 |
| `nb_cstj421_aj35_6p.json` | CSTJ-421 音频插座（THT） | Audio-Jack-3.5 | 第一个通孔件；槽孔；三种焊盘 |
| `nb_lmbr140et1g_2p.json` | LMBR140ET1G 二极管 | SOD-323HE | 非对称焊盘；首个全程 MCP 生成的件 |

## 单个文件的结构

```jsonc
{
  "schema": "layout-forge-spec/1",
  "name": "NB_LMBR140ET1G_2P",
  "saved_at": "2026-10-09 15:20:31",
  "family": "SOD-323HE",           // 同族查找用
  "source_pdf": "…/LMBR140ET1G.pdf",
  "notes": "…",                    // ★★ 最值钱：提取方法论 + 踩过的坑
  "fingerprint": {                 // 由 fingerprint() 自动算，别手改
    "mount": "smd", "pin_count": 2,
    "pitch_x": 1.9, "pitch_y": null,
    "body_x": 2.7, "body_y": 1.4,
    "pad_x": 2.0, "pad_y": 1.1
  },
  "spec": { /* 直接能喂 geometry_solver.solve() 的字段 */ }
}
```

## 关键约定与坑

1. **`notes` 字段是这个库最值钱的部分**。
   写"F=1.90 是两 pad 中心距不是总长"这类**踩过的坑**，下次同族就不用再推理一遍。
   只存参数不存坑，等于只省了一半。
2. **改 spec 请改 JSON，别再往 `geometry_solver.py` 里塞常量**。
   2026-10-09 已把 3 份硬编码常量迁到这里；源码只保留 `_load_builtin()` 加载。
   （源码 1612 行 → 1438 行）
3. **手改 JSON 后必须 `rebuild_index()`**，否则指纹和索引不同步，
   `find_spec` 会按旧指纹匹配。
4. **`pitch` 为 0 会在指纹里归一成 `None`**（单排件没有 y 向间距）。
   所以"查询方不传 pitch_y"与"单排件没有 pitch_y"能互相匹配 —— 这是有意的。
5. **文件名统一小写**（`_slug()` 处理），`load_spec` 因此大小写不敏感。
6. 库缺失时 `_load_builtin()` **抛错而不是静默降级** —— 三份基线样例是自检基准，
   静默降级会让 231 项断言在错误基准上"全绿"。

## 配套工具（MCP）

`save_spec` / `load_spec` / `find_spec` / `list_specs` —— 见 SKILL 的
`references/spec-library.md`。
