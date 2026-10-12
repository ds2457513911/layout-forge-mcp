# compile-psm —— 出 .psm（Create Symbol）与产物归档

> 收录原 SKILL.md **第七、十三节**。
> 内容：编译的正确姿势、**成功判据为什么必须用"磁盘证据制"**、
> 产物落点为什么是「Allegro 的工作目录」而不是图纸目录、编译日志诊断表，
> 以及封装文件夹的归档规范（含归档事故留档）。
>
> grep 关键词：`axlCompileSymbol`、`返回 nil`、`证据`、`Create symbol completed`、
> `getWorkingDir`、`落点`、`工作目录`、`SPMH`、`归档`、`三件套`

## 七、★ 出 `.psm`（Create Symbol）的正确姿势

**API：`axlCompileSymbol(?symbol t_name ?type t_type)` ⇒ 文档说 `t_symbolName` / `nil`**

### ★★★★★ 纠正：`axlCompileSymbol` **恒返回 nil**（2026-09-29 实测定论）

文档写"成功返回符号名"，**本机（SPB 17.2）不返回**。两种写法都试过，都是 `None`：

```skill
axlCompileSymbol(?symbol "NB_XTAL4_3D2X2D5X0D7" ?type "package")  -> None
axlCompileSymbol(?symbol 'NB_XTAL4_3D2X2D5X0D7  ?type "package")  -> None
```

**但编译确实成功**：`nb_xtal4_3d2x2d5x0d7.psm` 真的落盘（8840 B），日志写着
`Create Symbol of type: PACKAGE` / `Create symbol completed.`

> ⚠️ **上一版技能里"返回值就是路径"是错的**（我曾据此写死判据）。
> 拿返回值当成功判据 → **把成功的编译判成失败** → 触发"失败回滚"
> → **用上一次的备份把刚产出的 `.psm` 盖掉**。
> 这就是"产物凭空消失"的真凶之一，排查了很久。

### ★★★ 正确判据：**以证据为准**（三条独立证据，任一成立即成功）

| # | 证据 | 怎么取 |
|--|--|--|
| ① | 返回值非 nil | 直接看（Allegro 哪天回心转意了仍然认）|
| ② | **编译前后 `.psm` 快照出现新文件 / mtime 变化** | ★ 最硬。编译前对候选目录 `os.stat` 拍快照，编译后再拍一次做差集 |
| ③ | 日志含 `Create symbol completed` 且无 ERROR | 解析 `<symbol>.log` |

```python
# 范式（services/footprint_builder.py::export_psm / _snapshot_psms）
pre  = {p: (st.st_mtime, st.st_size) for p in glob_psms(dirs)}   # 编译前
raw  = eval_skill("axlCompileSymbol(...)")
post = {p: (st.st_mtime, st.st_size) for p in glob_psms(dirs)}   # 编译后
new  = [p for p, sig in post.items() if pre.get(p) != sig]        # ← 真产物
ok   = bool(new) or is_creation_ok(raw) or log_clean
```

★ **文件系统的 mtime 是 Allegro 骗不了证据** —— 一切都以磁盘产物为准。
★ 落点也直接认 `new[0]`（本次真实变动过的那个），比任何返回值/路径推算都可靠。

### ★★★★★ 弹框已被压住 —— 实测结论（2026-09-29 在线跑通）

**别再跟弹框搏斗了。** 在线连续跑三次 `export_psm()`（后两次目标 `.psm`
都已存在 = 覆盖场景），耗时 **0.7s / 0.6s / 0.6s，全程零对话框**，
产出 8644 B，旧件均自动进 `.trash/`。

压住弹框的是**两层机制**：
1. 会话级 `NOCONFIRM` + `noconfirm_savedb` 变量族（进会话自动设、退出自动恢复）
2. 编译前把旧 `.psm` 移入 `.trash/` —— 根本不给它"文件已存在"的机会

