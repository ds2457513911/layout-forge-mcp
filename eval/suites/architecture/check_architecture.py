# -*- coding: utf-8 -*-
"""
eval/suites/architecture/check_architecture.py —— 架构与文档断言（防 L1/L2/L3 退化）

纯标准库，不需要 Allegro、不需要项目依赖。在**项目根**跑（任何 3.10+ 解释器都行）：

    python eval/suites/architecture/check_architecture.py
    python eval/suites/architecture/check_architecture.py --json

## 判据（ERROR → 退出码 1）

| 码 | 判据 | 为什么 |
|--|--|--|
| A1 | `services/` 不得 import `mcp_server` / `orchestration` | 层方向单向：`mcp_server → orchestration → services` |
| A2 | `observability/` 不得 import `mcp_server` / `orchestration` / `services` 业务模块（`services.paths` 例外） | 埋点是跨切面叶子，否则成依赖环 |
| A3 | `CORE_MODULES` 不得 import 通道 / COM / 网络 / PDF 库 | 确定性内核对 Allegro 零依赖 = 能离线对拍金标准的前提 |
| A4 | 登记目录必须各有 `README.md` | 用文档代替反复遍历目录 |
| A5 | 登记目录的 README 文件清单表要出现该目录**每个** `.py` 的文件名 | 清单过期 = 文档骗人 |
| A6 | 代码根 3 层内含 `.py` 的目录必须已登记 | 新目录不能偷偷绕过文档 |
| A7 | 代码根下的 `.py` 必须能解析（无 BOM、无语法错） | 解析失败会被静默跳过 = 该文件的 import 断言其实没跑 |
| A8 | `docs/` 顶层每个 `.md` 必须出现在 `docs/README.md` 文件清单 | 没登记的文件 = 没人会读到它（A4/A5 只管 `.py`，文档过去无人管） |
| A9 | `docs/边界契约.md`「权威落点」列里每个路径必须存在 | 权威路径腐烂 = 契约指空，读的人被送去不存在的文件 |
| A10 | 契约「指针义务」表里每个投影文件必须还能搜到那句指针 | 投影文件悄悄改掉指向 = 读者读的是过期正文（票 #12 把 W4 升格） |

## 警告（WARN → 不影响退出码）

| 码 | 判据 |
|--|--|
| W1 | `services/` import `observability`（暂允许，需在 `LAYERS.md` 记录理由） |
| W2 | 登记目录的 README 缺「最后更新 / 最近更新」行 |
| W3 | 已声明的历史偏差（`DECLARED_EXCEPTIONS`）—— **仍然打印**，只是不计 ERROR |

★ **Phase 0 基线：现状允许非空。** 先如实记录，再在 Phase 1/3 逐条清偿；
不要为了让脚本变绿而改判据 —— 那等于把尺子锯短。

★ **2026-11-19：A5/A6 跳过 git 忽略的路径**（`.agent-cache/` 的探针、`Tools/test/` 的干活脚本）。
降的是「管错对象」，不是尺子：那些文件根本不进库，逼 README 登记它们只会制造第二份垃圾清单。
判定走 `git check-ignore`；git 不可用时退回不过滤（宁可多报）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # eval/suites/architecture/ -> 项目根

# ---------------------------------------------------------------- 判据数据
#: A3：确定性内核 —— 必须能离线跑（零 Allegro、零 COM、零网络）
CORE_MODULES = [
    "services/paths.py",
    "services/geometry_solver.py",
    "services/skill_snippets.py",
    "services/rule_checker.py",
    "services/convention_engine.py",
    # 票 #16：padstack 形态决策入口（与执行器 footprint_builder 分开后必须能离线调用）
    "services/padstack_derive.py",
    # 票 #17：规则库加载器 + 判定/派生纯函数入口（吃知识库 JSON，零 I/O 外部依赖）
    "services/rule_engine.py",
    "services/spec_library.py",
    "services/case_library.py",
    "services/json_utils.py",
    "services/validation.py",
    "services/report_renderer.py",
]

#: A3：内核禁止 import 的顶层模块 → 理由
FORBIDDEN_CORE = {
    "skillbridge": "内核不许连 Allegro（SkillBridge 通道）",
    "win32com": "COM",
    "pythoncom": "COM",
    "socket": "通道 / 网络",
    "requests": "网络",
    "urllib": "网络",
    "http": "网络",
    "fitz": "PDF 渲染（属于读取/PDF 层）",
    "pymupdf": "PDF 渲染",
    "pdfplumber": "PDF 解析",
    "PIL": "图像处理",
    "fastmcp": "MCP 框架（工具层的事）",
    "mcp_server": "层方向反了（内核 → 工具层）",
    "orchestration": "层方向反了（内核 → 编排层）",
    "subprocess": "内核应为纯函数，不起进程",
}

#: A4/A5：必须有 README、且文件清单覆盖自己 `.py` 的目录
REGISTERED_DIRS = [
    "",
    "services",
    "mcp_server",
    "knowledge",
    "knowledge/specs",
    "knowledge/cases",
    "knowledge/rules",
    "docs",
    "Tools",
    "Tools/deploy",
    "Tools/debug",
    "Tools/test",
    "Tools/log",
    "orchestration",
    "observability",
    "eval",
]

#: A6：不做「每个目录都要 README」检查的目录（由上级 README 覆盖，理由写在这）
DOC_EXEMPT_DIRS = {
    "eval/suites/architecture": "断言套件本身，规则见本文件 docstring + eval/README.md 五套件表",
    "eval/suites/tool_contract": "同 architecture：由 eval/README.md 的五套件表覆盖（判据写在脚本 docstring）",
    "eval/suites/geometry_regression": "同 architecture：把 253 件对拍、三套既有自检与票 #16 的决策入口对拍变成分数，判据（G1–G6/R1–R6）写在脚本 docstring + eval/README.md",
}

#: A6：代码根（只在这几个子树里找「未登记的代码目录」，避免扫 1,242 个日志文件）
CODE_ROOTS = ["", "services", "mcp_server", "orchestration", "observability", "eval"]

#: A6：扫描时剪掉的目录名 / 相对路径
PRUNE_NAMES = {".git", ".venv", "venv", "__pycache__", ".trash", "node_modules", ".idea", ".vscode"}
PRUNE_SUFFIX = ".egg-info"
PRUNE_RELPATHS = {"Tools/log", "Tools/test", "Tools/testdata", "Tools/deploy/skills", "Tools/deploy/scripts"}

# observability/ 唯一允许的 services 依赖（目录真源）；其余一律算越界
OBS_ALLOWED_SERVICES = {"services.paths"}

#: W3：已声明的历史偏差 —— 打印但不算 ERROR。清偿后从这里删掉。
DECLARED_EXCEPTIONS = [
    {
        "path": "services/skillbridge_writer.py",
        "module": "mcp_server",
        "reason": "`AllegroSession.__init__` 里 `from mcp_server import config` 取 `SKILLBRIDGE_WORKSPACE_ID`（服务层反向依赖工具层）。清偿方向：把该默认值下沉到 services（如 paths.py / 新的 services 配置模块），或改由调用方传入。",
    },
]

#: A8/A9/A10：边界契约与 docs 索引
CONTRACT_DOC = "docs/边界契约.md"
DOCS_INDEX = "docs/README.md"


# ---------------------------------------------------------------- 工具
def _imports(py: Path):
    """返回 [(顶层模块, 完整模块, 行号, 导入名元组)]；语法错误返回 None。

    第 4 位（导入名）用于区分 `from services import paths`（合法）与
    `from services import footprint_builder`（越界）—— 光看 `node.module` 两者都是 "services"。
    """
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((a.name.split(".")[0], a.name, node.lineno, (a.name,)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # 相对 import 不参与层方向判断
                continue
            if node.module:
                names = tuple(a.name for a in node.names) or (node.module,)
                found.append((node.module.split(".")[0], node.module, node.lineno, names))
    return found


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _git_ignored(rel_paths):
    """返回被 .gitignore 忽略的相对路径集合（posix 形式）。

    A5/A6 只该管**仓库里的**代码：`Tools/test/`（图纸干活区）与 `.agent-cache/`（会话探针缓存）
    都是 git 忽略的目录，逼 README 去登记里面的临时脚本，只会把清单变成第二份垃圾。
    git 不可用时返回空集 —— 断言按原样跑（宁可多报，不静默放过）。
    """
    rels = [r for r in rel_paths if r]
    if not rels:
        return set()
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-z", "--stdin"],
            input="\0".join(rels).encode("utf-8"),
            capture_output=True, check=False)
    except (OSError, ValueError):
        return set()
    if proc.returncode not in (0, 1):
        return set()
    return {p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p}


def _dir_py(rel: str):
    d = ROOT / rel if rel else ROOT
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file() and f.name.endswith(".py"))


def _readme_text(rel: str):
    p = (ROOT / rel / "README.md") if rel else (ROOT / "README.md")
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _iter_code_files():
    """代码根下的所有 `.py`（与 A6 同一套剪枝规则），供 A7 用。"""
    for root in CODE_ROOTS:
        base = ROOT / root if root else ROOT
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            d = Path(dirpath)
            rel = _rel(d) if d != ROOT else ""
            dirnames[:] = [n for n in dirnames
                           if n not in PRUNE_NAMES and not n.endswith(PRUNE_SUFFIX)
                           and (f"{rel}/{n}".lstrip("/") not in PRUNE_RELPATHS)]
            if d != ROOT and len(d.relative_to(ROOT).parts) > 3:
                dirnames[:] = []
                continue
            for f in filenames:
                if f.endswith(".py"):
                    yield d / f


def _table_rows(text: str, header_keyword: str):
    """取 Markdown 表格：找含 header_keyword 的表头行，跳过 `|---|`，收到非 `|` 行止。

    返回 [[单元格, ...], ...]；单元格已 strip `|` 与空白。找不到表头返回 []。
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if header_keyword in line and line.lstrip().startswith("|"):
            rows = []
            for row_line in lines[i + 2:]:          # +2：跳过表头与分隔行
                if not row_line.lstrip().startswith("|"):
                    break
                rows.append([c.strip() for c in row_line.strip().strip("|").split("|")])
            return rows
    return []


def _contract_authorities(text: str):
    """契约「事实归属」表第 2 列（权威落点）里带 `/` 的反引号 token。"""
    for row in _table_rows(text, "权威落点"):
        if len(row) < 2:
            continue
        for token in re.findall(r"`([^`]+)`", row[1]):
            if "/" in token and " " not in token:
                yield token


def _contract_pointers(text: str):
    """契约「指针义务」表：(投影文件, 必须还能搜到的那句话)。"""
    for row in _table_rows(text, "投影文件"):
        if len(row) >= 2 and row[0].startswith("`"):
            yield row[0].strip("`"), row[1].strip("`")


# ---------------------------------------------------------------- 检查
def check():
    findings = []

    def add(code, severity, message, path=""):
        findings.append({"rule": code, "severity": severity, "path": path, "message": message})

    exceptions = {(e["path"], e["module"]) for e in DECLARED_EXCEPTIONS}

    # A1 / W1：services/ 层方向
    for py in sorted((ROOT / "services").glob("*.py")):
        for top, full, line, _names in (_imports(py) or []):
            rel = _rel(py)
            if top in ("mcp_server", "orchestration"):
                if (rel, top) in exceptions:
                    why = next(e["reason"] for e in DECLARED_EXCEPTIONS
                               if e["path"] == rel and e["module"] == top)
                    add("W3", "warn", f"{rel}:{line} import {full} —— 已声明偏差：{why}", rel)
                else:
                    add("A1", "error", f"{rel}:{line} import {full}（services 不得依赖 {top}）", rel)
            elif top == "observability":
                add("W1", "warn", f"{rel}:{line} import {full}（services 依赖埋点层，需在 LAYERS.md 记录理由）", rel)

    # A2：observability/ 跨切面叶子
    obs = ROOT / "observability"
    if obs.is_dir():
        for py in sorted(obs.rglob("*.py")):
            for top, full, line, names in (_imports(py) or []):
                rel = _rel(py)
                if top in ("mcp_server", "orchestration"):
                    add("A2", "error", f"{rel}:{line} import {full}（observability 不得依赖 {top}）", rel)
                elif top == "services":
                    allowed = full in OBS_ALLOWED_SERVICES or (
                        full == "services" and set(names) <= {"paths"})
                    if not allowed:
                        add("A2", "error",
                            f"{rel}:{line} import {full}（observability 只允许 services.paths）", rel)

    # A3：确定性内核
    for rel in CORE_MODULES:
        py = ROOT / rel
        if not py.is_file():
            add("A3", "warn", f"内核清单里的文件不存在：{rel}", rel)
            continue
        for top, full, line, _names in (_imports(py) or []):
            if top in FORBIDDEN_CORE:
                add("A3", "error", f"{rel}:{line} import {full} —— {FORBIDDEN_CORE[top]}", rel)

    # A4 / A5 / W2：README 与文件清单
    for rel in REGISTERED_DIRS:
        text = _readme_text(rel)
        where = rel or "<root>"
        if text is None:
            add("A4", "error", f"{where} 缺 README.md", where)
            continue
        if ("最后更新" not in text) and ("最近更新" not in text):
            add("W2", "warn", f"{where}/README.md 缺「最后更新 / 最近更新」行", where)
        names = _dir_py(rel)
        ignored = _git_ignored([f"{rel}/{n}" if rel else n for n in names])
        missing = [n for n in names
                   if n not in text and (f"{rel}/{n}" if rel else n) not in ignored]
        if missing:
            add("A5", "error",
                f"{where}/README.md 文件清单未覆盖 {len(missing)} 个 .py：{', '.join(missing)}", where)

    # A6：未登记的代码目录
    # ★ CODE_ROOTS 里的子根（eval / services / ...）本身也在根遍历范围内，
    #   同一个目录会被走到两次 → 用 checked 去重，避免同一问题报两行（2026-10-14）。
    checked_a6 = set()
    a6_candidates = []
    for root in CODE_ROOTS:
        base = ROOT / root if root else ROOT
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            d = Path(dirpath)
            rel = _rel(d) if d != ROOT else ""
            dirnames[:] = [n for n in dirnames
                           if n not in PRUNE_NAMES and not n.endswith(PRUNE_SUFFIX)
                           and (f"{rel}/{n}".lstrip("/") not in PRUNE_RELPATHS)]
            if d != ROOT and len(d.relative_to(ROOT).parts) > 3:
                dirnames[:] = []
                continue
            if not any(f.endswith(".py") for f in filenames):
                continue
            if rel in checked_a6:
                continue
            checked_a6.add(rel)
            if rel in REGISTERED_DIRS or rel in DOC_EXEMPT_DIRS:
                continue
            if (d / "README.md").is_file():
                continue
            a6_candidates.append(rel)
    ignored_a6 = _git_ignored(a6_candidates)
    for rel in a6_candidates:
        if rel in ignored_a6:
            continue
        add("A6", "error",
            f"{rel or '<root>'} 含 .py 但未登记：补进 REGISTERED_DIRS（并写 README），或列入 DOC_EXEMPT_DIRS", rel)

    # A7：解析失败必须可见 —— 静默跳过等于该文件的 import 断言从未执行
    for py in _iter_code_files():
        if _imports(py) is None:
            rel = _rel(py)
            add("A7", "error",
                f"{rel} 无法解析为 Python（BOM 或语法错）→ 该文件的 import 断言未生效", rel)

    # A8：docs/ 顶层每个 .md 都要登记进 docs/README.md 的文件清单
    docs_dir = ROOT / "docs"
    index_path = ROOT / DOCS_INDEX
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    if docs_dir.is_dir():
        if not index_text:
            add("A8", "error", f"{DOCS_INDEX} 不存在 —— docs/ 的索引没了，新文档没人能找到", DOCS_INDEX)
        for md in sorted(docs_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            if md.name not in index_text:
                add("A8", "error", f"docs/{md.name} 未登记进 {DOCS_INDEX} 的文件清单", f"docs/{md.name}")

    # A9 / A10：边界契约 —— 权威落点必须存在；指针义务里的那句话必须还在
    contract_path = ROOT / CONTRACT_DOC
    if not contract_path.is_file():
        add("A9", "error", f"{CONTRACT_DOC} 不存在 —— 它是「哪类事实归哪一层」的唯一权威", CONTRACT_DOC)
    else:
        contract_text = contract_path.read_text(encoding="utf-8")
        authorities = list(_contract_authorities(contract_text))
        if not authorities:
            add("A9", "error", f"{CONTRACT_DOC} 的「事实归属」表读不出任何权威路径（表头或反引号格式被改坏了）", CONTRACT_DOC)
        for path in authorities:
            if not (ROOT / path).exists():
                add("A9", "error", f"{CONTRACT_DOC} 的权威落点不存在：{path}", CONTRACT_DOC)
        for path, needle in _contract_pointers(contract_text):
            p = ROOT / path
            if not p.is_file():
                add("A10", "error", f"指针义务的文件不存在：{path}", CONTRACT_DOC)
            elif needle not in p.read_text(encoding="utf-8"):
                add("A10", "error", f"{path} 已搜不到指针「{needle}」—— 投影文件必须指回权威，否则读者读的是过期正文", path)

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
    ap = argparse.ArgumentParser(description="架构与文档断言（L1/L2/L3 防退化）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    findings = check()
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]

    if args.json:
        print(json.dumps({"project_root": str(ROOT), "counts": {"error": len(errors), "warn": len(warns)},
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(f"架构断言 @ {ROOT}")
        print(f"  ERROR {len(errors)} 项 / WARN {len(warns)} 项")
        for label, group in (("ERROR", errors), ("WARN", warns)):
            if not group:
                continue
            print(f"\n[{label}]")
            for f in group:
                print(f"  {f['rule']:3s} · {f['message']}")
        if not findings:
            print("\n全绿 ✅")
        elif errors:
            print("\n★ Phase 0 现状基线：红是预期的，逐条清偿（先在 LAYERS.md 登记）。")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
