# api-facts —— API 事实核查

> 收录原 SKILL.md **第一、六、八、十二节**。
> 用途：查「这个 AXL 函数到底存不存在」「17.2 实测与文档对不上怎么办」
> 「官方手册在哪」「别人怎么写的」。
>
> grep 关键词：`函数存在性`、`unbound`、`真调用`、`版本差异`、`axlSetDesignType`、
> `手册`、`algroskill`、`extracta`、`开源`、`社区`

## 一、★ 探测 AXL 函数存在性（方法学，三轮踩坑才定论）

### 错误做法（都会给出错误结论）

| 做法 | 为什么错 |
|--|--|
| `errset(axlFoo)` 裸符号 | **恒报** `unbound variable` —— 连 `axlSaveDesign`（明明能用）也报 |
| 发裸名字 `axlFoo` | 同上。Allegro 侧 `evalstring` 把裸符号当**变量**求值，必 unbound |
| 只查 Cadence 文档 | 文档有 ≠ 运行时绑定（也用不着反向怀疑：见下方「假 unbound 纠正」） |

### ★★★ 假 unbound 纠正（2026-09-29 实测推翻旧结论）

用**真调用法**复测后，之前用裸符号法得出的「unbound」结论**大部分是误判**：

| 函数 | 旧结论（错） | 真调用结论（对） |
|--|--|--|
| `axlOpenDesign` | ❌ unbound | ✅ **存在**（无参 = 弹框选文件） |
| `axlKillDesign` | ❌ unbound | ✅ **存在**（无参即用，见第六节） |
| `axlOpenDesignForBatch` | ❌ unbound | ✅ **存在**（需 2 个**位置**参数） |
| `axlSaveDesign` | ✅ 可用 | ✅ 存在，但**无参 = 弹框**（必须给 `?noConfirm t`） |

> **教训**：裸符号法连「能用的函数」都报 unbound，所以它给出的「不存在」**一律不可信**。
> 只有在真调用报 `undefined function` / `unbound variable` 时才可判不存在。

### ★ 唯一可靠的做法：真调用一次

发送 `axlFoo(<无害参数>)`，按**错误类型**判定：

```
报 "undefined function"   -> 不存在
报 "unbound variable"     -> 不存在（符号未绑定）
报其它任何错误            -> ★存在（函数已被解析到，只是参数/环境不满足）
正常返回                  -> 存在
```

### ★★ 必须区分「SKILL 层错误」与「连接层错误」

```
连接层超时/断开(TimeoutError / ConnectionRefused / 无法连接)
    -> ★无法判定存在性！绝不能算「存在」
    -> 断连时立即停止后续探测，否则全是噪音
```

### 工具

```bash
python tools/probe_api_exists.py axlCurrentDesign          # 单个
python tools/probe_api_exists.py --suite symbol            # 内置套件
python tools/probe_api_exists.py --file candidates.txt     # 批量
```
套件：`calib`（校准）/ `symbol` / `design` / `server` / `server-danger`

**校准惯例**：每次探测前先用「已知存在 + 已知不存在」各一个做对照，确认探测法本身有效。

---


## 六、版本差异警示（已用真调用法修正）

Cadence 手册里写了、SPB 17.2-2016 实测**确实存在**的：

| 函数 | 文档 | 真调用 |
|--|--|--|
| `axlKillDesign` | ✅ | ✅ 可用（无参，开新 **BOARD** 图）|
| `axlOpenDesign` | ✅ | ✅ 可用（`?mode "wf"` 无确认）|
| `axlOpenDesignForBatch` | ✅ | ✅ 可用（2 位置参数）|
| `axlSaveDesign` | ✅ | ✅ 可用（**必须** `?noConfirm t`，无参会弹框）|
| `axlCompileSymbol` | ✅ | ✅ 可用（出 `.psm` 的正路）|
| `axlDesignType` | ✅ | ✅ 可用（`nil`=粗类 LAYOUT/SYMBOL；`t`=细类 BOARD/package/...）|
| `axlDeleteByLayer` | ✅ | ✅ 可用（★ 不含 pin/via）|
| `axlDeleteObject` | ✅ | ✅ 可用 |

### ★ 文档自身有错：`axlSetDesignType` **不存在**

