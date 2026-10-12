# -*- coding: utf-8 -*-
"""
services/library_install.py —— 「影子库」落位

把本轮产出的 `.dra` / `.psm` / `.pad` 按既有约定装进
`..\\Data\\footprint\\<PN>\\`，让生成物**可归档、可复用**，且**不污染正式库**。

为什么要「影子」
================
攻坚清单 **P2-8 库落位与索引**：`padpath` 全项目**没有任何配置**
（ini / env / 代码都查过），靠 site 默认；`.dml` 器件库未更新。
换句话说 —— 生成出来的封装**目前根本没有落脚点**，只能躺在
`Tools\\test\\` 里当测试件，下一轮 `new_symbol_drawing` 一扫还可能被
当成模板（见 `paths.py` 里"为什么 test/ 单独放图纸"那段说明）。

而 `..\\Data\\footprint\\<PN>\\` 早已是**事实上的影子库**，里面的件长这样：

```
Data/footprint/130-221-100003-W3G--FPC 0.5-10PH=1.5/
    nb_con10_9d1x5x1d5_0d5.dra        <- 图纸
    nb_con10_9d1x5x1d5_0d5.psm        <- 编译产物
    r0d9x0d3.pad  r1d8x1d1.pad        <- padstack
    nb_con10_9d1x5x1d5_0d5.log        <- 编译日志
    tolerance_report.json             <- 公差核对报告
```

所以本模块只做一件单纯的事：**把本轮产物按同一约定拷进去**。

★★★ 四条硬约束 ★★★
1. **绝不删**：目标已存在时走 `.trash/` 隔离（复用
   `footprint_builder._quarantine_file`），与 `export_psm` 的覆盖保护同构。
   归档环节同样**不能把上一次的成果弄丢**。
2. **ASCII 路径守卫（P2-9）**：Allegro 不支持非 ASCII 路径。
   目标路径里出现中文等非 ASCII 字符 → **直接拒绝**，不做"尽力而为"。
   （空格 / `=` / `-` / `.` 都是 ASCII，允许 —— 现有目录名里就有。）
3. **不碰 Allegro 的搜索路径 —— 而且确认「不需要碰」**（2026-09-30 结论）
   ============================================================
   原攻坚项「影子库挂 `padpath`」**已关闭**，依据两条实测：
     · **约定**：`Data\\footprint\\<PN>\\` 下每个封装都是
       `.dra` + 它用到的 `.pad` **放在同一个文件夹里**（14/14 都如此）
       → 影子库照抄这个约定即可，不需要额外的 padstack 搜索目录。
     · **实测**（`probe_pad_resolve.py`，extracta 离线跑）：
       同一个 `.dra` 在「有同目录 `.pad`」/「没有 `.pad`」/「`.pad` 被改名」
       三种情况下，回读出的焊盘尺寸**完全一样**
       （ETCH/TOP 1.2×1.4、SOLDERMASK 1.3×1.5）——
       说明 **`.dra` 里自带 padstack 的副本**，读取端根本不依赖外部 `.pad`。
   → 所以本模块只保证**同目录落位**（`DEFAULT_EXTS` 已含 `.pad`），
     并在装完后用 `verify_colocation()` 报出"有符号但没 pad"的孤儿件；
     **不写 env、不改 `padpath` / `psmpath`、不调用任何挂载 API**。
4. **拷完逐个校验**：size + sha1 一致才算装好。

因此本模块**不需要 Allegro 会话** —— 纯文件操作，不占 SkillBridge 通道，
也就不会因为通道问题把归档搞挂。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths as P
from .footprint_builder import _quarantine_file

logger = logging.getLogger("layout-forge")

#: ★ 影子库根目录：`<Layout>/Data/footprint`
#:   与 `<PROJECT_ROOT>` 平级（`Layout/layout-forge-mcp/` 的上一级）
SHADOW_LIB_ROOT = os.path.join(
    os.path.dirname(P.PROJECT_ROOT), "Data", "footprint"
)

#: 默认归集的扩展名（顺序即报告顺序）
DEFAULT_EXTS: Tuple[str, ...] = (".dra", ".psm", ".pad")

#: 附加归集的固定文件名（有就带上，没有不报错）
OPTIONAL_FILES: Tuple[str, ...] = ("tolerance_report.json",)


# ============================================================
# 小工具
# ============================================================
def _sha1(path: str, chunk: int = 1 << 20) -> Optional[str]:
    """算文件 sha1；失败返回 None（调用方按"校验未通过"处理）。"""
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError as e:                        # noqa: BLE001
        logger.debug("sha1 失败 %s: %s", path, e)
        return None


def _is_ascii(s: str) -> bool:
    """Allegro 只认 ASCII 路径 —— 中文/全角一律挡掉。"""
    try:
        str(s).encode("ascii")
        return True
    except (UnicodeEncodeError, AttributeError):
        return False


def check_ascii_path(path: str) -> Optional[str]:
    """
    ASCII 路径守卫（P2-9）。

    :return: 通过返回 None；否则返回**人话错误信息**
    """
    p = str(path)
    if _is_ascii(p):
        return None
    # ★ 按**出现顺序**去重（dict.fromkeys 保序）—— 用 set/sorted 会把
    #   "晶振测试件" 打乱成 "件振晶测试"，读起来像乱码。
    bad = "".join(dict.fromkeys(c for c in p if not _is_ascii(c)))
    return (f"路径含非 ASCII 字符 「{bad}」 —— Allegro 不支持，"
            f"已拒绝落位：{p}")


def _stem(name: str) -> str:
    b = os.path.basename(str(name).replace("\\", "/"))
    for ext in (".dra", ".psm", ".pad", ".log"):
        if b.lower().endswith(ext):
            return b[: -len(ext)]
    return b.rsplit(".", 1)[0] if "." in b else b


def _case_variants(base: str) -> List[str]:
    """Allegro 会把名字小写化落盘（`NB_XTAL...` → `nb_xtal...`）→ 三种都试。"""
    out: List[str] = []
    for v in (base, base.lower(), base.upper()):
        if v not in out:
            out.append(v)
    return out


def find_artifact(name: str, exts: Iterable[str],
                  dirs: Sequence[Optional[str]]) -> Optional[str]:
    """
    在候选目录里按名找产物（大小写不敏感）。

    :param name: 符号名 / 基名（可带扩展名、可带路径）
    :param exts: 要试的扩展名，如 (".dra", ".psm")
    :param dirs: 候选目录（可含 None）
    :return: 命中的**真实路径**（正斜杠）；找不到 None
    """
    base = _stem(name)
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            actual = {n.lower(): n for n in os.listdir(d)}
        except OSError:
            continue
        for ext in exts:
            for cand in _case_variants(base + ext):
                real = actual.get(cand.lower())
                if real:
                    p = os.path.join(d, real)
                    if os.path.isfile(p):
                        return p.replace("\\", "/")
    return None


def collect_pads(dirs: Sequence[Optional[str]]) -> List[str]:
    """
    收集候选目录里的 `*.pad`（去重、按名排序）。

    ★ 为什么是"扫目录"而不是"列举 drawing 实际用到的 padstack"：
      后者要开 Allegro 会话（`axlDBGetDesign()->padstacks`）才拿得到，
      而本模块刻意做成**零会话的纯文件操作**。真实场景里图纸目录下
      通常只有本轮产出的那几个 `.pad`，扫描的误伤面很小；
      结果会在返回值的 `auto_collected_pads` 里如实报出来，
      调用方也可以用 `pad_names=` 显式指定来绕过扫描。
    """
    found: Dict[str, str] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.lower().endswith(".pad"):
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    found.setdefault(n.lower(), p.replace("\\", "/"))
    return [found[k] for k in sorted(found)]


def _pad_referenced_by_dra(dra_path: str, pad_path: str) -> bool:
    """
    判断 `.dra` 二进制里是否出现某 padstack 名（零会话过滤）。

    ★ 2026-10-12 加（修 XTAL/FPC 残留误卷）：Allegro 的 `.dra` 以二进制
      存储，但 padstack 名以 ASCII 明文出现 —— 按**大小写不敏感**的
      子串检索即可判定「这个 padstack 是否被该符号引用」。
      实测（Tools/test 6 份 .dra × 5 个 .pad 交叉验证）：
      每份 .dra 都只命中自己的 padstack，无关 .pad 全部剔除，
      零误报零漏报。
    读不了文件时**返回 True 不滤**（宁多勿漏，交给告警兜底）。
    """
    try:
        with open(dra_path, "rb") as fh:
            blob = fh.read().lower()
    except OSError:
        return True
    base = os.path.splitext(os.path.basename(pad_path))[0].lower()
    return base.encode("ascii", "ignore") in blob


def verify_colocation(
    dir_path: str,
    *,
    symbol_exts: Sequence[str] = (".dra", ".psm"),
) -> Dict[str, Any]:
    """
    检查「符号文件 + 它的 padstack」是否同处一个目录。

    ★ 为什么要这条检查（2026-09-30 用户裁决 + 实测）
      `Data\\footprint\\<PN>\\` 的事实约定就是 **`.dra` 和 `.pad` 同目录**；
      原攻坚项「影子库挂 `padpath`」已关闭（见模块 docstring 第 3 条），
      取而代之的是这条**约定守卫**：归档后报出"有符号但一个 pad 都没有"
      的孤儿目录，让人一眼看出是漏带了 pad，而不是等到 Allegro 里
      接手时才发现 padstack 找不到。

    ★ 注意口径：这是**约定检查，不是可用性检查**。
      实测 `.dra` 自带 padstack 副本，缺 `.pad` 也能读；所以缺 pad
      只记 `warnings`，**不算失败**。

    :param dir_path:    要检查的目录（通常是影子库子目录）
    :param symbol_exts: 视作"符号"的扩展名
    :return: {"dir": str, "pads": [文件名...], "symbols": [文件名...],
              "orphans": [符号名...], "ok": bool, "error": str|None}
    """
    root = os.path.abspath(str(dir_path))
    if not os.path.isdir(root):
        return {"dir": P.as_posix(root), "pads": [], "symbols": [],
                "orphans": [], "ok": False,
                "error": f"目录不存在: {P.as_posix(root)}"}
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return {"dir": P.as_posix(root), "pads": [], "symbols": [],
                "orphans": [], "ok": False, "error": str(e)}

    pads = [n for n in names
            if n.lower().endswith(".pad") and os.path.isfile(os.path.join(root, n))]
    symbols = [n for n in names
               if os.path.splitext(n)[1].lower() in
               tuple(e.lower() for e in symbol_exts)
               and os.path.isfile(os.path.join(root, n))]
    # 同一符号往往同时有 .dra / .psm → 按基名归并，只报一次
    orphans = sorted({os.path.splitext(n)[0] for n in symbols}) if not pads else []
    return {
        "dir": P.as_posix(root),
        "pads": pads,
        "symbols": symbols,
        "orphans": orphans,
        "ok": bool(symbols) and bool(pads),
        "error": None,
    }


# ============================================================
# 主流程
# ============================================================
def install_to_shadow_library(
    symbol_name: Optional[str] = None,
    *,
    pn: Optional[str] = None,
    artifacts: Optional[Sequence[str]] = None,
    pad_names: Optional[Sequence[str]] = None,
    src_dirs: Optional[Sequence[Optional[str]]] = None,
    lib_root: Optional[str] = None,
    exts: Sequence[str] = DEFAULT_EXTS,
    extra_files: Sequence[str] = OPTIONAL_FILES,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    ★ 把产物归集进影子库 `..\\Data\\footprint\\<PN>\\`。

    ★★★ 默认 `overwrite=False` —— **归档默认绝不覆盖** ★★★
      2026-09-29 实测：影子库里的件**未必是生成物，可能是人工金标准**。
      例：`Data/footprint/3S48000163/nb_xtal4_3d2x2d5x0d7.dra` 的 md5
      （`38c0b301…`）与 `Tools/test/golden_3S48000163.dra` **完全相同** ——
      那正是本项目逐值对拍用的参照物。
      若默认覆盖，一次"顺手归档"就会把参照物换成生成件，
      后续所有对拍都会变成"自己跟自己比"，**且毫无提示**。
      → 所以默认只装**新的**，已存在的一律记进 `collisions` 原样不动；
        真要替换必须显式 `overwrite=True`（此时旧件仍先移入 `.trash/`
        隔离区，拷贝校验通过才算替换完成，随时可恢复）。

    ★ 覆盖路径的保护策略与 `export_psm` 完全一致：**先隔离、后覆盖、
      校验通过才算数**；隔离失败则该文件直接跳过，绝不退化成 `os.remove`。

    :param symbol_name: 符号名（用于按名找 `.dra` / `.psm`）
    :param pn:         影子库里的子目录名（料号）。None 时用 `symbol_name`
    :param artifacts:  显式文件清单（给定则**不再按名搜索**，只归集这些）
    :param pad_names:  显式 padstack 名（如 ["R1D4X1D2"]）；None 时扫目录
    :param src_dirs:   产物所在目录（按顺序找）；None 时用
                       `[图纸目录?]` 无从得知 → 默认 `Tools/test` + `Tools`
    :param lib_root:   影子库根；None 时用 `SHADOW_LIB_ROOT`
    :param overwrite:  **默认 False**：目标已存在就跳过并记进 `collisions`
                       （不隔离、不覆盖）。见上面金标准那段说明。
    :param dry_run:    True 时只报告"会装什么"，**一个字节都不动**
    :return: {"success": bool, "lib_root": str, "target_dir": str,
              "installed": [{"ext","name","src","dst","size","sha1",
                             "replaced"(旧件隔离路径|None)}],
              "collisions": [{"name","dst","size_existing"}],
              "skipped": [{"name","reason"}],
              "missing": [{"ext","name"}],
              "auto_collected_pads": [str],
              "colocation": {"dir","pads","symbols","orphans","ok","error"},
              "warnings": [str], "error": str|None}
    """
    warnings: List[str] = []
    installed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []

    # ---- 0) 参数归一 ----
    base_name = _stem(symbol_name) if symbol_name else None
    sub = str(pn or base_name or "").strip()
    root = os.path.abspath(lib_root or SHADOW_LIB_ROOT)

    if not sub:
        return {"success": False, "lib_root": P.as_posix(root),
                "target_dir": None, "installed": [], "skipped": [],
                "missing": [], "auto_collected_pads": [],
                "warnings": warnings,
                "error": "必须给 symbol_name 或 pn 之一，否则无法确定影子库子目录"}

    srcs: List[str] = [s for s in (src_dirs or (P.TEST_DIR, P.TOOLS_DIR)) if s]
    target_dir = os.path.join(root, sub)

    # ---- 1) ASCII 路径守卫（P2-9）—— 不合格直接拒绝，不"尽量试试" ----
    for label, pth in (("影子库根", root), ("影子库子目录", target_dir)):
        err = check_ascii_path(pth)
        if err:
            return {"success": False, "lib_root": P.as_posix(root),
                    "target_dir": P.as_posix(target_dir),
                    "installed": [], "collisions": [], "skipped": [], "missing": [],
                    "auto_collected_pads": [], "warnings": warnings,
                    "error": f"{label}不合格：{err}"}

    # ---- 2) 收集要装的文件 ----
    plan: List[Tuple[str, str]] = []        # (源路径, 目标文件名)
    auto_pads: List[str] = []

    if artifacts:
        for a in artifacts:
            ap = str(a).replace("\\", "/")
            if not os.path.isfile(ap):
                missing.append({"ext": os.path.splitext(ap)[1] or "?",
                                "name": os.path.basename(ap)})
                continue
            plan.append((ap, os.path.basename(ap)))
    else:
        if not base_name:
            return {"success": False, "lib_root": P.as_posix(root),
                    "target_dir": P.as_posix(target_dir),
                    "installed": [], "collisions": [], "skipped": [], "missing": [],
                    "auto_collected_pads": [], "warnings": warnings,
                    "error": "未给 artifacts 时必须有 symbol_name 才能按名搜索"}
        for ext in exts:
            if ext == ".pad":
                # padstack 名字与符号名无关（如 `R1D4X1D2`），
                # 不能按符号名找 —— 走下面的 pad_names / 扫目录分支。
                continue
            hit = find_artifact(base_name, (ext,), srcs)
            if hit:
                plan.append((hit, os.path.basename(hit)))
            else:
                missing.append({"ext": ext, "name": base_name + ext})

        # padstack：显式优先，否则扫目录
        if pad_names:
            for nm in pad_names:
                hit = find_artifact(nm, (".pad",), srcs)
                if hit:
                    plan.append((hit, os.path.basename(hit)))
                else:
                    missing.append({"ext": ".pad", "name": _stem(nm) + ".pad"})
        else:
            auto_pads = collect_pads(srcs)
            if auto_pads:
                # ★ 2026-10-12 修误卷（XTAL/FPC 残留两次应验）：
                #   自动扫到的 .pad 不再"全部归集"，先按 .dra 二进制里
                #   实际引用的 padstack 名过滤（零会话，见
                #   `_pad_referenced_by_dra` 的实测记录）。
                _dra_hit = next(
                    (s for s, n in plan if n.lower().endswith(".dra")), None)
                if _dra_hit:
                    _kept = [p for p in auto_pads
                             if _pad_referenced_by_dra(_dra_hit, p)]
                    _drop = [os.path.basename(p) for p in auto_pads
                             if p not in _kept]
                    if _drop:
                        warnings.append(
                            "自动扫到的 .pad 已按 .dra 实际引用过滤，剔除: "
                            + ", ".join(_drop))
                    auto_pads = _kept
                if not auto_pads:
                    warnings.append(
                        "扫描到的 .pad 均未被 .dra 引用 —— 本次不归集任何 pad"
                        "（.dra 自带 padstack 副本，不影响使用）；"
                        "若确需带 pad 请用 pad_names= 显式指定。")
                else:
                    warnings.append(
                        "自动扫到并归集 "
                        + ", ".join(os.path.basename(p) for p in auto_pads)
                        + "（已按 .dra 实际引用过滤）—— 若不全请用 "
                          "pad_names= 显式指定。")
            plan += [(p, os.path.basename(p)) for p in auto_pads]

    # 附加固定文件（有就顺带带上，没有不算错）
    for fn in extra_files or ():
        for d in srcs:
            if not d:
                continue
            cand = os.path.join(d, fn)
            if os.path.isfile(cand):
                plan.append((cand.replace("\\", "/"), fn))
                break

    # 去重（同一目标名只装一次，先到先得）
    seen: Dict[str, str] = {}
    deduped: List[Tuple[str, str]] = []
    for src, name in plan:
        key = name.lower()
        if key in seen:
            skipped.append({"name": name,
                            "reason": f"重名，已用 {seen[key]}"})
            continue
        seen[key] = src
        deduped.append((src, name))
    plan = deduped

    if not plan:
        return {"success": False, "lib_root": P.as_posix(root),
                "target_dir": P.as_posix(target_dir),
                "installed": [], "collisions": collisions,
                "skipped": skipped, "missing": missing,
                "auto_collected_pads": [P.as_posix(p) for p in auto_pads],
                "warnings": warnings,
                "error": "没找到任何可归集的产物（.dra/.psm/.pad），"
                         "请确认符号名与源目录是否正确"}

    # ---- 3) dry-run 到此为止（但照样把 collisions 提前算出来）----
    if dry_run:
        if auto_pads:
            warnings.append(
                "dry_run 预览将归集上述自动扫到的 .pad —— 正式执行前请核对是否"
                "为该符号实际 padstack（误卷历史：XTAL/FPC 无关残留曾应验；"
                "现已按 .dra 实际引用过滤，仍建议核对一眼）。")
        for _s, n in plan:
            _dst = os.path.join(target_dir, n)
            if os.path.exists(_dst):
                try:
                    _sz = os.path.getsize(_dst)
                except OSError:
                    _sz = None
                collisions.append({"name": n, "dst": P.as_posix(_dst),
                                   "size_existing": _sz})
        return {"success": True, "dry_run": True,
                "lib_root": P.as_posix(root),
                "target_dir": P.as_posix(target_dir),
                "installed": [{"name": n, "src": P.as_posix(s),
                               "dst": P.as_posix(os.path.join(target_dir, n)),
                               "collides": os.path.exists(
                                   os.path.join(target_dir, n))}
                              for s, n in plan],
                "collisions": collisions,
                "skipped": skipped, "missing": missing,
                "auto_collected_pads": [P.as_posix(p) for p in auto_pads],
                "warnings": warnings, "error": None}

    # ---- 4) 落盘：先隔离旧件，再拷，再校验 ----
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        return {"success": False, "lib_root": P.as_posix(root),
                "target_dir": P.as_posix(target_dir),
                "installed": [], "collisions": collisions,
                "skipped": skipped, "missing": missing,
                "auto_collected_pads": [P.as_posix(p) for p in auto_pads],
                "warnings": warnings,
                "error": f"创建影子库子目录失败: {e}"}

    for src, name in plan:
        dst = os.path.join(target_dir, name)
        if os.path.exists(dst):
            if not overwrite:
                # ★ 原样不动 —— 影子库里可能是**人工金标准**（见函数 docstring），
                #   一次"顺手归档"不该把它换掉。要换必须显式 overwrite=True。
                try:
                    _sz = os.path.getsize(dst)
                except OSError:
                    _sz = None
                collisions.append({"name": name, "dst": P.as_posix(dst),
                                   "size_existing": _sz})
                continue
            # ★ 不删 —— 移入 .trash/ 隔离，隔离失败则**中止这一个文件**
            #   （绝不能退化成 os.remove：那会让上一次的成果凭空消失）
            moved = _quarantine_file(dst, reason="superseded_by_shadow_install")
            if not moved:
                skipped.append({"name": name,
                                "reason": "旧件隔离失败，为免丢失已跳过该文件"})
                warnings.append(f"{name}: 旧件无法隔离，未覆盖")
                continue
        else:
            moved = None

        try:
            shutil.copy2(src, dst)
        except OSError as e:
            warnings.append(f"{name}: 拷贝失败 {e}")
            skipped.append({"name": name, "reason": f"拷贝失败: {e}"})
            continue

        # ---- 校验：size + sha1 ----
        try:
            s_size, d_size = os.path.getsize(src), os.path.getsize(dst)
        except OSError as e:
            warnings.append(f"{name}: 校验取 size 失败 {e}")
            skipped.append({"name": name, "reason": f"校验失败: {e}"})
            continue
        s_sha, d_sha = _sha1(src), _sha1(dst)
        if s_size != d_size or not s_sha or s_sha != d_sha:
            warnings.append(
                f"{name}: 拷贝后校验不一致（size {s_size}->{d_size}, "
                f"sha1 {s_sha}->{d_sha}）—— 视为未装好")
            skipped.append({"name": name, "reason": "拷贝后校验不一致"})
            continue

        installed.append({
            "ext": os.path.splitext(name)[1].lower(),
            "name": name,
            "src": P.as_posix(src),
            "dst": P.as_posix(dst),
            "size": d_size,
            "sha1": d_sha,
            "replaced": moved,
        })

    ok = bool(installed) and not any(
        s.get("reason", "").startswith("拷贝失败") for s in skipped)

    if ok:
        err: Optional[str] = None
    elif installed:
        err = "部分文件落位失败，详见 skipped"
    elif collisions and not skipped:
        err = (f"没有文件需要落位：{len(collisions)} 个目标已存在于影子库，"
               f"且 overwrite=False（大概率是本轮产物已归档过，"
               f"或影子库里已有该料号的人工件）。"
               f"确需替换请显式传 overwrite=True —— 旧件会先移入 .trash/ 隔离，"
               f"可恢复。")
    else:
        err = "没有任何文件成功落位"

    if collisions and overwrite:
        warnings.append(
            f"已覆盖 {len(collisions)} 个同名文件（旧件在 .trash/ 隔离区可恢复）")

    # ---- 5) 约定守卫：符号与 padstack 必须同目录（不需要挂 padpath）----
    coloc = verify_colocation(target_dir)
    if coloc.get("symbols") and not coloc.get("pads"):
        warnings.append(
            "同目录里只有符号、一个 .pad 都没有（"
            + ", ".join(coloc["symbols"])
            + "）—— 现有模板库的约定是 `.dra` 与它的 `.pad` 放同一目录；"
              "请用 pad_names= 显式指定，或把 .pad 拷进来。"
              "（注：实测 .dra 自带 padstack 副本，缺 pad 仍能回读，"
              "故只告警不算失败。）")

    return {
        "success": ok,
        "lib_root": P.as_posix(root),
        "target_dir": P.as_posix(target_dir),
        "installed": installed,
        "collisions": collisions,
        "skipped": skipped,
        "missing": missing,
        "auto_collected_pads": [P.as_posix(p) for p in auto_pads],
        "colocation": coloc,
        "warnings": warnings,
        "error": err,
    }


