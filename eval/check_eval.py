"""eval/check_eval.py —— 五套断言的统一入口（Phase 2-d）

## 为什么需要它

Phase 0~2 期间，验收要**手敲 5 条命令**（architecture / tool_contract / behavior /
spec_reading / geometry_regression）。漏跑一条没有任何人会知道 —— 这本身就是
本项目最恨的那类失败：**静默**。本脚本把五套断言收成一条命令，输出一张总表，
任何一套有 ERROR 就以非零码退出。

它不是"又一个真源"：判据仍然写在各自套件里（A1–A9 / C1–C6 / B1–B4 / S1–S8 / G1–G6 + R1–R4），
本脚本只负责**跑全 + 计数 + 汇总**，不复制任何阈值。

## 元判据（本脚本自己的判据）

| 号 | 判据 |
|----|------|
| E1 | 五套件**都必须跑到**：缺依赖（如 `skillbridge`）→ 记 ERROR 并给修复提示，**不静默跳过** |
| E2 | 每套件的退出码必须为 `0`（退出码是唯一权威，不靠解析人话） |
| E3 | 每套件的 `--json` 必须能解析：**解析失败 = ERROR**（否则措辞一变，汇总就悄悄少一行） |
| E4 | 汇总行数必须等于计划跑的行数（少一行说明有套件没跑起来） |

## 用法

    python eval/check_eval.py                       # 全跑（含 253 件对拍，约 2 分钟）
    python eval/check_eval.py --fast                # geometry 跳过 253 件对拍（冒烟用）
    python eval/check_eval.py --only architecture,behavior
    python eval/check_eval.py --json                # 机器可读汇总
    python eval/check_eval.py --list                # 只列套件，不跑
    python eval/check_eval.py --python <解释器>      # 指定解释器
    python eval/check_eval.py --allow-unsigned      # 起草期：允许 spec_reading 真值未签字
    python eval/check_eval.py --show-fail           # 红了的套件，附带打印它的输出尾部

★ **解释器**：五套件共用一个解释器，选择顺序与 `suites/geometry_regression` 一致
  （`--python` → `$LAYOUT_FORGE_PY` → 缓存区不受限副本 `$LAYOUT_FORGE_UNCONFINED_PY`
  → `<项目>/.venv` → 兄弟仓 `<Layout>/layout-check-mcp/.venv` → `sys.executable`）。
  本机实测：**工作区内的解释器会被沙箱掐**，geometry 的 extracta 会崩在 PAD_DEF 视图，
  分数整片变红（见 `eval/suites/geometry_regression/check_geometry_regression.py` 文件头）。
  所以这里优先选「工作区外 + 有依赖」的那一个，并在表头如实打出选中的是哪个。

★★ **光选对解释器还不够 —— 完整性级别是继承的，且换解释器逃不出去**。本机实测三组
  对照（同一脚本、同一解释器路径，只有驱动进程不同）：驱动=工作区内 `.venv`（IL=Low）
  → geometry 报 7 条 ERROR（G6 崩 + R4 四条连带假红）；驱动=缓存区副本（IL=Medium）
  → 只报 3 条真问题。原因是 `icacls` 显示**工作区那个 python.exe 自身带
  `Mandatory Label/Low Mandatory Level:(I)(NW)`**，进程 IL 取「父进程令牌」与「exe 标签」
  的较低者；连 Low 父进程去启动无标签副本，子进程也仍是 Low（实测仍是 rid=0x1000）。
  所以 `main()` 先自检 IL，**是 Low 就拒绝出分并打印正确用法**（退出码 2），
  而不是硬跑出一片假红、也不假装能自己换解释器。
  `--python` 显式指定仍然权威：给了就照用，不替用户改意图。

退出码：0 = 五套全绿；1 = 有任一套红。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]        # eval/check_eval.py -> 项目根
LAYOUT_DIR = ROOT.parent                          # Layout/（Data 在项目外一层）

#: 缓存区里那份「工作区外」的解释器副本（换机器/换缓存区用环境变量覆盖）
UNCONFINED_PY = Path(
    os.environ.get("LAYOUT_FORGE_UNCONFINED_PY")
    or (Path.home() / "WorkBuddy" / "Layout" / ".workbuddy" / "cache" / "tools"
        / "venv-unconfined" / "Scripts" / "python.exe")
)

#: SECURITY_MANDATORY_* 的 RID → 名字（只用于表头/告警文案）
_IL_NAMES = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium",
             0x3000: "High", 0x4000: "System"}


def current_integrity_rid() -> int | None:
    """读当前进程的完整性级别（IL）；读不到返回 None（不谎报成 Medium）。

    为什么需要这个：IL 是**继承**的。本机实测同一脚本、同一解释器路径，只有驱动进程
    的 IL 不同 —— 驱动=工作区 `.venv`（Low）时 geometry 报 7 条 ERROR（G6 崩 + R4 四条
    连带假红）；驱动=缓存区副本（Medium）时只报 3 条真问题。所以只"选对解释器"不够，
    还得确认**自己**不在 Low 上跑。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:  # noqa: BLE001
        return None

    TOKEN_QUERY, TokenIntegrityLevel = 0x0008, 25

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 必须声明 argtypes/restype：GetCurrentProcess 返回伪句柄 -1，
        # 不声明会被截成 32 位 int，OpenProcessToken 直接 ERROR_INVALID_HANDLE(6)。
        k32.GetCurrentProcess.restype = wt.HANDLE
        adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        adv.OpenProcessToken.restype = wt.BOOL
        adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        adv.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)
        adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]

        token = wt.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY,
                                    ctypes.byref(token)):
            return None
        try:
            size = wt.DWORD(0)
            adv.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not adv.GetTokenInformation(token, TokenIntegrityLevel, buf, size.value,
                                           ctypes.byref(size)):
                return None
            label = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
            sid = label.Label.Sid
            count = adv.GetSidSubAuthorityCount(sid).contents.value
            if not count:
                return None
            return int(adv.GetSidSubAuthority(sid, count - 1).contents.value)
        finally:
            k32.CloseHandle(token)
    except Exception:  # noqa: BLE001 - 静态环境（非 Windows / 受限）一律当作未知
        return None


