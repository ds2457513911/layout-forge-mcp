# spec 库 —— 让「读规格书」这件事只做一次

> 建立：2026-10-09 ｜ 真源在 MCP 项目 `layout-forge-mcp\knowledge\specs\`（本文件只讲怎么用）
> 配套 MCP 工具：`save_spec` / `load_spec` / `find_spec` / `list_specs`

## 1. 为什么有这个库

建封装的成本分布（2026-10-09 实测）：

| 环节 | 谁做 | 能不能省 |
|--|--|--|
| 读规格书提参数 | **AI 读图** | ★ 只能省这一次 —— 靠本库 |
| 规则计算（阻焊/外扩/原点/字块） | solver（代码） | 本来就是代码，随 MCP 走 |
| 落 Allegro | builder（代码） | 同上 |

**每次新建封装真正要重做的只有"读图"一步**；spec 库就是把这一步的成果存下来。

## 2. ★ 建封装前：先查库，别急着读图

```
list_specs()                       ← 看库里有什么（便宜，一次调用）
find_spec(mount=?, pin_count=?, pitch_x=?, body_x=?, body_y=?, pad_x=?, pad_y=?)
```

`find_spec` 返回三档：

| 档 | 含义 | 下一步 |
|--|--|--|
| `exact` | **同封装**（八项指纹全等） | ★ `load_spec` → 改 `symbol_base`/`symbol_name` → 直接 `solve`，**一张图都不读** |
| `family` | 同族（mount+pad+body 同，差脚数/pitch） | `load_spec` → 只重算差异部分，读图也只需看差异 |
| 都为空 | 库里没有 | 正常读图流程，读完记得 `save_spec` |

指纹 = `(mount, pin_count, pitch_x, pitch_y, body_x, body_y, pad_x, pad_y)`，
容差 0.05mm。**`pitch` 为 0 会归一成"不适用"**，所以单排件不传 `pitch_y` 也能匹配上。

## 3. ★ 建封装后：顺手存档（这是库能滚起来的唯一方式）

```
save_spec(
    name   = "<符号名>",
    spec_json = "<喂给 solve_footprint_geometry 的那套字段，JSON 字符串>",
    family = "SOD-323HE",          # 同族查找的key
    source_pdf = "<规格书路径>",
    notes  = "★ 提取方法论 + 踩过的坑",
)
```

**`notes` 是最值钱的字段**，写这些：

- 尺寸口径坑（例："F=1.90 是两 pad **中心距**不是总长；B/2+C+D/2=1.90 交叉验证"）
- 与人工件相左的地方及裁决结果（例："人工件 pin1 pad 是竖的，本件按规格书取横向，待用户确认"）
- 返工原因（例："外扩 0.35 会与含阻焊缘浮点贴边 → 取 0.40"）

只存参数不存坑，等于只省了一半。

## 4. 定位不到尺寸页时：contact sheet 兜底

`locate_land_pattern_page` 靠**文本关键词**打分，实测 13 份规格书：强命中 4、弱命中 4、
**no_match 5**（两 FPC / TF 卡座 / HDMI / LMBR）。失配的全是文本层稀薄的国产件规格书。

LMBR140ET1G 那份是**纯位图扫描件**（文本 0 字符 + 矢量 0 条），任何文本方案都无解。

```
locate_land_pattern_page(pdf)      → page: null + hint 提示"文本层为空"
render_pdf_contact_sheet(pdf)      → ★ 一张缩略图拼版（11 页 → 840×945 一张）
   ↓ AI 扫一眼定位到 p3
render_pdf_page(pdf, page=3)       → 读尺寸数字
```

成本对比：翻 6 张整页 + 1 张放大裁剪 → **1 张拼图 + 1 张目标页**。
缩略图只够"定位哪一页"，**读数字必须回到原页**（必要时再裁剪放大）。

## 5. 坑

1. **手改 `knowledge/specs/*.json` 后要 `rebuild_index()`**，否则指纹不同步、`find_spec` 按旧指纹匹配。
2. **`nb_xtal4_3d2x2d5x0d7.json` 是所有几何自检的基线**（239 项断言），改它等于改基准。
3. 库文件缺失时 `geometry_solver._load_builtin()` **抛错不降级** ——
   这是有意的：静默降级会让自检在错误基准上"全绿"。
4. 别把 spec 写进 SKILL 目录 —— SKILL 是知识包，MCP 工具读不到里面的数据。
