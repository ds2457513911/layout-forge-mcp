# -*- coding: utf-8 -*-
"""
observability/stats.py —— 聚合统计（**一条命令 + 一个数字**）

    python -m observability.stats            # 人看的摘要（默认 7 天窗口）
    python -m observability.stats --days 1
    python -m observability.stats --json     # 机器读（MCP 工具 get_run_stats 走这个函数）

回答四类问题（这正是 2026-10-14 评价里「可观测性较弱」指的那四个）：

  1. 一次任务多少步、多少工具调用、**哪一步最慢**（`tool_stats`，读 eval/runs/*.jsonl）
  2. **哪条规则最常 FAIL**（`rule_stats`，读 Tools/log/checklist_report_*.json）
  3. 知识库长什么样（`knowledge_stats`：cases 裁定分布 / specs 数量）
  4. **现在缺什么数据**（`gaps`）—— 没数据时明确喊「没有遥测」，绝不返回一个像有数据的空壳

分层：只 import `services.paths` + 本层模块（见 ../README.md / ../../LAYERS.md）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from observability import runstore, telemetry
from services import paths


# ============================================================
# 小工具
# ============================================================
def _percentile(values: Sequence[int], q: float) -> Optional[int]:
    """最近秩百分位（样本少时比插值更不容易给出「漂亮但假」的数）。"""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q / 100.0 * len(ordered)) - 1))
    return int(ordered[idx])


def _load_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ============================================================
# 1. 遥测：工具与步骤
# ============================================================
def tool_stats(days: int = 7) -> Dict[str, Any]:
    """工具调用统计：次数 / 失败 / 降级 / p50 / p95 / 错误码分布。"""
    events = runstore.events(days)
    buckets: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "failed": 0, "degraded": 0, "ms": [], "codes": Counter()}
    )
    calls = 0
    for ev in events:
        tool = ev.get("tool")
        if not tool:
            continue
        calls += 1
        b = buckets[str(tool)]
        b["calls"] += 1
        if not ev.get("ok"):
            b["failed"] += 1
            b["codes"][str(ev.get("error_code") or "UNKNOWN")] += 1
        deg = ev.get("degraded") or []
        if deg:
            b["degraded"] += len(deg) if isinstance(deg, list) else 1
        ms = ev.get("ms")
        if isinstance(ms, int):
            b["ms"].append(ms)

    rows: List[Dict[str, Any]] = []
    for name, b in buckets.items():
        rows.append(
            {
                "tool": name,
                "calls": b["calls"],
                "failed": b["failed"],
                "degraded": b["degraded"],
                "p50_ms": _percentile(b["ms"], 50),
                "p95_ms": _percentile(b["ms"], 95),
                "error_codes": dict(b["codes"].most_common()),
            }
        )
    rows.sort(key=lambda r: (-r["calls"], r["tool"]))
    slowest = sorted(
        [r for r in rows if r["p95_ms"] is not None], key=lambda r: -int(r["p95_ms"] or 0)
    )[:5]
    return {
        "calls": calls,
        "failed": sum(r["failed"] for r in rows),
        "degraded": sum(r["degraded"] for r in rows),
        "tools": rows,
        "slowest": slowest,
    }


def step_stats(days: int = 7) -> List[Dict[str, Any]]:
    """按 `step` 归并（没有 tool 字段的事件，例如编排里的阶段标记）。"""
    buckets: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "failed": 0, "ms": []})
    for ev in runstore.events(days):
        step = ev.get("step")
        if not step or ev.get("tool"):
            continue
        b = buckets[str(step)]
        b["count"] += 1
        if not ev.get("ok"):
            b["failed"] += 1
        ms = ev.get("ms")
        if isinstance(ms, int):
            b["ms"].append(ms)
    out = [
        {"step": k, "count": v["count"], "failed": v["failed"], "p50_ms": _percentile(v["ms"], 50)}
        for k, v in buckets.items()
    ]
    out.sort(key=lambda r: -r["count"])
    return out


# ============================================================
# 2. 规则：哪条最常 FAIL
# ============================================================
def rule_stats(days: Optional[int] = None, log_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    汇总 `Tools/log/checklist_report_*.json`（工具自己写的报告 → 规则命中分布）。

    :param days: 只看最近 N 天（按文件 mtime）；None = 全量
    """
    root = log_dir or paths.LOG_DIR
    files: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith("checklist_report") and name.endswith(".json"):
                files.append(os.path.join(dirpath, name))
    if days:
        cutoff = datetime.now().timestamp() - days * 86400
        files = [f for f in files if os.path.getmtime(f) >= cutoff]

    reports = 0
    items_total = 0
    by_status: Counter = Counter()
    by_source: Counter = Counter()
    by_rule: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"name": "", "fail": 0, "warn": 0, "seen": 0})
    conclusions: Counter = Counter()
    generated: List[str] = []

    for path in files:
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        reports += 1
        conclusions[str(data.get("conclusion") or "(无)")] += 1
        if data.get("generated_at"):
            generated.append(str(data["generated_at"]))
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            items_total += 1
            status = str(item.get("status") or "?").upper()
            by_status[status] += 1
            by_source[str(item.get("source") or "default")] += 1
            key = str(item.get("id") or "?")
            rec = by_rule[key]
            rec["name"] = rec["name"] or str(item.get("name") or "")
            rec["seen"] += 1
            if status == "FAIL":
                rec["fail"] += 1
            elif status in ("WARN", "WARNING"):
                rec["warn"] += 1

    top_fail = [
        {"id": k, "name": v["name"], "fail": v["fail"], "warn": v["warn"], "seen": v["seen"]}
        for k, v in by_rule.items()
        if v["fail"] or v["warn"]
    ]
    top_fail.sort(key=lambda r: (-r["fail"], -r["warn"], r["id"]))

    return {
        "reports": reports,
        "items": items_total,
        "by_status": dict(by_status.most_common()),
        "by_source": dict(by_source.most_common()),
        "conclusions": dict(conclusions.most_common()),
        "top_fail": top_fail[:10],
        "generated_range": [min(generated), max(generated)] if generated else [],
        "dir": paths.as_posix(root),
    }


