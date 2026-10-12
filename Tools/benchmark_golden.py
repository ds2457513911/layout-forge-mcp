# -*- coding: utf-8 -*-
"""
Tools/benchmark_golden.py —— 金标准批量对拍（三层漏斗 · 第 1 层）

目的：
    把人工金标准封装库（Layout/Data/PCB_Footprint，250+ 件）批量跑一遍
    离线提取 + 数值规则检查，产出「机器判定 vs 金标准」差异清单。

    ★ 全程 0 token、0 次 Allegro 启动：
      - 提取走 extracta_reader（Cadence extracta 二进制，离线）
      - 环境变量 LAYOUT_CHECK_FORCE_SOURCE=extracta 强制单源，
        read_full_footprint 绝不触碰 SkillBridge
      - 规则阈值来自 knowledge/rules/checker_rules.json（热更新层）

产出（默认 Tools/log/golden_benchmark/）：
    benchmark_summary.csv      每件一行：结论 / PASS/FAIL/WARN 数 / 指纹匹配档位
    benchmark_report.json      全量明细（含每件完整 items）
    exceptions/                例外件单独 JSON —— ★ 第 2 层 AI 复核的输入，
                               只需把这些文件喂给 AI 逐件裁定
    控制台摘要 + 退出码（0 = 全过；1 = 有 FAIL；2 = 有提取失败）

用法：
    python Tools/benchmark_golden.py                     # 全量
    python Tools/benchmark_golden.py --only qfn          # 名字含 qfn 的件
    python Tools/benchmark_golden.py --src <dir> --out <dir>
    python Tools/benchmark_golden.py --limit 10          # 冒烟：只跑前 10 件

分层口径（与用户 2026-10-12 确认的方案一致）：
    第 1 层（本脚本）  脚本全量对拍，0 token
    第 2 层            AI 只复核 exceptions/ 里的例外件（预计 20~40 件），
                       裁定结果 save_case 写入 knowledge/cases/
    第 3 层            实机抽样 2~3 件跑完整链路验证
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

# ★ 必须在 import services 之前设置：强制 extracta 单源（离线，不碰 Allegro）
os.environ["LAYOUT_CHECK_FORCE_SOURCE"] = "extracta"

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from services import footprint_extractor, rule_checker, spec_library  # noqa: E402

logging.getLogger("layout-forge").setLevel(logging.WARNING)

# 金标准库：与项目同级的 Layout\Data\PCB_Footprint（不硬编码本机绝对路径）
PROJECT_ROOT = _HERE.parent
DEFAULT_SRC = PROJECT_ROOT.parent / "Data" / "PCB_Footprint"
DEFAULT_OUT = _HERE / "log" / "golden_benchmark"


# ============================================================
# 指纹推导（footprint_data → find_spec 查询八元组）
# ============================================================
def _pitch_from_pins(pins: list) -> tuple:
    """复用 rule_checker 的口径：x/y 方向相邻 pin 中心最小间距。"""
    xs = sorted({round(p["xy"][0], 4) for p in pins if p.get("xy")})
    ys = sorted({round(p["xy"][1], 4) for p in pins if p.get("xy")})

    def min_gap(vals):
        if len(vals) < 2:
            return None
        return min(round(vals[i + 1] - vals[i], 4) for i in range(len(vals) - 1))

    px, py = min_gap(xs), min_gap(ys)
    # 归一口径与 spec_library.fingerprint 一致：0 → None
    return (px or None, py or None)


def _pad_size_from_pins(pins: list) -> tuple:
    """取第一个有 etch 层的 pad 的 (w, h)。"""
    for pin in pins:
        layers = pin.get("layers") or []
        for lay in layers:
            if str(lay.get("layer", "")).upper().startswith("PIN/") \
                    and lay.get("size"):
                w, h = lay["size"][0], lay["size"][1]
                if w and h:
                    return (round(w, 4), round(h, 4))
    return (None, None)


def _body_from_place_bound(fp_data: dict) -> tuple:
    """place_bound_top 最大元素的 bbox 尺寸，作为 body 估计值。"""
    elements = (fp_data.get("layers") or {}).get("place_bound_top") or []
    best = None
    for el in elements:
        bb = el.get("bbox")
        if not bb or len(bb) != 2:
            continue
        w, h = abs(bb[1][0] - bb[0][0]), abs(bb[1][1] - bb[0][1])
        if best is None or w * h > best[0] * best[1]:
            best = (w, h)
    if best:
        return (round(best[0], 4), round(best[1], 4))
    return (None, None)


def fingerprint_query(fp_data: dict) -> dict:
    """从提取数据拼出 find_spec 的查询参数（未知=不参与匹配）。"""
    pins = fp_data.get("pins") or []
    px, py = _pitch_from_pins(pins)
    pw, ph = _pad_size_from_pins(pins)
    bx, by = _body_from_place_bound(fp_data)
    return {
        "mount": None,                       # extracta 数据不含 mount，不参与
        "pin_count": len(pins) or None,
        "pitch_x": px, "pitch_y": py,
        "body_x": bx, "body_y": by,
        "pad_x": pw, "pad_y": ph,
    }


def match_level(q: dict) -> str:
    """exact / family / none —— 摘要里的一列。"""
    r = spec_library.find_spec(**q, keyword=None)
    if r["exact"]:
        return f"exact({r['exact'][0]['name']})"
    if r["family"]:
        return f"family({r['family'][0]['name']})"
    return "none"


# ============================================================
# 主流程
# ============================================================
def run_one(dra: Path) -> dict:
    """单件：离线提取 → 数值检查 → 打包结果。"""
    t0 = time.time()
    rec: dict = {"file": dra.name, "path": str(dra)}
    try:
        fp_data = footprint_extractor.read_full_footprint(
            dra_path=str(dra), pad_paths=[], workspace_id=None)
    except Exception as e:                                   # noqa: BLE001
        rec.update(extract_error=f"exception: {e}")
        return rec

    if fp_data.get("error"):
        rec.update(extract_error=fp_data["error"])
        return rec

    rec["symbol_name"] = fp_data.get("symbol_name")
    rec["pin_count"] = len(fp_data.get("pins") or [])
    rec["units"] = fp_data.get("units")
    rec["source"] = fp_data.get("_source")

    result = rule_checker.run_numeric_checks(
        footprint_data=fp_data, theoretical=None, rules=None)
    summary = result.get("summary") or {}
    rec["conclusion"] = result.get("conclusion")
    rec.update({k: summary.get(k, 0) for k in
                ("total", "pass", "fail", "warn", "na")})

    # 例外项明细（第 2 层复核用）
    bad = []
    for it in result.get("items") or []:
        if it.get("status") in ("FAIL", "WARN"):
            bad.append({
                "rule": it.get("id"), "name": it.get("name"),
                "status": it.get("status"), "value": it.get("value"),
                "expected": it.get("expected"), "detail": it.get("detail"),
            })
    rec["exceptions"] = bad
    rec["spec_match"] = match_level(fingerprint_query(fp_data))
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="金标准批量对拍（第 1 层，离线）")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="金标准 .dra 目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="报告输出目录")
    ap.add_argument("--only", default="", help="文件名子串过滤（不分大小写）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 件（冒烟）")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if not src.is_dir():
        print(f"[X] 源目录不存在: {src}")
        return 2
    (out / "exceptions").mkdir(parents=True, exist_ok=True)

    dras = sorted(p for p in src.glob("*.dra") if not p.name.endswith(".lck"))
    if args.only:
        kw = args.only.lower()
        dras = [p for p in dras if kw in p.name.lower()]
    if args.limit:
        dras = dras[: args.limit]
    if not dras:
        print(f"[X] {src} 下没有匹配的 .dra")
        return 2

    print(f"[benchmark_golden] 源={src}  件数={len(dras)}  输出={out}")
    print(f"[benchmark_golden] 强制离线：LAYOUT_CHECK_FORCE_SOURCE=extracta")

    records: list[dict] = []
    n_fail = n_err = 0
    t_start = time.time()
    for i, dra in enumerate(dras, 1):
        rec = run_one(dra)
        records.append(rec)
        if rec.get("extract_error"):
            n_err += 1
            state = f"提取失败: {rec['extract_error'][:60]}"
        else:
            if rec["conclusion"] == "FAIL":
                n_fail += 1
            # 例外件单独落盘 —— 第 2 层 AI 复核的输入
            if rec.get("exceptions"):
                slug = dra.stem
                (out / "exceptions" / f"{slug}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            state = (f"{rec['conclusion']}  P{rec['pass']}/F{rec['fail']}"
                     f"/W{rec['warn']}/NA{rec['na']}  {rec['spec_match']}")
        print(f"  [{i:>3}/{len(dras)}] {dra.name:<44} {state}")

    # ---------- 汇总落盘 ----------
    with (out / "benchmark_summary.csv").open("w", newline="",
                                              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "symbol_name", "pin_count", "units", "source",
                    "conclusion", "pass", "fail", "warn", "na",
                    "spec_match", "seconds", "extract_error"])
        for r in records:
            w.writerow([r.get("file"), r.get("symbol_name", ""),
                        r.get("pin_count", ""), r.get("units", ""),
                        r.get("source", ""), r.get("conclusion", ""),
                        r.get("pass", ""), r.get("fail", ""),
                        r.get("warn", ""), r.get("na", ""),
                        r.get("spec_match", ""), r.get("seconds", ""),
                        r.get("extract_error", "")])
    (out / "benchmark_report.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 控制台摘要 ----------
    n = len(records)
    ok = sum(1 for r in records if r.get("conclusion") == "PASS")
    warn_only = sum(1 for r in records if r.get("conclusion") == "WARN")
    na_cnt = sum(1 for r in records if r.get("conclusion") not in
                 ("PASS", "WARN", "FAIL"))
    n_exc = sum(1 for r in records if r.get("exceptions"))
    match = sum(1 for r in records if str(r.get("spec_match", "")).startswith("exact"))

    print("\n" + "=" * 64)
    print(f"  完成：{n} 件，用时 {time.time() - t_start:.0f}s")
    print(f"  PASS {ok} ｜ WARN {warn_only} ｜ FAIL {n_fail} ｜ 其他/提取失败 {na_cnt + n_err}")
    print(f"  例外件（有 FAIL/WARN 项）：{n_exc} 件 → exceptions/（第 2 层 AI 复核输入）")
    print(f"  指纹 exact 命中：{match}/{n}")
    print(f"  报告：{out / 'benchmark_summary.csv'}")
    print("=" * 64)

    if n_err:
        return 2
    if n_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
