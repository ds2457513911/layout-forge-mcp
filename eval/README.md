# eval/ —— L5 度量与评测层（准确率 / 回归 / 基线）

> 最后更新：2026-10-15（★ 补两条编码纪律：**非 ASCII 标记要自己 reconfigure UTF-8** —— GBK 控制台下
> `print("✓")` 抛 `UnicodeEncodeError` 让「五套全绿」反而 exit 1，六个入口都加了 `_force_utf8_stdout()`；
> **汇总入口与各套件必须能单独手跑**。Phase 4（票 #10 / D4）：`suites/behavior/` 新增 **B6** —— 守 `services/channel_guard.py` 的通道锁与建图租约（撞锁报 `CHANNEL_BUSY` + 持有人、同进程第二线程按超时返回、`taskkill /F` 后锁自动回收、租约撞上只报告不排队），夹具 **6/6 通过**。此前：Phase 3：`suites/behavior/` 新增 **B5** —— 守 `orchestration/playbook.json` 的硬约定（25 条 `rule_id` + 13 条 `G-*` 门禁，每条都带「检测方式 + 后果类别」）与 `orchestration/pipeline.py` 状态机的放行/拦停；夹具 **5/5 通过**。此前：Phase 2-d：新增统一入口 **`check_eval.py`** —— 五套断言一条命令跑完，输出总表；缺依赖记 ERROR 而**不静默跳过**，每套以退出码为准、JSON 解析失败也记 ERROR（E1–E4）；同日接入 `docs/SOP-工程维护手册.md` 发布检查。此前：Phase 2-c：`suites/behavior/` 四个行为夹具落地 —— 把 SKILL 的 S1/S2/S4 硬规则变成可执行断言，B1–B4 全绿；同日把 `orchestration/preflight.py` 的噪声规则从「AUTOSAVE / 备份后缀」扩到「Allegro 运行痕迹 + 历史检查报告」并公开 `is_noise()` —— 扩展后 `geometry_regression` 的 **G4 先转红、当日清理后复绿**（原形 **145 个**噪声文件：110 个 `*.log` + 9 个 `checklist_report_*.json` + 26 个运行痕迹 `allegro.jrl` / `master.tag` / `padstack_editor.jrl`）；清理走 `.trash/`，噪声名单与判据一个字没放宽。此前：Phase 2-b `suites/spec_reading/` 落地 + 10 例真值签字 + S8「定位器不撒谎」）

## 这个目录是干什么的

把「改一下读图策略 / 调一下 dpi / 换个模型」从**试**变成**量**。

**为什么这是唯一的真空白**（2026-10-14 实测）：

| 事实 | 含义 |
|--|--|
| 全库**没有任何读图准确率记录** | 「AI 从 PDF 读出参数」这一步从来没被量化过 |
| 253 件金标准只覆盖**确定性几何**（`.dra` 对拍） | 它守得住求解器，守不住读图 |
| `knowledge/specs/` 有 5 份 spec | 它们是**产物不是真值** —— 拿 AI 自产 spec 当真值 = 循环论证 |

## 五套件

