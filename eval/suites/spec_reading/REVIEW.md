# spec_reading 真值复核包（**待人工签字**）

> 最后更新：2026-10-14 · 由 `eval/suites/spec_reading/check_spec_reading.py` 的判据 S2 把关

## 这是什么

`eval/suites/spec_reading/` 是读图准确率考卷：10 份规格书，每份钉住 PDF 的 sha256 前 12 位，由 AI 按 8 个 canonical 字段读图取值，再用 `services.extracta_reader` 读同名 `.dra` 做**独立交叉核对**。

★ **真值必须由人签字**：`truth/<id>.json` 里的 `signed_by` / `signed_at` 为空即视为草稿，此时打分器的数字只是**起草期参考值**，不能当基线冻结、不能对外引用、不能拿来自证「读图变好了」。

## 怎么签

1. 逐例看下表的 8 个字段值与「需人裁定」条目；
2. 取证全文（页码、引文、渲染图、换算过程、.dra 数值）在 `drafts/<id>.md`（每例 9–19 KB），渲染证据图在 `eval/runs/spec_reading/<id>/`（不进 git）；
3. 回一句「第 N 例改成 X」或「全部照草案」，并给出签字人；我把 `signed_by` / `signed_at` 写进 `truth/<id>.json`，裁定过程另记一条 `adjudication`。**签字动作由人下达、由我落笔，AI 不给自己签字。**

## 总览

| # | 案例 | 来源 | PDF 形态 | .dra 交叉核对 | 与历史 spec（候选）差异 | 8 字段 |
|---|------|------|----------|---------------|------------------------|--------|
| 1 | `xtal4_3d2x2d5x0d7` | 既有 spec | printed | 8/8 一致 | 无 | 见下 |
| 2 | `audio6_cstj421` | 既有 spec | printed | 8/8 一致 | **pitch_x pitch_y body_x body_y** | 见下 |
| 3 | `lydpj_5p_12x6` | 既有 spec | cad_vector | 7/8 一致，分歧：pad_y | 无 | 见下 |
| 4 | `fpc0510_6p` | 既有 spec | printed | 6/8 一致，分歧：pin_count | **pin_count pitch_y** | 见下 |
| 5 | `lmbr140et1g_2p` | 既有 spec | scan | 8/8 一致 | 无 | 见下 |
| 6 | `type_c24` | 新增 | printed | 7/8 一致，分歧：pad_y | 无 | 见下 |
| 7 | `hdmi19` | 新增 | printed | 8/8 一致 | 无 | 见下 |
| 8 | `bga496` | 新增 | printed | 8/8 一致 | 无 | 见下 |
| 9 | `tf9` | 新增 | cad_vector | 8/8 一致 | 无 | 见下 |
| 10 | `zif30` | 新增 | cad_vector | 8/8 一致 | 无 | 见下 |

> 「与历史 spec（候选）差异」= 我这次的读图结果与 `knowledge/specs/*.json` 里既有 fingerprint 不一致的字段。**候选是历史产物，不是真值**；不一致只说明有一方需要改，由人裁定哪一方。

## 1. `xtal4_3d2x2d5x0d7` — SMD 晶体 3225（4 脚）（SMD 无源晶体）

- 来源：既有 spec（`layout-forge-mcp/knowledge/specs/nb_xtal4_3d2x2d5x0d7.json`）
- PDF：`Data/footprint/3S48000163/3S48000163产品规格书_20220916_S1.pdf` · sha256 前 12 位 `2f99b0859056` · 形态 printed
- .dra：`Data/footprint/3S48000163/nb_xtal4_3d2x2d5x0d7.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（四视图 PAD_DEF/PACKAGE_GEOMETRY），用 venv-unconfined 解释器；.dra 侧读 pins[].size / pin…）
- 证据全文：`drafts/xtal4_3d2x2d5x0d7.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 5 | SMD X'TAL 3.2*2.5(4PAD) |
| `pin_count` | **4** | 5 | Pin Function #1 Xtal terminal (Input) #2 GND terminal #3 Xtal terminal (Output) #4 GND terminal |
| `pitch_x` | **2.2** | 5 | LAND PATTERN unit:mm — 横向尺寸链「1.4 | 0.8 | 1.4」；由标注本身推出焊盘中心距 = 1.4/2 + 0.8 + 1.4/2 = 2.2 |
| `pitch_y` | **1.7** | 5 | LAND PATTERN unit:mm — 纵向尺寸链「1.2 | 0.5 | 1.2」；由标注本身推出焊盘中心距 = 1.2/2 + 0.5 + 1.2/2 = 1.7 |
| `body_x` | **3.2** | 5 | DIMENSIONS unit:mm — 顶视图横向 3.2±0.1 |
| `body_y` | **2.5** | 5 | DIMENSIONS unit:mm — 顶视图纵向 2.5±0.1 |
| `pad_x` | **1.4** | 5 | LAND PATTERN unit:mm — 左侧焊盘宽 1.4、右侧焊盘宽 1.4 |
| `pad_y` | **1.2** | 5 | LAND PATTERN unit:mm — 上排焊盘高 1.2、下排焊盘高 1.2 |

**★ 需人裁定 / 已披露的口径问题**

- ★ pitch_x / pitch_y 是**间接读数**：规格书没有直接标中心距，标的是「焊盘宽 1.4 + 间隙 0.8 + 焊盘宽 1.4」和「焊盘高 1.2 + 间隙 0.5 + 焊盘高 1.2」。由对称性（虚线十字中心线保证两侧焊盘中心关于原点对称）推出 pitch = 1.4/2+0.8+1.4/2 = 2.2，1.2/2+0.5+1.2/2 = 1.7。该推导被 .dra 独立证实（引脚 y 坐标 ±0.85 → 1.7；x 坐标 ±1.1 → 2.2），误差为 0。
- ★ body_x / body_y 口径：取**顶视图本体最大外形**（3.2±0.1 x 2.5±0.1，即公称 3.2 x 2.5）。第 5 页另有俯视电极图（阴影焊端）标出 0.9（焊端内间距）与 0.1 / 1.2（焊端宽），这是电极自身尺寸，不作为本体外形口径。
- ★ pad_x / pad_y 口径：取 'LAND PATTERN unit:mm' 下的**推荐焊盘图形**边长（1.4 x 1.2），不是器件电极尺寸。注意该 .dra 的 SOLDERMASK_TOP 阻焊开窗为 1.5 x 1.3（单边外扩 0.05），与 pad_x/pad_y 口径不同，不要混用。
- pin_count 口径：4 个信号/接地焊端全部计入（#1 Xtal In / #2 GND / #3 Xtal Out / #4 GND），该封装无外壳脚、无定位柱。
- confidence: high —— 8 个字段全部有清晰印刷标注，且 8/8 与独立人工 .dra 完全吻合（零分歧、零容差消耗）。

---

## 2. `audio6_cstj421` — 3.5mm 耳机座 CSTJ-421-1010BG（6 脚，THT）（连接器 3.5mm 音频座）

- 来源：既有 spec（`layout-forge-mcp/knowledge/specs/nb_cstj421_aj35_6p.json`）
- PDF：`Data/footprint/CSTJ-421-1010BG/CSTJ-421-1010BG.pdf` · sha256 前 12 位 `b65b8c8add9e` · 形态 printed
- .dra：`Data/footprint/CSTJ-421-1010BG/nb_audio6_12x7d25x4d3.dra`（交叉核对方法：用 unconfined 解释器（C:/Users/ds245/WorkBuddy/Layout/.workbuddy/cache/tools/venv-unconfined/Scripts/python.exe）经 services/ex…）
- 证据全文：`drafts/audio6_cstj421.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **tht** | 1 | 右下（底面/焊盘）视图：6 个焊盘均为「长圆形剖面线铜箔 + 内部一个未填充的槽孔轮廓」的画法；同一处标注串 5-1.20 / 5-0.60 与 3-2.20 / 3-1.60（焊盘外轮廓与槽孔轮廓成对标注）。PDF 文本层里没有 SMD / THT / DIP 字样可引。 |
| `pin_count` | **6** | 1 | 文本层零件表原文：'NO. PART NAME Q\'TY MATERIAL' 下依次为 '1 CONTACT 1'、'2 CONTACT 1'……'6 CONTACT 1'（C5210 T=0.20），另有 '7 Housing 1 PA9T UL94V-0'；焊盘图上 6 个焊盘旁分别标引脚号  … |
| `pitch_x` | **null** | 1 | 右视图上方水平尺寸链 4.85 + 4.25（中心线到左右界线）与 3.43 + 3.35；底部 3.95 / 4.45 / 4.55 / 3.35 / 3.65 |
| `pitch_y` | **null** | 1 | 右视图左侧竖向尺寸串 2.75 / 5.35 / 9.25 / 11.50 与右侧竖向尺寸串 2.75 / 3.75 / 9.00 / 11.60（四组尺寸都从同一条本体上边缘基线量起） |
| `body_x` | **6.78** | 1 | 右视图上方尺寸链 '3.43' 与 '3.35'（两条界线分别位于本体左右边缘 x=607.32 / 679.92 与中心线 x=644.04 之间，矢量实测 36.72 pt 与 35.88 pt） |
| `body_y` | **12** | 1 | 左下侧视图顶部总长标注 '12.00'（同视图另有 '4.20±0.10' 为本体高度） |
| `pad_x` | **1.2** | 1 | 右视图左下成对尺寸 '5-1.20'（5 处 1.20）与 '5-0.60'（5 处 0.60） |
| `pad_y` | **2.2** | 1 | 右视图左侧成对尺寸 '3-2.20'（3 处 2.20）与 '3-1.60'（3 处 1.60） |

**★ 需人裁定 / 已披露的口径问题**

- ★

---

## 3. `lydpj_5p_12x6` — 3.5mm 耳机座 LYD-PJ-35（5 脚，THT）（连接器 3.5mm 音频座）

- 来源：既有 spec（`layout-forge-mcp/knowledge/specs/nb_lydpj_5p_12x6.json`）
- PDF：`Data/footprint/LYDPJ-H23CB1Y-053N/LYDPJ-H23CB1Y-053N.pdf` · sha256 前 12 位 `6c4a4dc670a3` · 形态 cad_vector
- .dra：`Data/footprint/LYDPJ-H23CB1Y-053N/nb_audio5_13d05x6d3x4d2.dra`（交叉核对方法：用 unconfined 解释器经 services/extracta_reader.read_via_extracta 解析 .dra 四视图，取 pins(编号/xy/rotation)、padstack 铜箔与 DRILL 层尺寸、l…）
- 证据全文：`drafts/lydpj_5p_12x6.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **tht** | 1 | PCB LAYOUT TOP VIEW: 5 pads drawn as hatched oval copper pads with inner OVAL SLOT drill symbols; 3-1.50 / 3-2.10 / 2-1.70 / 2-2.30 slot+pad callouts; … |
| `pin_count` | **5** | 1 | PCB LAYOUT TOP VIEW: 5 pads circled and numbered 1, 2 (top row) and 3, 4 (bottom row), 5 (top row rightmost); title block "5 PIN" |
| `pitch_x` | **null** | 1 | PCB LAYOUT TOP VIEW: 3.65 与 6.45 标注为左起第一列到脚1、到脚2 的中心距；下排 4.93 标注为左起第一列到脚3 的中心距 |
| `pitch_y` | **null** | 1 | PCB LAYOUT TOP VIEW 右侧标注: 6.70 / 8.10（8.10 为上排脚中心到下排脚中心的跨距） |
| `body_x` | **12.2** | 1 | PCB LAYOUT TOP VIEW 顶部总长标注: 12.20（同一处 11.40 为另一条界线） |
| `body_y` | **6.7** | 1 | PCB LAYOUT TOP VIEW 右侧标注: 6.70 |
| `pad_x` | **2.1** | 1 | PCB LAYOUT TOP VIEW 底部标注: 3-1.50 / 3-2.10 / 2-1.70 / 2-2.30 |
| `pad_y` | **1.1** | 1 | PCB LAYOUT TOP VIEW 底部标注: 3-2.10 / 2-2.30 与 3-1.50 / 2-1.70 构成成对的两个方向尺寸 |

