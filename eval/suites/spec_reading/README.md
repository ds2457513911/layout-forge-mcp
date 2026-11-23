# eval/suites/spec_reading —— 读图准确率考卷（L5 度量与评测层）

> 最后更新：2026-10-15（**10 例真值已人工签字**、正式跑分 **92.5%**（80 字段：correct 74 / wrong 3 / spurious 3）
> 并冻结基线 `baselines/spec_reading.json`（`min_accuracy = 0.925`）；2026-10-14：加 S8 定位判据 `--with-locate`
> 并修掉 `services/pdf_locate.py` 的错页/漏页）

## 这个目录是干什么的

回答一个此前**全库没有答案**的问题：**「AI 从规格书 PDF 读 land pattern 参数，到底读得准不准？」**

- 253 件金标准对拍（`eval/suites/geometry_regression/`）考的是**确定性几何**——给定参数能不能把封装建对；
- 本套件考的是**读图**：给定 PDF，那 8 个数读对没有。换模型 / 换 dpi / 改读图策略 / 改提示词之后，
  这里是唯一能给出「变好了还是变坏了」的数字的地方。

## canonical 字段集（v1）

**刻意不另造 schema**：直接用项目自己 `knowledge/specs/*.json` 里的 `fingerprint` 键 ——
评测考的必须是「项目真正复用的那 8 个数」，而不是一个只有考卷认得的字段名。

| 字段 | 含义 | 容差 | 备注 |
|--|--|--|--|
| `mount` | `smd` / `tht` | 精确 | 表面贴装 / 插件 |
| `pin_count` | 引脚数（信号脚） | 精确 | ★ 外壳脚、定位柱不计数，口径写在真值备注里 |
| `pitch_x` | x 方向相邻脚中心距 mm | ±0.02 | 无规则间距写 `null` |
| `pitch_y` | y 方向相邻脚中心距 mm | ±0.02 | 单排/单列写 `null` |
| `body_x` | 本体长 mm | ±0.05 | |
| `body_y` | 本体宽 mm | ±0.05 | |
| `pad_x` | 推荐焊盘宽 mm | ±0.05 | 以规格书推荐的 land pattern 为准 |
| `pad_y` | 推荐焊盘高 mm | ±0.05 | |

容差存在的理由：规格书标注精度 0.05；间距标 0.5 读成 0.65 是错，读成 0.51 不该判错。

## 判据（`check_spec_reading.py`，S 段）

| 判据 | 内容 | 为什么 |
|--|--|--|
| S1 | `cases.json` 每例都要有 `truth/<id>.json` | 没真值就不是考卷 |
| S2 | 真值必须有 `signed_by` + `signed_at` | ★ **防循环论证**：AI 自产自签的真值 = 自己给自己打分 |
| S3 | 真值声明的 `pdf_sha256` == PDF 实际哈希 | 换版 PDF 会让真值静默失效 |
| S4 | 真值里 8 个字段**每个都要出现**（值可为 `null` + `null_reason`） | 「忘了读」和「确实没有」是两件事 |
| S5 | 非 null 真值必须带 `page` | 没有出处的真值是猜的 |
| S6 | 每例都要有 `candidates/<id>.json` | 没有候选读数就没有对照 |
| S7 | 准确率不得低于 `eval/baselines/spec_reading.json` 的 `min_accuracy` | 「不比基线差」，与 geometry_regression 的 R4 同口径 |
| S8 | **定位器不撒谎**（`--with-locate` 才跑）：报 `page` 时必须 `needs_visual=true` 或该页在真值页集合内；`page=null` 时必须给 `candidates`（`ambiguous`）或写明 `scan_only` | 错页/漏页是**静默错**——读错数还算错，页找错了会拿另一页的数当答案 |

S8 要 `import services.pdf_locate`（依赖 `pdfplumber`），必须用**带依赖的解释器**跑；import 失败会报 ERROR、**不静默跳过**（默认不跑，本套件保持纯 stdlib）。

