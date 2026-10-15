# lmbr140et1g_2p 读图证据报告（真值草稿，待人工签字）

- **案例**：`lmbr140et1g_2p` — SOD-323HE 肖特基二极管 LMBR140ET1G（2 脚）
- **读图人**：ai-read-2026-10-15
- **PDF**：`layout-forge-mcp/Tools/test/NB_LMBR140ET1G_2P/LMBR140ET1G.pdf`
- **PDF sha256（实测前 12 位）**：`7556edb5bce3` ✅ **与任务书给定值一致**
  （全哈希 `7556edb5bce31cfca41db394b2a68b23dd222285287c57bf2e537c039f02af2a`；
  另核对 `Data/footprint/LMBR140ET1G/LMBR140ET1G.pdf` 亦为同一哈希 —— 两份逐字节相同，与 `cases.json` 备注相符）
- **金标准 .dra**：`Data/footprint/LMBR140ET1G/nb_sod-323_2_2d7x1d4x0d75.dra`
- **是否签字**：否（本报告与同名 JSON 均不含 `signed_by` / `signed_at`，签字权在人）

---

## 1. PDF 形态与页面拓扑（先定位，避免盲读）

`pdf_service.probe_pdf()` 结果：

| 项 | 值 |
|--|--|
| pages | 11 |
| text_chars | **0**（文本层完全为空） |
| drawings | 0 |
| bitmaps | 11（每页 1 张整页位图） |
| form | **scan** |
| read_hint | 纯位图/扫描件：先 contact_sheet 定位页，再 render 区域读数 |

> ⚠️ **链路必然失配**：`locate_land_pattern_page()` 返回
> `{"page": null, "matched": [], "score": 0, "method": "no_match"}` ——
> 文本层 0 字符时关键词打分无从命中。这正是任务书点名的「风险最高一例」。
> **该 hint 不可用**，须走视觉定位。

**定位手段**：`build_contact_sheet(pdf, out, dpi=100)` → 1 张 840×945 的 4×3 拼版，
一次扫完 11 页即确认 **第 3 页**同时含「3. dimension:」标题、SOD-323HE 尺寸表与
Suggested solder pad layout + Recommended solder pad layout 表。全程**没有**整本高 dpi 渲染。

页级实读（`build_contact_sheet` 缩略图目视）：

| 页 | 内容 | 是否本任务目标 |
|--|--|--|
| 1 | 封面 + 特性 + 极限参数 + 电特性表 | — |
| 2 | 特性曲线（I_F–V_F / 电容 / 齐纳） | — |
| **3** | **3. dimension: SOD-323HE 尺寸表 + Suggested solder pad layout + Recommended solder pad layout 表** | ✅ **唯一目标页** |
| 4 | 焊接热剖面 + 带盘/载带（Power SMD GB Package） | — |
| 5 | 编带/胶盘机械图 | — |
| 6–9 | 包装与卷盘尺寸 | — |
| 10 | 包装/焊盘细节 | — |
| 11 | Update Record（版本修订表） | — |

---

## 2. 8 个 canonical 字段证据表

