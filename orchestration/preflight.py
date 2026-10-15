# -*- coding: utf-8 -*-
"""orchestration/preflight.py —— 动手前的预检：把「歧义」变成返回值里的字段

## 为什么需要它

`services/allegro_service.py::list_folder_files` 返回的结构里**只有一个 `pdf` 字段**
（内部 `find_first_pdf(folder)`）。也就是说：一个目录里**躺着两份规格书**时，
工具链**看不见第二份**，模型只能自己 `ls` 去猜 —— 这就是铁律 S1（"目录里多个 PDF/.dra
必须先确认"）反复被违反的根因：**规则写在文档里，而工具的输出里根本没有这个事实**。

本模块把该事实**算出来并写进返回值**：

    verdict = ok | ambiguous_pdf | ambiguous_dra | missing_pdf | missing_dra
            | empty | not_found | not_a_dir
    action  = proceed | confirm | stop

★ 只**上报**歧义，**绝不替调用方拒绝执行**：同一目录出现多份 PDF，在「批量模式 B」
  （每个子目录各一对 PDF/.dra）下完全合法。是否继续由调用方决定。
★ 零依赖、零副作用：只读目录名，不打开 PDF、不起 Allegro、不写任何文件
  （因此没有 fitz/pymupdf 也能跑 —— 预检不该成为新的失败点）。

## 用法

    python -m orchestration.preflight <目录> [--recursive] [--json]
    python -m orchestration.preflight Data/footprint --recursive
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

__all__ = ["classify_folder", "preflight", "PDF_SUFFIX", "DRA_SUFFIX", "ACTION_HINT"]

PDF_SUFFIX = ".pdf"
DRA_SUFFIX = ".dra"

#: 明显不是封装的噪声文件（Allegro 自动保存 / 编辑器备份 / 运行痕迹 / 历史检查报告）
#: ——**不计入候选**，但必须上报（S1「歧义可见」+ S2「禁复用旧报告」的物证）
NOISE_NAMES = {"autosave.dra", "autosave.brd", "master.tag", "allegro.jrl"}
NOISE_PREFIXES = ("~$", "autosave", ".~", "allegro.jrl")
NOISE_SUFFIXES = (".bak", ".tmp", ".lck", ".jrl", ".run")
#: 通配噪声（历史检查报告 / Allegro 日志）——**旧报告不是证据**（S2：本次结论只能来自本次运行）
NOISE_GLOBS = ("checklist_report_*.json", "signoise.log*", "dbdoctor.log*", "*.log")

#: 每种 verdict 对应的建议动作与给模型看的一句话提示
ACTION_HINT = {
    "ok": ("proceed", "目录里恰好一份 PDF + 一份 .dra，可以直接进入比对流程。"),
    "ambiguous_pdf": ("confirm",
                      "目录里有 **{pdf_count} 份 PDF**：先与用户确认用哪一份规格书，"
                      "再调 locate_land_pattern_page / render_pdf_region。"),
    "ambiguous_dra": ("confirm",
                      "目录里有 **{dra_count} 份 .dra**：先与用户确认检查哪一颗封装，"
                      "再调 read_full_footprint。"),
    "missing_pdf": ("stop",
                    "没有规格书 PDF：只能做 .dra 侧的结构检查（read_full_footprint + "
                    "check_footprint_by_rules 的部分类别），**无法**做规格书参数比对。"),
    "missing_dra": ("stop",
                    "没有 .dra：可以读规格书（locate_land_pattern_page / render_pdf_region），"
                    "但**无法**与实做封装比对。"),
    "empty": ("stop", "目录里既没有 PDF 也没有 .dra。"),
    "not_found": ("stop", "路径不存在。"),
    "not_a_dir": ("stop", "路径不是目录。"),
}

#: 批量模式下最多枚举的子目录数（防止一个误传的根目录把返回值撑爆）
MAX_UNITS = 200


def _is_noise(name: str) -> bool:
    low = name.lower()
    return (low in NOISE_NAMES or low.startswith(NOISE_PREFIXES)
            or low.endswith(NOISE_SUFFIXES)
            or any(fnmatch.fnmatch(low, pat) for pat in NOISE_GLOBS))


def is_noise(name: str) -> bool:
    """公开版：**任何**外部判「这个文件名算不算噪声」的地方都调它。

    2026-10-15 的教训：`eval/suites/geometry_regression` 曾自己拼 `NOISE_NAMES/PREFIXES/SUFFIXES`
    三个常量做判断，preflight 新增 `NOISE_GLOBS` 后它看不见新规则 → 同一批目录算出两个数
    （G4 报 26、preflight 报 145）。**复用常量 ≠ 复用规则**：要么调同一个函数，要么迟早分叉。
    """
    return _is_noise(name)


def _scan(folder: Path):
    """返回 (pdf 文件名列表, dra 文件名列表, 噪声文件名列表)。

    只认文件不认子目录；`AUTOSAVE.dra` 这类噪声**不算候选** —— 否则按名字排序取第一个时
    会正好取到它（`AUTOSAVE` 排在 `nb_*` 前面），等于拿自动保存的垃圾去对金标准。
    """
    pdfs, dras, noise = [], [], []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return [], [], []
    for f in entries:
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if _is_noise(f.name):
            noise.append(f.name)
            continue
        suffix = f.suffix.lower()
        if suffix == PDF_SUFFIX:
            pdfs.append(f.name)
        elif suffix == DRA_SUFFIX:
            dras.append(f.name)
    return pdfs, dras, noise


def classify_folder(folder) -> dict:
    """判定单个目录，返回带 `verdict` / `action` / `hint` 的字典。

    `ok` 表示**预检本身是否成功**（不是"目录是否完美"）—— 与项目其它工具一致：
    `ok:false` 时调用方回退旧流程。目录有问题时 `ok` 仍为 True，看 `action` / `ambiguous`。
    """
    p = Path(folder)
    result = {"folder": str(p), "verdict": "", "action": "", "ok": True,
              "ambiguous": False, "pdf_count": 0, "dra_count": 0,
              "pdf_files": [], "dra_files": [], "noise_files": [], "hint": ""}

    if not p.exists():
        result["verdict"] = "not_found"
    elif not p.is_dir():
        result["verdict"] = "not_a_dir"
    else:
        pdfs, dras, noise = _scan(p)
        result["pdf_files"], result["dra_files"] = pdfs, dras
        result["noise_files"] = noise
        result["pdf_count"], result["dra_count"] = len(pdfs), len(dras)
        if not pdfs and not dras:
            result["verdict"] = "empty"
        elif len(pdfs) > 1:
            result["verdict"] = "ambiguous_pdf"
        elif len(dras) > 1:
            result["verdict"] = "ambiguous_dra"
        elif not pdfs:
            result["verdict"] = "missing_pdf"
        elif not dras:
            result["verdict"] = "missing_dra"
        else:
            result["verdict"] = "ok"

    action, hint = ACTION_HINT[result["verdict"]]
    result["action"] = action
    result["ambiguous"] = result["verdict"] in ("ambiguous_pdf", "ambiguous_dra")
    result["hint"] = hint.format(**result)
    if result["noise_files"]:
        result["hint"] += ("  ⚠ 另有 {} 个疑似噪声文件（**已剔除**，不计入候选）：{}"
                           " —— 建议移出该目录（项目约定绝不 os.remove，走 .trash）。").format(
            len(result["noise_files"]), "、".join(result["noise_files"]))
    return result


def preflight(target, recursive: bool = False) -> dict:
    """预检入口。

    :param target: 目录路径
    :param recursive: True = 批量模式 B —— 把 `target` 的**直接子目录**各当一个单元
                      （每个单元应当各有一对 PDF/.dra），单元数上限 `MAX_UNITS`
    :return: 单目录 = `classify_folder()` 的结果 + `mode`;
             批量 = `verdict` 变 `batch_ok` / `batch_suspects`，父目录自身判定移到
             `parent_verdict`，并追加 `units[]` 与 `suspects[]`（非 ok 的单元）
    """
    base = classify_folder(target)
    if not recursive or not Path(target).is_dir():
        base["mode"] = "single"
        return base

    children = []
    try:
        children = sorted((d for d in Path(target).iterdir() if d.is_dir()),
                          key=lambda p: p.name.lower())
    except OSError:
        children = []
    truncated = len(children) > MAX_UNITS
    units = [classify_folder(d) for d in children[:MAX_UNITS]]

    suspects = [u for u in units if u["verdict"] != "ok"]
    noisy = [u for u in units if u["noise_files"]]
    base["mode"] = "batch"
    base["parent_verdict"] = base["verdict"]      # 父目录自身通常只是个壳目录
    base["verdict"] = "batch_suspects" if suspects else "batch_ok"
    base["units"] = units
    base["unit_count"] = len(units)
    base["suspects"] = suspects
    base["noisy"] = noisy
    base["truncated"] = truncated
    if truncated:
        base["hint"] += f"（子目录超过 {MAX_UNITS} 个，只看前 {MAX_UNITS} 个）"
    if suspects:
        base["action"] = "confirm"
        base["ambiguous"] = any(u["ambiguous"] for u in suspects)
        base["hint"] = (f"批量模式：{len(units)} 个子目录里 {len(suspects)} 个需要确认"
                        f"（{'、'.join(sorted({u['verdict'] for u in suspects}))}）——"
                        f"先逐条与用户确认，**不要**默认取第一个文件。")
    else:
        base["action"] = "proceed"
        base["hint"] = f"批量模式：{len(units)} 个子目录各一对 PDF/.dra，可以开始逐个比对。"
    if noisy:
        base["hint"] += (f"  ⚠ {len(noisy)} 个子目录含疑似噪声文件（已剔除，不影响判定）："
                         f"{'、'.join(u['folder'].replace(chr(92), '/').split('/')[-1] for u in noisy)}。")
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="动手前预检：目录里的 PDF / .dra 是否有歧义")
    ap.add_argument("target", help="目录路径")
    ap.add_argument("--recursive", action="store_true", help="批量模式：逐个子目录判定")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    result = preflight(args.target, recursive=args.recursive)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        tag = {"proceed": "OK  ", "confirm": "确认", "stop": "停  "}[result["action"]]
        print(f"[{tag}] {result['folder']}")
        print(f"       判定 {result['verdict']}  PDF {result['pdf_count']} / .dra {result['dra_count']}")
        print(f"       {result['hint']}")
        for u in result.get("units", []):
            if u["verdict"] != "ok":
                print(f"       · {u['folder']}  {u['verdict']}"
                      f"  PDF {u['pdf_count']} / .dra {u['dra_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
