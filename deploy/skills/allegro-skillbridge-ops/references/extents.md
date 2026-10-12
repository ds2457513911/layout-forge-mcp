# extents —— 图元被「静默丢弃」：extents 是硬边界

> 收录原 SKILL.md **第十五节**。
> 症状极具欺骗性：**不报错、不弹框，图元凭空消失**，返回值只是 `nil`。
> 图元（矩形与线段都一样）一落到 `drawing extents` 之外就会被丢弃。
> 排查顺序见文末清单。
>
> grep 关键词：`extents`、`静默丢弃`、`axlDBCreateRectangle`、`nil`、`bBox`、`页框`、`auto_extent`

## 十五、★★★ 图元被「静默丢弃」：extents 是硬边界（2026-10-08 定案）

### 15.1 症状（极具欺骗性，务必记住这个形状）

```
一轮建封装 87 步，其中 **86 步全成功**，只有 1 步失败：
  [FAIL] place_bound[1]/create_rect: 图元创建返回 nil（Allegro 不报错）
         expr = axlDBCreateRectangle(list(list(-5.545 -13.8) list(5.245 1.6))
                                     t "PACKAGE GEOMETRY/PLACE_BOUND_TOP")
```
- **同一条表达式事后单独再发一次，成功了** —— 因为那时 extents 已经被别的环节放大了
- 没有错误码、没有弹框、`allegro.jrl` 里干干净净
- 编译还能过（少一个 Place_Bound 而已），所以**极易被当成"偶发"放过去**

> ★ 结论先行：**`axlDBCreateRectangle` / `axlDBCreateLine` 等图元创建在
>   坐标落在 drawing extents 之外时，返回 `nil` 且什么都不说。**

### 15.2 铁证：界内/界外对照实验（在线实测）

同一坐标，只改距离：

| 写法 | 结果 |
|--|--|
| `axlDBCreateRectangle(list(list(20 20) list(22 22)) t "PACKAGE GEOMETRY/PLACE_BOUND_TOP")` | ✅ `[<remote object>, None]` |
| `axlDBCreateRectangle(list(list(40 40) list(42 42)) t "…")` | ❌ **`None`** |
| `axlDBCreateLine(list(list(20 20) list(22 22)) 0.1 "PACKAGE GEOMETRY/SILKSCREEN_TOP")` | ✅ `[[<remote object>], None]` |
| `axlDBCreateLine(list(list(40 40) list(42 42)) 0.1 "…")` | ❌ **`None`** |
| 先 `axlDBChangeDesignExtents(list(list(-600 -600) list(600 600)))` 再建 500 处的矩形 | ✅ **立刻成功** |

★ **矩形和线段都被约束** —— 不存在"线可以超界"这种豁免。

**官方依据**（`algroskill/14dsnctl.html` → `axlDBChangeDesignExtents`）：
> "This may fail if an object falls outside the new extents…"

（这句说的是"改范围"会失败；图元创建同样受这套边界，只是**不报错**。）

### 15.3 根因不是"范围太小"，而是"范围是按错的依据算的"

CSTJ-421 的 `build_footprint` 旧实现里，`auto_extent` **只取引脚坐标**外扩：

```
引脚 y ∈ [-11.60, -2.75]，margin = max(2.0, 焊盘半尺寸+1.0) = 2.10
  -> extents y ∈ [-13.70, -0.65]
Place_Bound 实际 y ∈ [-13.80, +1.60]
  -> 下边缘差 0.10mm  ← 唯一的失败原因
```

★ **连接器 / 插座类器件**（音频插座、Type-C、排针座）是重灾区：
**焊盘集中在两侧、本体与 Place_Bound 比焊盘分布更大**。
纯 IC 器件（焊盘就是最外圈）不会暴露这个 bug —— 所以它能潜伏很久。

**修法（已落地）**：外扩必须覆盖**全部将要画的几何** ——
引脚 **+ 丝印 + 本体 + Place_Bound**（矩形取对角两点；折线取全部顶点）。
本项目实现：`services/footprint_builder.py::_compute_auto_extents()`（纯函数，可离线自检）。

### 15.4 ★★ 配套陷阱：空图纸的 `->bBox` 是「页框」不是「内容框」

`axlDBGetDesign()->bBox` 在**图纸里一个对象都没有**时，返回的是
**设计 extents（模板自带的页框）**。实测官方 `sym_template.dra` 复制出来的
新 symbol 图纸：

```
axlDBGetDesign()->bBox  ->  [[0, 0], [1117.6, 863.6]]    ; = 44000 × 34000 mil 的页框
```

**危害**：如果"扩范围"逻辑无条件拿它做并集，范围会被撑成
`(-6.55,-13.7)-(1119.6,865.6)` —— ① 日志里荒谬；
② **掩盖"究竟哪个方向需要扩"**（本例正是被掩盖了 0.10mm 的缺口）。

**正确姿势（与 Cadence 官方示例一致）**：**先精确设定，失败才并集**
```skill
; 官方示例（14dsnctl.html）
extents = axlExtentDB('obstacle)
axlDBChangeDesignExtents(extents)          ; 直接精确设定
```
只有 `axlDBChangeDesignExtents` **返回 nil** 时，才说明内容真的落在请求范围外 ——
**那一刻**的 `->bBox` 才是真实的内容框，用它并集重试才有意义。

> ★ 幂等性不受影响：同参数连调两次结果一致。
> ★ 本项目实现：`footprint_builder.change_extents(union_with_current=True)`
>   已改成"先精确、失败才并集"。

### 15.5 排查清单（下次再遇到"图元没画出来"）

1. **别先怀疑层名** —— 实测 `axlLayerGet("PACKAGE GEOMETRY/PLACE_BOUND_TOP")`
   能取到对象，层名没问题。
2. **算一下越界量**：把要画的矩形的 bbox 与 extents 逐边比一次。
   ★ 直接算 `min(所有几何点)` 和 `max(所有几何点)`，别只看引脚。
3. **看它是不是"唯一越界的那一个"** —— 一轮只挂 1 步，几乎就是这个原因。
4. 用 `extracta` 离线读回 `.dra` 数图元（`layers` 里没有的那个层就是没画上）。

**可复用判据**：`axlDBCreateRectangle` 成功返回 `[<remote obj>, None]`
（第二项是 DRC 标记），失败返回 **`nil`**。
★ 注意 `fill=nil`（不填充）那条路径本身就是返回 nil 的，别拿它当失败判据。