| 字段 | 值 | 页码 | 引文（图上标注串） | 怎么量的 |
|--|--|--|--|--|
| `mount` | `"smd"` | 3 | `Suggested solder pad layout` —— SOD-323HE 图示两枚**顶层矩形实心焊盘**（橙色块），整份规格书无插件孔/引脚穿孔图 | 整页 150 dpi 目视 + 区域 900 dpi；无 THT 痕迹 |
| `pin_count` | `2` | 3 | `Suggested solder pad layout` 图上恰 **2 枚焊盘**，标注串 `A/B/C/D/E/F`；第 1 页标题 `LMBR120ET1G thru LMBR1200ET1G` 为两脚肖特基系列 | 数图上焊盘个数；无外壳脚、无定位柱 |
| `pitch_x` | `1.9` | 3 | `Recommended solder pad layout` 表 `SOD123-FL` 行 `F` 列 = **`0.075(1.90)`**；自洽校验 `B/2 + C + D/2 = 2.00/2 + 0.50 + 0.80/2 = 1.90` | 表列直读 `1.90` + 尺寸链方程互证；.dra 独立复核 |
| `pitch_y` | `null` | — | — | 见 §4.1 |
| `body_x` | `2.7` | 3 | DIMENSIONS 表：`DIM A | MILLIMETERS MIN 2.30 MAX 2.70 | INCHES MIN 0.091 MAX 0.106` | 取 **MAX 列 2.70**（顶视图 A 为水平长轴） |
| `body_y` | `1.4` | 3 | DIMENSIONS 表：`DIM B | MILLIMETERS MIN 1.20 MAX 1.40 | INCHES MIN 0.047 MAX 0.055` | 取 **MAX 列 1.40**（顶视图 B 为竖直短轴） |
| `pad_x` | `2.0` | 3 | `Recommended solder pad layout` 表 `SOD123-FL` 行 `B` 列 = **`0.079(2.00)`**（大焊盘，对应 Suggested solder pad layout 图上标 `B` 的那枚） | 表列直读 `2.00`；由 §4.3 的轴向论证确定为 pad_x |
| `pad_y` | `1.1` | 3 | `Recommended solder pad layout` 表 `SOD123-FL` 行 `A` 列 = **`0.044(1.10)`**（同一焊盘的另一条边） | 表列直读 `1.10`；由 §4.3 的轴向论证确定为 pad_y |

### 2.1 SOD-323HE DIMENSIONS 表（第 3 页，`body_x` / `body_y` 来源）

| DIM | MILLIMETERS MIN | MILLIMETERS MAX | INCHES MIN | INCHES MAX |
|--|--|--|--|--|
| **A** | **2.30** | **2.70** | 0.091 | 0.106 |
| **B** | **1.20** | **1.40** | 0.047 | 0.055 |
| C | 1.75 | 1.95 | 0.069 | 0.077 |
| D | 0.30Typ | 0.30Typ | 0.012Typ | 0.012Typ |
| E | 0.55 | 0.75 | 0.030 | 0.022 |
| F | 0.10 | 0.20 | 0.004 | 0.008 |
| G | 0.65 | 0.95 | 0.026 | 0.037 |
| H | 0.50 | 0.70 | 0.020 | 0.028 |
| I | 0.40 | 0.80 | 0.016 | 0.031 |
| J | 1.15 | 1.55 | 0.045 | 0.061 |
| K | 0.8Typ | 0.8Typ | 0.032Typ | 0.032Typ |

### 2.2 Recommended solder pad layout 表（第 3 页，inch 与 (mm) 并排）

标题行 `Dimensions in inches and (millimeters)`，数据行 `PACKAGE = SOD123-FL`：

| PACKAGE | A | B | C | D | E | F |
|--|--|--|--|--|--|--|
| SOD123-FL | 0.044(**1.10**) | 0.079(**2.00**) | 0.019(**0.5**) | 0.032(**0.8**) | 0.04(**1.00**) | 0.075(**1.90**) |

### 2.3 Suggested solder pad layout 图上标注（第 3 页小图）

图上可辨识的标注串：`A`（左）、`B`（上方，有一条竖直中心分隔线穿过大焊盘）、`C`（两盘之间的水平双箭头）、
`D`（右）、`E`（右）、`F`（两盘中心之间的水平双箭头）。

- `C` = 两枚焊盘**对向净间隙** → 与表列 `0.5` 对应
- `F` = 两枚焊盘**中心距** → 与表列 `1.90` 对应（也是 .dra 的两 pin 中心距）

---

## 3. `.dra` 交叉核对结果

**方法**：`services.extracta_reader.read_via_extracta()`（由 **unconfined 解释器**驱动 Cadence extracta
四视图，`_gaps: 0`，无缺失通道）。

**`.dra` 原始读出**：

- `raw_pin_count = 2`，`pins` 数组两条：

| pin | padstack | `xy` | ETCH/TOP `size` | SOLDERMASK_TOP `size` | PASTEMASK_TOP `size` |
|--|--|--|--|--|--|
| `#1`（阴极） | `R2X1D1` | `[-0.95, 0.0]` | **[2.0, 1.1]** | [2.1, 1.2] | [2.0, 1.1] |
| `#2`（阳极） | `R1X0D8` | `[0.95, 0.0]` | **[0.8, 1.0]** | [0.9, 1.1] | [0.8, 1.0] |