> **★★★ 误判纠正**：长期以为"`.psm` 出不来是被弹框挡的"。
> 翻日志才发现，弹框只是偶发，**真实阻断源一直是 Edit Check 报错**：
> `SPMHCS-1`（缺位号）/ `SPMHA1-298`（pin 编号重复）。
> 数据对了编译就过 —— `Tools/log/nb_xtal4_3d2x2d5x0d7.log` 写的就是
> `Create symbol completed.`，同目录 8424 B 的 `.psm` 就是产物。

### 四个条件 & ★ 失败诊断

| 条件 | 不满足的报错 | 怎么修 |
|--|--|--|
| 当前是 symbol editor | 返 nil | 见 5.1c 建图法 |
| **有位号文字，且在 `REF DES/*` 层** | `ERROR(SPMHCS-1)` 缺位号 | 用 `add_text(target="refdes")` |
| **无重复 pin 编号** | `WARNING(SPMHA1-298)` + `ERROR(SPMHA1-291)` | 数 `->pins` 查重 |
| 目标 `.psm` 已存在 | **已解决**（见上，双层机制） | 无需处理 |

**★ `axlCompileSymbol` 失败时只返 nil，不给原因** —— 原因全在
**`<symbol>.log`**（同目录，或已被 `collect_run_logs` 归集到 `Tools/log`，
归集后会改名成 `<stem>_20260929-155731.log`）。

**不读日志就只能瞎猜 —— 本项目就猜偏过一整轮。** 实现范式：

```python
# services/footprint_builder.py::_scan_symbol_compile_log()
#   1) 候选目录：drawing 目录 + 其父目录 + <Tools>/log
#   2) 候选文件：<stem>.log 与 <stem>_<时间戳>.log（前缀匹配，兼容归集版）
#   3) 正则解析：^\s*(ERROR|WARNING)\s*\(\s*([A-Za-z0-9][A-Za-z0-9\-]*)\s*\)\s*:\s*(.*)$
#   4) 每个码附「人话解释 + 处方」→ 落在返回值 log_diagnosis 字段
```

已收录的错误码 → 处方映射（`_COMPILE_LOG_CODES`）：

| 码 | 含义 | 处方 |
|--|--|--|
| `SPMHCS-1` | 缺位号 | 往 `REF DES/SILKSCREEN_TOP` 放文字 |
| `SPMHA1-298` | pin 编号重复 | 逐个核对编号 |
| `SPMHA1-301` | DRC 未过（多与 298 连发） | 看同批 WARNING |
| `SPMHA1-291` | Create symbol 被中止 | 先修上面的错 |
| `SPMHA1-70` | 图形超 extents | 先放大 extents |
| `SPMHGE-268` / `-7` | `allegro.jrl` 里的外层症状 | 去看 `<symbol>.log` |

### ★★★ 产物落点 = **Allegro 的工作目录**，不是 `.dra` 目录（实测踩过）

`create symbol` 把 `.psm` 输出到 **Allegro 的当前工作目录（cwd）**：

```
.dra  在  Tools/test/NB_XTAL4_3D2X2D5X0D7.dra
.psm  落在 Tools/nb_xtal4_3d2x2d5x0d7.psm        ← 跑到父目录去了
```

同样的规则也适用于 `axlSaveDesign`：
**它写的是 cwd，完全不理会 `axlOpenDesign` 里给的那个目录。**
后果极具欺骗性 —— 返回的 `dra_path` 看着完全正常，
但那个文件**从头到尾没被写过**（md5 与模板一模一样），真产物悄悄躺在别处。

### ★★ 工作目录怎么读、怎么改（实测唯一可行的路）

| 目的 | API | 实测 |
|--|--|--|
| **读** cwd | `getWorkingDir()` | ✅ 存在，返回正斜杠路径 |
| **改** cwd | `axlShell("cd <dir>")` | ✅ **真的会改**（`getWorkingDir()` 复读确认），`cd` 是内建命令**不弹框** |
| 改 cwd（其它试法） | `setWorkingDir` / `axlSetWorkingDir` / `cdsSetWorkingDir` / `chdir` | ❌ 全是 `undefined function` |