def _guard_confined(has_explicit_python: bool) -> int | None:
    """自身 IL 比 Medium 低时**拒绝出分**，返回退出码 2；正常时返回 None。

    为什么是「拒绝」而不是「自己换解释器跑」：IL 取**父进程令牌**与**可执行文件标签**
    的较低者。本机 `icacls` 实测 `<项目>/.venv/Scripts/python.exe` 上带
    `Mandatory Label\\Low Mandatory Level:(I)(NW)`，缓存区那份副本上没有标签 —— 所以
    在 Low 父进程里就算启动无标签的副本，子进程仍是 Low（已实测：
    `Low 父 → unconfined 副本` 读回来还是 `IL rid=0x1000 (Low)`）。**换解释器逃不出去**，
    能做的只有如实报告，让调用方换一个不受限的启动环境（或直接以不受限副本为驱动）。

    不拦的两种情况：
      · `--python` 显式给了 → 尊重用户点名用的那个解释器，不偷偷改意图（同 pick_interpreter）
      · 非 Windows / 读不到 IL → 不猜，照常跑
    """
    if has_explicit_python:
        return None
    rid = current_integrity_rid()
    if rid is None or rid >= 0x2000:
        return None
    shown = _IL_NAMES.get(rid, hex(rid))
    print(
        f"[X] 环境无效：当前进程 IL={shown}（被沙箱压低，低于 Medium）。\n"
        f"    这样的进程拉起的 extracta 会崩在 PAD_DEF 视图（rc=3221226505），\n"
        f"    geometry 的 G6/R4 会整片假红 —— 本机实测同样脚本同样逻辑会从 3 条真问题\n"
        f"    变成 7 条 ERROR。为了不让你拿假红当回归，这里拒绝出分（退出码 2）。\n"
        f"\n"
        f"    修法：用工作区外那份无 Low 标签的解释器直接当驱动：\n"
        f"      \"{UNCONFINED_PY}\" eval/check_eval.py\n"
        f"    或在一个未被压低的终端里跑（那个环境下 IL=Medium，命令不变）。\n"
        f"    注意：`--python <工作区内解释器>` 也逃不出 Low（标签在 exe 上，是继承的）。",
        flush=True,
    )
    return 2