- 本体：`PACKAGE GEOMETRY/ASSEMBLY_TOP` 外框 bbox `[[-1.65,-0.7],[1.05,0.7]]` → **`size = [2.7, 1.4]`**
  （中心 `x = -0.30` 偏置，不影响尺寸）
- `PLACE_BOUND_TOP`：3.6 × 1.7（非 canonical 字段）
- 两个 pin 的 `pads` 仅含 `ETCH/TOP` + `PIN/SOLDERMASK_TOP` + `PIN/PASTEMASK_TOP`，**无钻孔层**
- 目录内另有 padstack 文件 `r2x1d1.pad` / `r1x0d8.pad`（`Units: Millimeters`，Begin Layer Rectangle），
  文件名与规格书 `B=2.00` / `D=0.80` 同名同值，作旁证
- `.dra` 文件名 `nb_sod-323_2_2d7x1d4x0d75` 自述 **2.7 × 1.4**，与 ASSEMBLY_TOP 外框一致

**逐字段比对**：

| 字段 | PDF 读数 | `.dra` 推出 | 结论 |
|--|--|--|--|
| `mount` | smd | smd（两 pin 仅顶层三层，无钻孔） | ✅ agree |
| `pin_count` | 2 | 2（`raw_pin_count=2`） | ✅ agree |
| `pitch_x` | 1.9 | 1.9（`±0.95` → 精确 1.90） | ✅ agree（**零误差**） |
| `pitch_y` | null | n/a（两 pin `y` 均为 0.0） | ✅ agree（两边同为「无 y 向间距」） |
| `body_x` | 2.7 | 2.7（ASSEMBLY_TOP `size[0]`） | ✅ agree（**完全一致**） |
| `body_y` | 1.4 | 1.4（ASSEMBLY_TOP `size[1]`） | ✅ agree（**完全一致**） |
| `pad_x` | 2.0 | 2.0（pin#1 ETCH `size[0]`） | ✅ agree（**完全一致**） |
| `pad_y` | 1.1 | 1.1（pin#1 ETCH `size[1]`） | ✅ agree（**完全一致**） |

**agree = 8 项**：`mount, pin_count, pitch_x, pitch_y, body_x, body_y, pad_x, pad_y`
**disagree = 0 项**

> ★ 这是本批案例里少见的「8/8 零分歧」案例。但**必须**把下面 §4.3 的轴向读法冲突一并呈上：
> 数值一致是因为两边都取到了 `2.0 × 1.1`，而**图面标注的直译会给出一组互换解**。
> 冲突不许偷偷改读数，两边的值与倾向都留在下面。

---

## 4. 不确定项与理由

### 4.1 `pitch_y = null`（已判 null，附理由）

- 器件是**单排单列**：两枚焊盘中心 `y` 坐标相同（PDF 图上两盘在同一水平轴线；`.dra` 两个 pin 的 `y` 均为 `0.0`），
  y 方向不存在「相邻引脚」。
- 规格书只在水平方向给了中心距 `F = 1.90`，垂直方向**没有任何**引脚间标注。
- 结论：`null` + `null_reason`（与 canonical 口径「单排/单列写 `null`」一致）。

### 4.2 `body_x` / `body_y` 取 MAX 列（口径需签字人确认）

规格书 DIMENSIONS 表给的是 `MIN / MAX` 双列（A: 2.30/2.70，B: 1.20/1.40），**没有单一公称值**。
本报告取 **MAX 列**（2.70 / 1.40），依据：

1. `.dra` 的 `ASSEMBLY_TOP` 本体外框实测正是 `[2.7, 1.4]`，取 MAX 才能与之精确对上；
2. `.dra` 文件名 `nb_sod-323_2_2d7x1d4x0d75` 亦以 `2.7 × 1.4` 命名；
3. 既有 spec 的 `dim_tables` 同时记了 min/max，而其 `body` 取 `2.7 / 1.4`（即 MAX 口径），
   说明项目内既有共识也是 MAX —— **但该文件未被用作本次读数来源**，此处仅作口径旁证。