`14dsnctl.html` 在 `axlOpenDesign` / `axlOpenDesignForBatch` 的说明里都写了：
> *"The designType of the new design is set the same as the current design.
>   It can be changed using **axlSetDesignType**."*

但实测：

```skill
axlSetDesignType(nil)
-> *Error* eval: undefined function axlSetDesignType
```

**全文档也 0 命中这个函数。** 这是 Cadence 文档自身的矛盾 ——
**别指望用它改图纸类型**，用 5.1c 的模板复制法绕开。

**结论**：**不要用裸符号法判存在性**。上手前用第一节的真调用法实测一遍；
遇到文档前后矛盾时，**以实测为准**。

---


## 八、手册定位（Cadence SPB 17.2）

```
<Cadence>/Cadence_SPB_17.2-2016/doc/algroskill/     ← Allegro SKILL 手册（38 个 html）
    02dbdesc.html   数据库对象属性（axlDBGetDesign()->xxx）
    03dbcre8.html   图元创建（axlDBCreateLine/Rectangle/Text/Pin/PadStack）
    12draw.html     drawing 操作
    14dsnctl.html   ★ 设计控制（axlOpenDesign/axlSaveDesign/axlCompileSymbol）
    21filacc.html   文件访问（axlDMOpenFile）
    26logacc.html   symbol 定义（axlDBCreateSymDefSkeleton）
```

`<Cadence>/doc/sklangref/` — SKILL 语言参考（`funcprog` / `inputoutput` /
`environment` / `dataoperator`）。

提取函数签名的正则：`(axl\w+)\s*\((.*?)\)\s*(?:⇒|=>)`

### 8.1 ★ 另一条读图路：extracta（**不需要 Allegro 运行**）

`<Cadence>/tools/pcb/bin/extracta.exe`（部分安装里在 `tools/bin/`）——
Cadence 自带的数据库导出工具，输出 `!` 分隔的 ASCII。
**能力边界在「视图文件」，不在工具本身。**

```bash
extracta <file.dra> <view.txt> <out1> <out2> <out3> [<out4> ...]
```
★ **每条视图对应一个输出文件**（命令行顺序一一对应）——
实测 **4 路可用**（pins / pads / 几何 / 位号），不是只能 3 路。

视图文件 = 视图名 + 字段声明 + `END`；`CLASS = "..."` 决定读哪个类。

**★★ 最容易误判的一点：`CLASS = "..."` 是过滤器，会造成静默缺口**

它不是"顺带多读一层"。视图里写死 `CLASS = "PACKAGE GEOMETRY"` 时，
`REF DES` 层的位号文字**读不到，而且不报错** —— 输出文件照样生成、
`S!` 行照样有，只是**静默少了一整类元素**，上层完全无感。
（本项目踩过：`texts` 里只有 `PIN_NUMBER`、没有 `#REF`，
自检判据还写着"extracta 读不到位号"——把视图缺陷当成了工具能力上限。）

```
FULL_GEOMETRY
CLASS = "PACKAGE GEOMETRY"    ; 几何（含丝印/装配/Place_Bound 图形）
END
FULL_GEOMETRY
CLASS = "REF DES"             ; 位号 —— **必须单独一路**
END
```
实测第 2 路立刻读出 `REF DES/ASSEMBLY_TOP = "#REF" @ (-0.15, 0.04)`。

★ 另一个坑：REF DES 的 SUBCLASS **也叫 `ASSEMBLY_TOP`** ——
  若位号与几何共用同一个解析器，位号会被归进几何的 `assembly_top`。
  → 位号那一路解析时**只取 TEXT、不建 layers**。

→ **读不到东西时，先看视图文件，别先怀疑工具。**
→ **正确姿势是「分层取数 + 每路报缺口」**：别指望一路读全，
   也别让缺的那路静默通过（缺口要显式上报，但**不该**拿它去触发
   "换数据源重读" —— 同一张图纸换 SkillBridge 读也一样没有）。

其他要点：
- **exit code 恒为 `0xC0000409`**（每次收尾都崩）→ 判成败只看**输出文件是否非空**
- 环境变量必须注入 `CDSROOT`（否则直接崩），并把 `tools/bin`、`tools/pcb/bin`
  前置进 `PATH`
