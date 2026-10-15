# -*- coding: utf-8 -*-
"""
observability/runstore.py —— 遥测的**读**侧（run 列表 / 时间线）

`telemetry.py` 负责写，本模块负责把 JSONL 还原成「一次任务发生了什么」：

* `list_runs(days)`    → 每个 run_id 一行：起止时间、步数、用了哪些工具、成败、降级
* `timeline(run_id)`   → 该 run 的逐步记录（按时间排序）
* `latest_run()`       → 最近一次 run（`get_run_stats` 与排障最常用）
* `parse_errors(days)` → 坏行计数（★「静默失败清零」延伸：读不动的行必须能看见）

本层只 import `services.paths`（分层约定见 ../README.md）。
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

from observability import telemetry


def _candidate_files(days: Optional[int]) -> List[str]:
    """days=None → 全部事件文件；否则最近 days 天。"""
    if days is None:
        d = telemetry.runs_dir()
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
        except OSError:
            return []
        return [os.path.join(d, n) for n in names]
    return telemetry.recent_event_files(days)


def iter_events(days: Optional[int] = 7) -> Iterator[Dict[str, Any]]:
    """按时间顺序（文件按日期 + 文件内原序）迭代事件；坏行直接跳过。"""
    for path in _candidate_files(days):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except (ValueError, TypeError):
                        continue
        except OSError:
            continue


def parse_errors(days: Optional[int] = 7) -> int:
    """坏行数量（JSON 解析不了的行）。>0 说明有东西在往 JSONL 里写非 JSON。"""
    bad = 0
    for path in _candidate_files(days):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        json.loads(raw)
                    except (ValueError, TypeError):
                        bad += 1
        except OSError:
            continue
    return bad


def events(days: Optional[int] = 7) -> List[Dict[str, Any]]:
    """`iter_events` 的列表版（数据量是本机单机量级，直接物化更省事）。"""
    return list(iter_events(days))


def list_runs(days: Optional[int] = 7) -> List[Dict[str, Any]]:
    """把事件按 run_id 归并成 run 摘要，**按开始时间升序**。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in iter_events(days):
        rid = str(ev.get("run_id") or "")
        if not rid:
            continue
        b = buckets.setdefault(
            rid,
            {
                "run_id": rid,
                "started": ev.get("ts"),
                "ended": ev.get("ts"),
                "events": 0,
                "ok": 0,
                "failed": 0,
                "ms_total": 0,
                "tools": Counter(),
                "error_codes": Counter(),
                "degraded": 0,
            },
        )
        b["ended"] = ev.get("ts") or b["ended"]
        b["events"] += 1
        if ev.get("ok"):
            b["ok"] += 1
        else:
            b["failed"] += 1
            code = str(ev.get("error_code") or "UNKNOWN")
            b["error_codes"][code] += 1
        ms = ev.get("ms")
        if isinstance(ms, int):
            b["ms_total"] += ms
        if ev.get("tool"):
            b["tools"][str(ev["tool"])] += 1
        deg = ev.get("degraded") or []
        if deg:
            b["degraded"] += len(deg) if isinstance(deg, list) else 1

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        b["tools"] = dict(b["tools"].most_common())
        b["error_codes"] = dict(b["error_codes"].most_common())
        out.append(b)
    out.sort(key=lambda r: str(r.get("started") or ""))
    return out


def timeline(run_id: str) -> List[Dict[str, Any]]:
    """某个 run 的逐步记录（按写入顺序，即时间顺序）。"""
    return [ev for ev in iter_events(None) if str(ev.get("run_id")) == str(run_id)]


def latest_run(days: Optional[int] = 7) -> Optional[Dict[str, Any]]:
    """最近一次 run 摘要；没有遥测时返回 None（调用方据此报「无数据」，不假装有）。"""
    runs = list_runs(days)
    return runs[-1] if runs else None


def health(days: Optional[int] = 7) -> Dict[str, Any]:
    """读侧体检：有多少 run、多少事件、多少坏行、最近一次 run 的 id。"""
    runs = list_runs(days)
    return {
        "runs": len(runs),
        "events": sum(r["events"] for r in runs),
        "parse_errors": parse_errors(days),
        "latest_run_id": runs[-1]["run_id"] if runs else None,
        "window_days": days,
        "runs_dir": telemetry.runs_dir(),
    }