若签字方希望改取 MIN（2.30 / 1.20）或中值，请显式改口径；那会使 `.dra` 交叉核对由 agree 变 disagree。

### 4.3 ★ A/B 标注的轴向归属（本次唯一实质不确定项，判 medium）

`Suggested solder pad layout` 小图把两枚焊盘画成**高瘦形**：`A` 标在左右、`B` 标在上下。
若按图面字样**直译**，会得到：

- **读法甲（图面直译）**：`pad_x = 1.10`、`pad_y = 2.00`（大焊盘横窄竖高）
- **读法乙（本报告采用）**：`pad_x = 2.00`、`pad_y = 1.10`（大焊盘横宽竖矮）

本报告采用**读法乙**，三条独立证据同时指向它：

1. **尺寸链方程只在读法乙下自洽**。规格书同时给了两盘之间的净间隙 `C = 0.50` 与中心距 `F = 1.90`。
   中心距 = 大焊盘沿排列方向的半边 + 间隙 + 小焊盘沿排列方向的半边：
   `2.00/2 + 0.50 + 0.80/2 = 1.90` ✅（读法乙）
   `1.10/2 + 0.50 + 0.80/2 = 1.45` ❌（读法甲，与 F=1.90 差 0.45）
   即「沿引脚排列方向」的边长必须是 **2.00**（表列 B），故 pad_x = 2.00、pad_y = 1.10。
2. **`.dra` 几何**：pin#1 的 `ETCH/TOP size = [2.0, 1.1]` —— x 向 2.0 为长边；
   padstack 文件名 `r2x1d1.pad` / `r1x0d8.pad` 与规格书 `B=2.00` / `D=0.80` 数值一一对应。
   人做的封装把大焊盘的 **2.0 放在引脚排列轴（x）上**。
3. **像素量取**：把第 3 页按 900 / 1200 dpi 区域渲染后逐像素统计橙色焊盘范围，
   大焊盘实测**宽/高 ≈ 1.57**（`2375×2125` 图内列向 997 px、行向 630 px）——
   横宽于高，与读法乙的 `2.00/1.10 = 1.82` 同向，与读法甲的 `1.10/2.00 = 0.55` 反向。
   小焊盘实测 356 × 420 px（宽/高 ≈ 0.85），与 `0.8/1.0 = 0.80` 吻合，佐证量取方法可靠。

> **图面非等比**（已核实）：大焊盘标称 `2.00/1.10 = 1.82`，图面实测 `1.57`，偏差 14%；
> 小焊盘标称 `0.8/1.0 = 0.80`，图面实测 `0.85`，偏差 6%。
> 故该小图是**示意**而非等比，本报告**不把像素比例当读数依据**，只作为轴向归属的辅助判断。

**倾向与保留**：倾向读法乙（`pad_x = 2.0` / `pad_y = 1.1`），故 8 字段两边数值 agree；
但该图的字形与标注位置确实与读法乙相左，**这是签字人最该复核的一点**，故 `pad_x` 单列 medium。

### 4.4 非对称焊盘（事实陈述，不影响 8 字段取值）

本封装两枚焊盘**不相等**：

| 侧 | 规格书标注 | `.dra` padstack | ETCH 实测 |
|--|--|--|--|
| 阴极（pin#1） | 大焊盘：`B = 2.00` / `A = 1.10` | `R2X1D1` | `[2.0, 1.1]` |
| 阳极（pin#2） | 小焊盘：`D = 0.80` / `E = 1.00` | `R1X0D8` | `[0.8, 1.0]` |

canonical 的 `pad_x` / `pad_y` 只有**一对**值，本报告取**阴极侧大焊盘**（`2.0 × 1.1`）。
若签字方希望改取阳极侧，则应改为 `0.8 × 1.0`。既有 spec 用 `pins_override` 逐脚 + `extra_padstacks` 分建两个 padstack 来保住这个非对称。

### 4.5 与既有 spec 的对照（**仅对照，非读数来源**）

既有 spec `knowledge/specs/nb_lmbr140et1g_2p.json` 的 `fingerprint` 为：