# ============================================================
# 3. 知识库
# ============================================================
def knowledge_stats() -> Dict[str, Any]:
    """cases 裁定分布 + specs 数量（只读 `knowledge/` 的 _index.json）。"""
    cases_dir = os.path.join(paths.PROJECT_ROOT, "knowledge", "cases")
    specs_dir = os.path.join(paths.PROJECT_ROOT, "knowledge", "specs")
    index = _load_json(os.path.join(cases_dir, "_index.json")) or {}
    by_verdict: Counter = Counter()
    by_family: Counter = Counter()
    saved: List[str] = []
    if isinstance(index, dict):
        for rec in index.values():
            if not isinstance(rec, dict):
                continue
            by_verdict[str(rec.get("verdict") or "(无)")] += 1
            by_family[str(rec.get("family") or "(无)")] += 1
            if rec.get("saved_at"):
                saved.append(str(rec["saved_at"]))
    specs = 0
    if os.path.isdir(specs_dir):
        specs = len([n for n in os.listdir(specs_dir) if n.endswith(".json") and not n.startswith("_")])
    return {
        "cases": len(index) if isinstance(index, dict) else 0,
        "cases_by_verdict": dict(by_verdict.most_common()),
        "cases_by_family": dict(by_family.most_common()),
        "cases_latest": max(saved) if saved else None,
        "specs": specs,
    }


