# allegro-skillbridge-ops —— 技能源文件目录

> 最后更新：**2026-10-08**（SKILL.md 拆分重构：body 从 38,946 → **3,595** 计量字符，压到 **9.2%**）
>
> ★ **本文件是维护者文档，不参与技能执行**，也**故意没有被 SKILL.md 引用** ——
> 它不会进入任何一次对话的上下文。只有人（或未来的我）要**改这个技能**时才读它。

## 这个目录是干什么的

Cadence Allegro + SkillBridge 自动化的**运维与排障技能源文件**。

它不是用户项目（`layout-forge-mcp` 那类），也不是 WorkBuddy 缓存区 —— 它是**技能本体**。
`workspace-hygiene` 的规则字面上覆盖的是「工作区和缓存区」，所以本目录长期没有说明文件，
属于**规则边界空白**（不是豁免）。

规模目标（沿用 `skill-creator` 的「渐进披露」三级加载口径）：

| 级 | 内容 | 官方预算 | 本技能现状 |
|--|--|--|--|
| 1 | `SKILL.md` 的 frontmatter（name + description） | 恒在上下文，~100 词 | 53 行 / **1,977** 计量字符 |
| 2 | `SKILL.md` body | 触发时载入，**< 5k 词** | 138 行 / **3,595** 计量字符 ✓ |
| 3 | `references/` + `scripts/` | 按需，不限 | 7 个知识文件 + 3 个脚本（各配 1 份 README） |

> 「计量字符」= CJK 字符 + 字母数字各计 1，空格标点不计。中文场景下约等于 token 数。
> 参考：拆分前整文件 1902 行 / 40,915 计量字符（body 38,946）。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `SKILL.md` | ★ **技能入口**。只放 6 块：30 秒判断表 / 红线清单 / **收尾铁律 A+B** / 标准流程 / 环境前提 / references 索引 | 触发时**全量载入上下文**，所以体积最敏感；改它等于改技能行为 |
| `references/lifecycle.md` | 进程生命周期（原 二、十、十一、十四节） | 按需读；收尾铁律的详细版在这里 |
| `references/popup-ban.md` | 弹框禁令全表（原 三节） | 按需读 |
| `references/drawing-ops.md` | 图纸操作与取值口径（原 五节） | 按需读 |
| `references/compile-psm.md` | 出 `.psm` 与归档（原 七、十三节） | 按需读 |
| `references/api-facts.md` | API 事实核查（原 一、六、八、十二节） | 按需读 |
| `references/skill-syntax.md` | SKILL 语法陷阱（原 九节） | 按需读 |
| `references/extents.md` | 图元静默丢弃（原 十五节） | 按需读 |
| `scripts/preflight_check.py` | 收尾复查：端口 / 进程 / 孤儿锁 / 项目根污染 | 不依赖第三方包，**任意 Python 3.8+ 可跑** |
| `scripts/find_allegro_window.ps1` | 定位并截图屏幕上的 Allegro 窗口，输出 JSON + PNG | 需 PowerShell |
| `scripts/shot_stats.py` | 对截图做像素统计（数值取证，替代肉眼） | 纯标准库 |

## 关键约定与坑

1. **新增知识一律进 `references/`**，`SKILL.md` 只在「30 秒判断表」和「索引表」各加一行指针。
   **不要把细节写回 SKILL.md** —— 2026-10-08 之前它已经涨到 40,915 计量字符，
   比官方预算超了约 8 倍，每次触发都要全量读一遍。

2. **frontmatter 的 `description` 是唯一的触发依据**，也是唯一常驻上下文的部分。
   加触发词可以；**删词要先想清楚会不会漏触发**。

3. **`description` 不能含尖括号 `<` `>`**（skill-creator 的规则）。
   ⚠️ 坑：`quick_validate.py` 用的正则 `description:\s*(.+)` 对 `description: |`
   这种块语法**只读到 `|` 那一行 → 漏检**。所以**别指望校验器兜底**。
   本目录已于 2026-10-08 把唯一一处 `axlDBGetDesign()->bBox` 改成汉字表述。

