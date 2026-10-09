# -*- coding: utf-8 -*-
"""
services/skillbridge_reader.py —— 通过 SkillBridge 读取 .dra（原有实现搬迁）

此文件是从原 footprint_extractor.py 搬迁的 SkillBridge 部分，
作为 extracta 不可用时的保底数据源。

与 extracta 版本的区别：
  - 需要 Allegro 正在运行，且端口 7777 可用
  - 需要 .dra 文件可读（会自动复制到临时英文目录规避中文路径/文件锁）
  - 读取速度受 SkillBridge socket 通信影响
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from skillbridge import Workspace
except ImportError:
    raise ImportError("请先安装 skillbridge: pip install skillbridge")


MIL_TO_MM = 0.0254


# 目标层配置
LAYER_TARGETS = {
    "silkscreen_top": {
        "layer_name": "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "filter": ["lines", "text", "shapes"],
    },
    "silkscreen_bottom": {
        "layer_name": "PACKAGE GEOMETRY/SILKSCREEN_BOTTOM",
        "filter": ["lines", "text", "shapes"],
    },
    "assembly_top": {
        "layer_name": "PACKAGE GEOMETRY/ASSEMBLY_TOP",
        "filter": ["lines", "text", "shapes"],
    },
    "assembly_bottom": {
        "layer_name": "PACKAGE GEOMETRY/ASSEMBLY_BOTTOM",
        "filter": ["lines", "text", "shapes"],
    },
    "place_bound_top": {
        "layer_name": "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
        "filter": ["lines", "shapes"],
    },
    "place_bound_bottom": {
        "layer_name": "PACKAGE GEOMETRY/PLACE_BOUND_BOTTOM",
        "filter": ["lines", "shapes"],
    },
}


# ============================================================
# 单位判定（保留原逻辑）
# ============================================================
def _detect_units(units_raw: Any) -> str:
    s = str(units_raw).lower()
    if "millimeter" in s or "millimetre" in s:
        return "mm"
    if "mils" in s or "thou" in s:
        return "mil"
    if " mm" in s or "'mm'" in s or '"mm"' in s:
        return "mm"
    if " mil" in s or "'mil'" in s or '"mil"' in s:
        return "mil"
    return "mil"


# ============================================================
# 辅助工具（原样保留）
# ============================================================
def _has_non_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _copy_to_temp(dra_path: str, pad_paths: List[str]) -> tuple[str, List[str], str]:
    temp_dir = tempfile.mkdtemp(prefix="allegro_tmp_")

    dra_name = os.path.basename(dra_path)
    if _has_non_ascii(dra_name):
        dra_name = "temp_footprint.dra"
    new_dra = os.path.join(temp_dir, dra_name)
    shutil.copy2(dra_path, new_dra)

    src_dir = os.path.dirname(dra_path)
    src_stem = os.path.splitext(os.path.basename(dra_path))[0]
    new_stem = os.path.splitext(dra_name)[0]
    src_psm = os.path.join(src_dir, src_stem + ".psm")
    if os.path.isfile(src_psm):
        shutil.copy2(src_psm, os.path.join(temp_dir, new_stem + ".psm"))

    new_pads = []
    for i, p in enumerate(pad_paths):
        try:
            pad_name = os.path.basename(p)
            if _has_non_ascii(pad_name):
                pad_name = f"temp_pad_{i}.pad"
            new_p = os.path.join(temp_dir, pad_name)
            shutil.copy2(p, new_p)
            new_pads.append(new_p)
        except Exception:
            new_pads.append(p)

    return new_dra, new_pads, temp_dir


def _safe_get(obj, key, default=None):
    if obj is None:
        return default
    try:
        v = obj[key]
        if v is not None:
            return v
    except Exception:
        pass
    try:
        v = getattr(obj, key)
        if v is not None:
            return v
    except Exception:
        pass
    return default


def _try_list(obj):
    if obj is None:
        return []
    try:
        return list(obj)
    except (TypeError, Exception):
        return []


def _to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bbox_to_list(bbox_raw) -> Optional[List[List[float]]]:
    if bbox_raw is None:
        return None
    try:
        return [
            [_to_float(bbox_raw[0][0]), _to_float(bbox_raw[0][1])],
            [_to_float(bbox_raw[1][0]), _to_float(bbox_raw[1][1])],
        ]
    except Exception:
        return None


def _bbox_center(bbox) -> Optional[List[float]]:
    if bbox is None:
        return None
    try:
        return [
            (bbox[0][0] + bbox[1][0]) / 2,
            (bbox[0][1] + bbox[1][1]) / 2,
        ]
    except Exception:
        return None


def _bbox_size(bbox) -> Optional[List[float]]:
    if bbox is None:
        return None
    try:
        return [
            abs(bbox[1][0] - bbox[0][0]),
            abs(bbox[1][1] - bbox[0][1]),
        ]
    except Exception:
        return None


def _extract_graphic_element(item) -> Dict[str, Any]:
    obj_type = str(_safe_get(item, "obj_type") or "unknown")
    layer = str(_safe_get(item, "layer") or "")

    bbox = _bbox_to_list(_safe_get(item, "b_box"))
    size = _bbox_size(bbox)

    segments = []
    segs = _safe_get(item, "segments")
    for s in _try_list(segs):
        se = _safe_get(s, "start_end")
        if se is not None:
            try:
                segments.append([
                    [_to_float(se[0][0]), _to_float(se[0][1])],
                    [_to_float(se[1][0]), _to_float(se[1][1])],
                ])
            except Exception:
                pass

    return {
        "obj_type": obj_type,
        "layer": layer,
        "bbox": bbox,
        "size": size,
        "center": _bbox_center(bbox),
        "n_segs": len(segments),
        "segments": segments,
        "is_rect": bool(_safe_get(item, "is_rect", False)),
    }


def _get_layer_elements(ws, layer_name: str, filter_types: List[str]) -> List[Dict[str, Any]]:
    try:
        ws["axlVisibleDesign"](None)
        ws["axlVisibleLayer"](layer_name, True)
        ws["axlVisibleUpdate"](None)
    except Exception:
        return []

    try:
        ws["axlClearSelSet"]()
    except Exception:
        return []

    try:
        enabled = ["noall"] + filter_types
        ws["axlSetFindFilter"](enabled=enabled, onButtons=enabled)
    except Exception:
        pass

    try:
        ws["axlAddSelectAll"]()
    except Exception:
        return []

    try:
        sel = ws["axlGetSelSet"]()
    except Exception:
        return []

    return [_extract_graphic_element(item) for item in _try_list(sel)]


def _extract_pins(design) -> List[Dict[str, Any]]:
    pins_out = []
    for pin in _try_list(_safe_get(design, "pins")):
        pin_number = str(_safe_get(pin, "number") or "")
        pin_name = str(_safe_get(pin, "name") or "")
        xy = _safe_get(pin, "xy")
        relxy = _safe_get(pin, "relxy")
        rotation = _to_float(_safe_get(pin, "rotation"))
        rel_rotation = _to_float(_safe_get(pin, "rel_rotation"))
        bbox = _bbox_to_list(_safe_get(pin, "b_box"))
        size = _bbox_size(bbox)

        pads_out = []
        for pad in _try_list(_safe_get(pin, "pads")):
            pads_out.append({
                "layer": str(_safe_get(pad, "layer") or ""),
                "figure_name": str(_safe_get(pad, "figure_name") or ""),
                "bbox": _bbox_to_list(_safe_get(pad, "b_box")),
                "size": _bbox_size(_bbox_to_list(_safe_get(pad, "b_box"))),
            })

        pins_out.append({
            "number": pin_number,
            "name": pin_name,
            "xy": [_to_float(xy[0]), _to_float(xy[1])] if xy else None,
            "relxy": [_to_float(relxy[0]), _to_float(relxy[1])] if relxy else None,
            "rotation": rotation,
            "rel_rotation": rel_rotation,
            "bbox": bbox,
            "size": size,
            "pads": pads_out,
        })
    return pins_out


def _extract_texts(design) -> List[Dict[str, Any]]:
    texts_out = []
    for t in _try_list(_safe_get(design, "text")):
        xy = _safe_get(t, "xy")
        texts_out.append({
            "text": str(_safe_get(t, "text") or ""),
            "layer": str(_safe_get(t, "layer") or ""),
            "xy": [_to_float(xy[0]), _to_float(xy[1])] if xy else None,
            "bbox": _bbox_to_list(_safe_get(t, "b_box")),
            "rotation": _to_float(_safe_get(t, "rotation")),
            "justify": str(_safe_get(t, "justify") or ""),
            "text_block": str(_safe_get(t, "text_block") or ""),
        })
    return texts_out


# ============================================================
# 主入口（与 extracta_reader.read_via_extracta 同结构）
# ============================================================
def read_full_footprint(
    dra_path: str,
    pad_paths: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
    include_layers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    通过 SkillBridge 读取 .dra，返回结构化的封装数据。

    与 extracta_reader 版本完全同结构，供上层无缝切换。
    """
    pad_paths = pad_paths or []
    temp_dir = None

    if not os.path.isfile(dra_path):
        return {"error": f".dra 文件不存在: {dra_path}",
                "pins": [], "texts": [], "layers": {}}
    for p in pad_paths:
        if not os.path.isfile(p):
            return {"error": f".pad 文件不存在: {p}",
                    "pins": [], "texts": [], "layers": {}}

    try:
        new_dra, new_pads, temp_dir = _copy_to_temp(dra_path, pad_paths)
    except Exception as e:
        return {"error": f"复制到临时目录失败: {e}",
                "pins": [], "texts": [], "layers": {}}

    ws = Workspace.open(workspace_id=workspace_id or "7777")

    try:
        try:
            result = ws["axlOpenDesign"](design=new_dra, mode="wf")
            if not result:
                return {"error": f"axlOpenDesign 返回失败: {new_dra}",
                        "pins": [], "texts": [], "layers": {}}
        except Exception as e:
            return {"error": f"无法打开 .dra: {e}",
                    "pins": [], "texts": [], "layers": {}}

        try:
            units_raw = ws["axlDBGetDesignUnits"]()
            units = _detect_units(units_raw)
        except Exception:
            units_raw = None
            units = "unknown"

        design = ws["axlDBGetDesign"]()
        if design is None:
            return {"error": "axlDBGetDesign 返回空",
                    "pins": [], "texts": [], "layers": {}}

        design_bbox_raw = _bbox_to_list(_safe_get(design, "b_box"))
        design_bbox = None
        if design_bbox_raw is not None:
            if units == "mil":
                value_mm = [
                    [round(design_bbox_raw[0][0] * MIL_TO_MM, 4),
                     round(design_bbox_raw[0][1] * MIL_TO_MM, 4)],
                    [round(design_bbox_raw[1][0] * MIL_TO_MM, 4),
                     round(design_bbox_raw[1][1] * MIL_TO_MM, 4)],
                ]
            else:
                value_mm = design_bbox_raw
            design_bbox = {
                "value": design_bbox_raw,
                "unit": units,
                "value_mm": value_mm,
                "size_mm": _bbox_size(value_mm),
                "center_mm": _bbox_center(value_mm),
            }

        symbol_name = os.path.splitext(os.path.basename(dra_path))[0]
        pins = _extract_pins(design)
        texts = _extract_texts(design)

        layers_to_extract = include_layers or list(LAYER_TARGETS.keys())
        layers_data: Dict[str, List[Dict[str, Any]]] = {}
        for key in layers_to_extract:
            cfg = LAYER_TARGETS.get(key)
            if cfg is None:
                continue
            layers_data[key] = _get_layer_elements(ws, cfg["layer_name"], cfg["filter"])

        return {
            "source_file": os.path.abspath(dra_path),
            "symbol_name": symbol_name,
            "units": units,
            "units_raw": str(units_raw) if units_raw is not None else None,
            "design_bbox": design_bbox,
            "pins": pins,
            "texts": texts,
            "layers": layers_data,
            "raw_pin_count": len(pins),
            "_source": "skillbridge",
        }

    finally:
        try:
            ws.close()
        except Exception:
            pass
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass