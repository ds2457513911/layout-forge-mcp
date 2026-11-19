# -*- coding: utf-8 -*-
"""
eval/suites/tool_contract/tool_catalog.py —— 工具目录的唯一生成器

## 它解决什么

工具清单过去靠手抄：全仓 14 个文件、29 行写着数量（36 与 39 各占一半），
5f153aa 加 3 个工具改了 12 个文件仍漏 11 处；紧接着 59284ad 专为改数而提，
也只修了 5 处里的 2 处。结论：清单不能手抄，只能由代码生成。

## 真源怎么分

| 事实 | 唯一产地 | 谁读它 |
|--|--|--|
| 名字 / 参数 / docstring | `mcp_server/tools.py` 的 `@mcp.tool` | 本生成器、运行期客户端 |
| 一个工具属于哪一组（编辑判断，代码推不出） | `knowledge/tool_groups.json` | 本生成器 |
| 全仓唯一的工具清单（人读） | `docs/tools-catalog.md`（本脚本产物，勿手改） | 人 |

数量不是事实，是名字集合的长度：本文件顶上的「共 N 个」也是生成的，
所以别处（README / SKILL / SOP）不要再写数字或抄清单，指到这里即可。

## 用法

    python eval/suites/tool_contract/check_tool_contract.py --write-catalog   # 重算（改完工具或分组必跑）
    python eval/suites/tool_contract/check_tool_contract.py                   # C8 会发现它过期

输出必须逐字节确定（不写时间戳、不写绝对路径），否则 C8 每次都会红。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]        # eval/suites/tool_contract/ -> 项目根
TOOLS_PY = ROOT / "mcp_server" / "tools.py"
GROUPS_JSON = ROOT / "knowledge" / "tool_groups.json"
CATALOG_MD = ROOT / "docs" / "tools-catalog.md"


# ---------------------------------------------------------------- 静态解析
def is_tool_decorator(node: ast.AST) -> bool:
    """@mcp.tool() / @mcp.tool 都算。"""
    if isinstance(node, ast.Call):
        node = node.func
    return (isinstance(node, ast.Attribute) and node.attr == "tool"
            and isinstance(node.value, ast.Name) and node.value.id == "mcp")


def parse_tools(path: Path = TOOLS_PY) -> tuple[list[dict], int]:
    """返回 (工具列表, 装饰器计数)；工具项 = {name, summary, doc_len, params, undocumented}。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tools, decorators = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(is_tool_decorator(d) for d in node.decorator_list):
            continue
        decorators += 1
        doc = ast.get_docstring(node) or ""
        args = node.args
        params = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
                  if a.arg not in ("self", "cls")]
        undocumented = [p for p in params if p not in doc]
        tools.append({
            "name": node.name,
            "summary": _first_line(doc),
            "doc_len": len(doc),
            "params": params,
            "undocumented": undocumented,
        })
    tools.sort(key=lambda t: t["name"])
    return tools, decorators


def _first_line(doc: str) -> str:
    """docstring 的首行摘要：折成一行、转义表格分隔符；还有下文就加省略号。"""
    lines = [" ".join(l.split()) for l in (doc or "").splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return ""
    return lines[0].replace("|", "\\|") + ("…" if len(lines) > 1 else "")


def load_groups(path: Path = GROUPS_JSON) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 归拢与渲染
def consolidate(tools: list[dict], groups_doc: dict) -> tuple[list, list[str]]:
    """把工具按组归拢。返回 ([(group, members)], 问题清单)。问题清单为空 = 分组完整。"""
    problems: list[str] = []
    groups = groups_doc.get("groups") or []
    mapping = groups_doc.get("tools") or {}
    group_ids = [g["id"] for g in groups]
    names = {t["name"] for t in tools}

    for missing in sorted(names - set(mapping)):
        problems.append(f"工具未分组：{missing}（在 knowledge/tool_groups.json 里给它一个组）")
    for ghost in sorted(set(mapping) - names):
        problems.append(f"分组指向不存在的工具：{ghost}（幽灵名字，删掉或改名）")
    for name, gid in sorted(mapping.items()):
        if gid not in group_ids:
            problems.append(f"{name} 指向未定义的组：{gid}（可用组：{', '.join(group_ids)}）")

    grouped = []
    for g in groups:
        members = [t for t in tools if mapping.get(t["name"]) == g["id"]]
        if not members:
            problems.append(f"组 {g['id']}（{g['title']}）没有任何工具 —— 分组过期了")
        grouped.append((g, members))
    return grouped, problems


def render_catalog(tools: list[dict], groups_doc: dict) -> str:
    grouped, problems = consolidate(tools, groups_doc)
    bt = chr(96)
    out: list[str] = []
    out.append("# MCP 工具目录（自动生成，勿手改）")
    out.append("")
    out.append("> 生成器：" + bt + "eval/suites/tool_contract/tool_catalog.py" + bt)
    out.append("> 真源：" + bt + "mcp_server/tools.py" + bt + " 的 " + bt + "@mcp.tool" + bt
               + "（名字 / 参数 / docstring）＋ " + bt + "knowledge/tool_groups.json" + bt
               + "（分组，唯一需要人手维护的一项）")
    out.append("> 重算：" + bt + "python eval/suites/tool_contract/check_tool_contract.py --write-catalog" + bt)
    out.append("> 过期检查：契约套件 C8（本文件与代码不一致即 ERROR）")
    out.append("")
    out.append("★ 这是全仓**唯一**列出全部工具名与数量的地方。其它文档不要再抄清单、"
               "也不要再写「N 个工具」——指到这里。")
    out.append("")
    live_groups = [g for g, m in grouped if m]
    out.append(f"共 **{len(tools)}** 个工具，分 {len(live_groups)} 组。")
    for g, members in grouped:
        out.append("")
        out.append(f"## {g['title']}（{len(members)}）")
        out.append("")
        if g.get("desc"):
            out.append(f"> {g['desc']}")
            out.append("")
        out.append("| 工具 | 说明（docstring 首句） |")
        out.append("|--|--|")
        for t in members:
            out.append(f"| {bt}{t['name']}{bt} | {t['summary']} |")
    if problems:
        out.append("")
        out.append("## 分组问题（生成时发现，需人工修 tool_groups.json）")
        out.append("")
        for p in problems:
            out.append(f"- {p}")
    out.append("")
    return "\n".join(out)


def current_catalog_text() -> str:
    tools, _ = parse_tools()
    return render_catalog(tools, load_groups())


def freshness_problem() -> str | None:
    """C8 用：盘上的目录与重算结果不一致时返回人话，否则 None。"""
    if not CATALOG_MD.is_file():
        return ("docs/tools-catalog.md 不存在 —— 跑 "
                "python eval/suites/tool_contract/check_tool_contract.py --write-catalog")
    disk = CATALOG_MD.read_text(encoding="utf-8")
    fresh = current_catalog_text()
    if disk == fresh:
        return None
    disk_lines, fresh_lines = disk.splitlines(), fresh.splitlines()
    first = next((i for i, (a, b) in enumerate(zip(disk_lines, fresh_lines)) if a != b),
                 min(len(disk_lines), len(fresh_lines)))
    return (f"docs/tools-catalog.md 已过期（第 {first + 1} 行起不一致）—— 跑 "
            "python eval/suites/tool_contract/check_tool_contract.py --write-catalog 重算")


def write_catalog() -> Path:
    CATALOG_MD.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：生成物与仓库统一 LF，不然 Windows 上每次重算都会整文件翻成 CRLF
    with CATALOG_MD.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(current_catalog_text())
    return CATALOG_MD


if __name__ == "__main__":
    print(write_catalog())