每字段四分类：`correct`（容差内，含 null 对 null）/ `wrong` / `missing`（真值有、候选没读出）/ `spurious`（真值 null、候选硬报）。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `README.md` | 本文件：字段口径、判据、案例表、真值纪律 | 改字段集必须同轮改这里与 `cases.json` |
| `cases.json` | 10 例案例清单（PDF / .dra / 既有 spec / 哈希） | 增删案例必须同步真值、候选与 `eval/README.md` 五套件表 |
| `check_spec_reading.py` | 打分器（S1–S8，四分类，`--update` 冻结基线） | 纯 stdlib，不 import `services`；S8 才延迟 import `services.pdf_locate`（`--with-locate`）；基线写 `eval/baselines/` |
| `truth/<id>.json` | **人工签字**真值（值 + 页码 + 引文 + .dra 交叉核对） | 必须签字才计分；不得由 AI 自行签字 |
| `candidates/<id>.json` | 候选读数（既有 spec 的历史指纹 / 新例的 AI 盲读） | 只读，不许为了「跑绿」回头改候选 |
| `drafts/<id>.json` `drafts/<id>.md` | 起草期证据包（读数 + 页码 + 区域图 + `extracta` 交叉核对） | 供签字人复核；签字后 drafts 保留作追溯 |
| `REVIEW.md` | **人工复核包**（总览 + 每例 8 字段值/页码/引文 + 需裁定条目 + 签字区） | 由 `drafts/` 生成；签字人只看这一份，证据全文再翻 `drafts/<id>.md` |

## 实测（2026-10-15：真值已签字，**这是正式成绩**，基线 `min_accuracy = 0.925`）

10 例 80 个字段：`correct 74 / wrong 3 / missing 0 / spurious 3` = **92.5%**（`signed_by = ds245（对话中确认：10 例全照草案）`）。

- 10 例中 7 例 8/8 与金标准 `.dra` 完全一致（`.dra` 交叉核对用的是 `services.extracta_reader` + 工作区外解释器）；
- **4 个字段的历史 spec 读数与本次新读冲突**（历史 spec 只当候选，谁对由人签）：
  `audio6_cstj421` 的 pitch_x/pitch_y（历史 1.76/1.0，本次判 **null**：6 脚确实无规则间距）+ body_x/body_y（历史 7.59/12.2，本次 6.78/12.0，差在「含不含拨杆」口径）；
  `fpc0510_6p` 的 pin_count（历史 8 = 6 信号脚 + 2 固定脚，本次 6）+ pitch_y（历史 0.5，本次判 null）。
- **读图链路三个既有缺口（本轮实测暴露；1、3 已修，2 记 backlog）**：
  1. ~~`services/pdf_locate.py` 的定位在「图纸页无文本层」时失配~~ → **2026-10-14 已重写为「文本 + 结构」双证据定位**（`_decide()` 纯函数、`DRAWING_RICH_MIN=1000` 判图纸页、并列即 `ambiguous` 拒绝瞎猜；新增 `confidence`/`needs_visual`/`candidates`/`reason`）。同一批 10 例实测：改造前 3 例正确 / **1 例错页**（`fpc0510_6p` 报 p3 正文，真值 p1）/ **1 例静默漏页**（`bga496` 报 p11，真值 p12，两页信号完全相同）/ 5 例 `no_match`；改造后 **6 例给对页、0 例错页、3 例诚实报并列且真值页就在 `candidates` 里**（`bga496` 候选 = p11,p12,p10）、1 例扫描件诚实报 `scan_only`（`lmbr140et1g_2p`）。判据 S8 把这个「不撒谎」变成自动闸门。
  2. `services/pdf_service.probe_pdf()` 对 `form=printed` 的 PDF 报 `drawings:0`，实测同一文件有 3833 条矢量图元（误报，会误导「无矢量可量」的判断）——**未修，backlog**。
  3. `services/pdf_renderer.py` 的 `safe_stem()` 修了「供应商 PDF 目录名以空格结尾 → Windows 吃掉尾空格 → `mkdir` 抛 `FileNotFoundError`」的坑（`tf9` 的 PDF 就是这种名字）；`pdf_service.py` 已改为三处都走 `_safe_stem`。

## 10 例案例（5 例既有 spec + 5 例新增）

