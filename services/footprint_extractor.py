# -*- coding: utf-8 -*-
"""
services/footprint_extractor.py —— .dra 数据提取分发器

优先级（v3，extracta 视图已修复）：
  1. extracta（Cadence 自带，不需要 Allegro 运行，快）—— 主路径
  2. SkillBridge（需要 Allegro 正在运行）—— 保底

extracta 数据完整性校验：
  如果 extracta 返回的 pins 没有 pads 或 layers 全空，视为失败，
  自动回退到 SkillBridge。
  ★ 校验**只看几何**：`texts` / 位号不在判据内 —— 见 `_extracta_gaps()`
    的说明（缺文字不触发回退，只显式上报，避免静默缺口）。

调试开关（环境变量）：
  LAYOUT_CHECK_FORCE_SOURCE   extracta | skillbridge | 不设置（默认两路）
  LAYOUT_CHECK_EXTRACTA_STRICT  =1 时，extracta 的任何缺口（含缺位号文字）
                                都算"不完整"→ 回退 SkillBridge。
                                默认关：缺位号回退也读不到，白唤起一次 Allegro。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _log(msg: str) -> None:
    print(f"[footprint_extractor] {msg}", file=sys.stderr)


def _strict_mode() -> bool:
    """LAYOUT_CHECK_EXTRACTA_STRICT=1 时，缺口也触发回退（默认关）。"""
    return os.environ.get("LAYOUT_CHECK_EXTRACTA_STRICT", "").strip().lower() \
        in ("1", "true", "yes", "on")


def _extracta_gaps(result: Dict[str, Any]) -> List[str]:
    """列出 extracta 结果里「本该能读到、这次却没读到」的项。

    ★ 这是本模块**唯一**该报"文字类缺口"的地方，且**只报告、不阻断**
      （是否阻断由 `_strict_mode()` 决定）。

    为什么位号文字不进 `_is_extracta_result_complete()` 的判据：
      · 缺几何 → 回退 SkillBridge 有意义（可能读到）；
      · 缺位号 → 回退也白搭（SkillBridge 读的是同一张 .dra，
        图纸本身没放位号就是没有），代价却是唤起一次 Allegro（几十秒）。
      → 所以缺位号只上报。它曾经是个**静默缺口**：extracta 的几何视图
        把 `CLASS = "PACKAGE GEOMETRY"` 当过滤器，REF DES 层的 `#REF`
        被挡在外面且不报错（2026-09-29 实测，已在视图文件里加第 4 路修掉）。
    """
    gaps = list(result.get("_gaps") or [])
    if gaps:
        return gaps
    # 兜底：`_gaps` 缺失时（旧数据源 / 手工构造的 dict）自己判一次
    texts = result.get("texts") or []
    if not texts:
        gaps.append("texts 为空 —— 连引脚编号都没读到，视图文件可能没配全")
    elif not (result.get("ref_des") or []):
        gaps.append("ref_des_text 未读到 —— 图纸可能确实没放 REF DES 位号")
    return gaps


def _is_extracta_result_complete(result: Dict[str, Any]) -> bool:
    """校验 extracta 返回的**几何**数据是否够用（决定是否回退 SkillBridge）。

    ★ 判据只有几何：pins 非空、pins 带 pads、三个关键层至少有一个非空。
      `texts` / 位号**刻意不在**判据内 —— 理由见 `_extracta_gaps()`。
    """
    pins = result.get("pins") or []
    if not pins:
        return False
    with_pads = sum(1 for p in pins if p.get("pads"))
    if with_pads == 0:
        return False
    layers = result.get("layers") or {}
    for key in ("assembly_top", "silkscreen_top", "place_bound_top"):
        if layers.get(key):
            return True
    return False


def read_full_footprint(
    dra_path: str,
    pad_paths: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
    include_layers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    读取 .dra 全量封装数据。

    默认顺序：extracta → SkillBridge
    """
    force = os.environ.get("LAYOUT_CHECK_FORCE_SOURCE", "").strip().lower()

    if force == "extracta":
        from services import extracta_reader
        r = extracta_reader.read_via_extracta(dra_path)
        if not r.get("error"):
            r["_gaps"] = _extracta_gaps(r)      # ★ 强制单源时也要报缺口
        return r

    if force == "skillbridge":
        from services import skillbridge_reader
        return skillbridge_reader.read_full_footprint(
            dra_path=dra_path, pad_paths=pad_paths,
            workspace_id=workspace_id, include_layers=include_layers,
        )

    # ---------- 默认：extracta 优先 ----------
    extracta_error = None
    try:
        from services import extracta_reader
        result = extracta_reader.read_via_extracta(dra_path)
        if not result.get("error"):
            gaps = _extracta_gaps(result)
            # ★ 缺口不静默：逐条打日志 + 挂回返回值，调用方可直接读
            result["_gaps"] = gaps
            for g in gaps:
                _log(f"[extracta] gap: {g}")
            if _is_extracta_result_complete(result) and not (
                    gaps and _strict_mode()):
                _log(f"[extracta] OK: {Path(dra_path).name}"
                     + (f"（{len(gaps)} 处缺口，见 _gaps）" if gaps else ""))
                return result
            if gaps and _strict_mode():
                extracta_error = (
                    f"严格模式（LAYOUT_CHECK_EXTRACTA_STRICT=1）："
                    f"extracta 有 {len(gaps)} 处缺口 → {'；'.join(gaps)}")
            else:
                extracta_error = "extracta 返回的数据不完整（pins 无 pads 或 layers 全空）"
            _log(f"[extracta] incomplete: {extracta_error}")
        else:
            extracta_error = result.get("error", "unknown")
            _log(f"[extracta] failed: {extracta_error}")
    except Exception as e:
        extracta_error = f"extracta exception: {e}"
        _log(extracta_error)

    # 回退 SkillBridge
    _log(f"[fallback] trying SkillBridge for {Path(dra_path).name}")
    try:
        from services import skillbridge_reader
        result = skillbridge_reader.read_full_footprint(
            dra_path=dra_path, pad_paths=pad_paths,
            workspace_id=workspace_id, include_layers=include_layers,
        )
        if not result.get("error"):
            _log(f"[skillbridge] OK: {Path(dra_path).name}")
            return result
        return {
            "error": (
                f"两个数据源都失败：\n"
                f"  extracta: {extracta_error}\n"
                f"  skillbridge: {result.get('error')}"
            ),
            "pins": [], "texts": [], "layers": {},
        }
    except Exception as e:
        return {
            "error": (
                f"两个数据源都失败：\n"
                f"  extracta: {extracta_error}\n"
                f"  skillbridge: {e}"
            ),
            "pins": [], "texts": [], "layers": {},
        }


if __name__ == "__main__":
    import json
    _this = Path(__file__).resolve()
    _root = _this.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    if len(sys.argv) < 2:
        print("用法: python services/footprint_extractor.py <dra路径> [pad路径1] ...")
        sys.exit(1)
    dra = sys.argv[1]
    pads = sys.argv[2:] if len(sys.argv) > 2 else []
    result = read_full_footprint(dra_path=dra, pad_paths=pads)
    print(json.dumps(result, ensure_ascii=False, indent=2))