★ 改完**必须用 `getWorkingDir()` 复读校验**，别看 `axlShell` 的返回值。
★ **实测跨会话保持**：一条命令里 `cd` 过之后，**另起一个 Python 进程**再读
`getWorkingDir()`，仍是改过的目录。

### ★★★ 防错位的正确姿势：**编译前断言**，不是"编译后刷新"

```
1. 生产工具入口断言：getWorkingDir() == 目标目录？
      不一致 → axlShell("cd <目标>") 纠偏 → 复读校验
2. 落点仍以「编译前后 .psm 快照差异」为准（见上）
```

> ★ **为什么不是"编译后调一次刷新"**：后置刷新**只能改 cwd，
> 救不回已经写错位置的文件**。防错位的那一刀必须在**编译前**落下。
> 而且快照证据已经把"落错地方"变成**可检测**的 —— 刷新只是心理安慰。

### ★★ `axlSaveDesign` 的坑：对**未修改**的图纸是空操作

实测：`axlSaveDesign(?noConfirm t)` 返回设计名、0.1s 返回，
但 `.dra` 的 **mtime 和大小都没变** —— 没写盘。

- 图纸无改动 → save 是 no-op（正常行为）
- ⚠️ **但"加过图元后再 save"也观察到没写盘**（`grep TMPDBG *.dra` = 0 命中），
  原因未查明。**不要依赖 save 的副作用**（如"存盘自动出 .psm"）做交付判据，
  一切以**磁盘产物**为准。

### 验证产物（唯一可信的判据）

```
<Tools>/<name>.log     ← Allegro 编译日志，关键证据
    成功: "Create symbol completed."
    失败: "ERROR(SPMHCS-1)... / ERROR(SPMHA1-291)..."

<name>.psm             ← 真产物；★ 用 mtime + size 双重确认是本次新出的
```

### ★★ 位号层名（逐条 `axlLayerGet` 实测）

| 层名 | 有效 |
|--|--|
| `REF DES/SILKSCREEN_TOP` | ✅ ← 用这个 |
| `REF DES/ASSEMBLY_TOP` | ✅ |
| `PACKAGE GEOMETRY/PLACE_TOP` | ❌ 返 None |
| `PACKAGE GEOMETRY/SILKSCREEN_TOP` | ✅ 有效，但**不认**是 refdes |

**关键认知**：图纸里"有 `U*` 文字" ≠ "有位号" —— 必须在 `REF DES/*` 类层。

### ★★ 覆盖保护

`axlCompileSymbol` 只有 `?symbol` / `?type`，**没有 `?noConfirm`**。
目标 `.psm` 已存在时会弹覆盖框卡死通道 → **编译前必须先把旧 `.psm` 挪开**。

★ **但严禁 `os.remove`** —— 编译失败就再也拿不回来了（真实丢过一个 8640 B 的产物）。
用 **quarantine**：搬进 `.trash/`，成功留作备份、失败自动搬回。见第三节"禁区"。
本项目返回值里的 `previous_psm_backup` 就是隔离件路径。

### ★★ 图纸污染陷阱（两层）

图元创建是**叠加**不是替换。同一张图纸反复跑会累积重复 pin，
编译时报 `duplicate pin numbers`（`WARNING(SPMHA1-298)` → `ERROR(SPMHA1-291)`）。

**① 建图前 —— 图纸本身要干净**
批量前先数一下 `length(axlDBGetDesign()->pins)`；
或先用 `axlDeleteByLayer` + `axlDeleteObject` 清空（见第五节）。

**② 建图时 —— 模板本身可能就是脏的**
用"模板复制法"建图纸时，若模板自带图元，脏东西会被原样带进新图纸。
工作目录下常躺着 `probe_test.dra` / `X.dra` 这类**被反复读写污染过的探针图纸**。
→ 打开模板后**立刻数一遍**，非空就拒绝：

