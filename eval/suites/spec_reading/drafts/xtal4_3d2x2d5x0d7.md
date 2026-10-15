# `xtal4_3d2x2d5x0d7` 读图真值草稿 —— 证据报告

> 案例：SMD 晶体 3225（4 脚） ｜ 起草：`ai-read-2026-10-15`
> **状态：DRAFT — 未经人工签字。** 本文件不含 `signed_by` / `signed_at`（签字权在人）。
> 配套机读件：`eval/suites/spec_reading/drafts/xtal4_3d2x2d5x0d7.json`

## 0. 素材与哈希核对

| 项 | 值 |
|--|--|
| PDF | `Data/footprint/3S48000163/3S48000163产品规格书_20220916_S1.pdf` |
| 实测 SHA256（前 12 位） | `2f99b0859056` ✅ **与任务书声明一致** |
| 实测 SHA256（全） | `2F99B085905686B429E7099AC02C25E2566F9E5D96F60D457F32F5687F626538` |
| 金标准 .dra | `Data/footprint/3S48000163/nb_xtal4_3d2x2d5x0d7.dra` |
| 既有 spec（**仅历史对照**） | `knowledge/specs/nb_xtal4_3d2x2d5x0d7.json` |
| 解释器 | `C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe`（3.10.11 / PyMuPDF 1.28.2） |

**形态探测**（`pdf_service.probe_pdf`）：11 页 / 27 位图 / **7110 文本字符** / 0 矢量图元 → `form = printed`。
**关键页定位**（`pdf_service.locate_land_pattern_page`）：**第 5 页**，命中关键词 `LAND PATTERN`，`score = 100`，`method = primary` —— 强命中，无歧义。

> ★ 注意：PDF 页脚印的是 `4/9`（规格书自身编号，不含 A/B 附录），**文件实际 11 页**。
> 本报告与 JSON 的 `page` 字段一律用**人眼 1-based 物理页码 = 5**。

## 1. 读数表（8 个 canonical 字段）

| 字段 | 值 | 页码 | 引文（图上标注串 / 文本层原文） | 怎么量的 |
|--|--|--|--|--|
| `mount` | `smd` | 5 | 第 1 页品名 `SMD X'TAL 3.2*2.5(4PAD)` | 文本层直读 + 第 5 页 CONNECTION DIAGRAM 为顶视/焊盘图形（无插件孔径标注） |
| `pin_count` | `4` | 5 | 文本层 `Pin Function` 表：`#1 Xtal terminal (Input)` / `#2 GND terminal` / `#3 Xtal terminal (Output)` / `#4 GND terminal` | 文本层引脚功能表直读；顶视图标 `#1 #2 #3 #4` 四个焊端 |
| `pitch_x` | `2.2` | 5 | `LAND PATTERN unit:mm` 横向尺寸链 **`1.4` │ `0.8` │ `1.4`** | **间接读数**：三本尺寸链关于纵向虚线中心线对称 → 中心距 = 1.4/2 + 0.8 + 1.4/2 = **2.2** |
| `pitch_y` | `1.7` | 5 | `LAND PATTERN unit:mm` 纵向尺寸链 **`1.2` │ `0.5` │ `1.2`** | **间接读数**：关于横向虚线中心线对称 → 中心距 = 1.2/2 + 0.5 + 1.2/2 = **1.7** |
| `body_x` | `3.2` | 5 | `DIMENSIONS unit:mm` 顶视图横向 **`3.2±0.1`** | 900 dpi 区域渲染肉眼读数（数值在嵌入位图内，文本层无） |
| `body_y` | `2.5` | 5 | `DIMENSIONS unit:mm` 顶视图纵向 **`2.5±0.1`** | 同上（另有 `0.7 max` 为高度，非本字段） |
| `pad_x` | `1.4` | 5 | `LAND PATTERN unit:mm` 左焊盘 **`1.4`**、右焊盘 **`1.4`** | 推荐焊盘图形边长，900 dpi 区域渲染读数 |
| `pad_y` | `1.2` | 5 | `LAND PATTERN unit:mm` 上排 **`1.2`**、下排 **`1.2`** | 同上 |

`unreadable`：**空**（8/8 全部读出）。

### 证据图

- 整页：`eval/runs/spec_reading/xtal4_3d2x2d5x0d7/3S48000163产品规格书_20220916_S1/pymupdf_pages/page_5.png`
- **land pattern 区域 900 dpi（主力证据）**：`.../regions/p5_245-275-400-465@900.png`
- 本体 + land pattern 联合区域 900 dpi：`.../regions/p5_95-270-420-420@900.png`

## 2. ★ 读法陷阱：数值不在文本层

`probe_pdf` 报 `printed`，`read_hint` 说「可提文本层表格」，**但本例不成立**：
第 5 页文本层只有标题与引脚功能表

```
DIMENSIONS  unit:mm
LAND PATTERN  unit:mm
CONNECTION DIAGRAM (TOP VIEW)
```

