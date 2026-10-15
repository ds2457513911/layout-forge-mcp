# -*- coding: utf-8 -*-
"""
_extracta_probe.py —— G6 的被调方：报「官方视图文件跑一次 extracta，四路输出各有几个字节」

## 为什么需要它（2026-10-14 实测）

`services/extracta_views/footprint_views.txt` 里定义 **4 个视图**，命令行按顺序对应
`pins.txt / pads.txt / geom.txt / refdes.txt`。实测（本机 Cadence SPB 17.2）：

    组合 [1]     → pins 正常，rc=0
    组合 [1,3]   → pins + geom 都正常，rc=0
    组合 [1,4]   → pins + refdes 都正常，rc=0
    组合 [1,2]   → pins 正常，**pads.txt 0 字节**，rc=0xC0000409（进程崩）

即 **第 2 个视图 `PAD_DEF` 会让 extracta 崩溃**，而它正好排在官方文件的第 2 位 ——
于是官方 4 视图调用**在这里就中断**：pads 空、geom 与 refdes 根本没生成。

后果（同一批实测）：
1. `services/extracta_reader.read_via_extracta()` 只拿到引脚坐标，`layers` 全空；
   而且它**不报缺口**（`_gaps` 只说"没读到 ref_des 文字"，把"视图中断"误报成
   "图纸没放位号"）—— 典型的静默缺口。
2. `Tools/benchmark_golden.py` 的 253 件对拍因此成片 FAIL：5.1 Place_Bound_Top
   (236)、6.1 Assembly_Top (226)、6.4 Silkscreen_Top (112) 全靠 `data["layers"]`，
   层数据空 → 全判 FAIL。
3. 2026-10-13 17:13 归档的 `Tools/log/conventions_raw/*.pads.txt`（253 份，内容非空）
   证明**当时 PAD_DEF 是能出数的** —— 所以这是**环境/工具链的退化**，不是数据本身的性质。

## 输出

    python eval/suites/geometry_regression/_extracta_probe.py [--sample <dra>] [--json]

`--json` 时打印一个 JSON 对象（供 `check_geometry_regression.py` 解析）：

    {"ok": true, "sample": "...", "cdsroot": "...", "rc": 3221226505,
     "views": [{"index": 1, "keyword": "COMPOSITE_PAD", "out": "pins.txt", "bytes": 420}, ...],
     "first_dead": 2, "first_dead_keyword": "PAD_DEF", "healthy": false}

`first_dead` = **第一个没有产出（文件缺失或 0 字节）的视图序号** —— 因为第 N 路的输出
只有第 N 个视图跑完才会落盘，所以它就是中断点。

★ 找不到 CDSROOT / extracta.exe 时返回 `{"ok": false, "reason": "no-cdsroot"}`，
   由调用方决定记 WARN（别的机器上没装 Cadence 属正常）。
★ 临时目录建在**系统 TEMP**（不进工作区），跑完即删；extracta 会在 cwd 落
   `extract.log` + `signoise.run/`，所以 cwd 必须是这个临时目录。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]                 # 项目根
VIEW_FILE = ROOT / "services" / "extracta_views" / "footprint_views.txt"
DEFAULT_SAMPLE = (ROOT.parent / "Data" / "footprint" / "3S48000163"
                  / "nb_xtal4_3d2x2d5x0d7.dra")
OUT_NAMES = ("pins.txt", "pads.txt", "geom.txt", "refdes.txt")
_VIEW_MARK = re.compile(r"^#\s*-+\s*视图\s*(\d+)")


def view_keywords(path: Path | None = None) -> list[str]:
    """从官方视图文件里取每个视图的关键字（`COMPOSITE_PAD` / `PAD_DEF` / ...）。

    规则：`# ---- 视图 N ...` 之后**第一行非注释非空**的行就是视图名。
    只用来把"第 N 路"翻译成人看得懂的名字，不参与判定。
    """
    path = path or VIEW_FILE
    if not path.is_file():
        return []
    names: list[str] = []
    pending = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if _VIEW_MARK.match(s):
            pending = True
            continue
        if pending:
            if not s or s.startswith("#"):
                continue
            names.append(s.split()[0])
            pending = False
    return names


def run_probe(sample: Path | None = None, timeout: int = 180) -> dict:
    sample = Path(sample) if sample else DEFAULT_SAMPLE
    rec: dict = {"ok": False, "sample": str(sample), "cwd": None}
    if not VIEW_FILE.is_file():
        rec["reason"] = "no-views"
        return rec
    if not sample.is_file():
        rec["reason"] = "no-sample"
        return rec

    sys.path.insert(0, str(ROOT))
    try:
        from services.cdsroot_locator import extracta_path, find_cdsroot   # noqa: PLC0415
    except Exception as exc:                                              # noqa: BLE001
        rec["reason"] = "no-service"
        rec["detail"] = f"{type(exc).__name__}: {exc}"
        return rec

    cdsroot = find_cdsroot()
    if cdsroot is None:
        rec["reason"] = "no-cdsroot"
        return rec
    exe = extracta_path(cdsroot)
    if not exe.is_file():
        rec["reason"] = "no-extracta"
        rec["detail"] = str(exe)
        return rec

    import os                                                             # noqa: PLC0415

    keywords = view_keywords()
    tmp = Path(tempfile.mkdtemp(prefix="extracta_probe_"))                # 系统 TEMP，不进工作区
    rec["cwd"] = str(tmp)
    rec["cdsroot"] = str(cdsroot)
    rec["extracta"] = str(exe)
    try:
        outs = [tmp / n for n in OUT_NAMES[:max(len(keywords), 1)]]
        env = os.environ.copy()
        env["CDSROOT"] = str(cdsroot)
        env["PATH"] = (f"{cdsroot / 'tools' / 'bin'};{cdsroot / 'tools' / 'pcb' / 'bin'};"
                       + env.get("PATH", ""))
        t0 = time.time()
        try:
            r = subprocess.run([str(exe), str(sample), str(VIEW_FILE), *map(str, outs)],
                               env=env, cwd=str(tmp), capture_output=True, timeout=timeout)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            rc = None
            rec["timeout"] = True
        rec["seconds"] = round(time.time() - t0, 1)
        rec["rc"] = rc
        views = []
        first_dead = None
        for i, out in enumerate(outs, start=1):
            size = out.stat().st_size if out.is_file() else None
            views.append({"index": i,
                          "keyword": keywords[i - 1] if i - 1 < len(keywords) else "?",
                          "out": out.name,
                          "bytes": size})
            if first_dead is None and not size:
                first_dead = i
        rec["views"] = views
        rec["first_dead"] = first_dead
        rec["first_dead_keyword"] = (views[first_dead - 1]["keyword"]
                                     if first_dead else None)
        rec["healthy"] = first_dead is None
        rec["ok"] = True
        return rec
    finally:
        import shutil                                                     # noqa: PLC0415
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="extracta 四路取数能力探针（G6 被调方）")
    ap.add_argument("--sample", default=None, help="被测 .dra（默认 3S48000163 那颗金标准）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)

    rec = run_probe(Path(args.sample) if args.sample else None, timeout=args.timeout)
    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0 if rec.get("ok") else 1
    if not rec.get("ok"):
        print(f"[?] 探针没跑成：{rec.get('reason')} {rec.get('detail', '')}")
        return 0
    print(f"extracta 四路取数 @ {Path(rec['sample']).name}  rc={rec.get('rc')}  "
          f"{rec.get('seconds')}s")
    for v in rec["views"]:
        state = f"{v['bytes']} 字节" if v["bytes"] else ("缺失" if v["bytes"] is None else "0 字节")
        print(f"  视图 {v['index']} {v['keyword']:18s} → {v['out']:11s} {state}")
    if rec.get("healthy"):
        print("  ✅ 四路都有产出")
    else:
        print(f"  ❌ 第 {rec['first_dead']} 路（{rec['first_dead_keyword']}）中断 —— "
              f"后面的视图全部没跑，几何/焊盘/位号取不到")
    return 0


if __name__ == "__main__":
    sys.exit(main())