4. **章节号沿用拆分前的原编号（一 ~ 十五）**。
   正文里有大量「见第十一节」「5.4.3」这类交叉引用，**重新编号会把它们全部打断**。
   `SKILL.md` 第 5 节有「章节 → 文件」对照表，够用。

5. **代码块内以 `#` 开头的行不要动**。
   例如 `references/lifecycle.md` 里 `# Allegro advisory lock file` 是
   **`.lck` 文件的实际内容**，不是注释；改成 `//` 就是改错事实。
   需要按标题机械切分文件时，**先让工具识别 ``` 围栏**（工具见下）。

6. **README 故意不被 SKILL.md 引用** —— 保持"惰性"，不占对话上下文。
   想让它生效反而要把它写进索引表，那就违背初衷了。

7. **别在技能目录里留下 `__pycache__`**：跑过 `py_compile` / 直接 import 之后记得清
   （2026-10-08 踩过）。

## 怎么验证"拆分没丢内容"（可复现）

拆分是一次性动作，但同样的对账手段以后改完也能用。工具在缓存区：

```
C:\Users\ds245\WorkBuddy\2026-09-28-11-36-34\.workbuddy\cache\skill-refactor\
├── split_skill.py      按下标切分并写出 references/（含对账；--dry-run 只算不写）
├── outline_stats.py    按标题统计每段体积（★ 已做 ``` 围栏识别）
├── verify_refactor.py  拆分后的验收：官方校验 + 孤儿引用扫描 + 体积 + 守恒
└── backup_20261008-1030\   拆分前的整目录备份（回滚源）
```

跑法：

```bash
PY="C:/Users/ds245/.workbuddy/binaries/python/versions/3.13.12/python.exe"
SK="C:/Users/ds245/.workbuddy/skills/allegro-skillbridge-ops"
"$PY" verify_refactor.py "$SK" backup_20261008-1030/SKILL.md
# 退出码 0 = 全部通过（快速回归用这条就够）
```

本次验收结果（`verify_refactor.py` → 结论：**全部通过 ✓**）：
- 字符对账：搬走 38,627 + 留用 293 = 38,920 = 原 body ✓
- 字节对账：搬走 91,571 B + 留用 731 B = 92,302 B = 原 body(L57-1902) ✓
- 逐字节校验：7 个 reference 去掉头部后与原文**逐字节一致**
  （唯一差异是章节接缝各插了 1 个空行，共 7 处，属有意为之）
- 官方校验器：`quick_validate.py` → `Skill is valid!`

## 相关目录

| 目录 / 文件 | 关系 |
|--|--|
| `../workspace-hygiene/SKILL.md` | 「目录说明文件」这条规则的出处 |
| `.../plugins/workbuddy-builtin/skills/skill-creator/SKILL.md` | 技能的官方规范（目录结构、渐进披露、校验器） |
| `C:\Users\ds245\Documents\Work_Bmorn\Layout\layout-forge-mcp` | 被本技能服务的实际项目 |
| 缓存区 `...\cache\skill-refactor\` | 重构工作区与备份（可删，备份建议留两周） |
| 缓存区 `...\cache\mcp-smoke\` | MCP 冒烟测试脚本与实测输出（可删） |

## 调用方式（2026-10-08 定稿）

**MCP 为主 + CLI 补位。**

- **主流程走 MCP**：`~/.workbuddy/mcp.json` 已注册 `layout-forge-mcp`（stdio，
  用项目 venv 的 python 拉起、`-c` 注入 `sys.path` 所以不依赖 cwd）。
  23 个工具已冒烟实测：注册 23/23、schema 23/23、离线工具实调全通过、
  Allegro 不在时返回规范错误而非崩溃。
  （★ 2026-10-13：工具数已增至 **34** —— 新增 `pdf_probe` / `render_pdf_region` /
  `measure_region` 三个读规格书工具，注册与实调均已通过。）
- **仍走 CLI 的四件**：启动/关闭 Allegro、离线自检、归档、收尾 ——
  因为 **MCP 工具里没有启停 Allegro 的能力**。

★ 启用注册后需在 WorkBuddy「连接器管理」页对新 server 点**信任**才生效，
且改动 `mcp.json` 后要重启客户端才加载。