class Suite(NamedTuple):
    name: str
    rel: str            # 相对项目根的脚本路径
    note: str           # 一句话：这套考什么
    requires: tuple     # 需要能 import 的模块（缺 → ERROR，不跳过）
    args: tuple         # 固定附加参数


SUITES: tuple[Suite, ...] = (
    Suite("architecture", "eval/suites/architecture/check_architecture.py",
          "分层依赖 / 目录登记 / README 覆盖 / 解析失败可见（A1–A9）", (), ()),
    Suite("tool_contract", "eval/suites/tool_contract/check_tool_contract.py",
          "MCP 工具数量·名字·docstring·遥测口径（C1–C6）", (), ()),
    Suite("behavior", "eval/suites/behavior/check_behavior.py",
          "S1 歧义可见 / S2 旧报告不算证据 / S4 先探通道（B1–B4）", ("skillbridge",), ()),
    Suite("spec_reading", "eval/suites/spec_reading/check_spec_reading.py",
          "10 例读图准确率 + 定位不撒谎（S1–S8）", (), ()),
    Suite("geometry_regression", "eval/suites/geometry_regression/check_geometry_regression.py",
          "239/222/35 自检 + 253 件对拍 + 取数探针（G1–G6/R1–R4）", ("skillbridge",), ()),
)