| 套件 | 守什么 | 判定方式 | 阶段 |
|--|--|--|--|
| `suites/geometry_regression/` | L1 确定性几何不退化 | **G1–G6 + R1–R4**：包住 **239** 项几何自检 + **222** 项 SKILL 语法自检 + **35** 项约定自检 + **253** 件金标准对拍，分数==基线；**G6 = 被调方探针**（extracta 四路取数健康，被沙箱掐住时会在这里红，而不是让 R4 悄悄假红）；基线 `baselines/geometry_regression.json` | **Phase 2-a ✅ 已就位** |
| `suites/spec_reading/` | **读规格书准确率** | canonical **8 字段**（= `knowledge/specs` 的 `fingerprint` 键，不另造 schema）+ **S1–S8**：每字段四分类 correct / wrong / missing / spurious，容差 body·pad ±0.05 mm、pitch ±0.02 mm；★ **S2 真值必须人工签字**（AI 自签 = 循环论证）、S3 真值绑 PDF 哈希、S5 非 null 真值必须带页码、**S8 定位器不撒谎**（`--with-locate`）；基线 `baselines/spec_reading.json` | **✅ 2026-10-15：10 例真值已签字、正式 92.5%、基线已冻结**（80 字段：correct 74 / wrong 3 / spurious 3） |
| `suites/behavior/` | L3 规则真的可执行 | **B1–B6 行为夹具**（把 SKILL 的 S 系硬规则 + L3 状态机造成可控临时目录，逼工具层表态）：B1 目录多份 `.dra`/PDF → 必须 `ambiguous` + `confirm` + 候选列全；B2 只有历史 `checklist_report_*.json` → 不算证据、判缺料 `stop`；B3 `AUTOSAVE.dra` → 剔除且不算候选；B4 **先探通道** → `connected=False` 时 `error` 必须非空（禁把没通道说成「没工具」）；**B5 L3 状态机 + `playbook.json` 硬约定** → 每条规则都带检测方式与后果类别、`pipeline.run()` 放行/拦停正确（歧义必须 `confirm` 且**停在 preflight 不许继续取数**、通道不可用必须 `stop` + `CHANNEL_NOT_READY`）、`stale_rule_ids`（已删除的 `6.5`）必须被单独点名。**无基线**（是断言套件，像 architecture） | **Phase 2-c ✅ 4 条 / Phase 3 ✅ B5 / Phase 4 ✅ B6 D4 通道锁（6/6 通过）** |
| `suites/tool_contract/` | L2 工具契约不破 | **C1–C8 静态断言**：工具数量 / 名字集合（改名=破坏）= 基线、每个工具有 docstring、每个参数名写进 docstring、遥测字段口径、`server.py` 是否挂了 `TelemetryMiddleware`、**C7 每个工具都在 `knowledge/tool_groups.json` 里有分组**、**C8 生成的 `docs/tools-catalog.md` 与代码一致（不陈旧）**；基线 `baselines/tool_contract.json` | **Phase 1 ✅ 已就位** |
| `suites/architecture/` | 层方向 + 文档覆盖 | `check_architecture.py`：**A1–A7**（层方向 / 登记 / README 覆盖 / **A7 解析失败必须可见**）+ W1–W3 | **Phase 0 ✅ 已就位** |

## 一条命令跑完（Phase 2-d）

```bash
python eval/check_eval.py            # 五套全跑（含 253 件对拍，约 2 分钟）
python eval/check_eval.py --fast     # 冒烟：geometry 跳过 253 件对拍
python eval/check_eval.py --only architecture,behavior
python eval/check_eval.py --json     # 机器可读汇总
python eval/check_eval.py --list     # 只列套件
python eval/check_eval.py --show-fail  # 红了就附打该套件输出尾部
```

`eval/check_eval.py` **不是又一个真源**：判据仍写在各自套件里（A1–A7 / C1–C6 / B1–B6 / S1–S8 / G1–G6 + R1–R4），
它只负责**跑全 + 计数 + 汇总**，不复制任何阈值。自己的元判据：

| 号 | 判据 |
|----|------|
| E1 | 五套件都必须跑到：缺依赖（如 `skillbridge`）→ 记 ERROR 并给修复提示，**不静默跳过** |
| E2 | 每套退出码必须为 0（退出码是唯一权威，不靠解析人话） |
| E3 | 每套 `--json` 必须能解析：解析失败 = ERROR（否则措辞一变汇总就悄悄少一行） |
| E4 | 实测行数必须等于计划行数（少一行说明有套件没跑起来） |

它按与 `geometry_regression` 相同的顺序自选解释器（`--python` → `$LAYOUT_FORGE_PY` → 项目 `.venv` →
缓存区不受限副本 → 兄弟仓 `.venv` → `sys.executable`），并在表头打出选中的是哪一个。

## 真值与基线纪律（★ 本层唯一的坑）

1. **真值必须人工核过，并标注规格书页码**。禁拿 AI 自产 spec 转正 —— 那是循环论证。
2. **起步 10 例**：现有 5 份 spec 逐字段复核后转正 + 新增 5 例。
   新增优先挑**几何形态差异大**的：Type-C 24P（多排 + 外壳脚）/ HDMI19 / BGA496 / TF 卡座（含 NPTH 定位柱）/ FPC 0.5-30P。
3. 每次跑出的结果与 `baselines/` 的**冻结值比差值**，不是只报绝对值。
4. **换模型 / 改 prompt / 调 dpi 之前必须先跑 `spec_reading` 并与基线对比**（这条纪律同时写进 SKILL，见 Phase 3）。

## 用法（三套断言现在就能跑，其余 Phase 2 生效）

```bash
# ★ 最常用：一条命令跑完五套（Phase 2-d）
python eval/check_eval.py --fast      # 冒烟；去掉 --fast 则含 253 件对拍
```