**所有尺寸数字（3.2±0.1 / 2.5±0.1 / 0.7 max / 1.4 / 0.8 / 1.2 / 0.5 / 0.9 / 0.1 / 1.2）都在嵌入位图里**，
`page.get_text()` 一个都拿不到。矢量图元数 = 0，`measure_region` 对焊盘矩形也无效（区域 68 个图元里没有任何
焊盘方形，只有尺寸线/箭头/文字轮廓）。→ **本例只能走 900 dpi 区域渲染 + 肉眼读数**，再靠 .dra 矢量几何对拍。

（`measure_region` 探针结果：区域内只有 0.14 pt 粗的尺寸线、箭头三角形、文字轮廓；把「已知标注 ↔ 实测 pt 长度」
做了标定尝试，但标注值与线长的对应关系不足以独立定标，故**未采信矢量量距结果作为读数**，仅作辅助。）

## 3. 交叉验证（不是读数来源，是自洽性检查）

已知标注推出的跨度自洽：

| 方向 | 焊盘跨度 = 尺寸链之和 | 本体 | 单边外伸 | 结论 |
|--|--|--|--|--|
| x | 1.4 + 0.8 + 1.4 = **3.6** | 3.2 | (3.6−3.2)/2 = **0.2** | 自洽 |
| y | 1.2 + 0.5 + 1.2 = **2.9** | 2.5 | (2.9−2.5)/2 = **0.2** | 自洽 |

两方向外伸完全相等（0.2），说明 1.4/0.8/1.4 与 1.2/0.5/1.2 读数是同一个设计意图，**交叉印证无笔误**。

## 4. `.dra` 交叉核对（独立的人工口径）

命令：

```python
from services import extracta_reader as er
d = er.read_via_extracta(r"...\Data\footprint\3S48000163\nb_xtal4_3d2x2d5x0d7.dra")
# → {"pins": 4, "texts": 5, "ref_des": 1, "units": "mm", "_gaps": [], ...}
```

### 两边各是什么

**PDF 侧** = 规格书 `LAND PATTERN unit:mm` 的**推荐焊盘图形** + `DIMENSIONS` 的**本体外形**（供应商推荐值）。

**.dra 侧** = 人做的封装符号 `nb_xtal4_3d2x2d5x0d7`，`extracta` 四视图解析结果：

| .dra 证据 | 内容 |
|--|--|
| `pins = 4`，padstack 全为 `R1D4X1D2` | 4 脚；padstack 名即 `R(ect) 1.4 x 1.2` |
| 引脚 `xy` | `#4 (-1.1, 0.85)` / `#3 (1.1, 0.85)` / `#2 (1.1, -0.85)` / `#1 (-1.1, -0.85)` |
| `pins[].pads[ETCH/TOP].size` | `[1.4, 1.2]`（四个脚全同），bbox 如 `[-1.8, 0.25] → [-0.4, 1.45]` |
| `pins[].pads[PIN/SOLDERMASK_TOP].size` | `[1.5, 1.3]`（阻焊开窗，单边外扩 0.05 —— **口径不同，勿混用**） |
| `PACKAGE GEOMETRY/ASSEMBLY_TOP` 本体外框 | bbox `[-1.6, -1.25] → [1.6, 1.25]`，`size = [3.2, 2.5]`，`center = [0, 0]` |
| `PACKAGE GEOMETRY/PLACE_BOUND_TOP` | `3.9 x 3.2` |
| 各引脚 `pads[]` 只出现在 `ETCH/TOP`、`PIN/SOLDERMASK_TOP`、`PIN/PASTEMASK_TOP` | **无钻孔/无 THT 层 → smd** |

### 比对结果

| 字段 | PDF 读数 | .dra 推出值 | .dra 证据 | 判定 |
|--|--|--|--|--|
| `mount` | `smd` | `smd` | 全部 pad 在 `ETCH/TOP`，无钻孔层 | ✅ agree |
| `pin_count` | `4` | `4` | `pins = 4` | ✅ agree |
| `pitch_x` | `2.2` | `2.2` | \|Δx\| = \|1.1 − (−1.1)\| = 2.2 | ✅ agree（误差 0） |
| `pitch_y` | `1.7` | `1.7` | \|Δy\| = \|0.85 − (−0.85)\| = 1.7 | ✅ agree（误差 0） |
| `body_x` | `3.2` | `3.2` | ASSEMBLY_TOP 外框 `size[0] = 3.2` | ✅ agree |
| `body_y` | `2.5` | `2.5` | ASSEMBLY_TOP 外框 `size[1] = 2.5` | ✅ agree |
| `pad_x` | `1.4` | `1.4` | `ETCH/TOP` pad `size[0] = 1.4` | ✅ agree |
| `pad_y` | `1.2` | `1.2` | `ETCH/TOP` pad `size[1] = 1.2` | ✅ agree |

**agree：8/8（全部）　disagree：0**　→ 无冲突，无需仲裁。