# ============================================================
# 汇总
# ============================================================
def summarize(days: int = 7) -> Dict[str, Any]:
    """一次拿全：遥测 + 规则 + 知识库 + **缺口清单**。"""
    health = runstore.health(days)
    tools = tool_stats(days)
    rules = rule_stats(days=None)
    knowledge = knowledge_stats()

    gaps: List[str] = []
    if not health["events"]:
        gaps.append(
            f"eval/runs/ 最近 {days} 天没有遥测事件 —— MCP 中间件没接线，或者接线后还没跑过工具调用"
        )
    if health["parse_errors"]:
        gaps.append(f"遥测文件里有 {health['parse_errors']} 行不是合法 JSON（有东西在乱写）")
    if not rules["reports"]:
        gaps.append(f"{rules['dir']} 下没有 checklist_report_*.json —— 规则命中分布无法统计")
    if not knowledge["cases"]:
        gaps.append("knowledge/cases/_index.json 为空 —— 裁定经验还没沉淀")
    if not os.path.isdir(paths.BASELINE_DIR):
        gaps.append("eval/baselines/ 还没有冻结基线 —— 评测结果只能看绝对值，看不出退化")

    return {
        "schema": telemetry.SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": days,
        "telemetry": health,
        "tools": tools,
        "steps": step_stats(days),
        "rules": rules,
        "knowledge": knowledge,
        "gaps": gaps,
    }


# ============================================================
# CLI
# ============================================================
def _render(data: Dict[str, Any]) -> str:
    L: List[str] = []
    add = L.append
    add("=" * 68)
    add(f"layout-forge 运行统计 @ {data['generated_at']}（窗口 {data['window_days']} 天）")
    add("=" * 68)

    t = data["telemetry"]
    add("")
    add(f"[遥测] run {t['runs']} 次 / 事件 {t['events']} 条 / 坏行 {t['parse_errors']} 行")
    add(f"       目录 {t['runs_dir']}")
    tools = data["tools"]
    add(f"       工具调用 {tools['calls']} 次（失败 {tools['failed']} / 降级 {tools['degraded']}）")
    if tools["tools"]:
        add("")
        add("  工具                    调用   失败  降级   p50    p95")
        for r in tools["tools"][:12]:
            add(
                f"  {r['tool']:<22} {r['calls']:>5} {r['failed']:>6} {r['degraded']:>5}"
                f" {str(r['p50_ms']):>6} {str(r['p95_ms']):>6}"
            )
    if tools["slowest"]:
        worst = tools["slowest"][0]
        add(f"  ★ 最慢：{worst['tool']} p95={worst['p95_ms']}ms")

    if data["steps"]:
        add("")
        add("[步骤]")
        for r in data["steps"][:8]:
            add(f"  {r['step']:<26} {r['count']:>4} 次  失败 {r['failed']}  p50={r['p50_ms']}ms")

    rules = data["rules"]
    add("")
    add(f"[规则] 报告 {rules['reports']} 份 / 条目 {rules['items']} 条")
    if rules["by_status"]:
        add("       状态分布 " + "  ".join(f"{k}={v}" for k, v in rules["by_status"].items()))
    if rules["conclusions"]:
        add("       结论分布 " + "  ".join(f"{k}={v}" for k, v in rules["conclusions"].items()))
    if rules["generated_range"]:
        add(f"       报告时间 {rules['generated_range'][0]} → {rules['generated_range'][1]}")
    for r in rules["top_fail"][:8]:
        add(f"       ! {r['id']:<6} {r['name'][:24]:<26} FAIL={r['fail']} WARN={r['warn']} / 出现 {r['seen']}")

    k = data["knowledge"]
    add("")
    add(f"[知识库] cases {k['cases']} 条 / specs {k['specs']} 份")
    if k["cases_by_verdict"]:
        add("       裁定分布 " + "  ".join(f"{a}={b}" for a, b in k["cases_by_verdict"].items()))

    add("")
    if data["gaps"]:
        add(f"[缺口] {len(data['gaps'])} 项")
        for g in data["gaps"]:
            add(f"       - {g}")
    else:
        add("[缺口] 无 —— 四类数据都齐了")
    add("")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="layout-forge 运行统计（L4 可观测性）")
    parser.add_argument("--days", type=int, default=7, help="遥测窗口天数（默认 7）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给脚本/工具读）")
    args = parser.parse_args(argv)

    data = summarize(days=args.days)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(_render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
