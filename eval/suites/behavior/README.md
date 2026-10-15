# eval/suites/behavior —— 行为夹具：S 系硬规则 + L3 状态机真的可执行吗（L3 编排与控制层）

> 最后更新：2026-10-15（Phase 3：新增 **B5** —— 守 `orchestration/playbook.json` 的硬约定与
> `orchestration/pipeline.py` 状态机的放行/拦停，夹具 **5/5 通过**；此前 Phase 2-c：B1–B4 四夹具落地，
> 同日把 `orchestration/preflight.py` 的噪声规则从「AUTOSAVE / 备份后缀」扩到「Allegro 运行痕迹 + 历史检查报告」）

## 这个目录是干什么的

`allegro-footprint-check` 的 **S 系硬规则**是写在文档里的约束：

| 规则 | 原文要点 |
|--|--|
| **S1 目标消歧** | 目录里有多个 `.dra` / 多个 PDF → **禁止默认拿第一个就跑**，必须列候选请用户确认 |
| **S2 禁复用旧报告** | 本次结论只能来自本次运行；目录里的 `checklist_report_*.json` 是历史产物，**不是证据** |
| **S4 先探通道** | 要触 Allegro 的工具必须先探通道；探针失败就如实说「未连接」，禁止把超时误读成「工具挂了」 |

**写在文档里的约束靠模型自觉是守不住的——违反了你也看不出来。** 本套件把这些场景造成**可控的临时目录**，
逼工具层表态：该报 `ambiguous` 就不许给出一个「我猜的」答案，该说没通道就不许沉默。

Phase 3 起，本套件还兼守 **L3 状态机自己**（`playbook.json` 的「每条规则都必须有检测方式 + 后果类别」
与 `pipeline.run()` 的放行/拦停）—— 规则装配表如果自己不合约定，就没资格去管别人。

## 五个夹具

| 夹具 | 考的规则 | 怎么造场景 | 通过条件 |
|--|--|--|--|
| **B1** | S1 目标消歧 | 临时目录放 2 份 `.dra` / 2 份 PDF | `verdict=ambiguous_dra`·`ambiguous_pdf` + `action=confirm`，且**候选列全**（≥2） |
| **B2** | S2 禁复用旧报告 | ① 一对 PDF/.dra + `checklist_report_20260101_120000.json`；② 目录里**只有**旧报告 | ① 判 `ok` 且旧报告落 `noise_files`（不算证据）；② 判缺料 + `action=stop` |
| **B3** | S1 噪声剔除 | 1 个真 `.dra` + `AUTOSAVE.dra` + 1 PDF | 判 `ok`、候选只剩 `real.dra`、`AUTOSAVE.dra` 落 `noise_files` |
| **B4** | S4 先探通道 | 子进程调 `services.footprint_builder.inspect_environment()` | 结构化返回、不抛异常；`connected=False` 时 `error` **必须非空**；`connected=True` 时 `units` 合法 |
| **B5** | L3 状态机 + playbook 硬约定 | 调 `orchestration.pipeline.run()` 造四种局面：一对 PDF/.dra / 两份候选 / 通道不可用 / 结论文本 | ① `load_playbook()` 的每条规则都有「检测方式 + 后果类别」（`deny`/`silent`/`recoverable`），且步骤引用的 gate 都在 playbook 里、无悬空；② 正常 → `proceed` + `next_step=extract`；③ **歧义 → `confirm` 且已执行步骤只有 `preflight`（后续一律 pending，不许继续取数）**，且 `G-ambiguous-src` 判失败；④ 通道不可用 → `stop` + `CHANNEL_NOT_READY`；⑤ `stale_rule_ids`（已删除的 `6.5`）在 `summarize_findings()` 里被**单独点名**、未实现的 rule_id 也不许静默通过 |

## 怎么跑

```bash
python eval/suites/behavior/check_behavior.py            # 纯 stdlib，任何 3.10+ 都能跑
python eval/suites/behavior/check_behavior.py --json
python eval/suites/behavior/check_behavior.py --python <带 skillbridge 的解释器>   # 显式指定 B4 用的解释器
```

退出码：`0` = 无 ERROR；`1` = 有 ERROR。**没有基线文件**——这是断言套件（像 architecture / tool_contract），
判据是「必须成立」，不是「不比上次差」。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `README.md` | 本文件：夹具表、判据、纪律 | 增删夹具必须同轮改这里 + `eval/README.md` 五套件表 |
| `check_behavior.py` | 五夹具实现（纯 stdlib；B4 用子进程） | 只读 import `orchestration.preflight`、`orchestration.pipeline`、`services.footprint_builder`；**不改生产代码** |