def list_shadow_library(lib_root: Optional[str] = None) -> Dict[str, Any]:
    """
    列出影子库里已归档的料号及其产物（只读，方便人工核对）。

    :return: {"root": str, "entries": [{"pn","path","files":[{"name","size"}]}],
              "error": str|None}
    """
    root = os.path.abspath(lib_root or SHADOW_LIB_ROOT)
    err = check_ascii_path(root)
    if err:
        return {"root": P.as_posix(root), "entries": [], "error": err}
    if not os.path.isdir(root):
        return {"root": P.as_posix(root), "entries": [],
                "error": f"影子库根目录不存在: {P.as_posix(root)}"}
    entries: List[Dict[str, Any]] = []
    try:
        subs = sorted(os.listdir(root))
    except OSError as e:
        return {"root": P.as_posix(root), "entries": [], "error": str(e)}
    for sub in subs:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        files: List[Dict[str, Any]] = []
        try:
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    try:
                        sz = os.path.getsize(p)
                    except OSError:
                        sz = None
                    files.append({"name": n, "size": sz})
        except OSError:
            continue
        entries.append({"pn": sub, "path": P.as_posix(d), "files": files,
                        "colocation": verify_colocation(d)})
    return {"root": P.as_posix(root), "entries": entries, "error": None}


__all__ = [
    "SHADOW_LIB_ROOT",
    "DEFAULT_EXTS",
    "OPTIONAL_FILES",
    "install_to_shadow_library",
    "list_shadow_library",
    "find_artifact",
    "collect_pads",
    "_pad_referenced_by_dra",
    "check_ascii_path",
    "verify_colocation",
]