| 字段 | 既有 spec（历史读数，候选） | 本次草稿 | 差异 |
|--|--|--|--|
| `mount` | `smd` | `smd` | 同 |
| `pin_count` | `2` | `2` | 同 |
| `pitch_x` | `1.9` | `1.9` | 同 |
| `pitch_y` | `null` | `null` | 同 |
| `body_x` | `2.7` | `2.7` | 同 |
| `body_y` | `1.4` | `1.4` | 同 |
| `pad_x` | `2.0` | `2.0` | 同 |
| `pad_y` | `1.1` | `1.1` | 同 |

**8/8 一致** —— 但请注意这是**独立读数后的会合**，不是抄录：本次全程只从 PDF 位图读数，
`.dra` 与既有 spec 都只是事后对照物；既有 spec **未被用作任何字段的读数来源**。

既有 spec 的 `notes` 另记了一处对人工件的观察（原样转述，本报告不改读数）：
> 「人工件 pin1 pad 是竖的 1.1x2.0（上下伸出本体），与规格书 p3 的横向 pad 相左」

**本次实测与这句描述不完全吻合**：`.dra` 的 pin#1 `ETCH/TOP size = [2.0, 1.1]`
（x 向 2.0、y 向 1.1，**x 向为长边**），既不是纯「竖的 1.1x2.0」也不与规格书的代表性取值冲突。
`r2x1d1.pad` 文件名亦支持 `2×1.1` 这一读法。此点建议一并交签字人裁决
（可能是当年读 `.dra` 时的旋转/轴向差异，而非真实几何差异）。

### 4.6 未使用的量（避免与 canonical 字段混淆）

- DIMENSIONS 表的 `C 1.75/1.95`、`D 0.30Typ`、`E 0.55/0.75`、`F 0.10/0.20`、`G 0.65/0.95`、
  `H 0.50/0.70`、`I 0.40/0.80`、`J 1.15/1.55`、`K 0.8Typ` —— 均为封装外形细节，非 8 字段。
- `Recommended solder pad layout` 表的 `E = 0.04(1.00)` —— 小焊盘的另一条边，非大焊盘。
- `.dra` 的 `PLACE_BOUND_TOP 3.6 × 1.7`、SOLDERMASK_TOP `2.1 × 1.2` / `0.9 × 1.1` —— 口径不同，不许当 `pad_x`/`pad_y`。
- ⚠️ **两套同名字母**：DIMENSIONS 表的 `A~K` 与 Recommended solder pad layout 表的 `A~F`
  是**互不相干的两套**（前者封装外形、后者焊盘推荐）。本报告引用处均已写明来源，勿混。

---

## 5. 结论与置信度

| 项 | 结论 |
|--|--|
| PDF 哈希 | `7556edb5bce3` ✅ 符合（全哈希已核对） |
| PDF 形态 | `scan`（11 页 / 11 位图 / **0 文本字符** / 0 矢量） |
| 真值页 | 第 3 页（唯一） |
| 8 字段完整性 | 8/8 出现；7 个带 `page`+`quote`，1 个（`pitch_y`）带 `null_reason` |
| `unreadable` | 空（无字段读不出） |
| `.dra` 交叉核对 | **agree 8 / disagree 0**（零分歧、零容差消耗） |
| 整体置信度 | **medium** |

**置信度拆解**：`mount / pin_count / pitch_x / pitch_y / body_x / body_y / pad_y` 为 high
（印刷标注直读 + `.dra` 精确吻合）；`pad_x` 为 **medium** —— 数值本身有表列直读与 `.dra` 双证，
但 Suggested solder pad layout 小图的字形给出了互换解，需按 §4.3 的论证采信，故不随其余字段给 high。

**待人工裁决的三个点**：

1. **§4.3**：`pad_x`/`pad_y` 取 `2.00 / 1.10`（本报告，尺寸链+`.dra`+像素量三证）还是 `1.10 / 2.00`（图面直译）；
2. **§4.2**：`body_x`/`body_y` 取 MAX 列（`2.7 / 1.4`，本报告）还是 MIN/中值；
3. **§4.5**：既有 spec notes 所述「人工件 pin1 pad 是竖的 1.1x2.0」与本次实测 `[2.0, 1.1]` 不符，需确认当年读 `.dra` 的口径。

---

## 6. 复现命令（含硬约束）

