# -*- coding: utf-8 -*-
"""
services/paths.py —— 项目目录约定（**单一真源**，别再各处硬编码）

目录布局（2026-10-12 重构后）：

```
<PROJECT_ROOT>/
├── services/           源码（含 json_utils / validation，无 shared/）
├── mcp_server/         MCP 工具层
├── orchestration/      ★ L3 编排与控制：规则装配 / 前置自检 / 状态机（2026-10-14 新增，见 LAYERS.md）
├── observability/      ★ L4 可观测性：遥测 / 时间线 / 统计（2026-10-14 新增）
├── eval/               ★ L5 度量与评测：suites / truth / baselines（runs/ 是产物，不进 git）
├── knowledge/          ★ 热更新数据层：specs / cases / rules（改 JSON 不用重连 MCP）
├── docs/               项目文档（SOP 维护手册、使用者 SOP、SKILL API 报告）
├── LAYERS.md           ★ 五层能力 ↔ 模块映射（分层与依赖方向的唯一权威）
├── .trash/             隔离区（**可恢复**，由 footprint_builder._quarantine_file 维护）
└── Tools/
    ├── *.py  *.il  *.md        CLI 脚本（人要读/要跑的）
    ├── test/           ★ 测试图纸干活区：每个封装一个子目录（.dra/.pad/.psm + PDF）
    ├── deploy/         ★ 一键部署：deploy.bat + setup_agent.py + skills 副本
    └── log/            ★ 日志区：.jrl/.log、master.tag、checklist_report、archive/
```

★ 为什么要 `log/` 集中：Allegro 和探针脚本会把 `.jrl` / `.log` / `master.tag`
**甩在"图纸所在目录"**（不是某个固定位置），跑几次就把干活目录搅乱。
`collect_run_logs()` 负责在跑完后把它们收拢到 `Tools/log`。

★ 为什么 `test/` 单独放图纸：`new_symbol_drawing()` 的退路模板扫描会扫**工作目录下的 `*.dra`**，
图纸和脚本混在一起时，很容易把探针脏图当成模板（→ 见该函数的污染校验）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("layout-forge")

# ============================================================
# 目录常量
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))

#: 项目根目录（services/ 的上一级）
PROJECT_ROOT = os.path.dirname(_HERE)

#: CLI 脚本与文档所在目录
TOOLS_DIR = os.path.join(PROJECT_ROOT, "Tools")

#: ★ 测试图纸干活区（新建的 symbol 图纸默认落这里）
TEST_DIR = os.path.join(TOOLS_DIR, "test")

#: ★ 日志区（Allegro 的 .jrl/.log、探针输出、脚本日志都进这里）
LOG_DIR = os.path.join(TOOLS_DIR, "log")

#: 文件隔离区（`footprint_builder._quarantine_file()` 用同一个）
TRASH_DIR = os.path.join(PROJECT_ROOT, ".trash")

#: ★ L3 编排与控制层（规则装配 / 前置自检 / 状态机）—— 2026-10-14 新增，分层见 LAYERS.md
ORCHESTRATION_DIR = os.path.join(PROJECT_ROOT, "orchestration")

#: ★ L4 可观测性层（遥测 / 时间线 / 统计）—— 2026-10-14 新增
OBSERVABILITY_DIR = os.path.join(PROJECT_ROOT, "observability")

#: ★ L5 度量与评测层（suites / truth / harness）—— 2026-10-14 新增
EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")

#: ★ 遥测 JSONL 落盘目录（每次运行的产物，**不进 git**）
RUNS_DIR = os.path.join(EVAL_DIR, "runs")

#: ★ 冻结的评测基线（**进 git**：它是「上一次被认可的行为」的唯一记录）
BASELINE_DIR = os.path.join(EVAL_DIR, "baselines")

# ------------------------------------------------------------
# ★ 工作区数据层（2026-10-16 新增）：库目录在 workspace 根，不在项目根
#
# 真实布局（刻意维持 plan A「不搬迁」，见 2026-10-14 重构计划）：
#   C:\Users\ds245\Documents\Work_Bmorn\Layout\        <- WORKSPACE_ROOT
#   ├── layout-forge-mcp\                              <- PROJECT_ROOT
#   └── Data\                                          <- DATA_DIR（三个库）
#       ├── PCB_Footprint\    大封装库（.dra，无规格书）
#       ├── footprint\        小封装库（每子目录 1 PDF + 1 .dra）
#       └── footprint_test\   测试干活区（建封装走这里）
# 注意：Data 在 workspace 根 —— geometry G3/G4、collect_run_logs、
# install_shadow_library 都要跨出 PROJECT_ROOT 才能扫到它。
# ------------------------------------------------------------

#: 工作区根（PROJECT_ROOT 的上一级）
WORKSPACE_ROOT = os.path.dirname(PROJECT_ROOT)

#: 工作区数据层（规格书 + 封装库）
DATA_DIR = os.path.join(WORKSPACE_ROOT, "Data")

#: 大封装库（253 颗 .dra，无规格书 PDF）
GOLDEN_LIB_DIR = os.path.join(DATA_DIR, "PCB_Footprint")

#: 小封装库（每个子目录 = 1 份规格书 + 1 颗 .dra）
TEMPLATE_LIB_DIR = os.path.join(DATA_DIR, "footprint")

#: 测试干活区（建封装产物落这里，check_log/ 归档位）
TEST_LIB_DIR = os.path.join(DATA_DIR, "footprint_test")


def ensure_dirs() -> None:
    """确保 test / log / .trash / eval 的 runs 目录都存在（幂等）。"""
    for d in (TEST_DIR, LOG_DIR, TRASH_DIR, RUNS_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:                # noqa: BLE001
            logger.warning("创建目录失败 %s: %s", d, e)


def as_posix(path: str) -> str:
    """统一成正斜杠（Allegro / SKILL 只认正斜杠，Windows 反斜杠会出怪事）。"""
    return str(path).replace("\\", "/")


# ============================================================
# 运行痕迹（日志）归集
# ============================================================
#: Allegro / 探针会甩在"图纸目录"里的运行痕迹文件名模式
#: ★ 只匹配**确实属于运行痕迹**的东西，绝不碰 .dra / .pad / .psm 等产物
RUN_ARTIFACT_PATTERNS: Tuple[str, ...] = (
    r".*\.jrl$",            # allegro.jrl        —— Allegro 会话日志
    r".*\.jrl,\d+$",        # allegro.jrl,1      —— 轮转后的旧会话
    r".*\.log$",            # <symbol>.log / batch_drc.log / skillbridge_*.log
    r".*\.log,\d+$",        # <symbol>.log,1     —— 轮转
    r"^master\.tag$",       # Cadence 生成的图纸索引
    r"^probe_result\.txt$", # skill_api_probe.il 的输出
)

_RUN_ARTIFACT_RE = re.compile("|".join(f"(?:{p})" for p in RUN_ARTIFACT_PATTERNS))


def is_run_artifact(name: str) -> bool:
    """判断一个文件名是否属于"运行痕迹"（该归到 `Tools/log`）。"""
    return bool(_RUN_ARTIFACT_RE.match(name))


def _unique_target(dst_dir: str, name: str, stamp: str) -> str:
    """
    在 `dst_dir` 下给 `name` 找一个不冲突的目标路径。
    重名时把时间戳插在**第一个点之前**：`x.log,1` -> `x_20260929-150000.log,1`
    （不能用 `os.path.splitext` —— 它会把 `x.log,1` 的扩展名当成 `.log,1`，
      时间戳插到末尾会变成 `x_2026...`，反而丢掉 `,1` 的可读性）
    """
    cand = os.path.join(dst_dir, name)
    if not os.path.exists(cand):
        return cand
    stem, dot, ext = name.partition(".")
    tagged = f"{stem}_{stamp}.{ext}" if dot else f"{name}_{stamp}"
    cand = os.path.join(dst_dir, tagged)
    n = 1
    while os.path.exists(cand):
        cand = os.path.join(dst_dir, f"{tagged}.{n}")
        n += 1
    return cand


def collect_run_logs(
    *src_dirs: Optional[str],
    log_dir: Optional[str] = None,
    recursive: bool = False,
) -> Dict[str, Any]:
    """
    ★ 把图纸目录里本次运行产生的日志/痕迹**搬进 `Tools/log`**。

    覆盖对象见 `RUN_ARTIFACT_PATTERNS`（Allegro 的 `.jrl`/`.log`、`master.tag`、
    探针的 `probe_result.txt`）。

    ★ 三条硬约束：
      1. **绝不抛异常** —— 日志归集失败不能把主流程（建模/编译）拖下水
      2. **被占用的文件跳过不报错** —— Allegro 运行中一直攥着 `allegro.jrl`，
         Windows 上搬不动是**正常现象**，记进 `skipped` 就行
         （想收它，等 Allegro 退出后再跑一次 `Tools/collect_logs.py`）
      3. 源目录 == 目标目录时直接跳过，绝不自搬自

    :param src_dirs: 要扫的源目录；**不传**则默认扫 `Tools/test` + `Tools/`
    :param log_dir:  目标目录；默认 `Tools/log`
    :param recursive: True 时对每个源目录做**递归一层**扫描（2026-10-16 约定：
         库目录里每个封装一个子目录，`<封装>/allegro.jrl` 只能靠递归才扫得到；
         默认 False 保持旧的单层行为，`Tools/selfcheck_skill_syntax.py` 12d 依赖它）
    :return: {"log_dir": str, "moved": [...], "skipped": [...], "scanned": [...]}
    """
    ensure_dirs()
    dst_dir = os.path.abspath(log_dir or LOG_DIR)
    srcs: List[str] = [d for d in (src_dirs or (TEST_DIR, TOOLS_DIR)) if d]
    srcs = [os.path.abspath(s) for s in srcs]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    scanned: List[str] = []

    seen: set = set()

    def _scan_dir(src: str) -> None:
        """扫一个目录（单层），把运行痕迹搬走；绝不抛异常。"""
        if src == dst_dir or src in seen or not os.path.isdir(src):
            return
        seen.add(src)
        scanned.append(as_posix(src))
        try:
            names = sorted(os.listdir(src))
        except OSError as e:                # noqa: BLE001
            skipped.append({"path": as_posix(src), "error": str(e)})
            return
        subdirs: List[str] = []
        for name in names:
            path = os.path.join(src, name)
            if os.path.isdir(path):
                if recursive:
                    subdirs.append(path)    # 递归一层：库目录的 <封装>/ 子目录
                continue
            if not is_run_artifact(name):
                continue
            try:
                target = _unique_target(dst_dir, name, stamp)
                shutil.move(path, target)
                moved.append({"from": as_posix(path), "to": as_posix(target)})
                logger.debug("日志归集: %s -> %s", path, target)
            except OSError as e:
                # ★ 最常见：Allegro 还开着这个文件（共享冲突）—— 正常，跳过
                skipped.append({"path": as_posix(path), "error": str(e)})
        for sub in subdirs:
            _scan_dir(sub)

    for src in srcs:
        _scan_dir(src)

    return {
        "log_dir": as_posix(dst_dir),
        "moved": moved,
        "skipped": skipped,
        "scanned": scanned,
    }
