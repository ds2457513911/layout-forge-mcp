# -*- coding: utf-8 -*-
"""R7 · 判定侧其余三条平移 + 影子比对（票 #18）

判据（R7.1–R7.9）：

  R7.1 规则库对象 —— 2.8/2.9/2.10 的规则、参数、severity 全部在
       knowledge/rules 里，规则带 report_id（历史编号映射），finding 带
       direction=assert；加载后整库哈希是 64 位 hex。
  R7.2 环宽三档 —— [0.2,0.3] PASS；[0.15,0.2)∪(0.3,0.4] WARN；其余 FAIL；
       NP 件按数据里的 exempt 判 PASS；缺观测 SKIP 且写明 UNKNOWN。
  R7.3 命名反推 —— 名字里的数字必须与实测孔/盘尺寸相符（tol 0.011），
       长轴在前；支持 NP 前缀、H 后 OB 省略、OS 偏移后缀、盘段省略（仅 NP）；
       圆孔用 PC{孔}HC{盘}。
  R7.4 钢网三策略 —— 顶面钢网必须存在、尺寸=焊盘（|差|<=0.01）、底面不得有；
       违反都是 WARN。
  R7.5 检查器接线 —— 仍产出历史 id 2.8/2.9/2.10，finding 带 rule_id、
       direction、rule_set_hash；不再直接 import 旧判定函数。
  R7.6 UNKNOWN 不判 PASS —— judge 缺观测时 SKIP，detail 含「输入 UNKNOWN」。
  R7.7 253 件影子 —— eval/baselines/shadow_2_8_10_253.json 记录 253 件、
       逐件比 id+status 差异 0；rows/diffs/errors 交叉重算，影子哈希对应当前库；
       仓内生成器 eval/suites/geometry_regression/make_shadow_2_8_10.py 可重跑。
  R7.8 缺规则演练 —— 撤掉本票新增规则时 2.8/2.9/2.10 只能 NA（缺规则 SKIP），
       绝不 PASS；完整库上至少一项非 NA（对拍，证明不是全空）。
  R7.9 全量影子 —— eval/baselines/shadow_full_253.json：253 件跑整份 items 的
       (id,status) 序列差异 0；18 件语料不可读逐条登记原因；仓里的生成器可重跑，
       本条还会用当前代码实跑一遍；**缺语料直接红**（闸门的意义就是真跑，不降级）。

跑法（项目根，离线、不需要 Allegro）：
  python eval/suites/geometry_regression/check_rule_shift.py
  python eval/suites/geometry_regression/check_rule_shift.py --json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RULES_DIR = ROOT / "knowledge" / "rules"
SHADOW = ROOT / "eval" / "baselines" / "shadow_2_8_10_253.json"


class Checker:
    def __init__(self) -> None:
        self.n_pass = 0
        self.fails: List[str] = []
        self.items: List[Dict[str, Any]] = []

    def check(self, ok: bool, desc: str, detail: str = "") -> None:
        self.items.append({"desc": desc, "ok": bool(ok), "detail": detail})
        if ok:
            self.n_pass += 1
        else:
            self.fails.append(f"{desc}{('：' + detail) if detail else ''}")


def _item(items: list, id_: str) -> dict:
    return next(i for i in items if i.get("id") == id_)


def _pad(layer: str, size, figure_name: str = "", hole_type: str = "") -> dict:
    p = {"layer": layer, "size": list(size)}
    if figure_name:
        p["figure_name"] = figure_name
    if hole_type:
        p["hole_type"] = hole_type
    return p


def _th_pin(
    name: str = "POB1D6X0D9HOB2D2X1D5",
    hole=(0.9, 1.6),
    pad=(1.5, 2.2),
    *,
    hole_type: str = "OVAL SLOT",
    figure_name: str = "OBLONG_Y",
    paste_top=True,
    paste_size=None,
    paste_bottom=False,
    number: str = "1",
) -> dict:
    pads = [
        _pad("ETCH/TOP", pad),
        _pad("ETCH/BOTTOM", pad),
        _pad("PIN/SOLDERMASK_TOP", (pad[0] + 0.1, pad[1] + 0.1)),
        _pad("PIN/SOLDERMASK_BOTTOM", (pad[0] + 0.1, pad[1] + 0.1)),
        _pad("DRILL", hole, figure_name=figure_name, hole_type=hole_type),
    ]
    if paste_top:
        pads.append(_pad("PIN/PASTEMASK_TOP", paste_size or pad))
    if paste_bottom:
        pads.append(_pad("PIN/PASTEMASK_BOTTOM", paste_size or pad))
    return {"number": number, "name": name, "pads": pads}


def _subject(name: str, hole_type: str = "OVAL SLOT", mount: str = "tht") -> dict:
    return {
        "id": "1",
        "mount": mount,
        "layers": ["ETCH/TOP", "DRILL"],
        "drill": {"shape": hole_type},
        "name": name,
        "pad_count": 2,
    }


# ---------------------------------------------------------------- R7.1
def check_library(c: Checker, RE) -> Any:
    lib = RE.load_library(RULES_DIR)
    c.check(isinstance(lib.fingerprint, str) and len(lib.fingerprint) == 64,
            "R7.1 整库哈希是 64 位 hex", repr(lib.fingerprint))

    want = {
        "RULE-ANNULAR-RING": "2.8",
        "RULE-PADSTACK-NAMING": "2.9",
        "RULE-PASTE-TOP-PRESENT": "2.10",
        "RULE-PASTE-TOP-EQ-PAD": "2.10",
        "RULE-PASTE-BOTTOM-ABSENT": "2.10",
    }
    for rid, report_id in want.items():
        rule = lib.rules_by_id.get(rid)
        c.check(rule is not None and rule.get("report_id") == report_id,
                f"R7.1 {rid} 在库且 report_id={report_id}",
                "" if rule else "规则不存在")
    for pid in ("PARAM-ANNULAR-RING", "PARAM-NAMING-TH-OBLONG",
                "PARAM-NAMING-TH-CIRCLE", "PARAM-PASTE-TOP-PRESENT",
                "PARAM-PASTE-TOP-DIFF", "PARAM-PASTE-BOTTOM-PRESENT"):
        c.check(pid in lib.parameters_by_id, f"R7.1 参数 {pid} 在库")

    finding = RE.judge(lib, "RULE-ANNULAR-RING",
                       _subject("POB1D6X0D9HOB2D2X1D5"),
                       {"ring_x": 0.25, "ring_y": 0.25})
    c.check(finding.get("direction") == "assert",
            "R7.1 finding 标出 assert 方向", repr(finding.get("direction")))

    # 新增校验分支的负例：坏库必须拒绝加载，不能静默降级。
    def _mut(label, mutate, needle):
        tmp = Path(tempfile.mkdtemp(prefix="r7_bad_"))
        try:
            for src in RULES_DIR.glob("*.json"):
                shutil.copy2(src, tmp / src.name)
            mutate(tmp)
            try:
                RE.load_library(tmp)
                c.check(False, f"R7.1 坏库拒绝：{label}", "竟然加载成功")
            except RE.RuleLibraryError as exc:
                txt = json.dumps(getattr(exc, "errors", None) or [], ensure_ascii=False)
                c.check(needle in txt, f"R7.1 坏库拒绝：{label}", txt[:300])
            except Exception as exc:                       # noqa: BLE001
                c.check(False, f"R7.1 坏库拒绝：{label}",
                        f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _edit(datafile, obj_id, fn):
        p = Path(datafile)
        data = json.loads(p.read_text(encoding="utf-8"))
        target = next(i for i in data["items"] if i["id"] == obj_id)
        fn(target)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")

    _mut("warn 越出 allowed",
         lambda d: _edit(d / "parameters.json", "PARAM-ANNULAR-RING",
                         lambda x: x.update(warn=[0.1, 0.2])),
         "warn")
    _mut("命名 grammar 不在白名单",
         lambda d: _edit(d / "parameters.json", "PARAM-NAMING-TH-OBLONG",
                         lambda x: x.update(grammar="TH-NOPE")),
         "grammar")
    _mut("report_id 不合历史编号格式",
         lambda d: _edit(d / "rules.json", "RULE-ANNULAR-RING",
                         lambda x: x.update(report_id="2")),
         "report_id")
    _mut("exempt 只允许 name_prefix/reason",
         lambda d: _edit(d / "rules.json", "RULE-ANNULAR-RING",
                         lambda x: x.update(exempt={"name_prefix": "NP",
                                                    "reason": "x", "extra": 1})),
         "exempt")
    return lib


# ---------------------------------------------------------------- R7.2
def check_ring(c: Checker, RE, lib) -> None:
    subj = _subject("POB1D6X0D9HOB2D2X1D5")

    def st(x, y=None):
        return RE.judge(lib, "RULE-ANNULAR-RING", subj,
                        {"ring_x": x, "ring_y": x if y is None else y})

    for v in (0.2, 0.25, 0.3):
        f = st(v)
        c.check(f["status"] == "PASS", f"R7.2 环宽 {v} 在标准带 PASS", f["status"])
    for v in (0.15, 0.19, 0.3001, 0.4):
        f = st(v)
        c.check(f["status"] == "WARN", f"R7.2 环宽 {v} 非标准 WARN", f["status"])
    for v in (0.14, 0.41, 0.0):
        f = st(v)
        c.check(f["status"] == "FAIL", f"R7.2 环宽 {v} 超出范围 FAIL", f["status"])
    c.check(st(0.25, 0.15)["status"] == "WARN",
            "R7.2 两向取最差（X PASS/Y WARN → WARN）")
    c.check(st(0.25, 0.1)["status"] == "FAIL",
            "R7.2 两向取最差（Y FAIL → FAIL）")

    np_f = RE.judge(lib, "RULE-ANNULAR-RING", _subject("NPOB2D5X2D2"), {})
    c.check(np_f["status"] == "PASS" and np_f.get("exempt") is True,
            "R7.2 NP 件按 exempt 判 PASS", f"{np_f['status']} {np_f.get('detail')}")

    miss = RE.judge(lib, "RULE-ANNULAR-RING", subj, {"ring_x": 0.25})
    c.check(miss["status"] == "SKIP" and "输入 UNKNOWN" in miss.get("detail", ""),
            "R7.2 缺观测 SKIP 且写明输入 UNKNOWN",
            f"{miss['status']} {miss.get('detail')}")


# ---------------------------------------------------------------- R7.3
def check_naming(c: Checker, RE, lib) -> None:
    def st(name, hole, pad, hole_type="OVAL SLOT"):
        if hole_type == "OVAL SLOT":
            obs = {"name": name,
                   "hole_long": max(hole), "hole_short": min(hole),
                   "pad_long": max(pad), "pad_short": min(pad)}
        else:
            obs = {"name": name, "hole_dia": max(hole), "pad_dia": max(pad)}
        return RE.judge(lib, "RULE-PADSTACK-NAMING",
                        _subject(name, hole_type), obs)

    cases = [
        ("POB1D6X0D9HOB2D2X1D5", (0.9, 1.6), (1.5, 2.2), "OVAL SLOT", "PASS"),
        ("NPOB1D6X0D9HOB2D2X1D5", (0.9, 1.6), (1.5, 2.2), "OVAL SLOT", "PASS"),
        ("POB1D6X0D9HOB2D3X1D5", (0.9, 1.6), (1.5, 2.2), "OVAL SLOT", "WARN"),
        ("POB1D6X0D9", (0.9, 1.6), (1.5, 2.2), "OVAL SLOT", "WARN"),
        ("NPOB2D5X2D2", (2.5, 2.2), (0.0, 0.0), "OVAL SLOT", "PASS"),
        ("PC1D4HC2D05", (1.4, 1.4), (2.05, 2.05), "CIRCLE_DRILL", "PASS"),
        ("PC1D4HC2D0", (1.4, 1.4), (2.05, 2.05), "CIRCLE_DRILL", "WARN"),
        ("POB1D6X0D9HOB2D2X1D5OSX1D0", (0.9, 1.6), (1.5, 2.2), "OVAL SLOT", "PASS"),
    ]
    for name, hole, pad, ht, want in cases:
        f = st(name, hole, pad, ht)
        c.check(f["status"] == want, f"R7.3 命名 {name} → {want}", f["status"])

    # 盘段省略只对 NP 放行；非 NP 写省略盘段要报 WARN
    f = st("POB2D5X2D2", (2.5, 2.2), (0.0, 0.0), "OVAL SLOT")
    c.check(f["status"] == "WARN", "R7.3 非 NP 盘段省略 WARN", f["status"])


# ---------------------------------------------------------------- R7.4
def check_paste(c: Checker, RE, lib) -> None:
    subj = _subject("POB1D6X0D9HOB2D2X1D5")

    def top(present, dx=0.0, dy=0.0):
        return RE.judge(lib, "RULE-PASTE-TOP-PRESENT", subj,
                        {"paste_top_present": present})

    c.check(top(1)["status"] == "PASS", "R7.4 顶面钢网存在 PASS")
    c.check(top(0)["status"] == "WARN", "R7.4 顶面钢网缺失 WARN")

    def diff(dx, dy):
        return RE.judge(lib, "RULE-PASTE-TOP-EQ-PAD", subj,
                        {"paste_top_dx": dx, "paste_top_dy": dy})

    c.check(diff(0.0, 0.0)["status"] == "PASS", "R7.4 顶面钢网=焊盘 PASS")
    c.check(diff(0.01, 0.0)["status"] == "PASS", "R7.4 差值 0.01 仍 PASS（旧 >0.01 才报）")
    c.check(diff(0.02, 0.0)["status"] == "WARN", "R7.4 顶面钢网尺寸不符 WARN")

    bottom = RE.judge(lib, "RULE-PASTE-BOTTOM-ABSENT", subj,
                      {"paste_bottom_present": 1})
    c.check(bottom["status"] == "WARN", "R7.4 底面有钢网 WARN")
    bottom0 = RE.judge(lib, "RULE-PASTE-BOTTOM-ABSENT", subj,
                       {"paste_bottom_present": 0})
    c.check(bottom0["status"] == "PASS", "R7.4 底面无钢网 PASS")


# ---------------------------------------------------------------- R7.5
def check_checker_wiring(c: Checker) -> None:
    from services import rule_checker as RC

    payload = {"pins": [_th_pin()]}
    items = RC.check_pad_size(payload, None, RC.load_rules())
    for rid in ("2.8", "2.9", "2.10"):
        it = _item(items, rid)
        c.check(it["status"] == "PASS", f"R7.5 检查器 {rid} PASS",
                f"{it['status']} {it.get('detail')}")
        c.check(it.get("direction") == "assert", f"R7.5 {rid} 标 direction=assert",
                repr(it.get("direction")))
        c.check(bool(it.get("rule_id") or it.get("rule_ids")),
                f"R7.5 {rid} 带 rule_id", repr(it.get("rule_id")))
        c.check(bool(it.get("rule_set_hash")), f"R7.5 {rid} 带 rule_set_hash")

    # 2.8 WARN / FAIL 与 2.10 WARN 的历史状态
    warn_pin = _th_pin(pad=(1.5, 2.2), hole=(0.8998, 1.5998))
    w = _item(RC.check_pad_size({"pins": [warn_pin]}, None, RC.load_rules()), "2.8")
    c.check(w["status"] == "WARN", "R7.5 检查器 2.8 WARN 可复现",
            f"{w['status']} {w.get('detail')}")
    fail_pin = _th_pin(pad=(1.1, 1.8), hole=(0.9, 1.6),
                       name="POB1D6X0D9HOB1D8X1D1")
    f = _item(RC.check_pad_size({"pins": [fail_pin]}, None, RC.load_rules()), "2.8")
    c.check(f["status"] == "FAIL", "R7.5 检查器 2.8 FAIL 可复现",
            f"{f['status']} {f.get('detail')}")
    no_top = _th_pin(paste_top=False)
    t = _item(RC.check_pad_size({"pins": [no_top]}, None, RC.load_rules()), "2.10")
    c.check(t["status"] == "WARN", "R7.5 检查器 2.10 缺顶面钢网 WARN",
            f"{t['status']} {t.get('detail')}")

    # 旧路径在 2.7 硬失败（孔零）时提前 return，环宽/钢网都不判；新路径
    # 必须保留同一短路口径，不能因为跳过 judge 就把 2.10 变成 WARN。
    zero = _th_pin(hole=(0, 0), paste_top=False)
    zero_items = RC.check_pad_size({"pins": [zero]}, None, RC.load_rules())
    z27 = [i for i in zero_items if i.get("id") == "2.7"]
    c.check(any(i.get("status") == "FAIL" for i in z27),
            "R7.5 孔零仍记 2.7 FAIL",
            "; ".join(f"{i.get('status')} {i.get('detail')}" for i in z27))
    for rid in ("2.8", "2.10"):
        zi = _item(zero_items, rid)
        c.check(zi["status"] == "PASS", f"R7.5 2.7 硬失败时 {rid} 保持旧 PASS 口径",
                f"{zi['status']} {zi.get('detail')}")

    # 观测缺失（ETCH/TOP 无 size）→ judge 缺键 → 2.8 记 NA(SKIP)，不判 PASS。
    import copy as _copy
    unknown_pin = _copy.deepcopy(_th_pin())
    for _pd in unknown_pin["pads"]:
        if _pd["layer"] == "ETCH/TOP":
            _pd["size"] = []
    u = _item(RC.check_pad_size({"pins": [unknown_pin]}, None, RC.load_rules()), "2.8")
    c.check(u["status"] == "NA" and "输入 UNKNOWN" in str(u.get("detail", "")),
            "R7.5 观测缺失 2.8 记 NA 且写 UNKNOWN",
            f"{u['status']} {u.get('detail')}")

    # NP 的钢网豁免由规则库 exempt 决定，不再由检查器 if not np 硬跳过。
    np_pin = _th_pin(name="NPOB1D6X0D9HOB2D2X1D5", paste_top=False)
    np_items = RC.check_pad_size({"pins": [np_pin]}, None, RC.load_rules())
    for rid in ("2.8", "2.10"):
        ni = _item(np_items, rid)
        c.check(ni["status"] == "PASS", f"R7.5 NP {rid} 由库 exempt 判 PASS",
                f"{ni['status']} {ni.get('detail')}")

    # 顶面钢网存在但无尺寸：旧路径按 [0,0] 比焊盘 → WARN，新路径同样 WARN。
    bad_ts = _th_pin(paste_size=(0, 0))
    bt = _item(RC.check_pad_size({"pins": [bad_ts]}, None, RC.load_rules()), "2.10")
    c.check(bt["status"] == "WARN", "R7.5 钢网尺寸缺失仍按 0 比焊盘 WARN",
            f"{bt['status']} {bt.get('detail')}")

    # 空 padstack 名：旧路径直接放行，新路径保留。
    empty_name = _th_pin(name="")
    en = _item(RC.check_pad_size({"pins": [empty_name]}, None, RC.load_rules()), "2.9")
    c.check(en["status"] == "PASS", "R7.5 空名 2.9 保持旧 PASS 口径",
            f"{en['status']} {en.get('detail')}")

    # 2.2 也是 assert-only 规则，报告方向口径要与新三条一致。
    a22 = _item(items, "2.2")
    c.check(a22.get("direction") == "assert", "R7.5 2.2 也标 direction=assert",
            repr(a22.get("direction")))

    # 报告层：只有 assert 侧的规则标方向，derive-only 不标（票 #18 AC3）
    from services import report_renderer as RR

    def _md(item):
        return RR.render_markdown({
            "footprint_data": {"symbol_name": "DIR-TEST"},
            "conclusion": "PASS",
            "summary": {"total": 1, "pass": 1},
            "items": [item],
        })

    assert_item = dict(_item(items, "2.8"))
    md_assert = _md(assert_item)
    c.check("assert" in md_assert, "R7.5 报告渲染 assert 方向",
            md_assert[:200])
    derive_item = dict(assert_item, direction="derive")
    md_derive = _md(derive_item)
    c.check("derive" not in md_derive, "R7.5 报告不标 derive-only 方向")

    # 票 #22：旧判定函数与旧构造引擎已下线，源码里既不许调用也不许残留模块
    src = (ROOT / "services" / "rule_checker.py").read_text(encoding="utf-8")
    c.check("check_pin_conventions" not in src and "check_padstack_naming" not in src,
            "R7.5 检查器不再调用旧判定函数")
    for mod in ("legacy_judge.py", "convention_engine.py"):
        c.check(not (ROOT / "services" / mod).exists(),
                f"R7.5 旧模块文件已删除：{mod}")
    import ast as _ast
    offenders = []
    for p in sorted((ROOT / "services").glob("*.py")):
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            offenders.append(f"{p.name}: SyntaxError {exc}")
            continue
        for n in _ast.walk(tree):
            names = []
            if isinstance(n, _ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, _ast.ImportFrom):
                names = [n.module or ""] + [a.name for a in n.names]
            if any("legacy_judge" in s or "convention_engine" in s for s in names):
                offenders.append(p.name)
                break
    c.check(not offenders, "R7.5 服务层无 import 旧模块的残留", str(offenders))


# ---------------------------------------------------------------- R7.6
def check_unknown(c: Checker, RE, lib) -> None:
    subj = _subject("POB1D6X0D9HOB2D2X1D5")
    for rule_id, obs in (
        ("RULE-ANNULAR-RING", {}),
        ("RULE-PASTE-TOP-PRESENT", {}),
        ("RULE-PASTE-TOP-EQ-PAD", {"paste_top_dx": 0.0}),
    ):
        f = RE.judge(lib, rule_id, subj, obs)
        c.check(f["status"] == "SKIP" and "输入 UNKNOWN" in f.get("detail", ""),
                f"R7.6 {rule_id} 缺观测 SKIP 不判 PASS",
                f"{f['status']} {f.get('detail')}")


# ---------------------------------------------------------------- R7.7
def check_shadow(c: Checker, RE) -> None:
    try:
        shadow = json.loads(SHADOW.read_text(encoding="utf-8"))
    except Exception as exc:                               # noqa: BLE001
        c.check(False, "R7.7 253 件影子基线存在", f"{type(exc).__name__}: {exc}")
        return
    summ = shadow.get("summary") or {}
    rows = shadow.get("rows") or []
    errs = shadow.get("errors_detail") or []
    diffs = shadow.get("diffs") or []
    c.check(summ.get("files") == 253, "R7.7 影子覆盖 253 件",
            f"files={summ.get('files')}")
    c.check(len(rows) == 253 and summ.get("files") == len(rows),
            "R7.7 rows 253 条且与 summary 一致", f"rows={len(rows)}")
    c.check(summ.get("diff") == 0, "R7.7 逐件 id+status 差异 0",
            f"diff={summ.get('diff')}")
    # 不只信 summary.diff：从 rows 重算差异、错误与 judged 数，任何一个对不上都红。
    rediff = [r for r in rows if r.get("diff")]
    c.check(summ.get("diff") == len(diffs) == len(rediff),
            "R7.7 summary/diffs/rows 三处差异数一致",
            f"summary={summ.get('diff')} diffs={len(diffs)} rows={len(rediff)}")
    c.check(all(r.get("old") == r.get("new") and not r.get("diff")
                for r in rows if "old" in r and "new" in r),
            "R7.7 每条可比记录的 old/new 序列相同")
    error_rows = [r for r in rows if "error" in r or "old_error" in r]
    c.check(len(errs) == len(error_rows) == summ.get("errors"),
            "R7.7 每件不可读都有 errors_detail 归属",
            f"rows={len(error_rows)} detail={len(errs)} summary={summ.get('errors')}")
    c.check(all(e.get("reason") for e in errs), "R7.7 errors_detail 每条带原因")
    judged = sum(1 for r in rows if "old" in r and "new" in r)
    c.check(judged == summ.get("judged"), "R7.7 judged 与可比记录数一致",
            f"rows={judged} summary={summ.get('judged')}")
    # 库哈希必须与当前规则库一致：改了库却忘了重跑影子，这条会红。
    lib = RE.load_library(RULES_DIR)
    c.check(shadow.get("rule_set_hash") == lib.fingerprint,
            "R7.7 影子哈希对应当前规则库",
            f"{shadow.get('rule_set_hash')} vs {lib.fingerprint}")


# ---------------------------------------------------------------- R7.8
def _library_without_issue18(td: Path) -> None:
    """复制规则库并删掉本票新增的规则/参数，模拟「撤掉新库」。"""
    drop_rules = {"RULE-ANNULAR-RING", "RULE-PADSTACK-NAMING",
                  "RULE-PASTE-TOP-PRESENT", "RULE-PASTE-TOP-EQ-PAD",
                  "RULE-PASTE-BOTTOM-ABSENT",
                  # 票 #20 的派生规则引用上面被删的参数，必须一起撤
                  "RULE-DERIVE-TH-OBLONG", "RULE-DERIVE-SMD"}
    drop_params = {"PARAM-ANNULAR-RING", "PARAM-NAMING-TH-OBLONG",
                   "PARAM-NAMING-TH-CIRCLE", "PARAM-PASTE-TOP-PRESENT",
                   "PARAM-PASTE-TOP-DIFF", "PARAM-PASTE-BOTTOM-PRESENT"}
    for name in ("forms.json", "parameters.json", "rules.json",
                 "sources.json", "_severity.json", "library.json",
                 "tolerances.json"):
        text = (RULES_DIR / name).read_text(encoding="utf-8")
        if name == "library.json":
            # library.json 是版本元信息，不是对象数组，原样复制。
            (td / name).write_text(text, encoding="utf-8")
            continue
        data = json.loads(text)
        drop = drop_rules if name == "rules.json" else (
            drop_params if name == "parameters.json" else set())
        data["items"] = [it for it in data.get("items", []) if it.get("id") not in drop]
        (td / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")


def check_rollback(c: Checker) -> None:
    from services import rule_checker as RC

    payload = {"pins": [_th_pin(), _th_pin(pad=(1.1, 1.8), hole=(0.9, 1.6),
                                           name="POB1D6X0D9HOB1D8X1D1", number="2")]}
    engine_items = RC.check_pad_size(payload, None, RC.load_rules())
    engine = {rid: _item(engine_items, rid)["status"] for rid in ("2.8", "2.9", "2.10")}
    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _library_without_issue18(tdp)
            from services import rule_engine as RE
            shrunk = RE.load_library(str(tdp))
            c.check("RULE-ANNULAR-RING" not in shrunk.rules_by_id
                    and "RULE-PADSTACK-NAMING" not in shrunk.rules_by_id
                    and "RULE-PASTE-TOP-PRESENT" not in shrunk.rules_by_id,
                    "R7.8 回滚夹具已撤掉本票新增规则")
            os.environ["LAYOUT_FORGE_RULES_DIR"] = str(tdp)
            RE.reset_cache()
            # 票 #22 起没有旧判定回退路径：规则缺了只能 SKIP（聚合成 NA），绝不能 PASS。
            shrunk_items = RC.check_pad_size(payload, None, RC.load_rules())
            shrunk = {rid: _item(shrunk_items, rid)["status"]
                      for rid in ("2.8", "2.9", "2.10")}
            gap = [it for it in shrunk_items
                   if it.get("status") == "NA" and "缺规则" in (it.get("detail") or "")]
    finally:
        os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
        RE.reset_cache()
    c.check(all(v == "NA" for v in shrunk.values()),
            "R7.8 撤掉规则后 2.8/2.9/2.10 只能 NA（缺规则不判 PASS）",
            f"{shrunk} gap={len(gap)}")
    c.check(len(gap) == 3 and all(it.get("status") == "NA" for it in gap),
            "R7.8 NA 的原因写明缺规则", str([it.get("detail") for it in gap]))
    c.check(any(v != "NA" for v in engine.values()),
            "R7.8 对照：完整库里至少一项有判定", str(engine))


# ---------------------------------------------------------------- R7.9
FULL_SHADOW = ROOT / "eval" / "baselines" / "shadow_full_253.json"
FULL_SHADOW_OLD = (ROOT / "eval" / "baselines" /
                   "shadow_full_253_old_fa92057.json")


def check_full_shadow(c: Checker, RE) -> None:
    """#22 全量闸门：切主路径后整份 items 与旧代码逐件逐项一致。"""
    for p in (FULL_SHADOW, FULL_SHADOW_OLD):
        if not p.exists():
            c.check(False, f"R7.9 全量影子基线存在：{p.name}", str(p))
            return
    try:
        doc = json.loads(FULL_SHADOW.read_text(encoding="utf-8"))
        old = json.loads(FULL_SHADOW_OLD.read_text(encoding="utf-8"))
    except Exception as exc:                               # noqa: BLE001
        c.check(False, "R7.9 全量影子基线可解析", f"{type(exc).__name__}: {exc}")
        return
    summ = doc.get("summary") or {}
    rows = doc.get("rows") or []
    diffs = doc.get("diffs") or []
    errs = doc.get("errors_detail") or []
    c.check(doc.get("schema") == "layout-forge/shadow@2" and doc.get("scope") == "full",
            "R7.9 全量影子 schema/scope 声明", f"{doc.get('schema')}/{doc.get('scope')}")
    c.check(summ.get("files") == 253 and len(rows) == 253,
            "R7.9 覆盖 253 件", f"files={summ.get('files')} rows={len(rows)}")
    c.check(summ.get("judged") == 235, "R7.9 可比 235 件", str(summ.get("judged")))
    c.check(summ.get("errors") == 18 and len(errs) == 18,
            "R7.9 不可读 18 件且都有明细",
            f"summary={summ.get('errors')} detail={len(errs)}")
    c.check(all(e.get("reason") for e in errs), "R7.9 每个 error 写明原因")
    c.check(summ.get("diff") == 0 and not diffs and not any(r.get("diff") for r in rows),
            "R7.9 整份 items 的 (id,status) 序列逐件逐项差异 0",
            f"summary={summ.get('diff')} diffs={len(diffs)}")
    # 比的是「旧代码 vs 切主路径后」两套实现：基线哈希必须与当前库不同，否则闸门空转。
    lib = RE.load_library(RULES_DIR)
    c.check(doc.get("rule_set_hash") == lib.fingerprint,
            "R7.9 影子哈希对应当前规则库",
            f"{doc.get('rule_set_hash')} vs {lib.fingerprint}")
    c.check(doc.get("baseline_rule_set_hash")
            and doc.get("baseline_rule_set_hash") != doc.get("rule_set_hash"),
            "R7.9 影子确实跨了库版本（旧基线哈希 != 当前哈希）",
            f"{doc.get('baseline_rule_set_hash')} vs {doc.get('rule_set_hash')}")
    c.check(doc.get("baseline_rule_set_hash") == old.get("rule_set_hash"),
            "R7.9 基线哈希与被比对的旧快照一致",
            f"{doc.get('baseline_rule_set_hash')} vs {old.get('rule_set_hash')}")
    # 状态分布与旧快照逐键相同：差异为 0 的独立证据（不只看 runner 自己算的 diff）。
    c.check(summ.get("status") == (old.get("summary") or {}).get("status"),
            "R7.9 状态分布与旧快照逐键一致（253 件）",
            f"diff keys={set((summ.get('status') or {})) ^ set(((old.get('summary') or {}).get('status') or {}))}")

    # 实跑闸门：不只信仓里的快照 —— 用当前代码重跑 253 件再比一遍。
    # 语料在仓外的 Data/PCB_Footprint（253 件 .dra）；缺语料直接红，不做静态降级。
    runner = ROOT / "eval" / "suites" / "geometry_regression" / "make_shadow_full_253.py"
    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, "shadow_live.json")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            proc = subprocess.run(
                [sys.executable, str(runner), "--baseline", str(FULL_SHADOW_OLD), "--out", outp],
                cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=900,
            )
        except subprocess.TimeoutExpired:
            c.check(False, "R7.9 实跑闸门：900s 内未跑完", "TimeoutExpired")
            return
        if proc.returncode == 2:
            # 本闸门的意义就是「用当前代码真跑一遍」；语料缺失时静默降级等于空转。
            c.check(False, "R7.9 实跑闸门需要 253 件语料（Data/PCB_Footprint 或 LAYOUT_FORGE_GOLDEN_DIR）",
                    (proc.stderr or proc.stdout)[-300:])
        else:
            live = {}
            if os.path.exists(outp):
                live = json.loads(Path(outp).read_text(encoding="utf-8"))
            lsumm = live.get("summary") or {}
            c.check(proc.returncode == 0 and lsumm.get("diff") == 0
                    and not (live.get("diffs") or []),
                    "R7.9 实跑闸门：当前代码重跑 253 件差异 0",
                    f"exit={proc.returncode} files={lsumm.get('files')} "
                    f"judged={lsumm.get('judged')} errors={lsumm.get('errors')} "
                    f"diff={lsumm.get('diff')}")
            # 防「语料整片坏 → judged=0、diff=0 也绿」：覆盖率必须与静态基线一致。
            c.check(lsumm.get("files") == 253 and lsumm.get("judged") == 235
                    and lsumm.get("errors") == 18,
                    "R7.9 实跑覆盖完整（253 件 / 235 可比 / 18 不可读）",
                    f"files={lsumm.get('files')} judged={lsumm.get('judged')} "
                    f"errors={lsumm.get('errors')}")
            c.check(live.get("rule_set_hash") == lib.fingerprint,
                    "R7.9 实跑结果的库哈希对应当前规则库",
                    f"{live.get('rule_set_hash')} vs {lib.fingerprint}")


