# -*- coding: utf-8 -*-
"""services/footprint_selfcheck.py —— 建完即验的出厂自检（票 #22 从 rule_checker 迁出）

为什么单独一个模块：这里要起 extracta 子进程回读 .dra，而 services/rule_checker.py
属于 A3 确定性内核（纯函数、零进程、零外部 I/O）。内核里 import extracta_reader
会让「能离线跑」这条边界名存实亡，所以把自检放到本模块，由执行器
footprint_builder 在建图后调用。

判定逻辑不重复：规则仍由 services/rule_checker.py::run_numeric_checks 跑，
阈值只来自 knowledge/rules/tolerances.json。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from services.extracta_reader import read_via_extracta
from services.rule_checker import run_numeric_checks

#: 出厂自检只看这四项（TH 构造约定）；其余规则由 check_footprint 做全量复核。
SELFCHECK_IDS = ("2.7", "2.8", "2.9", "2.10")


def verify_built_footprint(dra_path: str) -> Dict[str, Any]:
    """
    对刚建出的 .dra 做 extracta 回读 + 规则库自检（check/create 同一份真源）。

    :return: {"ok": bool|None, "issues": [(level, msg)], "is_th": bool,
              "pin_count": n}；ok=None 表示 extracta 读不了（Allegro 占用
              等），调用方降级为警告、不阻断。错误分支同样带 is_th /
              pin_count（False / 0），调用方不必再判键是否存在。
    """
    res = read_via_extracta(dra_path)
    if res.get("error"):
        return {"ok": None, "error": res["error"], "issues": [],
                "is_th": False, "pin_count": 0}
    pins = res.get("pins") or []
    layers = set()
    for p in pins:
        for pd in (p.get("pads") or []):
            if isinstance(pd, dict):
                layers.add(str(pd.get("layer", "")).upper())
    is_th = bool(layers & {"DRILL", "ETCH/BOTTOM"})
    issues: List[Tuple[str, str]] = []
    for it in run_numeric_checks(res)["items"]:
        if it.get("id") not in SELFCHECK_IDS:
            continue
        level = it.get("status")
        if level not in ("FAIL", "WARN"):
            continue
        value = it.get("value") or it.get("detail") or ""
        issues.append((level, f"[{it.get('id')}] {it.get('title')}: {value}"))
    fails = [m for level, m in issues if level == "FAIL"]
    return {"ok": not fails, "issues": issues, "is_th": is_th,
            "pin_count": len(pins)}
