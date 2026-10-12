# popup-ban —— 弹框 API 禁令与 NOCONFIRM 变量族

> 收录原 SKILL.md **第三节**。
> 任何一个模态对话框都可能把 SkillBridge 通道**彻底堵死**（且外面看不出来），
> 本文件是「哪些 API 会弹框 + 怎么从根上压住」的完整清单。
>
> grep 关键词：`弹框`、`NOCONFIRM`、`覆盖保护`、`os.remove`、`no_symbol_onsave`、`升级框`、`wf`

## 三、★★★ 弹框 API 禁令（会搞死通道）

**任何可能弹出模态对话框的 AXL 调用，在自动化通道里都等于自杀** —— 调用永不返回，
SkillBridge 回复队列彻底错位，之后所有调用都报「无法对齐」。

### 已知地雷

| 调用 | 后果 |
|--|--|
| `axlOpenDesign(?design "<不存在的路径>")` | 弹 **Drawing Parameters** 框，通道卡死 |
| `axlOpenDesign(?design "<已存在的文件>")` | ★ 弹 **`File Exists. Overwrite?`** —— **`?mode "wf"` 拦不住它**！|
| **打开 16.0 老版本 `.dra`** | ★ 弹 **版本升级框**：*"The design created using release '16.0' will be updated for compatibility..."* |
| **`axlSaveDesign()` 不带任何参数** | 弹**保存确认/命名**框（文档："if the value is nil, you are prompted for the name"） |
| 不带 `?noConfirm t` 的 `axlSaveDesign(?design "x")` | 弹保存确认框，报 `ConnectionAbortedError(10053)` |
| `axlCompileSymbol(?symbol nil ?type ...)` | 弹**「另存为」**框 |
| `axlCompileSymbol(...)` 且目标 `.psm` 已存在 | 弹**「File Exists. Overwrite?」**框 ← ★ **此框已可压制** |

> ★ **实测状态（2026-09-29 在线）**：上表最后一行曾是本项目最大的心病，
> 现在已解决 —— `noconfirm_savedb` + 编译前把旧 `.psm` 移入 `.trash/`，
> 双保险下连续两次编译耗时 0.6s / 0.7s，**零对话框**。
> 详见第七节「弹框已被压住」。
> → **教训**：不要一遇到失败就归咎弹框；先读日志（`<symbol>.log` / `allegro.jrl`），
>   否则会在错误的方向上耗掉整轮。

### ★★★★★ 首选解法：`NOCONFIRM` 变量族（官方文档明载，比"挪文件"更根本）

★ 2026-09-29 调研 Cadence 官方文档发现 —— **上面那些弹框，官方早有变量级开关**。
来源：`<Cadence>/doc/algroenvvar/Drawing_Settings.html`（Script Settings 段）

| 变量 | 官方原文 | 对应弹框 |
|--|--|--|
| `NOCONFIRM` | "If set Allegro does not prompt you to confirm certain actions. **This variable should be used only with scripts.**" | 通用确认框 |
| `noconfirm_savedb` | "does not display warning message **if save will overwrite an existing database**. The database will be overwritten." | ★ `File Exists. Overwrite?` |
| `noconfirm_uprev` | "does not display an **uprev warning message on open drawing**" | ★ 老版本升级框 |
| `uprev_answer` | 提供默认回答；置 `"no"` 则**反而禁止**打开旧版本（并禁用 `noconfirm_uprev`） | 升级框自动回答 |
| `padstack_nowarning_display` | "**provided for the creation of pad stacks via automation**, and should not be set in a typical user environment" | Padstack 保存警告 |
| `allegro_noabort_confirm` | "include this variable in your script if that script is used to run Allegro in an **unattended mode**" | 中止确认框 |
| `db_tier_nomsg` | 抑制"用不同产品级别打开设计"的确认 | 产品级别提示 |

**读写 API**（`algroskill/09cmdshl.html`）：
```skill
axlGetVariable( t_variable )        ⇒ t_value/nil    ; 查询
axlSetVariable( t_variable t_value )                  ; 设置
axlUnsetVariable( t_variable )                        ; 清除
```

★ 用完必须恢复现场（`NOCONFIRM` 是全局状态，设了连用户手工操作也失去确认保护）。
范式照抄 Cadence 论坛版主 eDave 的 `AFn_OpenDesign`：
```skill
noconfirmStatus = axlGetVariable("NOCONFIRM")
axlSetVariable("NOCONFIRM" t)
;; ... 危险操作 ...
if(noconfirmStatus then axlSetVariable("NOCONFIRM" t)
                   else axlUnsetVariable("NOCONFIRM"))
```

**★ 本项目已落地（2026-09-29）—— 别重复实现，直接复用：**