```skill
;; 可用属性只有三个：pins / text / padstacks
;; ★ lines / shapes / rectangles 不在 axlDBGetDesign() 属性表里（读恒为 0，不能当判据）
;; ★ 必须判空 —— 属性为空时是 nil，直接 length(nil) 会报错
(lambda((x) if(listp(x) length(x) if(x 1 0))) axlDBGetDesign()->pins)
```

> ★ lambda 形参名**避开 `t` / `lst`**（SKILL 里有特殊语义）。
> 本项目实现：`services/skill_snippets.py::build_content_count()`。

---


## 十三、★★★ 产物归档：封装文件夹规范（2026-09-30 用户裁决）

> ★ **唯一正确的归档口径**：**以「规格书所在目录」为锚点**，在那里建封装文件夹，
> 把规格书**移进去**，生成的 `.dra` / `.pad` / `.psm` **也全部放进去**。
> 一句话：**一个封装 = 一个与符号同名的文件夹，规格书与三件套同处一室**。

### 13.1 规则（照做，别自由发挥）

```
<规格书所在目录>/
└── <符号名>/                 ← ★ 新建的封装文件夹（与 .dra 同名，不含扩展名）
    ├── <符号名>.dra          ← 生成：封装图纸
    ├── <符号名>.psm          ← 生成：编译后的符号（Allegro 实际调用它）
    ├── <padstack>.pad        ← 生成：焊盘栈（如 r1d4x1d2.pad）
    ├── <规格书>.pdf          ← ★ 从上级目录**移入**（move，不是 copy）
    └── README.md             ← 目录说明（清单 + md5 + 校验记录）
```

1. **锚点是规格书**，不是库目录根、也不是 `Tools\test` 根目录。
   规格书在哪一级，封装文件夹就建在哪一级。
2. **规格书用「移动」**（`shutil.move`），不要留在原处产生两份。
   ★ 例外（用户 2026-09-30 11:43 裁决）：**还不是正式项目/过渡阶段可以先只 `copy`**
   —— 原件留在规格书原位，封装文件夹里放一份副本即可，等正式建库时再 move。
   移动或复制前都先确认目标文件夹里没有同名 PDF（有就先隔离旧的）。
3. `.dra` / `.pad` / `.psm` **必须同目录** —— 本项目事实约定：
   `.dra` 自带 padstack 副本 → **不需要挂 `padpath`/`psmpath`**（见 §5.4.4）。
   落盘后用 `library_install.verify_colocation()` 复核。
4. 归档完**写 `README.md`**：文件清单 + md5 + 联机校验结论 + 规格书出处
   （用户目录纪律：每个目录都要有说明，改完就更新）。

### 13.2 ★ 禁区（2026-09-30 事故 + **2026-10-09 用户更新口径**）