```powershell
$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
# ★ 必须用工作区外的 unconfined 解释器；工作区内解释器会让 extracta 第 2 路 PAD_DEF 崩成 0xC0000409
$py = "C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe"
& $py "C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp\eval\runs\spec_reading\lmbr140et1g_2p\read_lmbr.py"
```

`read_lmbr.py` 的内容（**临时件，落在 `eval/runs/` 之下，不进 git**）；
它复用项目 `services/pdf_service.py` 的渲染链路，并在进程内补上 §7 所述的缺失名字：

```powershell
& $py -c @"
import sys; sys.path.insert(0, r'C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp')
from services import pdf_service as ps
from services import extracta_reader as er
from pathlib import Path
def _safe_stem(s):
    keep='-_.() '
    return ''.join(c for c in str(s) if c.isalnum() or c in keep).strip() or 'pdf'
ps._safe_stem = _safe_stem            # ★ 绕过 pdf_service 的 import 缺陷（见 §7）
pdf = Path(r'C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp\Tools\test\NB_LMBR140ET1G_2P\LMBR140ET1G.pdf')
out = Path(r'C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp\eval\runs\spec_reading\lmbr140et1g_2p')
print(ps.probe_pdf(pdf)); print(ps.locate_land_pattern_page(pdf))
print(ps.build_contact_sheet(pdf, out, dpi=100))                              # 定位第 3 页
print(ps.render_page(pdf, 3, out, engine='pymupdf'))                          # 整页 150 dpi
print(ps.render_region(pdf, 3, (120.0,265.0,390.0,480.0), out, dpi=900))      # DIMENSIONS 表
print(ps.render_region(pdf, 3, (110.0,600.0,470.0,652.0), out, dpi=900))      # 推荐焊盘表整行
print(ps.render_region(pdf, 3, (150.0,470.0,340.0,640.0), out, dpi=900))      # Suggested pad layout 图
d = er.read_via_extracta(r'C:\Users\ds245\Documents\Work_Bmorn\Layout\Data\footprint\LMBR140ET1G\nb_sod-323_2_2d7x1d4x0d75.dra')
print({k: (len(v) if isinstance(v, list) else v) for k, v in d.items()})
"@
```

**证据图（全部落在 `eval/runs/spec_reading/lmbr140et1g_2p/` 之下）**：

- `LMBR140ET1G/contact_sheet.png` — 11 页拼版（840×945），定位到第 3 页
- `LMBR140ET1G/pymupdf_pages/page_3.png` — 第 3 页整页（1240×1754）
- `LMBR140ET1G/regions/p3_120-265-390-480@900.png` — **DIMENSIONS 表（`body_x`/`body_y` 来源）**
- `LMBR140ET1G/regions/p3_110-600-470-652@900.png` — **Recommended solder pad layout 表整行（`pad_x`/`pad_y`/`pitch_x` 来源）**
- `LMBR140ET1G/regions/p3_163-480-317-619@900.png`、`p3_legend_wide@900.png`、`p3_150-470-285-580@1200.png` — Suggested solder pad layout 图（A/B 轴向判断）
- `LMBR140ET1G/regions/p3_padfig_full@600.png` — 焊盘图整幅（像素量取用）

---

## 7. ★ 本轮踩到的工具链缺陷（未改仓库，仅记录）

`services/pdf_service.py:22`：

```python
from services.pdf_renderer import _render_page, safe_stem as _safe_stem
```

`pdf_renderer` 中**不存在** `_render_page`，该 import 整条失败 → 模块级 `_safe_stem` **从未绑定** →
`render_region()` 与 `get_or_render_image()` 一旦走到 `_safe_stem(...)` 就抛
`NameError: name '_safe_stem' is not defined`（实测报错原文：`NameError: name '_safe_stem' is not defined`，
包在 `render_region` 的 `except` 里返回 `{'ok': False, 'error': "NameError: name '_safe_stem' is not defined"}`）。
`render_page()` 因另走 `pdf_renderer.render_page` 而不受影响，所以此前没人发现。

本轮**未修改仓库文件**，而是在临时脚本内注入同名实现绕过。建议单独修这一行 import。

---

*更新日期：2026-10-15*
