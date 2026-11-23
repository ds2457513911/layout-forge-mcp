# -*- coding: utf-8 -*-
"""#22 全量影子生成器：253 件金标准上重跑整套数值检查，逐件比 (id,status) 序列。

与 #18 的 make_shadow_2_8_10.py 的分工：
  - 2.8/2.9/2.10 的单点平移已由 shadow_2_8_10_253.json 守（旧检查器 vs 新库）；
  - 本脚本是**全量闸门**：跑 run_numeric_checks 的整份 items（0/1.x/2.x/3.x/5.x/6.x），
    用切主路径前的同一份代码生成旧侧基线，切完后重跑必须逐件逐项一致。
    这样「旧阈值文件收编成规则库容差」「删旧判定/旧补全」就不可能悄悄改变判定。

用法：
    python eval/suites/geometry_regression/make_shadow_full_253.py --out <基线>
    python eval/suites/geometry_regression/make_shadow_full_253.py --baseline <基线>
退出码：0 = 无差异 / 1 = 有差异或错误 / 2 = 语料不可用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.extracta_reader import read_via_extracta
from services import rule_checker as RC
from services import rule_engine as RE

DEFAULT_OUT = ROOT / "eval" / "baselines" / "shadow_full_253.json"


def _find_corpus() -> Path:
    """金标准语料在项目外一层；worktree 里往上找，避免默认落空。"""
    env = os.environ.get("LAYOUT_FORGE_GOLDEN_DIR")
    if env:
        return Path(env)
    for base in (ROOT, *ROOT.parents):
        cand = base / "Data" / "PCB_Footprint"
        if cand.is_dir():
            return cand
    return ROOT.parent / "Data" / "PCB_Footprint"


def compact(items):
    """整份 items 的 (id,status) 序列；保持顺序（同一 id 可多次出现）。"""
    return [[it.get("id"), it.get("status")] for it in items]


def main(argv=None):
    ap = argparse.ArgumentParser(description="253 件全量影子比对")
    ap.add_argument("--corpus", default=str(_find_corpus()))
    ap.add_argument("--baseline", default=None,
                    help="旧侧基线；给了就逐件比，不给只写快照")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    corpus = Path(args.corpus)
    out_path = Path(args.out)
    base_doc = None
    if args.baseline:
        base_doc = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_rows = {r["file"]: r for r in (base_doc or {}).get("rows") or []}

    files = sorted(corpus.glob("*.dra"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"语料目录没有 .dra：{corpus}", file=sys.stderr)
        return 2

    rows, diffs, judged = [], [], 0
    errors_detail = []
    t0 = time.time()
    for i, p in enumerate(files):
        row = {"file": p.name}
        try:
            data = read_via_extracta(str(p))
            if data.get("error"):
                row["error"] = data["error"]
                errors_detail.append({"file": p.name, "stage": "extracta",
                                      "reason": data["error"]})
            else:
                row["items"] = compact(RC.run_numeric_checks(data)["items"])
                if base_doc is not None:
                    old_row = base_rows.get(p.name) or {}
                    if "error" in old_row:
                        row["old_error"] = old_row["error"]
                    else:
                        row["old"] = old_row.get("items")
                        judged += 1
                        if row["old"] != row["items"]:
                            row["diff"] = True
                            diffs.append({"file": p.name, "old": row["old"],
                                          "new": row["items"]})
        except Exception as exc:                       # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["trace"] = traceback.format_exc()[-1000:]
            errors_detail.append({"file": p.name, "stage": "new",
                                  "reason": row["error"]})
        rows.append(row)
        if (i + 1) % 20 == 0:
            print(f"{i+1}/{len(files)} {time.time()-t0:.1f}s diff={len(diffs)}",
                  flush=True)

    def counts(key):
        out = {}
        for r in rows:
            for rid, st in r.get(key) or []:
                out[f"{rid}:{st}"] = out.get(f"{rid}:{st}", 0) + 1
        return dict(sorted(out.items()))

    summary = {
        "files": len(rows),
        "judged": judged,
        "errors": len(errors_detail),
        "diff": len(diffs),
        "status": counts("items"),
        "error_note": "extracta 输出为空/基线不可读为既有语料问题，不计入 judged；"
                      "每件 error 均在 errors_detail 留原因",
    }
    lib = RE.load_library()
    doc = {
        "schema": "layout-forge/shadow@2",
        "name": "全量数值检查影子（253 件，run_numeric_checks 整份 items 的 (id,status) 序列）",
        "scope": "full",
        "baseline_rev": (base_doc or {}).get("baseline_rev", ""),
        "baseline_rule_set_hash": (base_doc or {}).get("rule_set_hash", ""),
        "corpus": str(corpus),
        "rule_set_hash": lib.fingerprint,
        "method": "对每件 .dra 跑 rule_checker.run_numeric_checks，记整份 items 的 "
                  "(id,status) 序列（保持顺序），逐件逐项比较；extracta 不可读件不计入 judged",
        "summary": summary,
        "errors_detail": errors_detail,
        "diffs": diffs,
        "rows": rows,
    }
    # 原子写：中途被杀不会留下半截基线（临时文件同目录 + os.replace）。
    fd, tmp = tempfile.mkstemp(prefix=out_path.name + ".", suffix=".tmp",
                               dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("elapsed", round(time.time() - t0, 1), "size", out_path.stat().st_size)
    for d in diffs[:10]:
        print("DIFF", d["file"], "old", d["old"], "new", d["new"])
    # 有基线时：差异必须为 0，且**不允许新增不可读件**（18 件既有语料缺陷已在旧快照里，
    # 不算回归；新坏件会静默缩小判定覆盖面，必须红）。无基线只做快照。
    if base_doc is not None:
        base_err = {str(e.get("file")) for e in (base_doc.get("errors_detail") or [])}
        new_err = sorted({str(e.get("file")) for e in errors_detail} - base_err)
        for f in new_err:
            print("NEWERR", f)
        return 0 if (not diffs and not new_err) else 1
    return 0 if not errors_detail else 1


if __name__ == "__main__":
    sys.exit(main())