> ★ **2026-10-09 用户裁决（覆盖旧第一条）**：
>   归档锚点 = **规格书所在目录**，在其下建**与符号同名的文件夹**，
>   自动封装任务生成的文件放进这个文件夹。
>
>   原第一条「禁放进 `Data\footprint\<料号>\` 根目录」的**理由**（那里躺着人工
>   金标准、会被替换）**已不作为主口径**；但**产物仍不得平铺在规格书目录本身** ——
>   必须进 `<符号名>/` 子目录：平铺会与规格书 PDF 混在一起，且规格书目录下
>   往往仍有人工作为参照物，压掉一样没得对比。

| 禁区 | 后果 |
|--|--|
| 把生成件**平铺进规格书目录本身**（不建 `<符号名>/` 子目录） | 与规格书 PDF 混放；若同目录有人参照物，Windows 下文件名只差大小写即**同名**，会被替换掉（见 §3「往库目录归档默认不要覆盖」） |
| 不备份就 `overwrite=True` | 参照物丢失 → 后续对拍变成"自己跟自己比"。**先备份到缓存区**再覆盖 |
| 用 `os.remove` 挪旧件 | 一律隔离到 `.trash/`（§3 已定） |
| 忽略**文件名大小写** | 覆盖会把目标文件名改成源名的大写形式（`nb_…dra` → `NB_…dra`），回滚时**连名字一起修** |

### 13.3 当前实例（照抄这个形状）

- 规格书位置：`C:\Users\ds245\Documents\Work_Bmorn\Layout\Data\footprint\<料号>\<料号>产品规格书_*.pdf`
  （实测 13/13 料号都是"规格书与封装同目录"）
- 所以封装文件夹应为 **`Data\footprint\3S48000163\NB_XTAL4_3D2X2D5X0D7\`**，PDF **移入**其中。
- 生成件来源：`layout-forge-mcp\Tools\test\`（`Tools/e2e_<料号>.py` 的产物）。

### 13.4 可复用流程（幂等）

```bash
# 0) 离线自检（改过代码必须先跑这两样，★ 用项目 venv）
./.venv/Scripts/python.exe Tools/selfcheck_geometry.py      # 180/180
./.venv/Scripts/python.exe Tools/selfcheck_skill_syntax.py   # 212/212

# 1) 联机生成 + 校验（Allegro 必须在跑，见第十一节）
./.venv/Scripts/python.exe Tools/e2e_<料号>.py               # 出 .dra/.psm/.pad
./.venv/Scripts/python.exe Tools/online_calibrate.py         # 单位/位号/视图中心/形态 全绿

# 2) 归档：规格书所在目录 → 建 <符号名>/ → 移 PDF + 放三件套 + 写 README
#    （封装文件夹内 dra+pad 同目录；PDF 用 move 不是 copy）
#
#    ★ 2026-10-09 起可走 MCP（落点 = lib_root/<pn>/，传对参数即合口径）：
#      install_shadow_library(symbol_name="<符号名>", pn="<符号名>",
#                             lib_root="<规格书所在目录>", overwrite=False)
#      → 先 dry_run=True 看会装什么。它只装 .dra/.psm/.pad，
#        **PDF 移入 + 写 README 仍需手工/CLI 补**。

# 3) 收尾：**必须关 Allegro**（残留 CWD 会改变下一次 save 的落点）
./.venv/Scripts/python.exe Tools/stop_allegro.py
```

### 13.5 三件套是什么（给不看文档的人）

| 文件 | 本质 | 谁用 |
|--|--|--|
| `.pad` | **焊盘栈**——单个引脚的铜皮/阻焊/钢网形状（可复用，与符号无关） | Padstack Editor；被 `.dra` 引用 |
| `.dra` | **封装图纸**——焊盘摆放 + 丝印 + 装配层 + Place_Bound + 位号（可编辑源文件） | Package Symbol Editor |
| `.psm` | **编译后的符号**——由 `.dra` 编译生成，放进板子的**实际是这个** | Allegro 布局时调用 |

★ 交付顺序：`.pad` → `.dra` → `.psm`；**改了 `.dra` 必须重新编译 `.psm`**，
否则板子上用的还是旧的。详见 §7（出 `.psm` 的正确姿势）。

### 13.6 事故留档：误归档 → 回滚（2026-09-30）

- 误把生成件装进 `Data\footprint\3S48000163\` 根目录，替换了人工金标准。
- 回滚脚本：`cache\scratch\rollback_and_relocate.py`
  （**先备份 → 隔离 → 恢复 → md5 对拍 → 修文件名大小写**）。
- 金标恢复后 md5：`nb_…dra` `38c0b301…` / `nb_…psm` `f668fb6c…` / `r1d4x1d2.pad` `7bd56338…`。
- ★ 教训：**动手前先确认"目标目录到底指哪儿"**——"封装自己的文件夹"指的是
  **新建的同名子目录**，不是已有库目录。

