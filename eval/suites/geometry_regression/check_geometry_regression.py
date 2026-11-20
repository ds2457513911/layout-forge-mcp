# -*- coding: utf-8 -*-
"""
eval/suites/geometry_regression/check_geometry_regression.py —— L5 评测（几何回归，把既有资产变成"分数"）

## 为什么要有这一套

项目里**早就有** 239 项几何自检、222 项 SKILL 语法自检、39 项约定链路自检、
253 件金标准批量对拍（票 #16 起又加了 19 项 padstack 决策入口对拍） —— 但它们一直是"**跑一下看看**"：
没人记得住上一次的分数，少跑一项、少跑一件、把某段自检注释掉，**没有任何东西会红**。
本套件把分数**冻结成基线**，并在每次改动后重跑比对：分数掉了就是回归。

判据分两段：

| 段 | 码 | 判据 | 依赖 |
|--|--|--|--|
| 盘点 | G1 | `Data/PCB_Footprint` 下 `.dra` 件数 == 基线 | 无 |
| 盘点 | G2 | `Data/footprint` 的子目录 / PDF / 真 `.dra` 数 == 基线 | 无 |
| 盘点 | G3 | `knowledge/specs`、`knowledge/cases` 条目数 == 基线 | 无 |
| 盘点 | G4 | 三个库（`Data/PCB_Footprint` + `Data/footprint` + `Data/footprint_test`）**无噪声文件**；判定**只调** `orchestration.preflight.is_noise()`（2026-10-15 起含 `*.log` / `checklist_report_*.json` / `allegro.jrl` 等运行痕迹与历史报告） | 无 |
| 盘点 | G5 | 六份检查脚本仍在（`selfcheck_geometry` / `selfcheck_skill_syntax` / `selfcheck_conventions` / `benchmark_golden` / `check_derive` / `check_rule_engine`） | 无 |
| 盘点 | G6 | **被调方探针**：extracta 四路取数健康（崩在第 2 路 → 后面三路全没跑，R4 的分数就没意义） | 需 Cadence |
| 跑分 | R1 | `Tools/selfcheck_geometry.py` 失败 0 且总数 == 基线 | 需 skillbridge |
| 跑分 | R2 | `Tools/selfcheck_skill_syntax.py` 失败 0 且总数 == 基线 | 需 skillbridge |
| 跑分 | R3 | `Tools/selfcheck_conventions.py` 失败 0 且总数 == 基线 | 无 |
| 跑分 | R4 | `Tools/benchmark_golden.py`（253 件）：件数 == 基线、**提取失败 ≤ 基线**、FAIL 件数 ≤ 基线、exact ≥ 基线（**读 `benchmark_summary.csv`，不解析控制台**） | 无 |
| 跑分 | R5 | `eval/suites/geometry_regression/check_derive.py`（票 #16）：**决策入口只依赖纯模块** + 8 份 spec 的求解输出 / 派生输出与 `eval/baselines/padstack_derive.json` 逐字段相等 | 无 |
| 跑分 | R6 | `eval/suites/geometry_regression/check_rule_engine.py`（票 #17）：规则库整体加载 + 整库哈希 + 同输入逐字节相同；坏库六类必须整体失败并报位置；阻焊规则三形态 PASS/WARN/SKIP；改库即改判定；13 件真实夹具的 2.2 状态与旧判定一致；旧阈值文件 sha256 未变 | 无 |

R4 的语义是「**不比基线差**」而不是「必须全 0」：金标准库里本来就有一批提取不出
（`extracta pins.txt 输出为空`），硬要求 0 会让闸门永远红着 ＝ 等于没有闸门。
件数/提取失败/FAIL 任一退化 → ERROR；变好不会报错（要更新基线就 `--update`）。

★ **R4 的已声明不可读（`DECLARED_UNREADABLE`，2026-10-23）**：声明制，和
`check_architecture.py::DECLARED_EXCEPTIONS` 同一套思路 —— **仍然打印，但不计入提取失败**。
准入条件两条，缺一不可：
  1. 失败原因是**宿主 Cadence 版本能力**（不是解析器 bug、不是视图语法）；
  2. 有 `extracta` 自己的原文报错作证据（不是"我猜它读不了"）。
典型例：`NB_BGA1516_35x25x1d15_WCL.dra` 在基线冻结时（2026-10-14，extracta 取自
已不存在的 `D:/Dev_tools/Cadence_17.2/…`）能读出 `P14/F0/W2/NA1`；换到本机
`C:/Software/Cadence/Cadence_SPB_17.2-2016` 后同一份文件（mtime 未变）报
`ERROR(SPMHOD-29): … Design was saved under a newer release` —— 属**环境迁移事实**，
不是产品退化。**登记不等于豁免**：未登记的新失败照样红，且每一条都靠 "≥ 基线" 兜底。

★ **G4 的语义**：库目录必须是干净的 —— 里面只该有规格书、`.dra` 与配套的 `.pad`/`.psm`。
2026-10-15 噪声名单扩到「运行痕迹 + 历史报告」后，G4 当场转红（三库共 145 个：110 `*.log` +
9 个历史 `checklist_report_*.json` + 26 个 `allegro.jrl`/`master.tag` 之类）。**这是真发现**：
历史报告正是 S2 要防的「拿旧报告当本次结论」，运行痕迹说明有人把库目录当 Allegro 工作目录用过。
处置：`services.footprint_builder._quarantine_file()` 进 `.trash/`（**绝不 `os.remove`**）；
**不许为了变绿放宽噪声名单或 G4**。

## ★ 沙箱坑（2026-10-14 本机实测，会让 R4 分数整片假红）

**解释器文件在「工作区内」还是「工作区外」，会改变 extracta 的行为。**
同一份 `python.exe`（sha256 完全一致）驱动同一个探针：

| 驱动解释器位置 | extracta 结果 |
|--|--|
| 工作区内（`layout-check-mcp/.venv`，含在工作区内新建的 venv） | PAD_DEF 视图崩，rc=`3221226505`(0xC0000409)，`pads.txt` 0 字节，几何/位号缺失 |
| 工作区外（系统 python / `%TEMP%` 里新建的 venv / 缓存区里的 venv 副本） | 四路全出（pins 420 / pads 1472 / geom 2552 / refdes 544 字节） |

同一环境下 pwsh 直接调 extracta 也四路全出 ⇒ **不是 Cadence 坏了，是被 DSH 沙箱限制的
进程在驱动它**（实测同一个受限解释器还会 `FileNotFoundError: No usable temporary directory`
——写权限被掐）。所以 R4 / G6 必须用「工作区外」的解释器跑；`PY_CANDIDATES` 因此额外登记了
缓存区里的不受限副本，也可用 `--python` / `LAYOUT_FORGE_PY` 指定。

## 解释器（R 段的关键坑）

`selfcheck_geometry.py` / `selfcheck_skill_syntax.py` 会经 `services.footprint_builder`
拉起 `services.skillbridge_writer`，后者在 **import 期**就要求 `skillbridge` 包
（`raise ImportError: 请先安装 skillbridge`）。所以 R 段不能用"任何 3.10+"解释器跑。
本脚本按顺序探测，取**第一个能 `import skillbridge`** 的：

    1. `--python <exe>`    2. 环境变量 `LAYOUT_FORGE_PY`
    3. `<项目>/.venv/Scripts/python.exe`（文档里的标准位置）
    4. ★ 缓存区里的不受限副本（本机实测：只有它在工作区外，R4/G6 不假红）
        默认 `C:/Users/ds245/WorkBuddy/Layout/.workbuddy/cache/tools/venv-unconfined/Scripts/python.exe`
        可用环境变量 `LAYOUT_FORGE_UNCONFINED_PY` 覆盖（换机器 / 换缓存区时改这里）
    5. 兄弟仓 `<Layout>/layout-check-mcp/.venv/Scripts/python.exe`（本机实测可用，但**在工作区内** → 会假红）
    6. `sys.executable`

一个都没有 → **ERROR**，而不是静默跳过（静默跳过＝这个套件白装了）。

★ 附带污染：被沙箱限制的解释器连 `%TEMP%` 都用不了，`tempfile` 会退回到**当前工作目录**，
于是项目根会长出 `try_*` / `repeat_*` 临时目录（内含 `pins.txt` / `signoise.run/`）。
见到就按缓存区纪律挪走（`<缓存区>/scratch/extracta_probes_<日期>/`），别留在仓库里。

## 用法

    python eval/suites/geometry_regression/check_geometry_regression.py                 # 全跑（253 件约数分钟）
    python eval/suites/geometry_regression/check_geometry_regression.py --skip-benchmark  # 跳过 253 件对拍（冒烟）
    python eval/suites/geometry_regression/check_geometry_regression.py --reuse-benchmark <已抓取的输出>  # 复用对拍输出
    python eval/suites/geometry_regression/check_geometry_regression.py --update          # 重新冻结基线
    python eval/suites/geometry_regression/check_geometry_regression.py --json
    ... --python <工作区外的解释器>          # R4/G6 被沙箱掐住时（见上）
    ... --benchmark-dir <对拍输出目录>       # 默认 Tools/log/golden_benchmark

★ 基线 `eval/baselines/geometry_regression.json` **进 git**；跑分原始输出落在
`Tools/log/golden_benchmark/`（项目自己的 log 目录，不是污染）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]           # eval/suites/geometry_regression/ -> 项目根
LAYOUT_DIR = ROOT.parent                             # Layout/（Data 在项目外一层）
BASELINE = ROOT / "eval" / "baselines" / "geometry_regression.json"

TOOLS = ROOT / "Tools"
GOLDEN_LIB = LAYOUT_DIR / "Data" / "PCB_Footprint"   # 253 件金标准（无规格书）
TEMPLATE_LIB = LAYOUT_DIR / "Data" / "footprint"     # 14 个人工模板（有规格书）
TEST_LIB = LAYOUT_DIR / "Data" / "footprint_test"

#: 检查脚本（盘的是"产线还在不在"）。derive 在 eval/ 下：它是**新增**的判据
#: （票 #16 拆出决策入口后的对拍），不是既有资产，所以不落 Tools/（Tools/* 被 .gitignore
#: 整目录排除，只有白名单里的文件进 git；判据要可追溯就得进 git + 进基线）。
LEGACY_CHECKS = {
    "geometry": TOOLS / "selfcheck_geometry.py",
    "skill_syntax": TOOLS / "selfcheck_skill_syntax.py",
    "conventions": TOOLS / "selfcheck_conventions.py",
    "benchmark": TOOLS / "benchmark_golden.py",
    "derive": ROOT / "eval" / "suites" / "geometry_regression" / "check_derive.py",
    "rule_engine": ROOT / "eval" / "suites" / "geometry_regression" / "check_rule_engine.py",
}

#: 被调方探针（G6）：extracta 四路取数健康
PROBE = Path(__file__).resolve().parent / "_extracta_probe.py"
#: 对拍产物目录（R4 读这里的 benchmark_summary.csv）
BENCHMARK_DIR = TOOLS / "log" / "golden_benchmark"

#: R4 的**已声明不可读**（声明制：仍打印，但不计入提取失败）。准入条件见文件头。
#: ★ 只收“宿主 Cadence 版本能力”造成的失败，且必须有 extracta 原文报错作证据；
#:   解析器 bug / 视图语法 / 我猜它读不了 —— 一律不许登记。
#: ★ 登记不等于豁免：未登记的新失败照样让 R4 红。
#: ★ 清偿方向：换装能读该版本的 Cadence，或把这份资产重存为 17.2 可读，然后从这里删掉。
DECLARED_UNREADABLE = [
    {
        "file": "NB_BGA1516_35x25x1d15_WCL.dra",
        "reason": "本机 SPB 17.2-2016 无法读取高版本 .dra",
        "evidence": "extracta: ERROR(SPMHOD-29): Unable to open design, Design was saved "
                    "under a newer release and contains features not compatible with this "
                    "version of software.",
        "host": "C:\\Software\\Cadence\\Cadence_SPB_17.2-2016",
        "declared_at": "2026-10-23",
        "note": "基线冻结时（2026-10-14，extracta 取自已不存在的 D:\\Dev_tools\\Cadence_17.2\\…）"
                "同一份文件（mtime 未变）可读出 P14/F0/W2/NA1 —— 属环境迁移，不是产品退化。",
    },
]

#: 解释器探测顺序（除 --python / 环境变量外）
#: ★ 项目 .venv 在“工作区内”时会被 DSH 沙箱限制 → extracta 会崩（见文件头"沙箱坑"）；
#:   所以工作区外那份 venv 副本排在兄弟仓 .venv 之前（本机实测只有它 R4/G6 不假红）。
UNCONFINED_PY = Path(os.environ.get("LAYOUT_FORGE_UNCONFINED_PY")
                     or r"C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\tools\venv-unconfined\Scripts\python.exe")
PY_CANDIDATES = [
    # ★ 不受限副本必须排在 `<项目>/.venv` 之前，理由见本文件头「解释器」第 4 条：
    #   工作区内的解释器带 Low 完整性标签，extracta 会崩在 PAD_DEF（rc=3221226505）。
    UNCONFINED_PY,
    ROOT / ".venv" / "Scripts" / "python.exe",
    LAYOUT_DIR / "layout-check-mcp" / ".venv" / "Scripts" / "python.exe",
]


# ---------------------------------------------------------------- 小工具
def _findings() -> list[dict]:
    return []


def _add(findings: list[dict], rule: str, severity: str, message: str) -> None:
    findings.append({"rule": rule, "severity": severity, "message": message})


def _dra_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.dra") if p.is_file())


def _noise_predicate():
    """★ 直接复用 `orchestration.preflight.is_noise`（单一真源：调同一个函数，不是抄同一批常量）。

    曾经的写法是只取 `NOISE_NAMES / NOISE_PREFIXES / NOISE_SUFFIXES` 三个常量再自己拼判断 ——
    2026-10-15 preflight 新增 `NOISE_GLOBS`（`*.log` / `checklist_report_*.json` …）后，
    这里的判断**看不见新规则**，同一批目录同一个概念算出两个数（G4 报 26、preflight 报 145）。
    教训：复用常量 ≠ 复用规则。

    2026-10-16：这里改用**路径级**的 `preflight.is_noise_path` —— 递归扫描时会走进
    `<封装>/check_log/`，而里面的 `checklist_report_*.json` 是报告的合法归档位
    （2026-10-16 约定：每个封装一个 check_log/，报告直接搬进去），不算 G4 噪声；
    散放在封装目录根下的旧报告仍是噪声（S2）。
    """
    sys.path.insert(0, str(ROOT))
    from orchestration import preflight           # noqa: PLC0415
    return preflight.is_noise_path


def _noise_files(root: Path) -> list[Path]:
    is_noise = _noise_predicate()
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and is_noise(p.relative_to(root))]


def _inventory() -> dict:
    """G 段：纯文件盘点，不依赖任何第三方包。"""
    golden = _dra_files(GOLDEN_LIB)
    template_dirs = (sorted(p for p in TEMPLATE_LIB.iterdir() if p.is_dir())
                     if TEMPLATE_LIB.is_dir() else [])
    template_pdf = (sorted(TEMPLATE_LIB.rglob("*.pdf"))
                    if TEMPLATE_LIB.is_dir() else [])
    template_dra = _dra_files(TEMPLATE_LIB)
    specs = sorted((ROOT / "knowledge" / "specs").glob("*.json"))
    specs = [p for p in specs if not p.name.startswith("_")]
    cases = ROOT / "knowledge" / "cases"
    cases_n = len([p for p in cases.glob("*.json") if not p.name.startswith("_")]) if cases.is_dir() else 0
    noise = _noise_files(GOLDEN_LIB) + _noise_files(TEMPLATE_LIB) + _noise_files(TEST_LIB)
    return {
        "golden_dra": len(golden),
        "golden_dir": GOLDEN_LIB.relative_to(LAYOUT_DIR).as_posix(),
        "template_dirs": len(template_dirs),
        "template_pdf": len(template_pdf),
        "template_dra": len(template_dra),
        "specs": len(specs),
        "cases": cases_n,
        "noise_files": [p.relative_to(LAYOUT_DIR).as_posix() for p in noise],
    }


# ---------------------------------------------------------------- 解释器
def _has_skillbridge(py: str) -> bool:
    try:
        r = subprocess.run([py, "-c", "import skillbridge"],
                           capture_output=True, timeout=120, text=True,
                           encoding="utf-8", errors="replace")
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pick_interpreter(explicit: str | None) -> tuple[str | None, list[str]]:
    tried = []
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("LAYOUT_FORGE_PY"):
        cands.append(os.environ["LAYOUT_FORGE_PY"])
    cands += [str(p) for p in PY_CANDIDATES]
    cands.append(sys.executable)
    for py in cands:
        if py in tried:
            continue
        tried.append(py)
        if not Path(py).is_file():
            continue
        if _has_skillbridge(py):
            return py, tried
    return None, tried


# ---------------------------------------------------------------- 跑既有脚本
_RE_SUMMARY_NUM = re.compile(r"结果[:：]\s*(\d+)\s*/\s*(\d+)")


def _run(py: str, script: Path, timeout: int) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run([py, str(script)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(ROOT))
        out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        return {"rc": None, "seconds": round(time.time() - t0, 1), "timeout": True,
                "tail": f"超时（>{timeout}s）", "passed": None, "total": None, "failed": None}
    tail = "\n".join(out.strip().splitlines()[-12:])
    passed = total = failed = None
    m = _RE_SUMMARY_NUM.search(out)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        failed = total - passed
    rec = {"rc": rc, "seconds": round(time.time() - t0, 1), "timeout": False,
           "passed": passed, "total": total, "failed": failed, "tail": tail}
    if script.name == "benchmark_golden.py":
        rec.update(_parse_benchmark(out))
    return rec


def _parse_benchmark(out: str) -> dict:
    def grab(pattern: str, cast=int):
        m = re.search(pattern, out)
        return cast(m.group(1)) if m else None

    pieces = grab(r"完成[:：]\s*(\d+)\s*件")
    m = re.search(r"PASS\s*(\d+)\s*[｜|]\s*WARN\s*(\d+)\s*[｜|]\s*FAIL\s*(\d+)\s*[｜|]\s*其他/提取失败\s*(\d+)", out)
    rec: dict = {"pieces": pieces,
                 "pass": int(m.group(1)) if m else None,
                 "warn": int(m.group(2)) if m else None,
                 "fail": int(m.group(3)) if m else None,
                 "other_or_error": int(m.group(4)) if m else None}
    m = re.search(r"例外件[^：:]*[:：]\s*(\d+)\s*件", out)
    rec["exceptions"] = int(m.group(1)) if m else None
    m = re.search(r"指纹 exact 命中[:：]\s*(\d+)\s*/\s*(\d+)", out)
    rec["exact"] = int(m.group(1)) if m else None
    # 提取失败单列（= 2 的退出码来源）；benchmark 只把总数写在"其他/提取失败"里
    rec["extract_errors"] = rec["other_or_error"]
    return rec


def _parse_benchmark_csv(path: Path) -> dict | None:
    """R4 的计数来源：`benchmark_summary.csv`。

    为什么不解析控制台：控制台的"其他/提取失败"把两种东西混成一个数
    （`conclusion` 为空的件 + 真的 `extract_error` 非空的件），对拍 253 件时
    控制台写 34，而 CSV 里是 17 + 17。CSV 是逐件的账本，能算出「不比基线差」。
    """
    if not path.is_file():
        return None
    import csv                                                          # noqa: PLC0415
    rows = []
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            rows = [r for r in csv.DictReader(fh)]
    except OSError:
        return None
    if not rows:
        return None

    def _int(row: dict, key: str) -> int:
        try:
            return int(str(row.get(key) or "0").strip() or 0)
        except ValueError:
            return 0

    conclusions = [(r.get("conclusion") or "").strip().upper() for r in rows]
    matches = [(r.get("spec_match") or "").strip().lower() for r in rows]
    declared = {d["file"] for d in DECLARED_UNREADABLE}
    bad = [(r.get("file") or "").strip() for r in rows
           if (r.get("extract_error") or "").strip()]
    declared_hit = [n for n in bad if n in declared]
    undeclared_bad = [n for n in bad if n not in declared]
    return {
        "csv": path.as_posix(),
        "pieces": len(rows),
        "pass_rows": sum(1 for c in conclusions if c == "PASS"),
        "fail_rows": sum(1 for c in conclusions if c == "FAIL"),
        "warn_rows": sum(1 for c in conclusions if c == "WARN"),
        "empty_rows": sum(1 for c in conclusions if not c),
        # ★ 提取失败只算「未声明」的：已声明不可读单列成 declared_unreadable_rows，
        #   仍会打印出来（声明制不许静默），但不让闸门为环境迁移永远红着。
        "extract_errors": len(undeclared_bad),
        "extract_error_files": undeclared_bad,
        "declared_unreadable_rows": len(declared_hit),
        "declared_unreadable_files": declared_hit,
        "exception_rows": sum(1 for r in rows if _int(r, "fail") or _int(r, "warn")),
        "exact_rows": sum(1 for m in matches if m.startswith("exact")),
        "match_none_rows": sum(1 for m in matches if m == "none"),
    }


def _run_probe(py: str | None, timeout: int) -> dict:
    """G6：跑被调方探针（`_extracta_probe.py`），返回它的 JSON 记录。"""
    if not py:
        return {"ok": False, "reason": "no-interpreter",
                "detail": "没有解释器可直接判定；R1/R2 的报错里已说明原因"}
    if not PROBE.is_file():
        return {"ok": False, "reason": "no-probe", "detail": PROBE.as_posix()}
    try:
        r = subprocess.run([py, str(PROBE), "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(ROOT))
    except subprocess.SubprocessError as exc:
        return {"ok": False, "reason": "probe-crashed", "detail": f"{type(exc).__name__}: {exc}"}
    try:
        return json.loads(r.stdout)
    except (ValueError, TypeError):
        return {"ok": False, "reason": "probe-unparsable",
                "detail": (r.stdout or r.stderr or "")[-300:]}


# ---------------------------------------------------------------- 判据
def build_snapshot(py: str | None, timeout: int, skip_benchmark: bool,
                   reuse_benchmark: str | None = None,
                   benchmark_dir: Path | None = None) -> dict:
    inv = _inventory()
    runs: dict = {}
    for key in ("geometry", "skill_syntax", "conventions"):
        runs[key] = _run(py, LEGACY_CHECKS[key], timeout) if py else \
            {"rc": None, "skipped": "no-interpreter", "passed": None, "total": None,
             "failed": None, "tail": "未找到能 import skillbridge 的解释器"}
    # R5 的决策入口对拍是纯 stdlib（不 import skillbridge），所以不依赖那个解释器
    runs["derive"] = _run(py or sys.executable, LEGACY_CHECKS["derive"], timeout)
    # R6（票 #17）规则库与判定引擎：纯 stdlib，离线跑（273 个夹具 pin 数秒出分）
    runs["rule_engine"] = _run(py or sys.executable, LEGACY_CHECKS["rule_engine"], timeout)
    probe = _run_probe(py, timeout)
    runs["extracta"] = probe
    csv_path = (benchmark_dir or BENCHMARK_DIR) / "benchmark_summary.csv"
    if reuse_benchmark:
        # 复用一份已抓取的 benchmark 控制台输出（避免为了比对基线把 253 件重跑一遍）
        text = Path(reuse_benchmark).read_text(encoding="utf-8", errors="replace")
        rec = {"reused_from": reuse_benchmark, "seconds": None, "timeout": False,
               "passed": None, "total": None, "failed": None,
               "tail": "\n".join(text.strip().splitlines()[-12:])}
        rec.update(_parse_benchmark(text))
        rec["rc"] = 2 if rec.get("extract_errors") else (1 if rec.get("fail") else 0)
        rec["csv_data"] = _parse_benchmark_csv(csv_path)
        runs["benchmark"] = rec
    elif skip_benchmark:
        runs["benchmark"] = {"skipped": "by-flag", "pieces": None, "csv_data": None,
                             "tail": "本次 --skip-benchmark，未跑 253 件对拍"}
    else:
        rec = _run(py or sys.executable, LEGACY_CHECKS["benchmark"], timeout)
        rec["csv_data"] = _parse_benchmark_csv(csv_path)
        runs["benchmark"] = rec
    return {
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 2,
        "interpreter": py,
        "inventory": inv,
        "runs": runs,
    }


def check(baseline: dict, current: dict, skip_benchmark: bool) -> list[dict]:
    f = _findings()
    base_inv, cur_inv = baseline.get("inventory", {}), current["inventory"]

    # ---- G 段：盘点 ----
    for key, label, rule in (
        ("golden_dra", "金标准库 .dra 件数", "G1"),
        ("template_dirs", "模板库子目录数", "G2"),
        ("template_pdf", "模板库 PDF 数", "G2"),
        ("template_dra", "模板库 .dra 数", "G2"),
        ("specs", "knowledge/specs 条目数", "G3"),
        ("cases", "knowledge/cases 条目数", "G3"),
    ):
        if key not in base_inv:
            continue
        if cur_inv.get(key) != base_inv[key]:
            _add(f, rule, "error",
                 f"{label} 从基线 {base_inv[key]} 变成 {cur_inv.get(key)}"
                 f"（少=资产丢失；多=有意的就 --update 冻结新基线）")
    if cur_inv.get("noise_files"):
        shown = "、".join(cur_inv["noise_files"][:5])
        more = "" if len(cur_inv["noise_files"]) <= 5 else f" 等 {len(cur_inv['noise_files'])} 个"
        _add(f, "G4", "error",
             f"库目录里出现噪声文件（Allegro 自动保存产物会排在 nb_* 前面，被当成候选）：{shown}{more}"
             f" —— 走 .trash 隔离，不要 os.remove")
    for key, script in LEGACY_CHECKS.items():
        if not script.is_file():
            _add(f, "G5", "error", f"既有检查脚本不见了：{script.relative_to(ROOT).as_posix()}")

    # ---- G6：被调方（extracta）健康 ----
    probe = current["runs"].get("extracta", {})
    if not probe.get("ok"):
        reason = probe.get("reason")
        if reason in ("no-sample", "no-cdsroot", "no-extracta", "no-probe"):
            hint = {"no-sample": "探针样本 .dra 不在（资产被动过？）",
                    "no-cdsroot": "找不到 Cadence 安装（CDSROOT）—— 这台机器没装/没配",
                    "no-extracta": f"extracta.exe 不在：{probe.get('detail', '')}",
                    "no-probe": f"探针脚本不在：{probe.get('detail', '')}"}[reason]
            _add(f, "G6", "error", f"extracta 探针没跑成：{hint}")
        else:
            _add(f, "G6", "error",
                 f"extracta 探针异常（{reason}）：{probe.get('detail', '')[-200:]}")
    elif not probe.get("healthy"):
        dead = probe.get("first_dead")
        _add(f, "G6", "error",
             f"extracta 四路取数在第 {dead} 路（{probe.get('first_dead_keyword')}）崩了"
             f"（rc={probe.get('rc')}）—— 后面三路全没跑，R4 的分数不作数。"
             f"先确认解释器在**工作区外**（沙箱会掐住工作区内的解释器，见文件头「沙箱坑」）")

    # ---- R 段：跑分 ----
    base_runs, cur_runs = baseline.get("runs", {}), current["runs"]
    for key, rule, label in (("geometry", "R1", "selfcheck_geometry"),
                             ("skill_syntax", "R2", "selfcheck_skill_syntax"),
                             ("conventions", "R3", "selfcheck_conventions"),
                             ("derive", "R5", "check_derive（票 #16 决策入口对拍）"),
                             ("rule_engine", "R6", "check_rule_engine（票 #17 规则库与判定引擎）")):
        cur, base = cur_runs.get(key, {}), base_runs.get(key, {})
        if cur.get("skipped") == "no-interpreter":
            _add(f, rule, "error",
                 f"{label} 没跑：找不到能 import skillbridge 的解释器。"
                 f"装了依赖的项目 .venv 缺失时用 --python 指定（本机可用 "
                 f"layout-check-mcp/.venv/Scripts/python.exe）")
            continue
        if cur.get("timeout"):
            _add(f, rule, "error", f"{label} 超时：{cur.get('tail', '')}")
            continue
        if cur.get("total") is None:
            _add(f, rule, "error",
                 f"{label} 的汇总行解析不到（输出格式变了？）：{cur.get('tail', '')[-160:]}")
            continue
        if cur.get("failed"):
            _add(f, rule, "error", f"{label} 有 {cur['failed']} 项失败："
                                   f"{cur.get('tail', '')[-400:]}")
        if base.get("total") and cur.get("total") != base["total"]:
            _add(f, rule, "error",
                 f"{label} 检查项数从基线 {base['total']} 变成 {cur['total']}"
                 f"（少=自检被删；多=新增了，--update 冻结）")
        if cur.get("rc") not in (0, None) and not cur.get("failed"):
            _add(f, rule, "error", f"{label} 退出码 {cur['rc']}（非 0 但有解析到汇总行，需人工看）")

    # ---- R4 金标准对拍（计数一律来自 benchmark_summary.csv）----
    cur, base = cur_runs.get("benchmark", {}), base_runs.get("benchmark", {})
    if skip_benchmark or cur.get("skipped"):
        _add(f, "R4", "warn", "本次跳过了 253 件金标准对拍（--skip-benchmark）：发布前必须补跑")
        return f

    cur_csv, base_csv = cur.get("csv_data"), base.get("csv_data")
    if not cur_csv:
        _add(f, "R4", "error",
             "读不到对拍账本 benchmark_summary.csv —— 没法判「不比基线差」。"
             "先跑 python Tools/benchmark_golden.py（用**工作区外**的解释器），"
             "或用 --benchmark-dir 指定输出目录")
        return f
    if not base_csv:
        _add(f, "R4", "warn",
             "基线里没有 CSV 计数（老基线？）—— 本次只报数不判退化，"
             "确认无误后 --update 重新冻结")
    if not base_csv and not base.get("pieces"):
        return f

    if base_csv:
        # 件数：少=库被动过；多=新入库（要 --update 冻结）
        if cur_csv["pieces"] != base_csv["pieces"]:
            _add(f, "R4", "error",
                 f"金标准件数从基线 {base_csv['pieces']} 变成 {cur_csv['pieces']}"
                 f"（少=库被动过；多=新入库，--update 冻结）")
        # 已声明不可读：声明制 —— 仍然打印，但不算退化（准入条件见文件头）
        if cur_csv.get("declared_unreadable_rows"):
            names = "、".join(cur_csv["declared_unreadable_files"])
            _add(f, "R4", "warn",
                 f"{cur_csv['declared_unreadable_rows']} 件已声明不可读（不计入提取失败）：{names}"
                 f" —— 属宿主 Cadence 版本能力，换装或重存后可删除声明")
        # 提取失败 / FAIL / 例外件：只许变好，不许变差
        for key, label in (("extract_errors", "提取失败件数"), ("fail_rows", "FAIL 件数"),
                           ("exception_rows", "例外件数")):
            b, c = base_csv.get(key), cur_csv.get(key)
            if b is None or c is None:
                continue
            if c > b:
                _add(f, "R4", "error",
                     f"金标准对拍 {label}从基线 {b} 涨到 {c}（退化了）"
                     + ("　—— 先看 G6：extracta 崩了的话这个数会整片假红" if key != "exception_rows" else ""))
        if "pass_rows" in base_csv and cur_csv.get("pass_rows", 0) < base_csv["pass_rows"]:
            _add(f, "R4", "error",
                 f"金标准对拍 PASS 件数从基线 {base_csv['pass_rows']} "
                 f"掉到 {cur_csv.get('pass_rows')}")
        if cur_csv.get("exact_rows", 0) < base_csv.get("exact_rows", 0):
            _add(f, "R4", "error",
                 f"指纹 exact 命中从基线 {base_csv['exact_rows']}/{base_csv['pieces']} "
                 f"掉到 {cur_csv['exact_rows']}/{cur_csv['pieces']}")
    if base.get("extract_errors") is not None and cur.get("extract_errors") is not None \
            and not base_csv and cur["extract_errors"] > base["extract_errors"]:
        _add(f, "R4", "error",
             f"金标准对拍提取失败从基线 {base['extract_errors']} 涨到 {cur['extract_errors']}")
    return f


# ---------------------------------------------------------------- 入口
def _force_utf8_stdout() -> None:
    """GBK 控制台下 print 非 GBK 字符（✅/✓）会抛 UnicodeEncodeError → 全绿却 exit 1。

    2026-10-15 实测：`eval/check_eval.py` 给子进程注入了 `PYTHONIOENCODING=utf-8`，
    但**单跑**本套件（SOP 排障路径）没有那层保护 —— 绿色分支打印 ✅ 直接崩。
    """
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="几何回归评测（L5：把 253 件 + 三套自检变成分数）")
    ap.add_argument("--update", action="store_true", help="用当前状态重新冻结基线")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--python", default=None, help="跑 R 段用的解释器（需能 import skillbridge）")
    ap.add_argument("--skip-benchmark", action="store_true", help="跳过 253 件金标准对拍")
    ap.add_argument("--reuse-benchmark", default=None, metavar="FILE",
                    help="复用一份已抓取的 benchmark 控制台输出（避免重复跑 253 件）")
    ap.add_argument("--benchmark-dir", default=None, metavar="DIR",
                    help="对拍产物目录（默认 Tools/log/golden_benchmark，R4 读其中的 benchmark_summary.csv）")
    ap.add_argument("--timeout", type=int, default=2400, help="单个脚本超时秒数（默认 2400）")
    args = ap.parse_args()

    py, tried = pick_interpreter(args.python)
    t0 = time.time()
    bdir = Path(args.benchmark_dir) if args.benchmark_dir else None
    current = build_snapshot(py, args.timeout, args.skip_benchmark, args.reuse_benchmark, bdir)

    if args.update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n"：默认文本模式在 Windows 上会写成 CRLF，把整份基线变成「全文件改动」，
        # 掩盖真正变了的那几行（2026-11-20 票 #16 重冻时踩过一次）。基线统一 LF。
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", newline="\n",
                            encoding="utf-8")
        inv = current["inventory"]
        csv_data = current["runs"]["benchmark"].get("csv_data") or {}
        print(f"基线已冻结：{BASELINE.relative_to(ROOT).as_posix()}"
              f"（金标准 {inv['golden_dra']} 件 / 几何 {current['runs']['geometry'].get('total')} 项"
              f" / SKILL 语法 {current['runs']['skill_syntax'].get('total')} 项"
              f" / 约定 {current['runs']['conventions'].get('total')} 项"
              f" / 派生 {current['runs']['derive'].get('total')} 项"
              f" / 对拍 {csv_data.get('pieces', current['runs']['benchmark'].get('pieces'))} 件"
              f" FAIL {csv_data.get('fail_rows')} 提取失败 {csv_data.get('extract_errors')}）")
        if not py:
            print("  ⚠ 没有找到能 import skillbridge 的解释器，几何/SKILL 语法两项为空基线")
        if not current["runs"].get("extracta", {}).get("healthy"):
            _probe = current["runs"]["extracta"]
            _why = _probe.get("reason") or _probe.get("first_dead_keyword")
            print(f"  ⚠ G6 被调方探针不健康（{_why}）"
                  f" —— 这份基线的 R4 分数不可信，先解决解释器/沙箱问题再冻结")
        return 0

    if not BASELINE.is_file():
        print(f"[X] 基线不存在：{BASELINE.relative_to(ROOT).as_posix()}"
              f" —— 先跑 --update 冻结")
        return 1

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    findings = check(baseline, current, args.skip_benchmark)
    errors = [x for x in findings if x["severity"] == "error"]
    warns = [x for x in findings if x["severity"] == "warn"]

    if args.json:
        print(json.dumps({"project_root": str(ROOT), "baseline": str(BASELINE),
                          "interpreter": py, "inventory": current["inventory"],
                          "runs": {k: {kk: vv for kk, vv in v.items() if kk != "tail"}
                                   for k, v in current["runs"].items()},
                          "counts": {"error": len(errors), "warn": len(warns)},
                          "findings": findings}, ensure_ascii=False, indent=2))
        return 1 if errors else 0

    inv, runs = current["inventory"], current["runs"]
    print(f"几何回归评测 @ {ROOT}")
    print(f"  解释器：{py or '（未找到，R1/R2 记 ERROR）'}"
          + (f"  [探测过 {len(tried)} 个]" if not py else ""))
    print(f"  盘点：金标准 {inv['golden_dra']} 件 ｜ 模板库 {inv['template_dirs']} 目录/"
          f"{inv['template_pdf']} PDF/{inv['template_dra']} .dra ｜ "
          f"specs {inv['specs']} ｜ cases {inv['cases']} ｜ 噪声 {len(inv['noise_files'])}")
    for key, label in (("geometry", "selfcheck_geometry"), ("skill_syntax", "selfcheck_skill_syntax"),
                       ("conventions", "selfcheck_conventions"), ("derive", "check_derive"),
                       ("rule_engine", "check_rule_engine")):
        r = runs[key]
        state = (f"{r.get('passed')}/{r.get('total')} 通过"
                 if r.get("total") is not None else f"未跑（{r.get('skipped') or '无汇总行'}）")
        print(f"  {label:24s} {state:22s} rc={r.get('rc')} {r.get('seconds', '')}s")
    _pr = runs.get("extracta", {})
    if _pr.get("ok"):
        _state = ("四路都有产出 ✅" if _pr.get("healthy")
                  else f"第 {_pr.get('first_dead')} 路（{_pr.get('first_dead_keyword')}）崩了 rc={_pr.get('rc')} ❌")
    else:
        _state = f"没跑成（{_pr.get('reason')}）"
    print(f"  {'extracta 探针(G6)':24s} {_state}")
    b = runs["benchmark"]
    csv_data = b.get("csv_data") or {}
    print(f"  {'benchmark_golden':24s} "
          f"{csv_data.get('pieces', b.get('pieces'))} 件 "
          f"PASS {csv_data.get('pass_rows', b.get('pass'))} / "
          f"FAIL {csv_data.get('fail_rows', b.get('fail'))} / "
          f"例外 {csv_data.get('exception_rows', b.get('exceptions'))} / "
          f"提取失败 {csv_data.get('extract_errors', b.get('extract_errors'))} / "
          f"已声明不可读 {csv_data.get('declared_unreadable_rows', 0)} / "
          f"exact {csv_data.get('exact_rows')}  rc={b.get('rc')} {b.get('seconds', '')}s"
          + (f"  ← 复用 {b['reused_from']}" if b.get("reused_from") else ""))
    print(f"\n  ERROR {len(errors)} 项 / WARN {len(warns)} 项   （用时 {time.time() - t0:.0f}s）")
    for label, group in (("ERROR", errors), ("WARN", warns)):
        if not group:
            continue
        print(f"\n[{label}]")
        for x in group:
            print(f"  {x['rule']:3s} · {x['message']}")
    if not findings:
        print("\n全绿 ✅")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