| 位置 | 内容 |
|--|--|
| `services/skill_snippets.py` | `AUTO_NOCONFIRM_VARS`（四个变量名）、`build_get/set/unset_variable`、`build_get_variables` / `build_set_variables`（一条表达式批量处理，省往返）、`build_restore_variables`（按旧值分支恢复）、`build_normalize_var_reply`（把回复归一成 `{名: bool}`，容错三种回复形态） |
| `services/skillbridge_writer.py` | `AllegroSession` **进入会话自动设、退出自动恢复**；模块级 `AUTO_NOCONFIRM` 控制默认值。关闭：`AllegroSession(auto_noconfirm=False)` 或环境变量 `FORGE_NOCONFIRM=0` |
| `Tools/selfcheck_skill_syntax.py` | 第 `[13]` 节 23 条离线用例（含"`t` 必须裸符号不能写 `\"t\"`""批量设必须 errset 包住""通道死掉时不许抛异常"等守卫） |

★ 两个实现要点（踩过的）：
1. **SKILL 的 `t` / `nil` 是裸符号**，写 `axlSetVariable("NOCONFIRM" t)`，
   写成 `"t"`（字符串）是错的 —— 自检里专门守这一条。
2. **读不到旧值时，恢复方向要选"安全侧"** —— 统一 `unset`
   （回到默认＝会弹框），而不是留着 `t` 让用户手工操作失去确认保护。

### `axlSaveDesign` 自带 `?noConfirm`（不必靠变量）

```
axlSaveDesign( ?design t_design  ?mode t_option  ?noMru g_noMru
               ?noConfirm g_noConfirm  ?writeModel g_write )  ⇒ t_design/nil
```
- `g_noConfirm`：官方原文 "If `t` **does not present a confirmer if overwriting an existing design**. File is overwritten."
- `?mode "nocheck"`：跳过数据库 quick check

→ **`axlSaveDesign` 一律带 `?noConfirm t`**，这是最省事的一条。

### ★★ `no_symbol_onsave` —— **别设它**（设了就退回"必须手工 Create Symbol"）

官方原文（`algroenvvar/Drawing_Settings.html`）：
> "By default, on saving a dra file, the symbol file will also be created.
> **With this variable set, the old behavior where the user would need to use
> File->Create Symbol (create symbol) is restored.**"

`algrostart/chap1.html` 佐证：
> "The layout editor automatically creates a symbol every time you save a
> drawing (.dra) when you are in the Symbol Editor."

★ 方向别读反：**默认就是自动编译**，`no_symbol_onsave` 是"**关掉**自动编译"的开关。
→ **自动化场景绝对不要设它。**

⚠️ **但也别指望靠 save 出 `.psm`（2026-09-29 实测未走通）**：
- 图纸未修改时，`axlSaveDesign` 是**空操作** —— 返回设计名、0.1s 返回，
  但 `.dra` 的 mtime 与大小纹丝不动
- 即使先 `axlDBCreateText` 加了图元再 save，`.dra` 里 `grep` 不到那段文字 → 仍没写盘
  （原因未查明，**不要依赖 save 的副作用**）

**本项目结论**：`export_psm()` 老老实实走 `axlCompileSymbol`，
实测 0.6~0.7s 稳定出 `.psm`，不必绕路。
判据永远看**磁盘产物**（mtime + size），不看 API 返回值。

### ★★★ `?mode "wf"` 的语义边界（易误解，我踩过）

`axlOpenDesign` 的 `?mode "wf"`：
- ✅ `f` = **丢弃当前设计的未保存编辑时不弹确认框**
- ❌ **不是**"覆盖目标文件不确认" —— **目标已存在照样弹 `File Exists. Overwrite?`**
- ★ 官方注：**`"wf"` 等价于设 `NOCONFIRM`**，但**只在 `axlOpenDesign` / `axlOpenDesignForBatch` 里被识别** ——
  其它 API（`axlSaveDesign` / `axlCompileSymbol` …）不认 `wf`，必须靠变量。

→ **对策优先级：① 变量开关 → ② 参数开关（`?noConfirm`）→ ③ 调用前把目标文件挪开。★ "挪开"不等于"删掉"，见下方禁区。**

### ★★★ 禁区：覆盖保护不要用 `os.remove`（真实事故）

"目标已存在 → 先删掉"这个对策**只对"我们自己刚建的临时产物"成立**
（比如复制出来的 `.dra` 副本）。对**有价值的产物**（`.psm`）直接删会闯大祸：

> 2026-09-29 真实事故：`export_psm` 编译前 `os.remove` 旧 `.psm` →
> 随后编译**失败**（图纸被污染成 duplicate pin）→
> 上一次成功产出的 8640 B `.psm` **被自己的"保护"逻辑吃掉了**，永久丢失。

**正确姿势：可恢复隔离（quarantine）**

| 步骤 | 做法 |
|--|--|
| 1 | 把目标文件**搬进** `.trash/`（`shutil.move`，不是 remove），文件名带时间戳 |
| 2 | 再调那个会弹框的 API |
| 3 | **成功** → 隔离件留作备份（返回值里带上备份路径）|
| 4 | **失败** → **原样搬回原位** |
| 5 | 连搬都搬不动 → **中止操作**，宁可这次不干也不冒丢产物的险 |

