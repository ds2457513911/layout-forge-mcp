# -*- coding: utf-8 -*-
"""
compare_extracta.py —— 两份 extracta dump 的逐列程序化对拍（验收强制工具）

★ 背景（2026-10-13 holeType 事故）：验收对拍曾用肉眼比对，差异明明打印在
  终端里（CIRCLE_DRILL vs OVAL SLOT）仍被宣布「全字段一致」。
  本工具把对拍变成**断言**：逐行逐列 diff，有差异即非零退出码，
  不存在「看着像一致」。

用法：
    ./.venv/Scripts/python.exe Tools/compare_extracta.py <dirA> <dirB> [选项]

    dirA/dirB  各含 extracta 输出（pins.txt/pads.txt/geom.txt/refdes.txt，
               有哪个比哪个；通常由 extracta 跑两份 .dra 产生）

选项：
    --tol F          数值列容差（mm，默认 0.0005——覆盖金标准 0.5001 类截断尾差）
    --ignore-col N   忽略第 N 列（1 基，可重复；如 13=drillChar 钻头表字符）
    --max-show N     每类差异最多展示条数（默认 8）

退出码：0=逐列一致（容差内）/ 1=有差异 / 2=参数或文件错误

对拍口径（SKILL 模式 C 验收条款）：验收声明「与金标准一致」必须附本工具
的完整输出；手写「一致」无效。
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.extracta_reader import _read_extracta_output  # noqa: E402

# 默认只对拍 pads.txt（padstack 定义逐列）——验收核心主张是
# padstack 同名同参 + DRILL 行一致。pins/geom/refdes 含原点平移、
# 内部序号（RECORD_TAG）等无语义差异，整图对拍请显式 --files 指定。
DUMP_FILES_DEFAULT = ("pads.txt",)
DUMP_FILES_ALL = ("pins.txt", "pads.txt", "geom.txt", "refdes.txt")


def _try_float(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load_rows(dump_dir: str, files) -> dict:
    """读 dump 目录，返回 {文件名: {(key): row}}，key=(第1列, 第2列)。"""
    out: dict = {}
    for fn in files:
        path = os.path.join(dump_dir, fn)
        if not os.path.isfile(path):
            continue
        rows = _read_extracta_output(__import__("pathlib").Path(path))
        d = {}
        for r in rows:
            key = (r[0], r[1]) if len(r) >= 2 else (r[0], "")
            if key in d:
                # 同 key 多行（少见）：追加序号防覆盖
                n = 2
                while f"{key}#{n}" in d:
                    n += 1
                d[f"{key}#{n}"] = r
            else:
                d[key] = r
        out[fn] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="extracta dump 逐列对拍")
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--tol", type=float, default=0.0005)
    ap.add_argument("--ignore-col", type=int, action="append", default=[],
                    help="忽略的列（1 基，可重复）")
    ap.add_argument("--max-show", type=int, default=8)
    ap.add_argument("--files", nargs="+", default=list(DUMP_FILES_DEFAULT),
                    choices=list(DUMP_FILES_ALL),
                    help="参与对拍的 dump 文件（默认仅 pads.txt）")
    args = ap.parse_args()

    for d in (args.dir_a, args.dir_b):
        if not os.path.isdir(d):
            print(f"✗ 目录不存在: {d}")
            return 2

    a = load_rows(args.dir_a, args.files)
    b = load_rows(args.dir_b, args.files)
    ignore = {n - 1 for n in args.ignore_col}

    diffs: list = []
    n_rows_cmp = 0

    for fn in args.files:
        da, db = a.get(fn, {}), b.get(fn, {})
        only_a = set(da) - set(db)
        only_b = set(db) - set(da)
        for k in sorted(only_a, key=repr):
            diffs.append((fn, str(k), "-", f"仅 {args.dir_a} 有此行"))
        for k in sorted(only_b, key=repr):
            diffs.append((fn, str(k), "-", f"仅 {args.dir_b} 有此行"))
        for k in sorted(set(da) & set(db), key=repr):
            n_rows_cmp += 1
            ra, rb = da[k], db[k]
            width = max(len(ra), len(rb))
            for i in range(width):
                if i in ignore:
                    continue
                va = ra[i] if i < len(ra) else "<缺列>"
                vb = rb[i] if i < len(rb) else "<缺列>"
                if va == vb:
                    continue
                fa, fb = _try_float(va), _try_float(vb)
                if fa is not None and fb is not None and abs(fa - fb) <= args.tol:
                    continue
                diffs.append((fn, str(k), f"col{i + 1}", f"{va!r} vs {vb!r}"))

    print(f"对拍: {args.dir_a}  vs  {args.dir_b}")
    print(f"公共行: {n_rows_cmp}  |  忽略列: {sorted(n + 1 for n in ignore)}  |  "
          f"数值容差: {args.tol}")
    print("=" * 64)
    if not diffs:
        print("结果: 逐列一致（容差内）✓")
        return 0
    print(f"结果: {len(diffs)} 处差异 ✗")
    shown = {}
    for fn, key, col, detail in diffs:
        shown.setdefault(fn, 0)
        shown[fn] += 1
        if shown[fn] <= args.max_show:
            print(f"  [{fn}] {key}  {col}: {detail}")
    for fn, n in shown.items():
        if n > args.max_show:
            print(f"  [{fn}] …共 {n} 处（只展示前 {args.max_show}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