```bash
# 架构断言（Phase 0 已可用，纯 stdlib，任何 Python 3.10+ 都能跑）
python eval/suites/architecture/check_architecture.py
#   A1–A7：层方向 / 登记 / README 覆盖 / 可解析。ERROR 必须为 0（W3 是已声明偏差）

# 工具契约断言（Phase 1 已可用，同样纯 stdlib）
python eval/suites/tool_contract/check_tool_contract.py
python eval/suites/tool_contract/check_tool_contract.py --update   # 改完契约后重新冻结基线
python eval/suites/tool_contract/check_tool_contract.py --write-catalog   # 重生成 docs/tools-catalog.md（唯一的人读工具清单）
#   C1 工具数（现 36）/ C2 名字集合 / C3 docstring / C4 参数写进 docstring / C5 遥测口径 / C6 中间件接线

# 下面这些要项目依赖（skillbridge 等），必须用**装了依赖的解释器**
python Tools/selfcheck_geometry.py
python Tools/selfcheck_skill_syntax.py

# 几何回归评测（Phase 2-a 已可用；R1/R2 + G6/R4 要「带依赖且在工作区外」的解释器，套件会自己探测）
python eval/suites/geometry_regression/check_geometry_regression.py
python eval/suites/geometry_regression/check_geometry_regression.py --skip-benchmark   # 冒烟：不跑 253 件
python eval/suites/geometry_regression/check_geometry_regression.py --update           # 重新冻结基线
python eval/suites/geometry_regression/check_geometry_regression.py --reuse-benchmark Tools/log/golden_benchmark/stdout_*.txt
#   G1–G6 盘点（件数/目录数/specs/cases/噪声 + extracta 被调方探针）+ R1–R4 跑分（239 / 222 / 35 / 253 件对拍）
#   R4 的计数只认 benchmark_summary.csv（控制台把「空结论」与「提取失败」混成一个数），语义是「不比基线差」

# 读图准确率（Phase 2-b；打分器纯 stdlib，起草期用 --allow-unsigned）
python eval/suites/spec_reading/check_spec_reading.py --allow-unsigned   # 真值未签字，结果标 UNSIGNED
python eval/suites/spec_reading/check_spec_reading.py                    # 真值签字后正式跑分
python eval/suites/spec_reading/check_spec_reading.py --update           # 冻结基线（有未签字真值会拒绝）
python eval/suites/spec_reading/check_spec_reading.py --with-locate      # 加跑 S8（★ 需带 pdfplumber 的解释器）
#   S1 每例有真值 / S2 真值已签字 / S3 PDF 哈希符合 / S4 8 字段齐全 / S5 非 null 带页码 / S6 有候选读数 / S7 不低于基线 / S8 定位器不撒谎
#   四分类 correct / wrong / missing / spurious；真值 = truth/<id>.json，候选 = candidates/<id>.json

# 行为夹具（Phase 2-c 四条 + Phase 3 B5 + Phase 4 B6；纯 stdlib，断言套件、无基线）
python eval/suites/behavior/check_behavior.py
python eval/suites/behavior/check_behavior.py --python <带 skillbridge 的解释器>   # B4 探通道 / B6 通道锁用
#   B1 S1 歧义可见 / B2 S2 旧报告不算证据 / B3 S1 噪声剔除 / B4 S4 通道先探且不许沉默
#   B5 L3 状态机：playbook 硬约定 + pipeline 放行/拦停（歧义 confirm 且停住 / 无通道 stop）
#   B6 D4 通道锁：同进程第二线程按超时报 BUSY / 跨进程 BUSY 带持有人 / taskkill 后锁自动回收 /
#      建图租约撞上立刻返回持有人（不排队）/ 会话 open 持锁、close 放锁、嵌套不漏锁
```

## 已验证基线（2026-10-15 · Phase 2-c 实测）