## 三条纪律

1. **不用真实库做夹具**：全部在系统 TEMP 现场造目录（`tempfile.TemporaryDirectory`，跑完即删），
   不碰 `Data/footprint`。真实库只用来**发现问题**（见下）。
2. **不静默跳过**：B4 需要装了 `skillbridge` 的解释器；找不到就报 ERROR 并给出修复提示
   （重建缓存区 `venv-unconfined`，或用 `--python` 指定），**不是「没依赖所以算通过」**。
3. **夹具只读生产代码**：失败就是生产代码的失败，不许改夹具迎合现状。

## 实测与它抓到的东西（2026-10-15）

- 五夹具全绿（约 1 s；B4 用工作区外解释器）。B4 实测 `connected=False` + `error` 非空（本机没开 Allegro）→ `channel-down-explicit`，**正是要的行为**。
- B5 实测输出：`{"check":"playbook 硬约定","problems":[]}`、`{"check":"步骤引用的 gate 都在 playbook 里","dangling":[]}`、
  `{"check":"一对 PDF/.dra + 通道正常","action":"proceed","next_step":"extract"}`、
  `{"check":"歧义输入","action":"confirm","已执行步骤":["preflight"],"未通过的 gate":["G-ambiguous-src"]}`、
  `{"check":"通道不可用","action":"stop","error_codes":[...,"CHANNEL_NOT_READY"]}`、
  `{"check":"结论归类","silent":["2.7"],"stale":["6.5"],"unknown":["9.9"]}`。
  ★ 起 `G-deploy-drift` / `G-spec-baseline` / `G-tool-contract-drift` / `G-unsigned-truth` /
  `G-doc-coverage` / `G-layer-direction` / `G-selftest-baseline` 这 **7 条门禁**不在 `pipeline` 的五步里 ——
  它们由**套件与部署侧**执行（`eval/*` 与 `setup_agent.py --verify-skills`），playbook 只登记它们的
  `enforced_by`，所以 B5 只校验「步骤引用的 gate 不许悬空」，不要求它们在状态机里可达。
- ★ 把 `orchestration/preflight.py` 的噪声规则从「`AUTOSAVE` / 备份后缀」扩到「Allegro 运行痕迹 + 历史检查报告」
  （新增 `NOISE_GLOBS = checklist_report_*.json, signoise.log*, dbdoctor.log*, *.log`；`NOISE_NAMES` 增
  `master.tag` / `allegro.jrl`；后缀增 `.jrl` / `.run`）之后，**金标准模板库立刻现形**：
  `Data/footprint` 14 个子目录里 **13 个带噪声**（`allegro.jrl`、`allegro.jrl,1`、`master.tag`、`padstack_editor.jrl`、
  `signoise.log`、`dbdoctor.log`、`nb_*.log`，`3S48000163/` 里还有 **7 份 2026-09-17 的历史 `checklist_report_*.json`**）。
  也就是说：**S2 想防的「旧报告被当成本次结论」，在这个库里早就有物证了**——以前没人看见，是因为预检不报噪声。
- 扩展前（只认 AUTOSAVE/备份后缀）这些文件既不算候选也不算噪声 = **静默不可见**；扩展后它们进 `noise_files` 并在
  `hint` 里列名，S1/S2 才有落点。噪声剔除的候选账不变（`.dra`/PDF 候选数一个没变），所以 `geometry_regression`
  的 G4（库目录噪声必须为 0）会因此变红——**红的是现实，不是判据**；清理走 `.trash`（绝不 `os.remove`）。

## 相关目录

- `eval/README.md` —— 五套件总表与全局纪律
- `orchestration/preflight.py` —— 被夹具考的「歧义可见化 / 前置自检」实现（`classify_folder` 的 8 类 verdict）
- `orchestration/playbook.json` —— B5 的第一半：规则装配表（25 条 `rule_id` + 13 条 `G-*` 门禁）
- `orchestration/pipeline.py` —— B5 的第二半：五步状态机 + `evaluate_gate` / `summarize_findings` / `validate_playbook`
- `services/footprint_builder.py::inspect_environment` —— B4 的通道探针实现
- `Tools/deploy/skills/allegro-footprint-check/SKILL.md` 的「S 系硬规则」—— 本套件考的就是它
