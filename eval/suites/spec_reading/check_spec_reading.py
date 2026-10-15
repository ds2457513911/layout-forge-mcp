#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec_reading 打分器 —— 「AI 从规格书 PDF 读出的 8 个 land pattern 数」考卷。

为什么需要它（L5 度量与评测层）
    换模型 / 换 dpi / 改读图策略 / 改提示词之后，「读得准不准」必须是一个数字，
    而不是「这次看起来还行」。本套件把这件事变成：固定 PDF（哈希钉住）
    → 人工签字真值 → 每次改动跑一遍，出每字段对错与总准确率。

判据（S 段）
    | 判据 | 内容 | 为什么 |
    |--|--|--|
    | S1 | `cases.json` 里的每个案例都有 `truth/<id>.json` | 没真值就不是考卷 |
    | S2 | 真值必须有 `signed_by` + `signed_at` | ★ **防循环论证**：AI 自产自签的真值等于自己给自己打分 |
    | S3 | 真值声明的 `pdf_sha256` 必须等于 PDF 文件实际哈希 | 换版 PDF 会让真值静默失效 |
    | S4 | 真值里 8 个 canonical 字段必须**每个都出现**（值可为 `null`） | 「忘了读」与「确实没有」是两件事 |
    | S5 | 非 null 真值必须带 `page`（页码证据） | 没有出处的真值是猜的 |
    | S6 | 每个案例都有 `candidates/<id>.json` | 没有候选读数就没有对照 |
    | S7 | 聚合准确率不得低于冻结基线（`eval/baselines/spec_reading.json`） | 「不比基线差」，与 geometry_regression 的 R4 同一口径 |
    | S8 | 定位器**不撒谎**（`--with-locate` 才跑）：报 page 时必须 `needs_visual=true` 或该页在真值页集合内；page=null 时必须给 `candidates`（`ambiguous`）或写明 `scan_only` 理由 | 错页/漏页是**静默错**，比读错数更难发现（2026-10-14 实测旧版 1 例错页 + 1 例静默漏页） |

`--with-locate` 说明：S8 要 `import services.pdf_locate`（依赖 `pdfplumber`），所以必须用
带依赖的解释器跑；import 失败会报 ERROR，**不会静默跳过**。默认不跑，本套件保持纯 stdlib。

四分类（每个字段判一次）
    correct   值在容差内（`null` 对 `null` 也算 correct —— 「确实没有」也是要读对的东西）
    wrong     两边都有值但差出容差
    missing   真值有值、候选没读出来（缺键或 null）
    spurious  真值 `null`、候选硬报了一个值

容差口径
    长度（body_x/body_y/pad_x/pad_y）±0.05 mm，间距（pitch_x/pitch_y）±0.02 mm，
    mount / pin_count 精确相等。理由：规格书标注到 0.05，读图不可能比标注更准；
    间距标 0.5 读成 0.65 是错，读成 0.51 不该判错。

用法
    python eval/suites/spec_reading/check_spec_reading.py                # 跑分（需真值已签字）
    python eval/suites/spec_reading/check_spec_reading.py --allow-unsigned   # 起草期自检（结果标 UNSIGNED）
    python eval/suites/spec_reading/check_spec_reading.py --update           # 冻结基线（未签字真值会被拒绝）
    python eval/suites/spec_reading/check_spec_reading.py --with-locate      # 加跑 S8（需带 pdfplumber 的解释器）
    python eval/suites/spec_reading/check_spec_reading.py --json

退出码：0 = 无 ERROR 且（若存在基线）准确率不低于基线；1 = 有 ERROR 或低于基线。
纯 stdlib，任何 3.10+ 解释器可跑（不 import services / 不需要 venv）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]        # layout-forge-mcp/
LAYOUT = ROOT.parent                              # Layout/
SUITE = Path(__file__).resolve().parent
CASES_JSON = SUITE / "cases.json"
TRUTH_DIR = SUITE / "truth"
CAND_DIR = SUITE / "candidates"
BASELINE = ROOT / "eval" / "baselines" / "spec_reading.json"

FIELDS = ("mount", "pin_count", "pitch_x", "pitch_y", "body_x", "body_y", "pad_x", "pad_y")
TOL = {"body_x": 0.05, "body_y": 0.05, "pad_x": 0.05, "pad_y": 0.05,
       "pitch_x": 0.02, "pitch_y": 0.02}