| 命令 | 结果 | 退出码 |
|--|--|--|
| `python eval/check_eval.py` | ★ **五套全绿 / 红 0 套，用时 99 s**（geometry 96 s）；解释器自动选中「工作区外 + 有依赖」的缓存区副本 | 0 |
| `python eval/suites/architecture/check_architecture.py` | **ERROR 0 项 / WARN 1 项**（W3 是已声明偏差：`services/skillbridge_writer.py:195` 反向 import `mcp_server.config`） | 0 |
| `python eval/suites/tool_contract/check_tool_contract.py` | **ERROR 0 项 / WARN 0 项，全绿**（数量与名字对照冻结基线 `baselines/tool_contract.json`；分组真源 `knowledge/tool_groups.json` 由 C7 守、人读目录 `docs/tools-catalog.md` 由 C8 守） | 0 |
| `python eval/suites/behavior/check_behavior.py` | **夹具 5/5 通过；ERROR 0 项**（B4 实测 `connected=False` 且 `error` 非空 → 通道未连如实上报；B5 实测歧义 `confirm` 且停在 preflight、无通道 `stop` + `CHANNEL_NOT_READY`）；耗时约 1 s（B4 用工作区外解释器）。**2026-11-19 追加**：B6 接入后 **6/6 通过、ERROR 0 项**，B6 实测 18 项断言全过（同线程 0.51 s 报 BUSY、跨进程 0.61 s、`taskkill /F` 后 0.00 s 重取、撞租约 0.008 s 返回 `drawing=NB_XTAL`） | 0 |
| `python eval/suites/spec_reading/check_spec_reading.py` | **总准确率 92.5%**（80 字段：correct 74 / wrong 3 / spurious 3），S1–S8 通过；基线 `baselines/spec_reading.json` | 0 |
| `python eval/suites/geometry_regression/check_geometry_regression.py` | **ERROR 0 项 / WARN 0 项，全绿**（噪声 0 —— 2026-10-15 噪声名单扩展后曾现形 145 个、当日全部隔离清理；253 / 14 目录 / specs 5 / cases 10；239/239、222/222、35/35；extracta 四路由产出；对拍 253 件 PASS 7 / FAIL 81 / 例外 231 / 提取失败 17 / exact 0） | 0 |
| `python Tools/selfcheck_geometry.py` | **239/239 全部通过 ✓** | 0 |
| `python Tools/selfcheck_skill_syntax.py` | **222/222 通过, 0 失败** | 0 |
| `python Tools/selfcheck_conventions.py` | **35/35 全部通过 ✓** | 0 |
| `python -m observability.stats` | 遥测 **run 1 / 事件 3 / 坏行 0**；规则报告 5 份 85 条（最常 FAIL `5.2`） | 0 |

★ **G4 那次红是真发现，不是误报（2026-10-15 当日已清理复绿）**：这些噪声文件长期躺在三个库里（`allegro.jrl` / `master.tag` / `nb_*.log` / `checklist_report_*.json`），
证明**有人把金标准库目录直接当 Allegro 工作目录用过**（违反 `../Data/README.md` 的既有约定），而 S2 想防的「旧报告被当成本次证据」在这个库里早就有物证。
★ 处置：用 `services.footprint_builder._quarantine_file()` 把 **145 个全部移进 `layout-forge-mcp/.trash/`**（**绝不 `os.remove`**；从 `.trash/` 搬回即可恢复），复跑 G4 = 噪声 0。**噪声名单与 G4 判据一个字没放宽** —— 先改事实，再复跑。

★★ **沙箱坑（2026-10-14 实测，会让「253 件对拍」整片假红）**：**解释器文件在工作区内还是工作区外，会改变 extracta 的行为**。同一份 `python.exe`（sha256 一致）驱动同一个探针 —— 工作区内的（`layout-check-mcp/.venv`，或在工作区里新建的 venv）→ extracta 在 PAD_DEF 视图崩（rc=`3221226505`/0xC0000409），`pads.txt` 0 字节、几何与位号缺失，253 件里 PASS 0 / FAIL 236；工作区外的（系统 python / `%TEMP%` 新建的 venv / 缓存区里的 venv 副本）→ 四路全出（pins 420 / pads 1472 / geom 2552 / refdes 544 字节），253 件里 PASS 7 / FAIL 81。同一时刻 pwsh 直接调 extracta 也四路全出 ⇒ **不是 Cadence 坏了，是被 DSH 沙箱限制的进程在驱动它**（受限解释器还会 `FileNotFoundError: No usable temporary directory`，并把临时目录退回**当前工作目录** —— 项目根会长出 `try_*` / `repeat_*` 目录，见到就按缓存区纪律挪走）。套件因此把「工作区外的 venv 副本」排进解释器探测顺序（`LAYOUT_FORGE_UNCONFINED_PY` 可覆盖），并加了 **G6 被调方探针**：先把 extracta 染红在这里，而不是让 R4 悄悄给出好看的假数字。可用 `--python` / `LAYOUT_FORGE_PY` 指定。

★ **解释器坑（2026-10-14 实测，别照抄 `.venv/Scripts/python.exe`）**：本机 `layout-forge-mcp\.venv\Scripts\python.exe` **不存在** —— 而 WorkBuddy 的 MCP 配置正是指向它（`uv.lock` 在，`uv sync` 可重建；标准建库步骤见 `../docs/SOP-工程维护手册.md:121-125`）；当前能跑通全部自检的解释器是兄弟仓的 `..\layout-check-mcp\.venv\Scripts\python.exe`（Python 3.10.11，已装 `skillbridge`），以及缓存区里那份工作区外的副本。用系统 `python`（3.10.11）跑后两条会 `ImportError: 请先安装 skillbridge`。

