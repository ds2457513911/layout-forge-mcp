# services/extracta_views/ —— extracta 视图定义

> 最后更新：2026-09-30

## 这个目录是干什么的

`extracta.exe`（Cadence 自带、**离线**读 `.dra` 的导出工具）需要一个
**视图定义文件**，告诉它「导出哪些对象的哪些字段、按什么条件过滤」。

这里就放这个定义文件。**一个视图 → 一个输出文件**，命令行按顺序一一对应：

```
extracta <dra> footprint_views.txt pins.txt pads.txt geom.txt refdes.txt
               └── 本目录的文件        └──── 4 个视图按出现顺序对应 4 个输出 ────┘
```

## 文件清单

| 文件 | 功能 | 影响 |
|--|--|--|
| `footprint_views.txt` | 4 路视图定义：① `COMPOSITE_PAD`（引脚坐标）② `PAD_DEF`（焊盘逐层，**不能写字段声明**，它会自动输出固定列）③ `FULL_GEOMETRY` + `CLASS = "PACKAGE GEOMETRY"`（几何）④ `FULL_GEOMETRY` + `CLASS = "REF DES"`（位号） | 被 `services/extracta_reader.py::read_via_extracta()` 读取；**改这里等于改读取行为** |

## 关键约定与坑

1. ★ **`CLASS = "..."` 是过滤器，不是"顺带多读一层"。**
   视图 ③ 只取 `PACKAGE GEOMETRY`，位号（`CLASS = REF DES`）**读不到**，
   而且**不报错** —— 这叫**静默缺口**。
   表现：`texts` 里只有 `PIN_NUMBER`、没有 `#REF`。
   → 所以位号必须**单独开一路**（视图 ④），几何视图保持原样不动。

2. **一视图一文件，支持多路输出**（实测 4 路 OK）。
   少给一个输出路径 → 少一路数据，且不报错。

3. **`PAD_DEF` 视图不接受字段声明** —— 只写视图名 + `END`。

4. **无位号的图纸，视图 ④ 会输出 0 行 `S!`** —— 这是**正常**的，不是失败。
   `extracta_reader` 会把这种情况记进 `_gaps` 上报，但**不触发回退 SkillBridge**
   （回退也读不到，白唤起一次 Allegro）。

5. `extracta` 的**退出码恒为 `0xC0000409`（崩溃）**，但输出文件是完整的 ——
   **判成败看输出文件，不看退出码**。

## 验证方式

```bash
# 离线（不需要 Allegro），会打印每张图的文字与位号
.venv\Scripts\python.exe Tools\dump_golden_geom.py <某张.dra>
```

自检守门：`Tools/selfcheck_geometry.py` 的 `[7]` 段断言
`FULL_GEOMETRY` 视图数 == 2、且含 `CLASS = "REF DES"`。

## 相关目录

- 读取实现：`../extracta_reader.py`
- 分发与缺口上报：`../footprint_extractor.py`
