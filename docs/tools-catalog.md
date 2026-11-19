# MCP 工具目录（自动生成，勿手改）

> 生成器：`eval/suites/tool_contract/tool_catalog.py`
> 真源：`mcp_server/tools.py` 的 `@mcp.tool`（名字 / 参数 / docstring）＋ `knowledge/tool_groups.json`（分组，唯一需要人手维护的一项）
> 重算：`python eval/suites/tool_contract/check_tool_contract.py --write-catalog`
> 过期检查：契约套件 C8（本文件与代码不一致即 ERROR）

★ 这是全仓**唯一**列出全部工具名与数量的地方。其它文档不要再抄清单、也不要再写「N 个工具」——指到这里。

共 **39** 个工具，分 6 组。

## 图纸 / 器件写侧（12）

> 建图纸、建焊盘、加 pin / outline / text、扩图幅、保存与导出

| 工具 | 说明（docstring 首句） |
|--|--|
| `add_footprint_outline` | 画一个矩形轮廓，用于丝印 / Assembly / Place_Bound。… |
| `add_footprint_pin` | 在当前 drawing 中放置一个 pin（引脚）。… |
| `add_footprint_text` | 加一段文字。★ 位号（REFDES）是必加项 —— 缺它 File→Create Symbol… |
| `change_drawing_extents` | 设置 drawing 的 extents（有效范围）。… |
| `clear_footprint_drawing` | ★ 清空当前图纸里的图元（保留图纸本体、单位、范围不变）。… |
| `create_padstack` | 在当前 drawing 中创建 padstack（并默认落盘为 .pad 文件）。… |
| `export_psm` | ★ 把当前图纸编译成 `.psm` 封装符号（= 界面里的 File → Create Symbol）。… |
| `frame_drawing_view` | 把 drawing 的**图纸范围**和**视图窗口**都摆成以原点为中心，… |
| `inspect_allegro_environment` | 检查 Allegro/SkillBridge 连通性与当前 drawing 状态。… |
| `new_footprint_drawing` | ★ 新建一张全新的空图纸（`axlKillDesign()`，零弹窗）。… |
| `new_symbol_drawing` | ★★★ 全自动新建一张 **Package symbol** 类型图纸（零弹框）★★★… |
| `save_footprint_drawing` | 保存当前 drawing。… |

## 几何求解与库（5）

> 按参数算焊盘/本体/丝印/位号，以及 shadow library 的安装与列举

| 工具 | 说明（docstring 首句） |
|--|--|
| `build_footprint_from_params` | 一次调用建出完整封装：padstack + 所有 pin + 丝印 + 本体 + Place_Bound + 位号。… |
| `install_shadow_library` | ★ 把产物（`.dra` / `.psm` / `.pad`）归集进**影子库**… |
| `list_shadow_library` | 列出影子库里已归档的料号及其产物（只读，便于人工核对）。… |
| `solve_footprint_geometry` | 【参数 → 几何】把器件参数算成**可直接执行**的成品几何。… |
| `validate_land_pattern_json` | 校验 AI 输出的 land pattern JSON 是否符合预期 schema，并规范化。… |

## spec / 案例库（7）

> 规格书解析结果与人工裁定案例的存取、检索

| 工具 | 说明（docstring 首句） |
|--|--|
| `find_case` | ★ 查案例库：该封装 / 同族件有哪些已知坑与人工裁定 —— **检查前先查**。… |
| `find_spec` | 找库里有没有「同封装 / 同族」的 spec —— **判断能不能少读图**。… |
| `list_cases` | 列出案例库全部案例（名字 / 标签 / 族 / 来源 PDF / 结论 / 教训数 / 存档时间）。… |
| `list_specs` | 列出 spec 库里已存档的全部器件（名字 / 族 / 来源 PDF / 指纹 / 存档时间）。… |
| `load_spec` | 从 spec 库取回一份 spec（**直接能喂 `solve_footprint_geometry` 的字段**）。… |
| `save_case` | ★ 把一次检测的**人工裁定**存进案例库 `knowledge/cases/`。… |
| `save_spec` | ★ 把一份 spec（读规格书解析出的参数 JSON）存进 spec 库 `knowledge/specs/`。… |

## 读 PDF / 目录（11）

> 判形态、定位、渲染、量测、重建阵列——读规格书这一侧

| 工具 | 说明（docstring 首句） |
|--|--|
| `extract_page_numbers` | ★ 抽该页文本层里「像尺寸」的数字，**带 PDF 坐标** —— 读数前的线索表。… |
| `list_folder_files` | 列出封装文件夹里的 PDF / .dra / .pad 文件。… |
| `list_subfolders` | 列出父文件夹下的所有子文件夹，供批量模式侦察。… |
| `locate_land_pattern_page` | 定位 PDF 规格书中包含 land pattern（推荐焊盘）的页码。… |
| `measure_region` | ★ 量取区域内矢量图元的包围盒（PDF pt），用于**与读图结果交叉校验**。… |
| `pdf_probe` | ★ 读规格书的第一步：判这份 PDF 是哪种形态，决定用哪条读法（零成本，只读元数据）。… |
| `reconstruct_pad_array` | ★ 从 PDF **矢量图元**还原焊盘阵列：尺寸、节距、行列数、本体外廓（不调模型）。… |
| `render_focus_regions` | ★ 「该看哪几块、各自多清」一次给全：概览图 + 数字密集区（必要时 2x2 切块）。… |
| `render_pdf_contact_sheet` | ★ 把整份规格书拼成**一张缩略图**，一次扫完定位「哪一页是尺寸/焊盘图」。… |
| `render_pdf_page` | 把 PDF 指定页渲染成 PNG 图片，返回图片的本地路径。… |
| `render_pdf_region` | ★ 按 PDF 坐标裁一块区域、高 dpi 重渲染 —— **读尺寸数字的主力手段**。… |

## 检查（2）

> 读回已建封装并按规则判定

| 工具 | 说明（docstring 首句） |
|--|--|
| `check_footprint_by_rules` | 从 .dra 提取封装数据，跑完整的数值规则检查，并自动保存 JSON 报告、… |
| `read_full_footprint` | 从 .dra 全量提取封装数据（不做规则判断）。… |

## 编排 / 观测（2）

> 目录预检与遥测统计

| 工具 | 说明（docstring 首句） |
|--|--|
| `folder_preflight` | ★ 检查前的**第一步**：这个目录里有几份规格书 / 几颗封装？有歧义就先问用户。… |
| `get_run_stats` | ★「系统最近怎么样」：工具调用 / 失败 / 降级、**最常 FAIL 的规则**、知识库分布、缺口清单。… |