EXACT = ("mount", "pin_count")


# ---------- 读取 ----------
def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha12(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _cases() -> list[dict]:
    return _load(CASES_JSON)["cases"]


def _resolve(rel: str) -> Path:
    return (LAYOUT / rel) if rel else None


# ---------- 判据 S1–S6 ----------
def check_truth(case: dict, truth: dict, allow_unsigned: bool):
    """返回 (errors, warns)。"""
    errs, warns = [], []
    cid = case["id"]
    if not allow_unsigned:
        if not str(truth.get("signed_by") or "").strip():
            errs.append(f"S2 真值未签字：truth/{cid}.json 缺 signed_by（AI 自产真值 = 循环论证）")
        if not str(truth.get("signed_at") or "").strip():
            errs.append(f"S2 真值未签字：truth/{cid}.json 缺 signed_at")
    else:
        if not str(truth.get("signed_by") or "").strip():
            warns.append(f"S2 真值尚未签字（起草期放行）：truth/{cid}.json")

    pdf = _resolve(case["pdf"])
    if pdf is None or not pdf.is_file():
        errs.append(f"S3 PDF 不存在：{case['pdf']}")
    else:
        actual = _sha12(pdf)
        if truth.get("pdf_sha256") != actual:
            errs.append(f"S3 PDF 哈希不符：真值写 {truth.get('pdf_sha256')}，实际 {actual} —— 换版了")
        if truth.get("pdf_sha256") != case.get("pdf_sha256"):
            warns.append(f"S3 真值哈希与 cases.json 记录不一致（{truth.get('pdf_sha256')} vs {case.get('pdf_sha256')}）")

    tf = truth.get("fields") or {}
    for f in FIELDS:
        if f not in tf:
            errs.append(f"S4 真值缺字段：{f}（确实没有就写 null + null_reason，别省略）")
            continue
        spec = tf[f]
        if not isinstance(spec, dict):
            errs.append(f"S5 真值字段 {f} 不是对象：需要 {{value, page, quote}}")
            continue
        if spec.get("value") is not None and not isinstance(spec.get("page"), int):
            errs.append(f"S5 真值字段 {f} 只有值没有页码证据（page）")
    return errs, warns


def check_candidate(case: dict, cand: dict):
    errs = []
    cid = case["id"]
    if cand.get("case") != cid:
        errs.append(f"S6 候选文件 case 字段不符：{cand.get('case')} != {cid}")
    if not isinstance(cand.get("fields"), dict):
        errs.append(f"S6 候选文件缺 fields 对象：candidates/{cid}.json")
    return errs


# ---------- 判据 S8（opt-in）：定位器不撒谎 ----------
def check_locate(case: dict, truth: dict):
    """
    调用项目自己的 `services/pdf_locate.find_land_pattern_page`，判它有没有**撒谎**：
      · 报 page  → 要么该页在真值页集合内（hit），要么 `needs_visual=True`（明说是结构推定）；
                   否则 = 错页（lie，ERROR）。
      · page=null → `scan_only`（诚实：纯位图）或 `ambiguous` + 非空 `candidates`（诚实：证据并列）
                   才算过关；候选页里不含真值页 = 漏页（ERROR）；其他 = 静默漏页（ERROR）。
    返回 (info, errors)。import 失败**报 ERROR**，不静默跳过。
    """
    errs = []
    cid = case["id"]
    pdf = _resolve(case["pdf"])
    if pdf is None or not pdf.is_file():
        return None, [f"S8 PDF 不存在，无法跑定位判据：{case['pdf']}"]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from services.pdf_locate import find_land_pattern_page   # 延迟导入：默认跑分保持纯 stdlib
    except Exception as exc:                                    # noqa: BLE001
        return None, [f"S8 无法执行定位判据：import services.pdf_locate 失败"
                      f"（{type(exc).__name__}: {exc}）—— 用带 pdfplumber 的解释器，或去掉 --with-locate"]
    res = find_land_pattern_page(pdf)
    page = res.get("page")
    truth_pages = sorted({s.get("page") for s in (truth.get("fields") or {}).values()
                          if isinstance(s, dict) and isinstance(s.get("page"), int)})
    cands = [c.get("page") for c in (res.get("candidates") or [])]
    info = {"page": page, "method": res.get("method"), "confidence": res.get("confidence"),
            "needs_visual": res.get("needs_visual"), "candidates": cands,
            "truth_pages": truth_pages, "reason": res.get("reason")}
    if isinstance(page, int):
        if page in truth_pages:
            info["verdict"] = "hit"
        elif res.get("needs_visual"):
            info["verdict"] = "structural"      # 明说要渲染确认 → 不算撒谎
        else:
            info["verdict"] = "lie"
            errs.append(f"S8 {cid} 定位器报 page={page} 却既无 needs_visual 又不在真值页 {truth_pages} 内"
                        f"（method={res.get('method')}）—— 疑似错页")
    else:
        if res.get("method") == "scan_only":
            info["verdict"] = "honest-scan"
        elif res.get("method") == "ambiguous" and cands:
            info["verdict"] = "honest-ambiguous"
            if truth_pages and not (set(cands) & set(truth_pages)):
                errs.append(f"S8 {cid} 定位器报 ambiguous，但候选页 {cands} 不含真值页 {truth_pages}（漏页）")
        else:
            info["verdict"] = "silent"
            errs.append(f"S8 {cid} 定位器 page=None 且 method={res.get('method')}、候选页 {cands}"
                        f" —— 既不给页码也不给候选（静默漏页）")
    return info, errs


# ---------- 打分 ----------
def _close(a, b) -> bool:
    return abs(float(a) - float(b)) <= 1e-9


def compare(truth_fields: dict, cand_fields: dict) -> dict:
    """逐字段判 correct / wrong / missing / spurious。"""
    out = {}
    for f in FIELDS:
        t = (truth_fields.get(f) or {}).get("value")
        c = cand_fields.get(f, None)
        if t is None:
            out[f] = "correct" if c is None else "spurious"
            continue
        if c is None:
            out[f] = "missing"
            continue
        if f in EXACT:
            cap = str(c).strip().lower() if f == "mount" else c
            ref = str(t).strip().lower() if f == "mount" else t
            out[f] = "correct" if cap == ref else "wrong"
        else:
            try:
                tol = TOL.get(f, 0.05)
                out[f] = "correct" if abs(float(c) - float(t)) <= tol + 1e-9 else "wrong"
            except (TypeError, ValueError):
                out[f] = "wrong"
    return out


def run(allow_unsigned: bool, with_locate: bool = False):
    errors, warns, results = [], [], []
    cases = _cases()
    for case in cases:
        cid = case["id"]
        tpath, cpath = TRUTH_DIR / f"{cid}.json", CAND_DIR / f"{cid}.json"
        item = {"case": cid, "title": case.get("title", ""), "origin": case.get("origin"),
                "signed": False, "verdicts": {}, "counts": {}}
        if not tpath.is_file():
            errors.append(f"S1 缺真值：truth/{cid}.json")
            results.append(item)
            continue
        truth = _load(tpath)
        e, w = check_truth(case, truth, allow_unsigned)
        errors += e
        warns += w
        if with_locate:
            info, e3 = check_locate(case, truth)
            item["locate"] = info
            errors += e3
        if not cpath.is_file():
            errors.append(f"S6 缺候选读数：candidates/{cid}.json")
            results.append(item)
            continue
        cand = _load(cpath)
        e2 = check_candidate(case, cand)
        errors += e2
        item["signed"] = bool(str(truth.get("signed_by") or "").strip())
        item["read_by"] = cand.get("read_by", "")
        if not e and not e2:
            verdicts = compare(truth["fields"], cand["fields"] or {})
            item["verdicts"] = verdicts
            counts = {k: 0 for k in ("correct", "wrong", "missing", "spurious")}
            for v in verdicts.values():
                counts[v] += 1
            item["counts"] = counts
        results.append(item)

    scored = [r for r in results if r["verdicts"]]
    agg = {k: sum(r["counts"][k] for r in scored) for k in ("correct", "wrong", "missing", "spurious")}
    total = sum(agg.values())
    overall = (agg["correct"] / total) if total else 0.0
    per_case = [{"case": r["case"], "accuracy": (r["counts"]["correct"] / len(FIELDS))} for r in scored]
    return {"results": results, "agg": agg, "total": total, "overall": overall,
            "per_case": per_case, "errors": errors, "warns": warns,
            "allow_unsigned": allow_unsigned, "with_locate": with_locate}


def _render(rep: dict) -> None:
    print("=" * 74)
    print("spec_reading 读图准确率")
    print("=" * 74)
    print(f"{'案例':<26}{'来源':<16}{'候选读数':<20}{'正确/8':>8}")
    print("-" * 74)
    for r in rep["results"]:
        acc = f"{r['counts'].get('correct', 0)}"
        who = (r.get("read_by") or "-")[:18]
        origin = {"existing_spec": "既有 spec", "new": "新增"}.get(r.get("origin"), r.get("origin") or "-")
        bad = " ".join(f for f, v in (r["verdicts"] or {}).items() if v not in ("correct",))
        line = f"{r['case']:<26}{origin:<16}{who:<20}{acc:>8}" + (f"   错: {bad}" if bad else "")
        print(line)
        loc = r.get("locate")
        if loc:
            cand_s = ",".join(str(p) for p in (loc.get("candidates") or [])) or "-"
            print(f"{'':<26}定位: {loc['verdict']:<16} page={loc['page']} method={loc['method']} "
                  f"needs_visual={loc['needs_visual']} 候选={cand_s} 真值页={loc['truth_pages']}")
    a = rep["agg"]
    print("-" * 74)
    print(f"计分字段 {rep['total']} 项：correct {a['correct']} / wrong {a['wrong']} / "
          f"missing {a['missing']} / spurious {a['spurious']}")
    flag = "（UNSIGNED 起草期数字，不是真值下的成绩）" if rep["allow_unsigned"] else ""
    print(f"总准确率 = {rep['overall'] * 100:.1f}%  {flag}")
    if rep["warns"]:
        print("\n[!] 警告")
        for w in rep["warns"]:
            print("    " + w)
    if rep["errors"]:
        print("\n[X] ERROR")
        for e in rep["errors"]:
            print("    " + e)
    else:
        print(f"\n[✓] 判据 {'S1–S8' if rep.get('with_locate') else 'S1–S6'} 全部通过")
    print("=" * 74)


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


def main(argv=None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="spec_reading 读图准确率打分器")
    ap.add_argument("--allow-unsigned", action="store_true", help="起草期：允许未签字真值（结果标 UNSIGNED）")
    ap.add_argument("--with-locate", action="store_true",
                    help="加跑 S8「定位器不撒谎」（需带 pdfplumber 的解释器）")
    ap.add_argument("--update", action="store_true", help="冻结基线 eval/baselines/spec_reading.json")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    if not CASES_JSON.is_file():
        print(f"[X] 缺案例清单：{CASES_JSON}")
        return 1
    rep = run(args.allow_unsigned, args.with_locate)

    if args.update:
        if rep["errors"]:
            print("[X] 有 ERROR，拒绝冻结基线：")
            for e in rep["errors"]:
                print("    " + e)
            return 1
        if not rep["allow_unsigned"] and any(not r["signed"] for r in rep["results"]):
            print("[X] 存在未签字真值，拒绝冻结基线（防循环论证）")
            return 1
        if args.allow_unsigned:
            print("[X] --update 不允许与 --allow-unsigned 同时使用")
            return 1
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "frozen_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "cases": len(rep["results"]), "scored_fields": rep["total"],
                   "counts": rep["agg"], "accuracy": round(rep["overall"], 4),
                   "min_accuracy": round(rep["overall"], 4),
                   "per_case": {c["case"]: round(c["accuracy"], 4) for c in rep["per_case"]}}
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[✓] 基线已冻结：{BASELINE.relative_to(ROOT)}（准确率 {rep['overall'] * 100:.1f}%）")
        return 0

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _render(rep)

    if rep["errors"]:
        return 1
    if BASELINE.is_file() and not args.allow_unsigned:
        base = _load(BASELINE)
        floor = float(base.get("min_accuracy", 0.0))
        if rep["overall"] + 1e-9 < floor:
            print(f"[X] 准确率 {rep['overall'] * 100:.1f}% 低于基线下限 {floor * 100:.1f}% "
                  f"（退化就查退化，不许调松基线）")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