★ **既有自检不是零依赖**：`Tools/selfcheck_geometry.py:552` 与 `Tools/selfcheck_skill_syntax.py:648` 在检查项内部 `from services import footprint_builder`，经 `services/skillbridge_writer.py:31` 拉进第三方包 `skillbridge`；缺它就直接抛错中断整轮，跑不到结尾的 239/239。所以「离线」只等于**不连 Allegro**，不等于零依赖 —— 这也是架构套件刻意做成纯 stdlib 的原因。

## 关键约定与坑

1. `runs/` **不进 git**（每次产物），`baselines/` **进 git**（它是「上一次被认可的行为」的唯一记录）。现有基线：`baselines/tool_contract.json`（工具契约快照 + 遥测口径）、`baselines/geometry_regression.json`（资产件数 + 253 件对拍的 CSV 计数 + 四套既有检查的分数 + extracta 探针结论）、`baselines/spec_reading.json`（**2026-10-15 冻结：10 例 / 80 字段 / 准确率 92.5%**，真值已人工签字）。
2. 本层是**测试代码**：可以 import 项目任何模块；但**绝不被项目源码 import**（生产代码依赖测试 = 层方向反了）。
3. 真值文件里**只放数据 + 出处**（页码/区域），**不放结论性描述**，否则无法自动打分。
4. 用例路径以 `../../Data/footprint/<PN>/` 相对引用，**不复制客户 PDF 进仓库**。
5. **变红先改事实，不许调松阈值**：断言红了只有两条路 —— 修代码，或（确认是新契约后）显式 `--update` / 改基线与登记表。★ 悄悄把阈值放宽等于把闸门拆了。
6. **回归类套件的分数只在「工作区外的解释器」下有意义**：被沙箱限制的解释器会把被测工具（extracta）弄崩，得到的是环境分数不是产品分数。G6 就是为此存在的：它先红，别去调 R4 的阈值。
7. **R4 不比基线差 ≠ 全 0**：金标准库里本来就有 17 件提取不出（`extracta pins.txt 输出为空`，253 件里的既有状况）。要求全 0 会让闸门永远红着 ＝ 没有闸门。
8. **复用常量 ≠ 复用规则**（2026-10-15 踩到）：`check_geometry_regression.py` 原来自取 `NOISE_NAMES/PREFIXES/SUFFIXES` 三个常量自拼判断，`preflight` 新增 `NOISE_GLOBS` 后它**看不见新规则** —— 同一批目录，G4 报 26、preflight 报 145。现在两边都调 `orchestration.preflight.is_noise()`（单一真源）。**判定逻辑不要抄常量，要调同一个函数。**
9. ★ **非 ASCII 标记要自己给自己开 UTF-8**（2026-10-15 踩到）：GBK 控制台下 `print("✅")`/`print("✓")` 直接抛
   `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`，进程 **exit 1**。
   更阴的是**只有绿色分支打 ✓**、红色分支打 ASCII `[X]` —— 于是「红了正常退出、全绿反而崩」，
   而人只看最后几行输出，很容易把它当成"环境问题"。判据由 E2（只看退出码）抓：
   **五套都绿但汇总命令 exit 1** 就是这类 bug 的签名。
   修法：每个会被**单独手跑**的入口在自己进程里 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
   （`check_eval.py` + 五个套件都有 `_force_utf8_stdout()`）；给子进程注入 `PYTHONIOENCODING=utf-8`
   **不能替代**这一点 —— 那只保护子进程。
10. **汇总入口要能被单独跑**（2026-10-15）：`eval/check_eval.py` 与各套件都必须**独立可执行**
    （SOP 排障路径就是单跑某一套）—— 只在 `check_eval` 里能跑通的套件等于排障时没有工具。

## 相关目录

| 目录 | 关系 |
|--|--|
| `../Data/footprint/` | 14 个带规格书的封装目录 = 真值来源（金标准模板库，**默认不覆盖**） |
| `../knowledge/specs/` | 待逐字段复核转正的 5 份 spec |
| `../observability/` | 耗时与步骤字段 |
| `../orchestration/` | behavior 套件测的就是它的 findings |
| `../Tools/selfcheck_geometry.py` `selfcheck_skill_syntax.py` `benchmark_golden.py` `compare_extracta.py` | 被 `geometry_regression` 套件**包住**的既有基线（不搬家） |