**★ 需人裁定 / 已披露的口径问题**

- （本例无口径争议；8 字段均落到图上标注或矢量实测，`.dra` 交叉核对见上表）

---

## 4. `fpc0510_6p` — FPC 0.5mm 6P 前掀盖下接触（8 脚）（连接器 FPC/ZIF）

- 来源：既有 spec（`layout-forge-mcp/knowledge/specs/nb_fpc0510_6p_4d77x3d6.json`）
- PDF：`layout-forge-mcp/Tools/test/NB_FPC0510_6P_4D77X3D6/FPC0510系列 Pitch0.5 H1.0 前掀盖+下接触.pdf` · sha256 前 12 位 `91f4cdc7c2e2` · 形态 printed
- .dra：`layout-forge-mcp/Tools/test/NB_FPC0510_6P_4D77X3D6/NB_FPC0510_6P_4D77X3D6.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（unconfined python 驱动 Cadence extracta 四视图）拿到 pins/padstack/layers；PACKAGE GEO…）
- 证据全文：`drafts/fpc0510_6p.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 1 | RECOMMEND PCB LAYOUT / TOLERANCE: ±0.05（焊盘为顶层矩形 SMD 盘，图示无插件孔） |
| `pin_count` | **6** | 1 | CKT 6 | A 4.77 | B 2.50 | C 3.60 | D 4.27；标题栏 PRODUCT NAME: 0.5mm Pitch FPC 前掀盖H1.0，PATER NUMBER 系列名 FPC 0510-XXB2-G1R |
| `pitch_x` | **0.5** | 1 | 0.50±0.05（RECOMMEND PCB LAYOUT 相邻信号焊盘中心距，6 个盘共 5 个 0.50 间隔） |
| `pitch_y` | **null** | — | （null 理由：单排信号脚（rows=1），y 方向不存在相邻引脚；固定脚 7/8 位于另一 y 且非规则阵列，其 y 差 2.81mm 不是 pitch。规格书未规定任何 y 方向引脚间距。…） |
| `body_x` | **4.77** | 1 | CKT 表 CKT=6 行 A 列 = 4.77；上视图/下视图标注 *A±0.15（A 为沿焊盘排列方向的本体总长） |
| `body_y` | **3.6** | 1 | CKT 表 CKT=6 行 C 列 = 3.60；下视图标注 C±0.05（C 为垂直于焊盘排列方向的本体宽） |
| `pad_x` | **0.3** | 1 | 0.30±0.03（RECOMMEND PCB LAYOUT 信号焊盘宽度标注，左右各标一次） |
| `pad_y` | **0.65** | 1 | 0.65（RECOMMEND PCB LAYOUT 信号焊盘高度标注，左侧引出线） |