特别值得记的一点：`pitch_x` / `pitch_y` 在 PDF 侧是**间接推出**的（规格书没直接标中心距），
而 .dra 侧是**直接量引脚坐标**得到的，两条完全独立的路径给出**逐位相同**的 2.2 / 1.7 ——
这是本例最强的证据，把「间接读数」的残余风险压到接近零。

## 5. 与历史候选的对照（**仅对照，不是读数来源**）

`eval/suites/spec_reading/candidates/xtal4_3d2x2d5x0d7.json`（源自 `knowledge/specs/nb_xtal4_3d2x2d5x0d7.json @2026-10-09`）：

| 字段 | 本次 AI 读数 | 历史候选 | 差异 |
|--|--|--|--|
| mount | smd | smd | — |
| pin_count | 4 | 4 | — |
| pitch_x | 2.2 | 2.2 | — |
| pitch_y | 1.7 | 1.7 | — |
| body_x | 3.2 | 3.2 | — |
| body_y | 2.5 | 2.5 | — |
| pad_x | 1.4 | 1.4 | — |
| pad_y | 1.2 | 1.2 | — |

8/8 一致。既有 spec 的 `notes` 里另记有 `assembly 3.2x2.5`、`Place_Bound 3.9x3.2`，
与本报告从 .dra 读到的 ASSEMBLY_TOP `3.2 x 2.5`、PLACE_BOUND_TOP `3.9 x 3.2` 吻合。

> ⚠️ 纪律声明：`knowledge/specs/nb_xtal4_3d2x2d5x0d7.json` **未被用作任何字段的读数来源**。
> 本次读数全部来自 PDF 第 5 页图像 + 独立 .dra 解析。候选文件只读，未做任何修改。

## 6. 口径备注（签字人请重点确认）

1. **`body_x` / `body_y` 口径 = 顶视图本体最大外形公称值**（3.2 × 2.5）。
   第 5 页另有一张俯视电极图（阴影焊端）标 `0.9`（焊端内间距）、`0.1` / `1.2`（焊端宽与偏心），
   那是**器件电极尺寸**，**不作为本体外形口径**。（自洽验算：1.2 + 0.9 = 2.1，落在 2.5 本体之内。）
2. **`pad_x` / `pad_y` 口径 = 规格书推荐 land pattern 的焊盘图形边长**（1.4 × 1.2）。
   该 .dra 的 `SOLDERMASK_TOP` 开窗是 1.5 × 1.3（单边外扩 0.05），**与 pad_x/pad_y 不是同一口径**，
   对拍时已剔除，避免把阻焊开窗误当焊盘。
3. **`pitch_x` / `pitch_y` 取相邻信号焊端中心距**（4 脚两两相邻，即 2.2 与 1.7）。
   该封装无外壳脚、无定位柱。
4. `pin_count` 计 4：`#1` Xtal In / `#2` GND / `#3` Xtal Out / `#4` GND 全部是**信号/接地焊端**，无机械脚。
5. 图上所有尺寸都是**无公差单值标注**（除本体 `±0.1`），取标注值本身，不按公差中值放大。

## 7. 不确定项与置信度

**不确定项：无。** 8 个字段全部有清晰印刷标注，且 8/8 被独立人工 .dra 证实，零分歧、零容差消耗。

残余风险（低）：

- 数值靠肉眼读 900 dpi 位图。但 8 个数全部是「一位整数或带一个 .x 的短串」（3.2 / 2.5 / 1.4 / 1.2 / 0.8 / 0.5），
  在 900 dpi（区域 1938×2376 px）下字形清晰无粘连，且与 .dra 逐位一致 —— 误读概率可忽略。
- `0.7 max` 是**高度**，不在本 8 字段内，未采信为任何字段值。

**confidence：high。**

## 8. 复现步骤

```powershell
$py = "C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe"
# 1. probe + 定位
#    ps.probe_pdf(pdf)              → form=printed, pages=11, text_chars=7110
#    ps.locate_land_pattern_page(pdf) → page=5, matched=['LAND PATTERN'], score=100
# 2. 区域高 dpi 渲染（数值在位图内，必须走这条路）
#    ps.render_region(pdf, 5, (245, 275, 400, 465), out, dpi=900)   # LAND PATTERN
#    ps.render_region(pdf, 5, ( 95, 270, 420, 420), out, dpi=900)   # 本体 + LAND PATTERN
# 3. read_image 逐张看
# 4. .dra 对拍
#    er.read_via_extracta(r"...\nb_xtal4_3d2x2d5x0d7.dra")
```

★ 第 4 步的驱动器**必须是上面的 unconfined 解释器**；工作区内解释器受沙箱限制会让 `PAD_DEF` 崩成
`rc=0xC0000409`，`pads` / `layers` 缺失（该坑已在 `eval/README.md` 记录）。

---
*本报告由 AI 起草，供人工签字复核。签字前不得作为真值使用。*