| id | 器件 | 来源 | PDF | 金标准 .dra |
|--|--|--|--|--|
| `xtal4_3d2x2d5x0d7` | SMD 晶体 3225 | 既有 spec | `Data/footprint/3S48000163/…20220916_S1.pdf` | `nb_xtal4_3d2x2d5x0d7.dra` |
| `audio6_cstj421` | 3.5mm 耳机座 6P THT | 既有 spec | `Data/footprint/CSTJ-421-1010BG/CSTJ-421-1010BG.pdf` | `nb_audio6_12x7d25x4d3.dra` |
| `lydpj_5p_12x6` | 3.5mm 耳机座 5P THT | 既有 spec | `Data/footprint/LYDPJ-H23CB1Y-053N/…pdf` | `nb_audio5_13d05x6d3x4d2.dra` |
| `fpc0510_6p` | FPC 0.5mm 6P（8 脚） | 既有 spec | `Tools/test/NB_FPC0510_6P_4D77X3D6/…pdf` | `NB_FPC0510_6P_4D77X3D6.dra` |
| `lmbr140et1g_2p` | SOD-323HE 二极管 | 既有 spec | `Tools/test/NB_LMBR140ET1G_2P/LMBR140ET1G.pdf` | `nb_sod-323_2_2d7x1d4x0d75.dra` |
| `type_c24` | USB Type-C 24P | 新增 | `Data/footprint/DB_typec_…/步步精…pdf` | `nb_type_c24_9d17x8d94x3d31_0d5.dra` |
| `hdmi19` | HDMI 19P | 新增 | `Data/footprint/HDMI_CSTHM-1921-1KPD0A/科新城…（C版）.pdf` | `nb_hdmi19_15x12d5x5d55_1.dra` |
| `bga496` | LPDDR5 BGA 496 | 新增 | `Data/footprint/496b_y31m_qdp_8dp_mobile_lpddr5/…pdf` | `nb_bga496_14x12d4x0d77_0d4.dra` |
| `tf9` | TF 卡座 9P 外焊 | 新增 | `Data/footprint/CD.01.02-13-00039Pin H=1.85/…pdf` | `nb_tf9_14d5x16d19x2.dra` |
| `zif30` | FPC 0.5mm 30P | 新增 | `Data/footprint/130-221-300004-W3G--FPC 0.5-30P/…AFC68.pdf` | `nb_zif30_19x4d3x1d75_0d5.dra` |

**为什么是这 10 例**：5 例既有 spec 带**历史读数**（候选），配新读的真值 → 直接暴露 2026-10-09 那批读数的错；
5 例新增都带**金标准 .dra**（人做的封装），是独立于 AI 的人工口径 → 真值能被交叉核对，不是自说自话。
覆盖形态：印刷规格书（多数）/ 纯位图扫描件风险（LMBR、TF）/ 大页数连接器（Type-C、HDMI）/ 大阵列（BGA496）。

## 真值纪律（★ 这是本套件唯一不能妥协的地方）

1. 真值由 AI **起草**（值 + 页码 + 引文 + 区域图），由**人签字**（`signed_by` / `signed_at`）；
   未签字的真值只能 `--allow-unsigned` 自检，输出标 `UNSIGNED`，**不得冻结基线**（`--update` 直接拒绝）。
2. 真值绑定 PDF 哈希；PDF 换版 → S3 报 ERROR，必须重新读、重新签字。
3. 候选**只读**：跑出低分先查读数哪一步错（dpi？页找错？口径混了？），**不许改候选去迎合真值**，也不许调松容差。
4. 存量 spec 只当**候选**，绝不拿来当真值 —— 那正是被考的对象。

## 用法

```bash
# 起草期（真值未签字，能跑但结果标 UNSIGNED）
python eval/suites/spec_reading/check_spec_reading.py --allow-unsigned

# 真值签字后正式跑分
python eval/suites/spec_reading/check_spec_reading.py

# 冻结基线（有未签字真值 / 有 ERROR 会被拒绝）
python eval/suites/spec_reading/check_spec_reading.py --update

# 加跑 S8「定位器不撒谎」（**必须用带 pdfplumber 的解释器**，否则报 ERROR 不静默跳过）
<缓存区>/tools/venv-unconfined/Scripts/python.exe eval/suites/spec_reading/check_spec_reading.py --allow-unsigned --with-locate
```

## 关键约定与坑

- **解释器**：打分器纯 stdlib，系统 `python`（3.10.11）即可；但**读图侧**要用装得下 pymupdf/pdfplumber 的解释器 ——
  本机可用工作区外的副本 `C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe`
  （工作区内解释器受沙箱限制，见 `eval/README.md` 的 ★★ 沙箱坑）。
- **PDF 不在 git 里**：`fpc0510_6p` / `lmbr140et1g_2p` 的 PDF 位于 `Tools/test/`（被 `.gitignore` 的 `Tools/*` 挡住），
  `Data/` 整体在 `layout-forge-mcp` 仓之外 → 真值靠**哈希**钉住，文件丢了无法复核（这是已知的耐久性缺口）。
- **`pitch_x` / `pitch_y` 的口径**要写在真值的 `source_note` 里：THT 连接器取的是相邻信号脚中心距，
  不是外壳脚距；BGA 取 ball pitch（`bga496` 的 0.4 由 `.dra` 名 `…_0d4` 佐证）。
- 基线文件 `eval/baselines/spec_reading.json` **进 git**（与 `eval/runs/` 相反，后者是本地产物）。

## 相关目录

- `eval/README.md` —— 五套件总表与全局纪律
- `eval/suites/geometry_regression/` —— 确定性几何考卷（G1–G6 / R1–R7）
- `eval/suites/behavior/` —— 行为合规夹具（Phase 2-c，待建）
- `layout-forge-mcp/LAYERS.md` —— 五层能力模型与验收命令