# ------------------------------------------------------------------ 解释器
def _has_module(py: str, module: str) -> bool:
    try:
        r = subprocess.run([py, "-c", f"import {module}"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pick_interpreter(explicit: str | None) -> tuple[str, bool]:
    """返回 (解释器路径, 是否有 skillbridge)。

    ★ `--python` 是**权威**的：给了就用它，不再偷偷换成能跑的那个 —— 否则
      「显式指定一个没依赖的解释器」会被自动纠正成绿色，E1 永远不触发（静默改意图）。
    未显式指定时才按顺序探测，都不满足时返回第一个存在的解释器。
    """
    if explicit:
        return explicit, _has_module(explicit, "skillbridge")
    cands: list[Path] = []
    if os.environ.get("LAYOUT_FORGE_PY"):
        cands.append(Path(os.environ["LAYOUT_FORGE_PY"]))
    # ★ 顺序要害：**工作区外的不受限副本必须排在 `<项目>/.venv` 之前**。
    #   工作区内的解释器进程带 Low 完整性标签，它拉起的 extracta 会崩在 PAD_DEF 视图
    #   （rc=3221226505），R4/G6 整片假红 —— 本机实测：同一个脚本换个解释器就从 7 个
    #   ERROR 降到 3 个真问题。本文件头注释与 geometry 套件的候选清单都写明「优先选
    #   工作区外那个」，此前实现却把 .venv 放在前面，与设计意图相反（真 bug，非环境问题）。
    cands += [
        UNCONFINED_PY,
        ROOT / ".venv" / "Scripts" / "python.exe",
        LAYOUT_DIR / "layout-check-mcp" / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    seen: set[str] = set()
    existing: list[str] = []
    for c in cands:
        s = str(c)
        if s in seen:
            continue
        seen.add(s)
        if Path(s).is_file():
            existing.append(s)
            if _has_module(s, "skillbridge"):
                return s, True
    if existing:
        return existing[0], False
    return sys.executable, False


# ------------------------------------------------------------------ 跑一套
def _parse_json(text: str) -> dict | None:
    """套件用 --json 只打一个 JSON 对象；容错：从末尾起找第一个能解析的片段。"""
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].lstrip().startswith("{"):
            continue
        try:
            obj = json.loads("\n".join(lines[i:]))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def headline(s: Suite, payload: dict | None) -> str:
    """从各套件的 JSON 里取一句人话摘要（字段缺失就退化成 ERROR/WARN 计数）。"""
    if not payload:
        return "（无 JSON）"
    parts: list[str] = []
    counts = payload.get("counts") or {}
    err = counts.get("error")
    warn = counts.get("warn")
    if s.name == "behavior":
        parts.append(f"夹具 {payload.get('passed')}/{payload.get('fixture_count')} 通过")
        if err is None:
            err = payload.get("error_count")
    elif s.name == "spec_reading":
        overall = payload.get("overall")
        if isinstance(overall, (int, float)):
            flag = " (UNSIGNED)" if payload.get("allow_unsigned") else ""
            parts.append(f"准确率 {overall * 100:.1f}%{flag}")
        if err is None:
            err = len(payload.get("errors") or [])
        if warn is None:
            warn = len(payload.get("warns") or [])
    elif s.name == "geometry_regression":
        inv = payload.get("inventory") or {}
        if inv:
            noise = len(inv.get("noise_files") or [])
            parts.append(f"金标准 {inv.get('golden_dra')} 件 / 模板 {inv.get('template_dirs')} 目录"
                         f" / 噪声 {noise}")
    elif s.name == "tool_contract":
        base = payload.get("baseline") or {}
        tools = base.get("tools") if isinstance(base, dict) else None
        if isinstance(tools, list):
            parts.append(f"{len(tools)} 个工具（基线）")
    if warn is None:
        # 有些套件没有 WARN 概念（如 behavior）：只报 ERROR，别打成 "WARN ?" 让人以为漏了
        parts.append(f"ERROR {err if err is not None else '?'}")
    else:
        parts.append(f"ERROR {err if err is not None else '?'} / WARN {warn}")
    return " ｜ ".join(parts)


def run_suite(s: Suite, py: str, has_dep: bool, timeout: int,
              extra: tuple, allow_unsigned: bool) -> dict:
    missing = [m for m in s.requires if not has_dep]
    if missing:
        return {"name": s.name, "note": s.note, "ok": False, "rc": None, "seconds": 0.0,
                "headline": f"缺依赖 {', '.join(missing)} → 该套件无法验证（不静默跳过）",
                "error": (f"{s.name} 需要能 `import {', '.join(missing)}` 的解释器；"
                          f"当前选中：{py}。修复：按 docs/SOP-工程维护手册.md 重建 .venv，"
                          f"或用 --python <带依赖的解释器>。"),
                "stdout_tail": ""}
    args = [py, s.rel, "--json", *s.args, *extra]
    if allow_unsigned and s.name == "spec_reading":
        args.append("--allow-unsigned")
    t0 = time.time()
    try:
        r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           # 子进程自己也要用 UTF-8 写 stdout：中文 Windows 默认 GBK，
                           # 套件里打印 ✓/→ 会 UnicodeEncodeError，把「通过」变成栈回溯
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        rc, out, errout = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return {"name": s.name, "note": s.note, "ok": False, "rc": None,
                "seconds": time.time() - t0,
                "headline": f"超时（>{timeout}s）", "error": f"{s.name} 超过 {timeout}s 未结束",
                "stdout_tail": ""}
    seconds = time.time() - t0
    payload = _parse_json(out)
    if payload is None:
        return {"name": s.name, "note": s.note, "ok": False, "rc": rc, "seconds": seconds,
                "headline": "JSON 解析失败", "error": f"{s.name} --json 输出无法解析（E3）",
                "stdout_tail": (out or errout)[-1500:]}
    ok = (rc == 0)
    err = None if ok else f"{s.name} 退出码 {rc}"
    return {"name": s.name, "note": s.note, "ok": ok, "rc": rc, "seconds": seconds,
            "headline": headline(s, payload), "error": err,
            "stdout_tail": "" if ok else (out or errout)[-1500:]}


# ------------------------------------------------------------------ 汇总
def render(rows: list[dict], py: str, has_dep: bool, seconds: float, total: int,
           show_fail: bool) -> None:
    red = [r for r in rows if not r["ok"]]
    print(f"五套断言 @ {ROOT}")
    print(f" 解释器：{py}（skillbridge: {'有' if has_dep else '★ 无'}）")
    print(f" 计划 {total} 套 / 实跑 {len(rows)} 套"
          + ("　★ 少跑说明有套件没起来（见 E4）" if len(rows) != total else ""))
    print()
    for r in rows:
        mark = "ok " if r["ok"] else "ERR"
        print(f" [{mark}] {r['name']:<20} {r['headline']}"
              f" ｜ {r['seconds']:.0f}s")
    print()
    print(f"总计：{len(rows)} 套 / 绿 {len(rows) - len(red)} 套 / 红 {len(red)} 套"
          f" ｜ 用时 {seconds:.0f}s")
    if red:
        print("\n[X] 红了的套件（先改事实，不许调松阈值）")
        for r in red:
            print(f" - {r['name']}：{r['error'] or r['headline']}")
        if show_fail:
            for r in red:
                print(f"\n----- {r['name']} 输出尾部 -----")
                print(r["stdout_tail"] or "（空）")
    else:
        n = len(rows)
        scope = "五套全绿" if n == len(SUITES) else f"本批 {n} 套全绿（未跑的不算通过）"
        print(f"[✓] {scope}（L1 确定性内核 / L2 契约 / L3 编排 / L4 可观测 / L5 度量）")


def _force_utf8_stdout() -> None:
    """把本进程的 stdout/stderr 切到 UTF-8。

    ★ 2026-10-15 实测踩到：本文件给**子进程**注入了 `PYTHONIOENCODING=utf-8`，
      却忘了自己也要能打印 —— 在 GBK 控制台（Windows 默认）下，**全绿那一行**的
      `[✓]`（U+2713）直接抛 `UnicodeEncodeError: 'gbk' codec can't encode character
      '\\u2713'`，进程 exit 1：**五套明明全绿，汇总命令反而是失败**。
      更阴的是「跑了就崩」只发生在绿色分支 —— 红的分支打的是 ASCII `[X]`，
      于是「红了反而正常退出、绿了才崩」。这类 bug 只能靠**看退出码**抓（E2 的又一条理由）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 被重定向到不支持 reconfigure 的对象时忽略
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="五套断言的统一入口（Phase 2-d）")
    ap.add_argument("--json", action="store_true", help="机器可读汇总")
    ap.add_argument("--only", default=None, metavar="A,B", help="只跑指定套件（逗号分隔）")
    ap.add_argument("--fast", action="store_true", help="geometry 跳过 253 件对拍（冒烟）")
    ap.add_argument("--python", default=None, help="指定解释器（需能 import skillbridge）")
    ap.add_argument("--timeout", type=int, default=2400, help="单套件超时秒数（默认 2400）")
    ap.add_argument("--allow-unsigned", action="store_true",
                    help="起草期：允许 spec_reading 真值未签字（结果标 UNSIGNED）")
    ap.add_argument("--show-fail", action="store_true", help="红了的套件附带打印输出尾部")
    ap.add_argument("--list", action="store_true", help="只列套件，不跑")
    args = ap.parse_args(argv)

    if args.list:
        for s in SUITES:
            req = f"  需要 {', '.join(s.requires)}" if s.requires else ""
            print(f"{s.name:<20} {s.rel}{req}")
            print(f"{'':<20} {s.note}")
        return 0

    # 自身被沙箱压低（IL<Medium）时拒绝出分：这种进程拉起的 extracta 会崩，
    # geometry 会整片假红（详见 _guard_confined 的 docstring 与文件头）。
    # `--python` 显式指定时不拦 —— 用户点了名就按他的来，不偷偷改意图。
    rc = _guard_confined(bool(args.python))
    if rc is not None:
        return rc

    selected = list(SUITES)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = want - {s.name for s in SUITES}
        if unknown:
            print(f"[X] 未知套件：{', '.join(sorted(unknown))}（--list 看可用值）")
            return 1
        selected = [s for s in SUITES if s.name in want]

    py, has_dep = pick_interpreter(args.python)
    geo_extra: tuple = ("--skip-benchmark",) if args.fast else ()

    t0 = time.time()
    rows: list[dict] = []
    for s in selected:
        extra = geo_extra if s.name == "geometry_regression" else ()
        rows.append(run_suite(s, py, has_dep, args.timeout, extra, args.allow_unsigned))
    seconds = time.time() - t0

    if args.json:
        print(json.dumps({
            "project_root": str(ROOT), "interpreter": py, "has_skillbridge": has_dep,
            "planned": len(selected), "ran": len(rows),
            "green": len([r for r in rows if r["ok"]]),
            "red": len([r for r in rows if not r["ok"]]),
            "seconds": round(seconds, 1),
            "rows": [{k: v for k, v in r.items() if k != "stdout_tail"} for r in rows],
        }, ensure_ascii=False, indent=2))
    else:
        render(rows, py, has_dep, seconds, len(selected), args.show_fail)

    # E4：跑的行数必须等于计划的
    if len(rows) != len(selected):
        print(f"[X] E4：计划 {len(selected)} 套、只跑了 {len(rows)} 套")
        return 1
    return 1 if any(not r["ok"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
