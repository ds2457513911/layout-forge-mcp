# -*- coding: utf-8 -*-
"""
eval/suites/tool_contract/check_tool_contract.py —— L2 工具契约断言（防「改契约没人知道」）

纯标准库 + 静态 AST：**不需要 fastmcp、不需要项目依赖**，任何 3.10+ 解释器都能跑。

## 为什么要有这一套

工具就是**给模型看的 API**：`docstring` 是说明书、参数名是调用约定、返回结构是下游脚本
的依赖。过去改这些**没有任何东西会红** —— `Tools/selfcheck_geometry.py` 只做
`X in _mcp_src` 式字符串存在断言（见 `docs/规格书读取方案.md:227`），加一个工具、
改一个参数名、删一个 docstring，自检照样全绿。

## 判据（ERROR → 退出码 1）

| 码 | 判据 | 为什么 |
|--|--|--|
| C1 | 工具数量 == `eval/baselines/tool_contract.json` 里的数量 | 工具数变了要显式重冻基线（`--update`），不许悄悄变 |
| C2 | 工具名集合 == 基线 | 改名 / 删除 = 破坏契约（下游 SKILL 与脚本按名字调用） |
| C3 | 每个工具都有 docstring | docstring 就是接口文档；没有 = 模型只能猜 |
| C4 | 每个工具的参数名都在 docstring 里出现 | 参数改了不同步说明 = 说明书骗人 |
| C5 | 遥测事件的核心字段集 == 基线 | 观测口径是跨层约定，不能偷偷变 |
| C6 | `mcp_server/server.py` 挂了 `TelemetryMiddleware` | 没挂 = 遥测永远为空（`stats` 会一直喊缺口） |
| C7 | 每个工具在 `knowledge/tool_groups.json` 里恰好一个组，且没有幽灵名字 / 空组 | 分组是唯一需要人判断的工具事实，漏了就没人补 |
| C8 | `docs/tools-catalog.md` == 按代码+分组重算的结果 | 手抄清单会腐烂（实测漏 11 处）；生成物过期要红，不靠「记得重跑」 |

## 警告（WARN）

| 码 | 判据 |
|--|--|
| W1 | 新增了工具但基线未更新（`--update` 冻结） |
| W2 | 工具的 docstring 短于 `MIN_DOC_LEN` 字符（当作没写） |

## 用法

    python eval/suites/tool_contract/check_tool_contract.py                 # 比对基线 + C7/C8
    python eval/suites/tool_contract/check_tool_contract.py --update        # 重新冻结基线
    python eval/suites/tool_contract/check_tool_contract.py --write-catalog # 重算 docs/tools-catalog.md
    python eval/suites/tool_contract/check_tool_contract.py --json

★ 加/删工具的顺序：改 tools.py → 在 knowledge/tool_groups.json 给它分组 → `--update`
  → `--write-catalog`。C7/C8 会把漏掉的任一步变成红色。

★ 改了工具契约就**必须**跑这一套并提交新的基线 —— 基线进 git，`eval/runs/` 不进。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]        # eval/suites/tool_contract/ -> 项目根
TOOLS_PY = ROOT / "mcp_server" / "tools.py"
SERVER_PY = ROOT / "mcp_server" / "server.py"
BASELINE = ROOT / "eval" / "baselines" / "tool_contract.json"

#: docstring 短于此长度视为「没写说明书」
MIN_DOC_LEN = 60

#: 遥测事件必须存在的核心字段（可选字段如 error_* 不在内）
CORE_EVENT_FIELDS = [
    "args_hash", "args_keys", "degraded", "ms", "ok", "run_id", "schema", "step", "tool", "ts",
]


# ---------------------------------------------------------------- 静态解析（与生成器共用一份实现）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_catalog import (  # noqa: E402
    consolidate,
    freshness_problem,
    is_tool_decorator,     # noqa: F401  （供外部脚本复用）
    load_groups,
    parse_tools,
    write_catalog,
)


def telemetry_schema() -> dict:
    """读遥测层（纯标准库，可直接 import）拿到 schema 版本与核心字段。"""
    sys.path.insert(0, str(ROOT))
    from observability import telemetry                    # noqa: PLC0415
    return {
        "schema_version": telemetry.SCHEMA_VERSION,
        "core_fields": CORE_EVENT_FIELDS,
        "error_codes": sorted({"NOT_FOUND", "PERMISSION", "TIMEOUT", "DEP_MISSING",
                               "CHANNEL_DOWN", "IO_ERROR", "BAD_INPUT", "INVARIANT",
                               "INTERNAL", "CANCELLED"}
                              | {c for _, c in telemetry._ERROR_CODES}),
    }


def middleware_wired() -> bool:
    text = SERVER_PY.read_text(encoding="utf-8") if SERVER_PY.is_file() else ""
    return "TelemetryMiddleware" in text and "add_middleware" in text


def build_snapshot() -> dict:
    tools, decorators = parse_tools()
    # 基线只冻结契约字段（名字/长度/参数），生成器附带的 summary 不进基线
    tools = [{k: t[k] for k in ("name", "doc_len", "params", "undocumented")} for t in tools]
    return {
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "tool_count": len(tools),
        "decorator_count": decorators,
        "tools": tools,
        "telemetry": telemetry_schema(),
    }


# ---------------------------------------------------------------- 比对
def check() -> list:
    findings = []

    def add(code, severity, message):
        findings.append({"rule": code, "severity": severity, "message": message})

    if not BASELINE.is_file():
        add("C1", "error", f"缺基线 {BASELINE.relative_to(ROOT).as_posix()} —— 先跑 --update 冻结")
        return findings

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = build_snapshot()

    # C1 数量
    if current["tool_count"] != baseline["tool_count"]:
        add("C1", "error",
            f"工具数量 {current['tool_count']} != 基线 {baseline['tool_count']}"
            f"（同轮：knowledge/tool_groups.json 补分组 → --update → --write-catalog）")
    if current["decorator_count"] != current["tool_count"]:
        add("C1", "error",
            f"`@mcp.tool()` 装饰器 {current['decorator_count']} 个 != 解析到 {current['tool_count']} 个函数")

    # C2 名字集合
    base_names = {t["name"] for t in baseline["tools"]}
    cur_names = {t["name"] for t in current["tools"]}
    for name in sorted(base_names - cur_names):
        add("C2", "error", f"基线里的工具不见了：{name}（改名 / 删除 = 破坏契约）")
    for name in sorted(cur_names - base_names):
        add("W1", "warn", f"新增工具未进基线：{name}（确认无破坏后跑 --update 冻结）")

    # C3 / C4 / W2
    for t in current["tools"]:
        if not t["doc_len"]:
            add("C3", "error", f"{t['name']} 没有 docstring —— 模型只能靠猜")
        elif t["doc_len"] < MIN_DOC_LEN:
            add("W2", "warn", f"{t['name']} docstring 只有 {t['doc_len']} 字符（< {MIN_DOC_LEN}）")
        if t["undocumented"]:
            add("C4", "error",
                f"{t['name']} 的参数没写进 docstring：{', '.join(t['undocumented'])}")

    # C5 遥测口径
    if current["telemetry"] != baseline["telemetry"]:
        add("C5", "error",
            f"遥测口径变了：{json.dumps(current['telemetry'], ensure_ascii=False)}"
            f" != 基线 {json.dumps(baseline['telemetry'], ensure_ascii=False)}")

    # C6 接线
    if not middleware_wired():
        add("C6", "error", "mcp_server/server.py 没挂 TelemetryMiddleware —— 遥测会永远是空的")

    # C7 分组完整性（分组只有 knowledge/tool_groups.json 一处）
    try:
        groups_doc = load_groups()
    except Exception as exc:  # noqa: BLE001
        add("C7", "error", f"读不到 knowledge/tool_groups.json：{exc}")
    else:
        _, problems = consolidate(current["tools"], groups_doc)
        for p in problems:
            add("C7", "error", p)

    # C8 目录新鲜度（docs/tools-catalog.md 必须等于重算结果）
    stale = freshness_problem()
    if stale:
        add("C8", "error", stale)

    return findings


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
    ap = argparse.ArgumentParser(description="MCP 工具契约断言（L2）")
    ap.add_argument("--update", action="store_true", help="用当前状态重新冻结基线")
    ap.add_argument("--write-catalog", action="store_true", help="按代码+分组重算 docs/tools-catalog.md")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    if args.write_catalog:
        path = write_catalog()
        print(f"目录已重算：{path.relative_to(ROOT).as_posix()}")
        return 0

    if args.update:
        snap = build_snapshot()
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"基线已冻结：{BASELINE.relative_to(ROOT).as_posix()}"
              f"（{snap['tool_count']} 个工具 / schema v{snap['telemetry']['schema_version']}）")
        return 0

    findings = check()
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]
    if args.json:
        print(json.dumps({"project_root": str(ROOT), "baseline": str(BASELINE),
                          "counts": {"error": len(errors), "warn": len(warns)},
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        snap = build_snapshot()
        print(f"工具契约断言 @ {ROOT}（当前 {snap['tool_count']} 个工具）")
        print(f"  ERROR {len(errors)} 项 / WARN {len(warns)} 项")
        for label, group in (("ERROR", errors), ("WARN", warns)):
            if not group:
                continue
            print(f"\n[{label}]")
            for f in group:
                print(f"  {f['rule']:3s} · {f['message']}")
        if not findings:
            print("\n全绿 ✅")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
