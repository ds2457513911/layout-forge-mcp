# -*- coding: utf-8 -*-
"""#18 影子生成器：253 件金标准上重跑新检查器，与旧检查器基线逐件比 2.7-2.10。

旧基线（d87e172）在 eval/baselines/shadow_2_8_10_old_d87e172.json，只存
(id,status) 序列；本脚本不依赖 .agent-cache 或主工作区。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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

KEEP = ("2.7", "2.8", "2.9", "2.10")
DEFAULT_OLD = ROOT / "eval" / "baselines" / "shadow_2_8_10_old_d87e172.json"
DEFAULT_OUT = ROOT / "eval" / "baselines" / "shadow_2_8_10_253.json"


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
    return [[it.get("id"), it.get("status")] for it in items
            if it.get("id") in KEEP]


def main(argv=None):
    ap = argparse.ArgumentParser(description="重跑 253 件影子比对")
    ap.add_argument("--corpus", default=str(_find_corpus()))
    ap.add_argument("--old", default=str(DEFAULT_OLD))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    corpus = Path(args.corpus)
    old_path = Path(args.old)
    out_path = Path(args.out)
    old_doc = json.loads(old_path.read_text(encoding="utf-8"))
    old_rows = {r["file"]: r for r in old_doc["rows"]}
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
                new = compact(RC.check_pad_size(data, None, RC.load_rules()))
                row["new"] = new
                old_row = old_rows.get(p.name) or {}
                if "error" in old_row:
                    row["old_error"] = old_row["error"]
                    errors_detail.append({"file": p.name, "stage": "old",
                                          "reason": old_row["error"]})
                else:
                    old = [x for x in (old_row.get("items") or [])
                           if x[0] in KEEP]
                    row["old"] = old
                    judged += 1
                    if old != new:
                        row["diff"] = True
                        diffs.append({"file": p.name, "old": old, "new": new})
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
        return out

    summary = {
        "files": len(rows),
        "judged": judged,
        "errors": len(errors_detail),
        "diff": len(diffs),
        "old_status": counts("old"),
        "new_status": counts("new"),
        "error_note": "extracta 输出为空/旧基线不可读为既有语料问题，不计入 judged；"
                      "每件 error 均在 errors_detail 留原因",
    }
    lib = RE.load_library()
    out_path.write_text(json.dumps({
        "schema": "layout-forge/shadow@1",
        "name": "2.8/2.9/2.10 规则库平移影子（253 件）",
        "old_rev": old_doc.get("old_rev", "d87e172"),
        "corpus": str(corpus),
        "rule_set_hash": lib.fingerprint,
        "method": "旧检查器（d87e172）与新库检查器各跑一遍，逐件比较 2.7-2.10 的"
                  "(id,status) 序列；extracta 不可读件不计入 judged",
        "summary": summary,
        "errors_detail": errors_detail,
        "diffs": diffs,
        "rows": rows,
    }, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("elapsed", round(time.time() - t0, 1), "size", out_path.stat().st_size)
    for d in diffs[:10]:
        print("DIFF", d["file"], "old", d["old"], "new", d["new"])
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