def main() -> int:
    ap = argparse.ArgumentParser(description="R7 三条判定平移与影子")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    c = Checker()
    try:
        from services import rule_engine as RE
    except Exception as exc:                               # noqa: BLE001
        print(f"[FAIL] services/rule_engine.py 导入失败：{type(exc).__name__}: {exc}")
        print("结果: 0/1")
        return 1

    try:
        lib = check_library(c, RE)
        check_ring(c, RE, lib)
        check_naming(c, RE, lib)
        check_paste(c, RE, lib)
        check_checker_wiring(c)
        check_unknown(c, RE, lib)
        check_shadow(c, RE)
        check_rollback(c)
        check_full_shadow(c, RE)
    except Exception as exc:                               # noqa: BLE001
        c.check(False, "R7 运行未抛异常", f"{type(exc).__name__}: {exc}")

    total = len(c.items)
    if args.json:
        print(json.dumps({"passed": c.n_pass, "total": total, "items": c.items},
                         ensure_ascii=False, indent=2))
    else:
        for f in c.fails:
            print(f"[FAIL] {f}")
        for it in c.items:
            print(("  ok  " if it["ok"] else "  FAIL") + "  " + it["desc"])
    print(f"结果: {c.n_pass}/{total}")
    return 0 if c.n_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