一句话：**"为了防弹框而删文件"必须升级成"为了防弹框而搬文件"。**
（本项目实现：`services/footprint_builder.py::_quarantine_file()` / `_restore_quarantined()`）

### ★★★ 推论：往"库目录"归档时，**默认不要覆盖**（2026-09-29 实测）

即使有了 quarantine，**归档动作也应该默认 `overwrite=False`**。原因：
**库里躺着的件未必是生成物，可能是人工参照物。**

> 实测：`Data/footprint/3S48000163/nb_xtal4_3d2x2d5x0d7.dra` 的 md5
> （`38c0b301…`）与 `Tools/test/golden_3S48000163.dra`（逐值对拍用的**金标准**）
> **完全相同**。若归档默认覆盖，一次"顺手归档"就会把参照物换成生成件，
> 之后所有对拍都变成"自己跟自己比"，**而且毫无提示**。

→ 已存在的记进 `collisions` **原样不动**；要替换必须显式 `overwrite=True`
（此时才走 quarantine，可恢复）。**archiver 的默认值应该偏保守。**

### ★★★ `.dra` 版本标识（离线预判会不会弹升级框）

版本标识串固定在**文件 offset 248**，读 64 字节即可判定。

**★ 三种真实格式（2026-09-29 取样，务必都认）**：

| 样本 | offset 248 内容 | 大版本 |
|--|--|--|
| 17.2 自建（我用 API 建的） | `all375891/28/...` | **17** |
| 17.2 官方符号库（3277 个） | `allv17-08/24/.apdv16-02/22/...` | **17** |
| 17.2 官方模板 `sym_template.dra` | `qvuv17-28/6/2dbdv16-512/5/...` | **17** |
| 16.0 老版本 | `qvuv16-04/12` | 16 ⚠️ |
| 15.7 老版本 | `dbdv15-71/3` | 15 ⚠️ |

**★ 判定方法（不要精确匹配字符串！）**：

标识串是**以 `/` 分隔的版本链**，每段形如 `<prefix><major>-<rest>`。
取第一段，提取主版本号：

```python
import re
def dra_major(path):
    with open(path, "rb") as f:
        f.seek(248); raw = f.read(64)
    tag = raw.split(b"\x00")[0].decode("ascii", "replace")   # 截到 NUL
    head = tag.split("/", 1)[0]                              # 第一段
    m = re.search(r"[a-zA-Z]+(\d+)-", head)                  # allv17-08 / qvuv16-04
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", head)                           # all375891
    if m:
        n = int(m.group(1)[:4])
        return 17 if n == 375 else (n if 10 <= n <= 30 else None)
    return None
```

**判定：`dra_major(p) == 17` 才是安全模板。**

> ⚠️ **踩坑记录（务必引以为戒）**
> 我上一版把判据写成 `tag == "all375891"`（精确匹配），结果
> **Cadence 官方符号库 3277 个 17.2 符号全被判为"老版本"**——
> 它们是 `allv17-08/...`，同为 17.2 只是串写法不同。
> 教训：**版本串格式多样，必须解析主版本号，不能精确匹配单个样本。**

**★★ 更省事的做法：直接用官方模板**
```
<CDS_INST_DIR>/share/pcb/pcb_lib/symbols/template/sym_template.dra
```
这是 Cadence 自带的 **Package symbol 专用模板** —— 类型必定是 SYMBOL、
版本必定是当前版本、内容干净。**建 symbol 图纸优先用它**，
比"扫工作目录找 .dra 赌它是 symbol"可靠得多。
（本项目实现在 `footprint_builder._find_official_symbol_template()`）

**实测卡死记录**：`Tools/skillbridge_skill.log` 末行停在 `axlSaveDesign()`，
之后所有调用全部超时 —— 与截图里的 Save As 框完全对应。

### ★★ 规律（能提前预判弹框）

1. **文档里写 "you are prompted for the name"** → 传 `nil` 必弹框
2. **目标文件已存在**且 API 没有 `?noConfirm` 类开关 → 必弹框
   - 对策：**调用前先把目标挪开**（有价值的产物用 quarantine，见上方禁区）
3. **能显式传名字/加 `?noConfirm t` 的，一律显式传**

### 通用对策

1. 能加 `?noConfirm t` / `?noMru t` 之类的抑制开关就加
2. 目标文件已存在的，**先挪开再调**；
   ★ 自己刚建的临时件可以直接删，**别的产物一律 quarantine 而不是 remove**
3. 探测这类 API 时，**用 `errset` 包住 + 独立超时**，别裸发
4. 不确定的调用先在 `tools/probe_api_exists.py --dangerous` 下单独试，别批量跑

### 通道错位后的唯一解法

`axl` 层没有清队列的 API，客户端 `Channel.send()` 是「发一收一」原子操作，
队列长度守恒，**任何发送组合都无法缩短滞后**。唯一手段是重启服务（见第二节）。

---

