# -*- coding: utf-8 -*-
"""
services/paths.py —— 项目目录约定（**单一真源**，别再各处硬编码）

目录布局（2026-09-29 与用户约定）：

```
<PROJECT_ROOT>/
├── services/           源码
├── mcp_server/         MCP 工具层
├── .trash/             隔离区（**可恢复**，由 footprint_builder._quarantine_file 维护）
└── Tools/
    ├── *.py  *.il  *.md        CLI 脚本 + 文档（人要读/要跑的，放根目录）
    ├── test/           ★ 测试图纸干活区：.dra / .pad / .psm 等产物
    └── log/            ★ 日志区：Allegro 的 .jrl / .log、探针输出、脚本运行日志
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


def ensure_dirs() -> None:
    """确保 test / log / .trash 都存在（幂等）。"""
    for d in (TEST_DIR, LOG_DIR, TRASH_DIR):
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
    for src in srcs:
        if src == dst_dir or src in seen or not os.path.isdir(src):
            continue
        seen.add(src)
        scanned.append(as_posix(src))
        try:
            names = sorted(os.listdir(src))
        except OSError as e:                # noqa: BLE001
            skipped.append({"path": as_posix(src), "error": str(e)})
            continue

        for name in names:
            if not is_run_artifact(name):
                continue
            path = os.path.join(src, name)
            if not os.path.isfile(path):
                continue
            try:
                target = _unique_target(dst_dir, name, stamp)
                shutil.move(path, target)
                moved.append({"from": as_posix(path), "to": as_posix(target)})
                logger.debug("日志归集: %s -> %s", path, target)
            except OSError as e:
                # ★ 最常见：Allegro 还开着这个文件（共享冲突）—— 正常，跳过
                skipped.append({"path": as_posix(path), "error": str(e)})

    return {
        "log_dir": as_posix(dst_dir),
        "moved": moved,
        "skipped": skipped,
        "scanned": scanned,
    }