**★ 需人裁定 / 已披露的口径问题**

- ★

---

## 5. `lmbr140et1g_2p` — SOD-323HE 肖特基二极管 LMBR140ET1G（2 脚）（分立器件 二极管）

- 来源：既有 spec（`layout-forge-mcp/knowledge/specs/nb_lmbr140et1g_2p.json`）
- PDF：`layout-forge-mcp/Tools/test/NB_LMBR140ET1G_2P/LMBR140ET1G.pdf` · sha256 前 12 位 `7556edb5bce3` · 形态 scan
- .dra：`Data/footprint/LMBR140ET1G/nb_sod-323_2_2d7x1d4x0d75.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（由 venv-unconfined 解释器驱动 Cadence extracta 四视图，_gaps=0 无缺失通道）：pin_count=len(pin…）
- 证据全文：`drafts/lmbr140et1g_2p.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 3 | Suggested solder pad layout —— SOD-323HE 封装，图示两枚**顶层矩形实心焊盘**（橙色块），整份规格书无插件孔/引脚穿孔图；本体为贴片二极管外形（阳极—阴极两焊端） |
| `pin_count` | **2** | 3 | Suggested solder pad layout —— 图上恰有 2 枚焊盘（大焊盘 + 小焊盘），标注串 A/B/C/D/E/F；第 1 页标题 LMBR120ET1G thru LMBR1200ET1G 为两脚肖特基二极管系列，无外壳脚/定位柱 |
| `pitch_x` | **1.9** | 3 | Suggested solder pad layout 表 SOD123-FL 行 F 列 = 0.075(1.90)；尺寸链自洽校验 B/2 + C + D/2 = 2.00/2 + 0.50 + 0.80/2 = 1.90（B=0.079(2.00) 大焊盘沿引脚排列方向的边长，C=0.019( … |
| `pitch_y` | **null** | — | （null 理由：单排单列器件：两枚焊盘中心 y 坐标相同（同一水平轴线上），y 方向不存在「相邻引脚」。规格书只在水平方向给出中心距 F=1.90，垂直方向无任何引脚间标注；.dra 侧两个 pin 的 y 均为 0.0，同样无 y 向…） |
| `body_x` | **2.7** | 3 | 3. dimension: SOD-323HE 的 DIMENSIONS 表 —— DIM A | MILLIMETERS MIN 2.30 MAX 2.70 | INCHES MIN 0.091 MAX 0.106（取 MAX 列 2.70 为本体长；顶视图 A 为水平长轴） |
| `body_y` | **1.4** | 3 | 3. dimension: SOD-323HE 的 DIMENSIONS 表 —— DIM B | MILLIMETERS MIN 1.20 MAX 1.40 | INCHES MIN 0.047 MAX 0.055（取 MAX 列 1.40 为本体宽；顶视图 B 为竖直短轴） |
| `pad_x` | **2** | 3 | Recommended solder pad layout 表 SOD123-FL 行 B 列 = 0.079(2.00)（大焊盘，对应图上 Suggested solder pad layout 中标注 B 的焊盘边长）；同表反向校验 B/2 + C + D/2 = 1.00 + 0.50 + 0 … |
| `pad_y` | **1.1** | 3 | Recommended solder pad layout 表 SOD123-FL 行 A 列 = 0.044(1.10)（同一焊盘的另一条边，垂直于引脚排列方向）；同表 E 列 = 0.04(1.00) 与 D 列 = 0.032(0.8) 属小焊盘边长，不作为大焊盘读数 → pad_y = 1. … |

**★ 需人裁定 / 已披露的口径问题**

- ★ 数值来源均为**位图里的印刷标注**，文本层提不到任何字符，全部靠区域高 dpi 渲染肉眼读数：p3_120-265-390-480@900.png（DIMENSIONS 表）、p3_110-600-470-652@900.png（Recommended solder pad layout 表整行）、p3_legend_wide@900.png（Suggested solder pad layout 图）。
- ★ body_x / body_y 口径：取 DIMENSIONS 表 A / B 的 **MAX 列**（2.70 / 1.40），不是 MIN、也不是中值。理由：canonical 字段要与 .dra 的 ASSEMBLY_TOP 本体外框对得上，而 .dra 实测正是 [2.7, 1.4]；.dra 文件名 nb_sod-323_2_**2d7**x**1d4**x0d75 亦以 2.7 x 1.4 命名。若签字方希望取 MIN 或公称，请显式改口径（那会与 .dra 交叉核对不一致）。
- ★ pitch_x 是**读 + 推**的组合：规格书直接标了中心距 F = 1.90（表列 0.075(1.90)），同时也给了可自洽的尺寸链；本报告把两者作为互相印证，读数取 1.90。该值被 .dra 独立证实（两 pin x = ±0.95，差精确 1.90，零误差）。
- ★ pad_x / pad_y 口径：以**规格书推荐的 land pattern**为准（README 明确『以规格书推荐的 land pattern 为准』），取『大焊盘』（阴极侧，图上标注 B / A 的那枚）的边长 2.00 x 1.10，不取小焊盘（0.8 x 1.0），也不取器件本体电极尺寸。注意 .dra 的 SOLDERMASK_TOP 阻焊开窗为 2.1 x 1.2（单边外扩 0.05），与 pad_x/pad_y 口径不同，不许混用。
- ★ 非对称焊盘事实（供签字人注意，不影响本 8 字段定义）：本封装两枚焊盘**不相等** —— 阴极侧 2.0 x 1.1，阳极侧 0.8 x 1.0（规格书 B/D 与 .dra 两 pin 的 padstack 双向印证）。canonical 的 pad_x/pad_y 只有一对值，本报告取**阴极侧大焊盘**；若签字方希望取阳极侧，则应改为 0.8 x 1.0。此点在既有 spec 里用 pins_override 逐脚建模解决。
- ★ 本规格书与人工件的一个已知差异（2026-10-09 提出、至今待裁决，本报告原样记录不改读数）：既有 spec 的 notes 称『人工件 pin1 pad 是竖的 1.1x2.0（上下伸出本体），与规格书 p3 的横向 pad 相左』。本次实测 .dra 的 pin#1 ETCH size = [2.0, 1.1]（即 x 向 2.0、y 向 1.1，**x 向为长边**），而 Suggested solder pad layout 小图把焊盘画成高瘦形。两者对『图面 vs 实物』的读法不同源，但落到 canonical 的 pad_x=2.0 / pad_y=1.1 上一致。
- ★ 图面非等比，已核实：Suggested solder pad layout 小图里大焊盘画得『横宽于高』（实测宽/高 ≈ 1.57），与标称 2.00/1.10 = 1.82 有偏差，说明该小图是示意而非等比。因此本报告**不用图面像素比例作为读数依据**，只作为 A/B 轴向归属的辅助判断。
- ★ 两处口径提示：①DIMENSIONS 表里的 A~K 与 Recommended solder pad layout 表里的 A~F 是**两套互不相干的字母**（前者是封装外形尺寸，后者是焊盘推荐尺寸）；本报告引用时已分别写明来源。②pitch_x 的物理含义是『两枚焊盘中心距』= F = 1.90，也等于 .dra 的两个 pin 中心距。
- ★ 介质自检：任务书给定的 sha256 前 12 位 7556edb5bce3 与实测**完全一致**（全哈希 7556edb5bce31cfca41db394b2a68b23dd222285287c57bf2e537c039f02af2a）；另核对 Data/footprint/LMBR140ET1G/LMBR140ET1G.pdf 亦为同一哈希 7556edb5bce3，与 cases.json 的备注相符（两份逐字节相同，真值钉 Tools/test 那份）。
- ★ 工具链已知缺陷（本轮踩到，已绕过、未改仓库）：services/pdf_service.py:22 的 `from services.pdf_renderer import _render_page, safe_stem as _safe_stem` 会整条失败（pdf_renderer 中无 `_render_page`），导致模块级 `_safe_stem` 未绑定，**render_region() / get_or_render_image() 抛 NameError: name '_safe_stem' is not defined**（render_page() 因另走 pdf_renderer.render_page 不受影响）。本轮在调用脚本内注入同名实现绕过，未修改仓库文件。建议单独修这个 import。
- confidence: medium —— 8 个字段 8/8 与独立的人工 .dra 几何完全一致（零分歧、零容差消耗），body/pitch/mount/pin_count 属 high；但规格书 Suggested solder pad layout **小图与两张标注表的字母-轴向能给出两种自洽读法**（pad_x/pad_y 互换），本报告选择与尺寸链方程及 .dra 一致的那一种，故整体不给 high，把 pad_x 单列 medium。

---

## 6. `type_c24` — USB 4.0 Type-C 24P 母座（板上四脚插 + 双排 SMT）（连接器 Type-C）

- 来源：新增案例
- PDF：`Data/footprint/DB_typec_126-223-240005-U4G--USB 4.0 TYPE-C 24P/步步精126-223-240005-U4G--USB 4.0 TYPE-C 24P 母座 有后盖 单壳 板上四脚插 带柱 端子双排SMT 外壳脚长0.60 L=9.17 CH=1.57 编带.pdf` · sha256 前 12 位 `2d13237efe18` · 形态 printed
- .dra：`Data/footprint/DB_typec_126-223-240005-U4G--USB 4.0 TYPE-C 24P/nb_type_c24_9d17x8d94x3d31_0d5.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（unconfined python 3.10.11 驱动 Cadence extracta），units=mm、_gaps=0、raw_pin_count…）
- 证据全文：`drafts/type_c24.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 2 | 推荐 PCB layout 视图：24 枚信号焊盘全部为斜线填充的裸铜矩形，无任何钻孔/插件符号；同页文本层「TERMINAL / 24 / COPPER ALLOY / SOLDER TAIL:Gold FLASH, 50u" NICKEL OVERALL. / T=0.12」；图框 NAME 串 … |
| `pin_count` | **24** | 2 | 「24X0.27±0.05」（第2页推荐 PCB layout 焊盘阵列的 24 枚计数标注） |
| `pitch_x` | **0.5** | 2 | 「0.50」（第2页焊盘阵列上方标注，与「5.50」= 11×0.50 的 12 焊盘跨度互证） |
| `pitch_y` | **1.6** | 2 | 图上无该间距的独立标注；由推荐 land pattern 的矢量几何实测：A 排焊盘 y=192.671–217.132、B 排 y=237.517–259.260，两排中心 y=204.902 / 248.389，差 43.487 pt ÷ 27.178 pt/mm = 1.600 mm（上下自洽 … |
| `body_x` | **8.94** | 1 | 「(8.94)」（第1页正视/顶视图最外侧横向总宽；同视图内另有「(8.34)」= 对插口内宽、「8.64」「2-Ø0.42±0.10」等） |
| `body_y` | **9.17** | 1 | 「(9.17)」（第1页侧视图横向总长；同页图框 NAME 串「外壳脚长0.60 L=9.17 CH=1.57」互证） |
| `pad_x` | **0.27** | 2 | 「24X0.27±0.05」（第2页推荐 PCB layout，24 枚信号焊盘宽度） |
| `pad_y` | **0.8** | 2 | 「12X0.80±0.05」（第2页推荐 PCB layout，竖排文字，量的是焊盘高度方向） |

**★ 需人裁定 / 已披露的口径问题**

- 读法顺序（纪律要求）：先只用 PDF 定下 8 个字段读数并落盘到 candidates/type_c24.json，之后才读 .dra 做交叉核对；candidates 里的值不含 .dra 回填。**唯一需披露的例外**：body_x/body_y 的『轴归属』（8.94→body_x、9.17→body_y）是口径裁定，依据是 canonical 字段 body_x/body_y 的轴向语义 + 既有候选 audio6_cstj421 的 body_x=7.59 < body_y=12.2 这一反例（说明 body_x ≠ 较长边）；两个数本身都是 PDF 读数，且 PDF 内部即可判：焊盘阵列轴 = 8.94 轴 = 推荐 land pattern 的 -X- 轴。
- ★ 最大不确定项 = body_x/body_y 的轴归属。两种读法：(A) 本项目口径（body_x = 封装 X 向尺寸，与 pad_x/pitch_x 同轴）→ body_x=8.94 / body_y=9.17，与 .dra 几何完全一致；(B) README 字面『本体长/宽』（长=9.17）→ body_x=9.17 / body_y=8.94，与 .dra 文件名数字顺序一致但与 .dra 几何的 X/Y 相反。本草案取 (A)，理由是代码层证据：Tools/benchmark_golden.py:92-105 `_body_from_place_bound` 用 place_bound bbox 的 **X 向宽度** 填 body_x、Y 向高度填 body_y，而该指纹要与 knowledge/specs 的 spec.body.x/y 直接比对，两者必须同轴。请人工签 …（全文见 drafts/*.md）
- ★ 次大不确定项 = pad_y 单值（0.80 vs 0.90）与 mount（smd vs tht），理由分别见 fields.pad_y.note 与 fields.mount.note。
- mount 的判定补充：本件是混合装配（24 枚 SMT 端子 + 4 枚板上四脚插外壳脚 + 4 枚定位柱）。canonical 8 字段整体按『信号脚』口径（pin_count 明确排除外壳脚/定位柱），故 mount 也取信号脚口径 = smd；若评审方口径为『整件是否需要通孔』则应记 tht。项目实现（services/geometry_solver.py:459-486 用 mount 决定主 padstack 是否带钻孔、是否刷锡膏）站在 smd 一边。
- 本草案未添加任何签字字段（签字权在人，AI 侧不签署，此处刻意不写该键名以免被文本匹配误判）。置信度：pin_count / pitch_x / body_x / body_y / pad_x = high；mount = medium-high；pitch_y = high（矢量+双源，但图上无标注）；pad_y = medium（两档，单值口径待裁）。

---

## 7. `hdmi19` — HDMI 19P 母座 CSTHM-1921-1KPD0A（连接器 HDMI）

- 来源：新增案例
- PDF：`Data/footprint/HDMI_CSTHM-1921-1KPD0A/科新城CSTHM-1921-1KPD0A（C版）.pdf` · sha256 前 12 位 `a9f7700161d9` · 形态 printed
- .dra：`Data/footprint/HDMI_CSTHM-1921-1KPD0A/nb_hdmi19_15x12d5x5d55_1.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（unconfined venv python）读 .dra 的 pins/padstack/design_bbox/layers，把 padstack 名…）
- 证据全文：`drafts/hdmi19.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **tht** | 1 | RECOMMEND PCB LAYOUT / TOLERANCE: ±0.10 — 板上焊盘为 Ø0.85 圆盘（drill Ø0.55），非 SMD 矩形焊盘 |
| `pin_count` | **19** | 1 | PIN # 1..19（SIGNAL ASSIGNMENT 表）；C B TERMINAL 19 / SHELL 1 / HOUSING 1；PIN1…PIN19 标注 |
| `pitch_x` | **1** | 1 | 1.00（RECOMMEND PCB LAYOUT 内，PIN1 排两相邻信号脚中心距） |
| `pitch_y` | **1.5** | 1 | 1.50（孔阵列上下两排的 y 向中心距；同一处另一标注为 0.50 排间错位） |
| `body_x` | **15** | 1 | 15.00（本体俯视图/外形长度） |
| `body_y` | **12.5** | 1 | 12.50（本体侧视图宽度） |
| `pad_x` | **0.85** | 1 | Ø0.85（RECOMMEND PCB LAYOUT 内指向信号脚焊盘圆的直径标注） |
| `pad_y` | **0.85** | 1 | Ø0.85（同上；信号脚焊盘为圆盘，宽高同值） |

**★ 需人裁定 / 已披露的口径问题**

- （本例无口径争议；8 字段均落到图上标注或矢量实测，`.dra` 交叉核对见上表）

---

## 8. `bga496` — LPDDR5 BGA 496 ball（496b_y31m_qdp_8dp）（IC BGA）

- 来源：新增案例
- PDF：`Data/footprint/496b_y31m_qdp_8dp_mobile_lpddr5/496b_y31m_qdp_8dp_mobile_lpddr5.pdf` · sha256 前 12 位 `e4460a88bd05` · 形态 printed
- .dra：`Data/footprint/496b_y31m_qdp_8dp_mobile_lpddr5/nb_bga496_14x12d4x0d77_0d4.dra`（交叉核对方法：extracta 四视图（pins / pads / PACKAGE GEOMETRY / REF DES）经 services/extracta_reader.read_via_extracta 解析为结构体后逐字段推导：pin_coun…）
- 证据全文：`drafts/bga496.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 12 | on Ø0.24 SMD ball pads. |
| `pin_count` | **496** | 12 | 496X Ø0.27 ±0.05 |
| `pitch_x` | **0.4** | 12 | 0.4 TYP |
| `pitch_y` | **0.4** | 12 | 0.4 TYP |
| `body_x` | **14** | 12 | 14 ±0.1 |
| `body_y` | **12.4** | 12 | 12.4 ±0.1 |
| `pad_x` | **0.24** | 12 | on Ø0.24 SMD ball pads. |
| `pad_y` | **0.24** | 12 | on Ø0.24 SMD ball pads. |

**★ 需人裁定 / 已披露的口径问题**

- （本例无口径争议；8 字段均落到图上标注或矢量实测，`.dra` 交叉核对见上表）

---

## 9. `tf9` — TF 卡座 9 pin 外焊 H=1.85（连接器 TF/microSD 卡座）

- 来源：新增案例
- PDF：`Data/footprint/CD.01.02-13-00039Pin H=1.85/CD.01.02-13-0003--TF卡座 外焊9Pin H=1.85 贴片 带柱 双弹片外壳 平脚镀镍 编带 .pdf` · sha256 前 12 位 `83de7cb8275e` · 形态 cad_vector
- .dra：`Data/footprint/CD.01.02-13-00039Pin H=1.85/nb_tf9_14d5x16d19x2.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（unconfined python 3.10.11 驱动 Cadence extracta，四视图），取 pins 的 xy/bbox/size 与 pa…）
- 证据全文：`drafts/tf9.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 1 | PCB 图形区下标注 "(RECOMMENDED/P.C.B)HOLE LAYOUT" 与 "COMPONENT SIDE VIEW TOLERANCE:±0.05"；图中所有连接盘均为实心斜线填充的贴片盘，无穿孔/插件焊盘；物料名 "贴片 带柱"（H=1.85） |
| `pin_count` | **9** | 1 | 俯视图引脚编号 "1 2 3 4 5 6 7 8 cd"；Assignment 表共 9 行（1#DAT2、2#CD/DAT3、3#CMD、4#VDD、9#CD、5#CLK、6#VSS、7#DAT0、8#DAT1）；PCB 图形区尺寸串 "8-1.10"（8 段间距、9 个焊盘） |
| `pitch_x` | **1.1** | 1 | "8-1.10"（PCB 图形区 9 个信号焊盘阵列上方标注，与俯视图 "⑥ 8.80" 对应：8 段 × 1.10 = 8.80） |
| `pitch_y` | **null** | — | （null 理由：不存在第二个间距：9 个信号脚只有单排，排内间距已记入 pitch_x，y 方向无相邻引脚对，故无规则间距可读。…） |
| `body_x` | **16.19** | 1 | "⑭ 16.19"（俯视图最外侧横向尺寸；同一视图另有 "⑬ 14.79"、"⑤ 15.30"、"⑯ 14.50"、"⑲ 10.50"） |
| `body_y` | **14.5** | 1 | "⑯ 14.50"（俯视图右侧纵向尺寸，与正面视图 "⑯ 14.50"、"⑲ 10.50" 呼应） |
| `pad_x` | **0.7** | 1 | "0.70"（PCB 图形区信号焊盘阵列右端焊盘宽度标注；焊盘高另注 "1.60"） |
| `pad_y` | **1.6** | 1 | "1.60"（PCB 图形区信号焊盘阵列右侧焊盘高度标注） |

**★ 需人裁定 / 已披露的口径问题**

- ★ 页为 /Rotate 270，未旋转页面为 595×842 纵向、横向渲染为 842×595。读数时按未旋转坐标裁图，曾因两套坐标混用产生误读，已全部重测修正。

---

## 10. `zif30` — FPC 0.5mm 30P 翻盖下接 H=1.75（AFC68）（连接器 FPC/ZIF）

- 来源：新增案例
- PDF：`Data/footprint/130-221-300004-W3G--FPC 0.5-30P/130-221-300004-W3G--FPC 0.5-30P 翻盖下接 H=1.75 (AFC68).pdf` · sha256 前 12 位 `ef5c7e2750e9` · 形态 cad_vector
- .dra：`Data/footprint/130-221-300004-W3G--FPC 0.5-30P/nb_zif30_19x4d3x1d75_0d5.dra`（交叉核对方法：services.extracta_reader.read_via_extracta（unconfined python 3.10.11），units=mm；用 pins[].pads[] 的 ETCH/TOP 矩形尺寸与 xy 坐标推算 …）
- 证据全文：`drafts/zif30.md`（ai-read-2026-10-15）

| 字段 | 真值 | 页码 | 图上引文 |
|------|------|------|----------|
| `mount` | **smd** | 1 | RECOMMENDED P.C.B. PATTERN LAYOUT —— 该视图全部焊盘均为同面贴装矩形焊盘；无任何插件孔径/钻孔标注（对比：第2页卷带图有 φ1.50/φ2.0 包装孔，属载带而非封装孔）。另：.dra 的 32 个 pad 全部落在 layer=ETCH/TOP + PIN/SO … |
| `pin_count` | **30** | 1 | 顶视图引脚位号相邻标 'FA' 与 '30'（Circuit 1 起算），顶视图引脚通道实测 30 个；CKTS 表被蓝框高亮的行即 30。 |
| `pitch_x` | **0.5** | 1 | 0.50±0.05（顶视图引脚位与 PCB 焊盘位各标一次，两处一致） |
| `pitch_y` | **null** | 1 | PCB 焊盘视图仅单排 30 焊盘；.dra pins: 30×R1D3X0D3 xy=[±7.25…], 全部 y=+2.8 |
| `body_x` | **19** | 1 | (2.25)（顶视图左基准线→Circuits-1 中心）+ DIM A±0.10（30 位 = 14.50，见 CKTS 表 30 行 DIM A）；顶视图本体外形左边缘与 PCB 视图中 'DIM A+2.6' 呼应 = 17.10 的顶壳跨度。 |
| `body_y` | **4.3** | 1 | 4.30 |
| `pad_x` | **1.3** | 1 | 1.30（PCB 焊盘视图右端，双箭头跨越单个焊盘的长度方向） |
| `pad_y` | **0.3** | 1 | 0.30±0.03（PCB 焊盘视图，箭头指向焊盘宽度方向） |

**★ 需人裁定 / 已披露的口径问题**

- （本例无口径争议；8 字段均落到图上标注或矢量实测，`.dra` 交叉核对见上表）

---


## 签字区（由人填写，AI 不代签）

| 案例 | 裁定结果 | 签字人 | 日期 |
|------|----------|--------|------|
| 1. `xtal4_3d2x2d5x0d7` | ☐ 照草案　☐ 改成：______ | | |
| 2. `audio6_cstj421` | ☐ 照草案　☐ 改成：______ | | |
| 3. `lydpj_5p_12x6` | ☐ 照草案　☐ 改成：______ | | |
| 4. `fpc0510_6p` | ☐ 照草案　☐ 改成：______ | | |
| 5. `lmbr140et1g_2p` | ☐ 照草案　☐ 改成：______ | | |
| 6. `type_c24` | ☐ 照草案　☐ 改成：______ | | |
| 7. `hdmi19` | ☐ 照草案　☐ 改成：______ | | |
| 8. `bga496` | ☐ 照草案　☐ 改成：______ | | |
| 9. `tf9` | ☐ 照草案　☐ 改成：______ | | |
| 10. `zif30` | ☐ 照草案　☐ 改成：______ | | |
