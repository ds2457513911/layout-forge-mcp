# -*- coding: utf-8 -*-
"""legacy_judge.py —— #18 回滚演练适配器（#22 下线旧判定时删除）。

只在显式设置 LAYOUT_FORGE_LEGACY_JUDGE=1 时被 rule_checker 导入；默认检查
路径不碰这里。职责：把旧 convention_engine 的逐 pin 命中聚合成历史报告 id
（2.8/2.9/2.10）的 item，供「撤掉新库后判定退回旧路径且结果一致」的演练。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from services.convention_engine import check_padstack_naming, check_pin_conventions


def _item(rid: str, title: str, status: str, value: Any = None,
          rule: str = "", detail: str = "", expected: Any = None,
          source: Any = "default") -> Dict[str, Any]:
    return {
        "id": rid,
        "name": title,
        "status": status,
        "value": value,
        "rule": rule,
        "detail": detail,
        "expected": expected,
        "source": source,
    }


def evaluate_conventions(pins: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """返回 (2.8/2.9/2.10 的 items, 2.7 FAIL 明细)。

    聚合口径与 #18 之前的 rule_checker 完全一致：FAIL > WARN > PASS；
    2.8/2.10 的 NA 只作为 PASS 的附带说明；2.9 有 WARN 即 WARN。
    """
    hits: List[Tuple[str, str, str]] = []
    for pin in pins:
        hits.extend(
            (lvl, rid, f"pin{pin.get('number')}: {msg}")
            for lvl, rid, msg in check_pin_conventions(pin)
        )
        nm = check_padstack_naming(pin)
        if nm:
            hits.append(("WARN", nm[0], f"pin{pin.get('number')}: {nm[1]}"))

    items: List[Dict[str, Any]] = []
    rule_text = "（真源 conventions.json，check/create 同源）"
    for rid, title in (("2.8", "TH 环宽约定"), ("2.10", "TH 钢网策略")):
        fails = [m for lvl, r, m in hits if r == rid and lvl == "FAIL"]
        warns = [m for lvl, r, m in hits if r == rid and lvl == "WARN"]
        nas = [m for lvl, r, m in hits if r == rid and lvl == "NA"]
        if fails:
            items.append(_item(
                rid, title, "FAIL",
                value=f"{len(fails)} 处违反约定",
                rule=f"{title}{rule_text}",
                detail=" | ".join(fails[:3]),
                expected="见 detail",
            ))
        elif warns:
            items.append(_item(
                rid, title, "WARN",
                value=f"{len(warns)} 处非标准（允许但提示）",
                rule=f"{title}{rule_text}",
                detail=" | ".join(warns[:3]),
                expected="见 detail",
            ))
        else:
            items.append(_item(
                rid, title, "PASS",
                value="符合库约定",
                rule=f"{title}{rule_text}",
                detail="; ".join(nas[:2]),
                expected="见 conventions.json",
            ))

    n_warns = [m for lvl, r, m in hits if r == "2.9" and lvl == "WARN"]
    if n_warns:
        items.append(_item(
            "2.9", "TH padstack 命名语法", "WARN",
            value=f"{len(n_warns)} 处不合语法（库内存在 legacy 变体，仅提示）",
            rule="POB{孔长}X{孔短}HOB{盘长}X{盘短}（长轴在前，板面尺寸序）",
            detail=" | ".join(n_warns[:3]),
            expected="主语法或 NP/H省OB/OS偏移变体",
        ))
    else:
        items.append(_item(
            "2.9", "TH padstack 命名语法", "PASS",
            value="符合库命名语法",
            rule="POB{孔}X{孔}HOB{盘}X{盘}（长轴在前）",
            detail="", expected="主语法",
        ))

    fails_27 = [m for lvl, r, m in hits if r == "2.7" and lvl == "FAIL"]
    return items, fails_27