- `J!` 头行字段：`[0]`文件路径 `[2..5]`extent `[6]`精度 `[7]`单位

**什么时候用 extracta，什么时候必须 SkillBridge：**

| 场景 | 选 |
|--|--|
| 批量校验一堆 `.dra` | **extracta**（不必开 100 次 Allegro，可并行）|
| 只要几何 / 焊盘 / 文字，不要"活着的设计" | **extracta** |
| **必须读到指定的那个文件** | **extracta**（SkillBridge 读的是"当前打开的图纸"，会读错）|
| 要改设计（建图元、编译、存盘） | SkillBridge（extracta 只读）|

实测：读一个封装 **1.5s** 且 Allegro 完全不启动；同一时刻 SkillBridge 直接超时。

---


## 十二、现成作品与外部资源索引（2026-09-29 调研）

> 完整调研报告：`layout-forge-mcp/docs/Allegro_SKILL现成作品调研.md`
> 结论：**我们缺的那几块，社区早有成熟答案**。优先吸收 `NOCONFIRM` 族（见第三节）。

### Cadence 官方社区（权威度最高，可直接抄）

| 帖号 | 内容 | 价值 |
|--|--|--|
| **#17781 / #1267499** | `exampleSymbol` + `example_makePin` | 建封装最小可用代码；8 个核心 API 标准搭配 |
| **#16749** | create symbol with skill（eDave） | ★ `AFn_OpenDesign` + 完整"不丢当前设计"范式 |
| **#15132** | Padstack 圆角矩形（`cps2` / `TLU_makeSMDPadstack`） | padstack 参数化命名 + 四件套 API |
| **#8074** | Overide a Blocker Function | ★ `NOCONFIRM` 出处；"`?mode \"wf\"` works perfectly" |
| **#1408866** | `axlOpenDesignForBatch` 22.1 vs 25.1 | 批量打开**必须**设 `NOCONFIRM` |
| **#58594** | Introduction to SKILL | AXL 函数族 / `set telskill` |

### 开源仓库

| 仓库 | 亮点 |
|--|--|
| `github.com/whh5118/GTOpen` | 几楼科技；**中文注释**；导出封装用 `axlWritePackageFile`；创建焊盘 |
| `github.com/zonebee/AXL-SKILL` | `package_out.il`（39 行，`axlWritePackageFile` 最小示例） |
| `github.com/hanun2999/cadence-skill` | ~200 个 `.il`；`create_pad.il` / `Create_Thermal_Flash.il` / `Symbol_Check.il` / `check_no_refdes.il` |

### 论坛（需注册/积分，本次只取到用法描述）

| 作品 | 出处 | 说明 |
|--|--|--|
| **`Pad_IPC.cxt` V1.4**（`cpd`） | allegro-skill.com #146740 | **功能与我们最接近**：只输名字按 IPC-7351B 建焊盘，自动 Flash/Anti Pad |
| EDA365 Skill v2.7 | bbs.eda365.com | "封装输出"；插件机制 + `config.ini` 的 `NoAutoLoad` 隔离 |
| **FanySkill（凡亿）批量生成 PSM** | cnblogs 教程 | ★ **功能完全重叠**：批量 `.dra` → 循环 Create Symbol → `.psm`，含覆盖/校验/日志 |

### 官方自带工具（不用自己写）

- **Package Symbol Wizard**：8 种封装类型（DIP/SOIC/PLCC/QFP/TH DISCRETE/SMD DISCRETE/SIP/ZIP），带 `Load Template` → "Default Cadence supplied template"
- **LP Wizard**（PADS 附带）：一键出 Allegro 封装。⚠️ 与自研 SKILL **同开会冲突**，需 `NoAutoLoad` 隔离

### 尚未用上的官方 API

| API | 用途 | 来源文档 |
|--|--|--|
| `axlWritePackageFile( symdef  address )` | 从 `.brd` 按符号导出 `.dra`/`.psm`/`.pad`（原生只能整库导出） | `26logacc.html` |
| `axlDBCreateSymDefSkeleton` | 在设计中创建符号骨架 | `26logacc.html`（★ 注意：文档说**不适用于 symbol editor**） |
| `axlLayerCreateCrossSection` | 创建叠层（GTOpen 导出封装时用来补临时层） | `04parmgt.html` |

---

