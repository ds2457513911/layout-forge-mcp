# -*- coding: utf-8 -*-
"""
services/extracta_reader.py —— 通过 extracta.exe 读取 .dra 封装数据

extracta 是 Cadence 自带的数据库导出工具，输出为 ! 分隔的 ASCII 文本。
本模块负责调用 extracta 并把输出解析成与 read_full_footprint 兼容的 dict，
让上层 rule_checker 无感切换数据源。

设计要点：
  1. 每次调用创建一个临时目录，放视图文件和输出文件，不污染项目
  2. 环境变量 CDSROOT / PATH 必须注入（否则 extracta 会 0xC0000409 崩溃）
  3. **退出码不可信**——extracta 每次收尾都崩（0xC0000409），
     但输出文件完整；所以判成败要看输出文件
  4. 输出结构必须与 SkillBridge 版完全一致：
     pins[].number / xy / bbox / pads[]（pads 里 layer / size / bbox）
     layers.assembly_top / silkscreen_top / place_bound_top 等
     design_bbox / units / symbol_name
  5. ★ 四路视图（2026-09-29 加）：pins / pads / **PACKAGE GEOMETRY** /
     **REF DES**。位号必须单独一路 —— `CLASS = "..."` 是**过滤器**，
     几何视图里的过滤条件会把 REF DES 层的 `#REF` 挡在外面，而且
     **不报错**（静默缺口）。详见 footprint_views.txt 的注释。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.cdsroot_locator import find_cdsroot, extracta_path


# ============================================================
# pad 层名映射：extracta 层名 → SkillBridge 风格层名
# 目的：让 rule_checker 里写死的 "ETCH/TOP" / "PIN/SOLDERMASK_TOP" 等能匹配
# ============================================================
_PAD_LAYER_MAP = {
    "TOP": "ETCH/TOP",
    "BOTTOM": "ETCH/BOTTOM",
    "~TSM": "PIN/SOLDERMASK_TOP",
    "~BSM": "PIN/SOLDERMASK_BOTTOM",
    "~TPM": "PIN/PASTEMASK_TOP",
    "~BPM": "PIN/PASTEMASK_BOTTOM",
    "~TFM": "PIN/FILMMASK_TOP",
    "~BFM": "PIN/FILMMASK_BOTTOM",
    "~DRILL": "DRILL",
}


# ============================================================
# 层名映射：extracta SUBCLASS → 输出 dict 的 key
# ============================================================
_LAYER_KEY_MAP = {
    "ASSEMBLY_TOP": "assembly_top",
    "ASSEMBLY_BOTTOM": "assembly_bottom",
    "SILKSCREEN_TOP": "silkscreen_top",
    "SILKSCREEN_BOTTOM": "silkscreen_bottom",
    "PLACE_BOUND_TOP": "place_bound_top",
    "PLACE_BOUND_BOTTOM": "place_bound_bottom",
}


# ============================================================
# 工具函数
# ============================================================
def _to_float(v: Any, default=None) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _round(v, n=4):
    if v is None:
        return None
    return round(v, n)


def _parse_units(raw: str) -> str:
    """extracta 输出 'millimeters' / 'mils' → 统一为 'mm' / 'mil'"""
    s = (raw or "").lower()
    if "millimeter" in s or "millimetre" in s:
        return "mm"
    if "mils" in s or "mil" in s:
        return "mil"
    return "mm"


def _read_extracta_output(path: Path) -> List[List[str]]:
    """
    读取 extracta 输出文件，返回 S! 行的字段列表（按 ! 切分）。

    格式：
      A!field1!field2!...   列名
      J!file!date!...       文件全局信息
      S!val1!val2!...       数据行
    字段以 ! 分隔，每条记录结尾也有一个 !，所以 split 后最后一项是空串。
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    rows: List[List[str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if not line.startswith("S!"):
            continue
        # "S!a!b!c!" → ['S', 'a', 'b', 'c', '']，去掉首尾
        parts = line.split("!")
        # parts[0] = "S"，parts[1:] 是字段值，最后一项是空串
        values = parts[1:]
        # 去掉结尾空串（如果有）
        while values and values[-1] == "":
            values.pop()
        rows.append(values)
    return rows


def _read_extracta_header(path: Path) -> Dict[str, Any]:
    """
    读取 A!（列名）和 J!（全局信息），返回：
      {
        "columns": [列名, ...],
        "file_path": str,
        "extent": [x1, y1, x2, y2],
        "units_raw": str,
        "units": "mm" | "mil",
      }
    """
    info: Dict[str, Any] = {
        "columns": [],
        "file_path": "",
        "extent": None,
        "units_raw": "",
        "units": "mm",
    }
    if not path.is_file():
        return info
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return info

    for line in lines:
        line = line.strip()
        if line.startswith("A!"):
            info["columns"] = [x for x in line[2:].split("!") if x]
        elif line.startswith("J!"):
            parts = line[2:].split("!")
            # J! 字段位置（固定）：
            #   [0] 文件路径
            #   [1] 日期
            #   [2] extent X1
            #   [3] extent Y1
            #   [4] extent X2
            #   [5] extent Y2
            #   [6] 精度
            #   [7] 单位
            #   ...
            if len(parts) >= 8:
                info["file_path"] = parts[0]
                info["extent"] = [
                    _to_float(parts[2]), _to_float(parts[3]),
                    _to_float(parts[4]), _to_float(parts[5]),
                ]
                info["units_raw"] = parts[7]
                info["units"] = _parse_units(parts[7])
    return info


# ============================================================
# 解析 pins.txt
# ============================================================
def _parse_pins_file(path: Path) -> List[Dict[str, Any]]:
    """
    解析 pins.txt：

    A!PIN_NUMBER!SYM_NAME!PAD_STACK_NAME!PIN_X!PIN_Y!PIN_ROTATION!
    S!4!!R1D4X1D2!-1.1000!0.8500!90.000!

    返回：[{"number": "4", "pad_stack_name": "R1D4X1D2", "xy": [-1.1, 0.85],
            "rotation": 90.0}, ...]
    （rotation 兼容旧视图：列缺失时为 None）
    """
    rows = _read_extracta_output(path)
    pins: List[Dict[str, Any]] = []
    for r in rows:
        if len(r) < 5:
            continue
        number = r[0]
        pad_stack_name = r[2]
        x = _to_float(r[3])
        y = _to_float(r[4])
        rotation = _to_float(r[5]) if len(r) > 5 else None
        pins.append({
            "number": number,
            "pad_stack_name": pad_stack_name,
            "xy": [x, y] if (x is not None and y is not None) else None,
            "rotation": rotation,
        })
    return pins


# ============================================================
# 解析 pads.txt
# ============================================================
def _parse_pads_file(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """
    解析 pads.txt，按 PAD_NAME 分组成：
      {
        "R1D4X1D2": [
            {"layer": "ETCH/TOP", "figure_name": "RECTANGLE",
             "size": [1.2, 1.4], "offset": [0, 0]},
            {"layer": "PIN/SOLDERMASK_TOP", ...},
            ...
        ],
        ...
      }

    A!PAD_NAME!REC_NUMBER!LAYER!FIXFLAG!VIAFLAG!PADSHAPE1!PADWIDTH!PADHGHT!PADXOFF!PADYOFF!...
    S!R1D4X1D2!00001!TOP!f!v!RECTANGLE!1.2000!1.4000!0.0000!0.0000!!!!...
    """
    rows = _read_extracta_output(path)
    pads_by_name: Dict[str, List[Dict[str, Any]]] = {}

    for r in rows:
        if len(r) < 10:
            continue
        pad_name = r[0]
        raw_layer = r[2]
        shape = r[5] if len(r) > 5 else ""
        w = _to_float(r[6]) if len(r) > 6 else None
        h = _to_float(r[7]) if len(r) > 7 else None
        xoff = _to_float(r[8], 0.0) if len(r) > 8 else 0.0
        yoff = _to_float(r[9], 0.0) if len(r) > 9 else 0.0

        # 层名映射
        layer = _PAD_LAYER_MAP.get(raw_layer, raw_layer)

        # ---- DRILL 行：有效性锚点 = drillDiameter/figureSize 任一 > 0 ----
        # ★ 不能套用普通行的"双尺寸>0"过滤（2026-10-13 收割回归实锤）：
        #   NPTH 圆孔（NPC*/_NP 族，语料 4 件：NPC3/NPC6D5/PC2D5HC5D5_NP/
        #   NPC3D3）figureSize 可全零，只有 drillDiameter 有值——按普通行
        #   过滤会连孔径一起丢，mount 误判 smd、plated 断供。
        if raw_layer == "~DRILL":
            dia = _to_float(r[5], 0.0)
            fw = _to_float(r[6], 0.0) if len(r) > 6 else 0.0
            fh = _to_float(r[7], 0.0) if len(r) > 7 else 0.0
            if dia <= 0 and fw <= 0 and fh <= 0:
                continue          # 真空 drill 行（SMD padstack 的占位行）
            # DRILL 行字段与焊盘行不同：
            #   r[5]=drillDiameter 字符串（非图形名）、r[10]=drill figure、
            #   r[12]=drillChar、r[19]=holeType（"OVAL SLOT" / "CIRCLE_DRILL"…）。
            #   entry["size"] 在 DRILL 行语义是 figureSize [W, H]（可为 0）。
            # ★ holeType 是 2026-10-13 工程师否决 LYDPJ 的第 4 个静默差异：
            #   OBLONG_DRILL 是非法 holeType，被静默回落成 CIRCLE_DRILL
            #   （Editor 显示 Thru Pin），必须 OVAL_SLOT 才是槽孔。
            entry = {
                "layer": layer,
                "figure_name": r[10] if len(r) > 10 else "",
                "size": [fw, fh],
                "offset": [
                    _to_float(r[8], 0.0) if len(r) > 8 else 0.0,
                    _to_float(r[9], 0.0) if len(r) > 9 else 0.0,
                ],
            }
            entry["hole_figure"] = entry["figure_name"]
            entry["hole_type"] = r[19] if len(r) > 19 else ""
            entry["drill_char"] = r[12] if len(r) > 12 else ""
            # ★ drillDiameter 单独透传（2026-10-13）：此前只留在局部 shape 变量里
            #   被 figure_name 覆盖丢弃，miner 复用本解析时拿不到孔径。
            entry["drill_diameter"] = dia
            # ★ plated（P/N）透传（2026-10-13）：DRILL 行该列在 r[11]
            #   （PADSHAPENAME 位置被挪用），旧 miner 内联解析读的就是它；
            #   不透传则 miner 切到本解析后 NP/金属化判定断供。
            entry["plated"] = r[11] if len(r) > 11 else ""
            pads_by_name.setdefault(pad_name, []).append(entry)
            continue

        # 跳过无尺寸定义（如 internal_pad_def / 空的 BOTTOM）
        if w is None or h is None or w <= 0 or h <= 0:
            continue

        entry = {
            "layer": layer,
            "figure_name": shape,
            "size": [w, h],
            "offset": [xoff or 0.0, yoff or 0.0],
        }
        # ★ PADFLASH / PADSHAPENAME 透传（2026-10-13 建模裁定）：
        #   PADFLASH 非 0 = flash 型焊盘（全语料 0 行，透传备查）；
        #   PADSHAPENAME 非 0 = SHAPE 型焊盘（figure 名为 'SHAPE' 的自绘
        #   轮廓，语料 106 行，如 JOPEN25X30_S-L-P）——轮廓几何在 shape
        #   symbol 里 extracta 不给顶点，shape_name 是唯一可辨识特征，
        #   检查规则靠它把"自定义轮廓焊盘"与标准 figure 焊盘区分开。
        pad_flash = r[10] if len(r) > 10 else ""
        shape_name = r[11] if len(r) > 11 else ""
        if pad_flash.strip():
            entry["pad_flash"] = pad_flash.strip()
        if shape_name.strip():
            entry["shape_name"] = shape_name.strip()
        pads_by_name.setdefault(pad_name, []).append(entry)

    return pads_by_name


# ============================================================
# 解析 geom.txt（PACKAGE GEOMETRY 全层）
# ============================================================
def _parse_geom_file(path: Path, collect_layers: bool = True) -> Dict[str, Any]:
    """
    解析 geom.txt，返回：
      {
        "layers": { "assembly_top": [element, ...], ... },
        "texts": [{"text": "1", "layer": "...", "xy": [...], ...}, ...]
      }

    每行 S! 记录的字段位置：
      [0] CLASS           "PACKAGE GEOMETRY"
      [1] SUBCLASS        "ASSEMBLY_TOP"
      [2] RECORD_TAG      "17 1"
      [3] GRAPHIC_DATA_NAME  "LINE" / "RECTANGLE" / "ARC" / "TEXT"
      [4] GRAPHIC_DATA_NUMBER  257 / 259 / 256 / 260
      [5..] GD1..GD10

    RECORD_TAG 形如 "17 1"，前一个数字是元素序号（同一元素的多段共享），
    后一个数字是段序号。

    :param collect_layers: False 时**只取 texts**、不建 layers。
        ★ 专给 REF DES 那一路用 —— 它的 SUBCLASS 也叫 ASSEMBLY_TOP，
        若建 layers 会与几何视图的 assembly_top 撞车（虽然 REF DES 层
        当前只放文字、撞不进 grouped，但不依赖这个巧合更稳）。
    """
    rows = _read_extracta_output(path)

    # 按 (subclass, element_id) 分组元素
    # group_key = (subclass, record_tag 的第一个数字)
    grouped: Dict[tuple, Dict[str, Any]] = {}

    texts: List[Dict[str, Any]] = []
    extent_all = []  # 收集所有坐标，用于 design_bbox

    for r in rows:
        if len(r) < 6:
            continue
        cls = r[0]
        subclass = r[1]
        record_tag = r[2]
        gd_name = r[3]
        gd_number = r[4]
        # GD1..GD10 从 r[5] 开始
        gd = r[5:]

        def _gd(i: int):
            """取 GD 第 i 个（1-based）"""
            idx = i - 1
            return _to_float(gd[idx]) if idx < len(gd) else None

        # 解析 record_tag 得到元素 id
        parts = record_tag.split()
        elem_id = parts[0] if parts else record_tag
        seg_id = parts[1] if len(parts) > 1 else "1"

        group_key = (subclass, elem_id)

        if gd_name == "TEXT":
            # TEXT: GD1,GD2 = 插入点；GD3 = 旋转角；GD4 = 镜像；GD5 = 对齐；GD7 = 内容
            text_content = gd[6] if len(gd) > 6 else ""
            xy = [_gd(1), _gd(2)]
            if xy[0] is not None:
                extent_all.append((xy[0], xy[1]))
            texts.append({
                "text": text_content,
                "layer": f"{cls}/{subclass}",
                "cls": cls,            # ★ 用于区分 REF DES 位号 / PIN_NUMBER
                "subclass": subclass,
                "xy": xy,
                "bbox": None,  # 无 bbox，插入点定位
                "rotation": _gd(3),
                "justify": (gd[4] if len(gd) > 4 else ""),
                "text_block": (gd[5] if len(gd) > 5 else ""),
            })
            continue

        # 图形元素：LINE / RECTANGLE / ARC
        if not collect_layers:
            continue
        el = grouped.setdefault(group_key, {
            "obj_type": gd_name.lower(),
            "layer": f"{cls}/{subclass}",
            "subclass": subclass,
            "segments": [],
            "n_segs": 0,
            "is_rect": gd_number == "259",
        })

        if gd_name == "LINE":
            # GD1,GD2 = 起点；GD3,GD4 = 终点；GD5 = 线宽
            x1, y1, x2, y2 = _gd(1), _gd(2), _gd(3), _gd(4)
            if None not in (x1, y1, x2, y2):
                el["segments"].append([[x1, y1], [x2, y2]])
                extent_all.extend([(x1, y1), (x2, y2)])

        elif gd_name == "RECTANGLE":
            # GD1,GD2 = 左下；GD3,GD4 = 右上；GD5 = 填充
            x1, y1, x2, y2 = _gd(1), _gd(2), _gd(3), _gd(4)
            if None not in (x1, y1, x2, y2):
                # 展开成 4 段，与 SkillBridge 的 n_segs==4 语义对齐
                el["segments"] = [
                    [[x1, y1], [x2, y1]],
                    [[x2, y1], [x2, y2]],
                    [[x2, y2], [x1, y2]],
                    [[x1, y2], [x1, y1]],
                ]
                extent_all.extend([(x1, y1), (x2, y2)])

        elif gd_name == "ARC":
            # GD1,GD2 = 起点；GD3,GD4 = 终点；GD5,GD6 = 圆心；GD7 = 半径
            x1, y1, x2, y2 = _gd(1), _gd(2), _gd(3), _gd(4)
            if None not in (x1, y1, x2, y2):
                el["segments"].append([[x1, y1], [x2, y2]])
                extent_all.extend([(x1, y1), (x2, y2)])

    # ---- 补全每个元素的 bbox / size / center / n_segs ----
    for el in grouped.values():
        el["n_segs"] = len(el["segments"])
        el["bbox"] = _segments_bbox(el["segments"])
        el["size"] = _bbox_size(el["bbox"])
        el["center"] = _bbox_center(el["bbox"])

    # ---- 按 SUBCLASS 分组 ----
    layers: Dict[str, List[Dict[str, Any]]] = {}
    for el in grouped.values():
        key = _LAYER_KEY_MAP.get(el["subclass"])
        if key is None:
            continue
        layers.setdefault(key, []).append(el)

    return {
        "layers": layers,
        "texts": texts,
        "extent_all": extent_all,
    }


def _segments_bbox(segments: List[List[List[float]]]):
    """从 segments 计算合并 bbox"""
    pts = []
    for seg in segments:
        if len(seg) >= 2:
            pts.append(seg[0])
            pts.append(seg[1])
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [[min(xs), min(ys)], [max(xs), max(ys)]]


def _bbox_size(bbox):
    if not bbox:
        return None
    return [abs(bbox[1][0] - bbox[0][0]), abs(bbox[1][1] - bbox[0][1])]


def _bbox_center(bbox):
    if not bbox:
        return None
    return [(bbox[0][0] + bbox[1][0]) / 2, (bbox[0][1] + bbox[1][1]) / 2]


def _merge_bboxes(bboxes):
    valid = [b for b in bboxes if b]
    if not valid:
        return None
    return [
        [min(b[0][0] for b in valid), min(b[0][1] for b in valid)],
        [max(b[1][0] for b in valid), max(b[1][1] for b in valid)],
    ]


# ============================================================
# 主入口
# ============================================================
# ============================================================
# 单位归一（2026-10-12 金标准对拍实锤：mil 图纸几何全是 mil 原值）
# ============================================================
MIL_PER_MM = 1.0 / 25.4

def _mil2mm(v):
    return v * MIL_PER_MM if isinstance(v, (int, float)) else v


def _normalize_result_units(result: Dict[str, Any], units: str) -> Dict[str, Any]:
    """
    ★ mil 图纸 → 全部几何换算成 mm（与 SkillBridge 路径的 value_mm 口径对齐）。

    背景：项目约定「对外统一 mm」，skillbridge_reader 一直做换算，
    extracta 路径此前漏了 —— 旧图纸（设计单位 mil）跑规则检查时
    焊盘尺寸/间距/外扩全部大 25.4 倍，产生成片假 FAIL（2026-10-12
    金标准 253 件对拍实锤：2.1×72、5.2×123 等大半由它引起）。

    转换对象：pin xy/bbox/size、pad bbox/size、layers 元素 segments/
    bbox/size/center、texts xy、design_bbox。转换后 units='mm'
    （units_raw 保留原始值供追溯）。
    """
    if units != "mil":
        return result

    for p in result.get("pins") or []:
        for k in ("xy", "relxy"):
            if p.get(k):
                p[k] = [_mil2mm(p[k][0]), _mil2mm(p[k][1])]
        if p.get("bbox"):
            p["bbox"] = [[_mil2mm(x), _mil2mm(y)] for x, y in p["bbox"]]
        if p.get("size"):
            p["size"] = [_mil2mm(p["size"][0]), _mil2mm(p["size"][1])]
        for pd in p.get("pads") or []:
            if pd.get("bbox"):
                pd["bbox"] = [[_mil2mm(x), _mil2mm(y)] for x, y in pd["bbox"]]
            if pd.get("size"):
                pd["size"] = [_mil2mm(pd["size"][0]), _mil2mm(pd["size"][1])]

    for els in (result.get("layers") or {}).values():
        for el in els:
            el["segments"] = [
                [[_mil2mm(x), _mil2mm(y)] for x, y in seg]
                for seg in el.get("segments") or []
            ]
            if el.get("bbox"):
                el["bbox"] = [[_mil2mm(x), _mil2mm(y)] for x, y in el["bbox"]]
            if el.get("size"):
                el["size"] = [_mil2mm(el["size"][0]), _mil2mm(el["size"][1])]
            if el.get("center"):
                el["center"] = [_mil2mm(el["center"][0]), _mil2mm(el["center"][1])]

    for t in result.get("texts") or []:
        if t.get("xy") and t["xy"][0] is not None:
            t["xy"] = [_mil2mm(t["xy"][0]), _mil2mm(t["xy"][1])]

    db = result.get("design_bbox")
    if db and db.get("value"):
        db["value"] = [[_mil2mm(x), _mil2mm(y)] for x, y in db["value"]]
        db["value_mm"] = db["value"]
        db["unit"] = "mm"
        db["size_mm"] = _bbox_size(db["value"])
        db["center_mm"] = _bbox_center(db["value"])

    result["units"] = "mm"
    return result


def read_via_extracta(dra_path: str) -> Dict[str, Any]:
    """
    用 extracta 读取 .dra，返回与 read_full_footprint 相同结构的 dict。

    失败时返回 {"error": "..."}，让上层决定是否回退 SkillBridge。
    """
    # ★ 必须先 resolve：extracta 的 cwd 已改为专用临时目录（2026-10-13），
    #   相对路径会在临时目录里找不到 .dra → 输出为空（实锤过一次）。
    dra = Path(dra_path).resolve()
    if not dra.is_file():
        return {"error": f".dra 文件不存在: {dra_path}"}

    cdsroot = find_cdsroot()
    if cdsroot is None:
        return {"error": "未找到 Cadence CDSROOT（extracta 不可用）"}

    exe = extracta_path(cdsroot)
    if not exe.is_file():
        return {"error": f"extracta.exe 不存在: {exe}"}

    # ---- 视图文件路径 ----
    view_file = Path(__file__).resolve().parent / "extracta_views" / "footprint_views.txt"
    if not view_file.is_file():
        return {"error": f"视图定义文件不存在: {view_file}"}

    # ---- 临时目录 ----
    tmp_dir = Path(tempfile.mkdtemp(prefix="extracta_"))
    try:
        out_pins = tmp_dir / "pins.txt"
        out_pads = tmp_dir / "pads.txt"
        out_geom = tmp_dir / "geom.txt"
        out_refdes = tmp_dir / "refdes.txt"   # ★ 第 4 路：REF DES 位号

        # ---- 准备环境变量 ----
        env = os.environ.copy()
        env["CDSROOT"] = str(cdsroot)
        tools_bin = str(cdsroot / "tools" / "bin")
        tools_pcb_bin = str(cdsroot / "tools" / "pcb" / "bin")
        env["PATH"] = f"{tools_bin};{tools_pcb_bin};" + env.get("PATH", "")

        # ---- 调用 extracta ----
        # 注意：exitcode 恒为 0xC0000409（-1073740791），不能用来判成败
        # ★ 输出文件按**视图出现顺序**一一对应；视图文件里现在有 4 个视图。
        try:
            subprocess.run(
                [str(exe), str(dra), str(view_file),
                 str(out_pins), str(out_pads), str(out_geom), str(out_refdes)],
                env=env,
                # ★ 2026-10-13：必须显式设 cwd——extracta 会在**进程工作目录**
                #   落 extract.log（+轮转 ,1/,2/,3）。不设则继承 MCP 服务进程
                #   的 cwd = 项目根，污染仓库根目录（2026-10-12 实锤 4 份）。
                #   落进本次专用临时目录，随目录一起清理。
                cwd=str(tmp_dir),
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return {"error": "extracta 执行超时（120s）"}
        except Exception as e:
            return {"error": f"extracta 执行失败: {e}"}

        # ---- 校验输出 ----
        # ★ extracta 偶发抽风（进程本身收尾必崩 0xC0000409，输出偶尔为空），
        #   2026-10-13 LYDPJ 对拍实锤空输出一次。空则重试一次再判失败。
        if not _read_extracta_output(out_pins):
            subprocess.run(
                [str(exe), str(dra), str(view_file),
                 str(out_pins), str(out_pads), str(out_geom), str(out_refdes)],
                env=env,
                cwd=str(tmp_dir),
                capture_output=True,
                timeout=120,
            )
        if not _read_extracta_output(out_pins):
            return {"error": "extracta pins.txt 输出为空（重试后仍为空；可能视图文件语法错误或 .dra 无效）"}

        # ---- 解析 ----
        pins_raw = _parse_pins_file(out_pins)
        pads_by_name = _parse_pads_file(out_pads)
        geom_data = _parse_geom_file(out_geom)
        header = _read_extracta_header(out_pins)

        # ★ 第 4 路（REF DES 位号）：整段容错 —— 它是"锦上添花"，
        #   读不到绝不能影响几何主流程（无位号的图纸本就输出 0 行）。
        refdes_texts: List[Dict[str, Any]] = []
        refdes_error: Optional[str] = None
        try:
            refdes_texts = _parse_geom_file(
                out_refdes, collect_layers=False)["texts"]
        except Exception as e:                      # noqa: BLE001
            refdes_error = f"{type(e).__name__}: {e}"

        # ---- 组装 pins ----
        pins_out: List[Dict[str, Any]] = []
        for p in pins_raw:
            pad_name = p.get("pad_stack_name", "")
            xy = p.get("xy")
            pad_defs = pads_by_name.get(pad_name, [])

            # 每个 pin 附上 pad 层数据（绝对 bbox）
            # ★ 旋转处理（2026-10-12 第 3 层实机对拍实锤）：padstack 的
            #   w×h 是未旋转定义，pin 旋转 90°/270° 时实体宽高互换。
            #   不处理则侧边 pin bbox 宽高反 90°，3.3 出成片伪重叠
            #   （qfn24：真实无重叠，伪影判 0.216mm²×10 对）。
            rot = p.get("rotation")
            swapped = rot is not None and abs((rot % 180) - 90) < 1e-6
            pads_attached = []
            etch_size = None
            max_size = [0.0, 0.0]
            for pd in pad_defs:
                size = pd.get("size") or [0, 0]
                if swapped:
                    size = [size[1], size[0]]
                off = pd.get("offset") or [0, 0]
                if xy:
                    center = [xy[0] + off[0], xy[1] + off[1]]
                else:
                    center = off
                pad_bbox = [
                    [center[0] - size[0] / 2, center[1] - size[1] / 2],
                    [center[0] + size[0] / 2, center[1] + size[1] / 2],
                ]
                pads_attached.append({
                    "layer": pd["layer"],
                    "figure_name": pd.get("figure_name", ""),
                    "bbox": pad_bbox,
                    "size": size,
                    # DRILL 行透传 holeType（"OVAL SLOT"/"CIRCLE_DRILL"…），
                    # 2.7c 校验 oblong 槽孔必须 OVAL SLOT（2026-10-13）
                    **({"hole_type": pd["hole_type"]}
                       if pd.get("hole_type") else {}),
                    # SHAPE 型焊盘的轮廓定义名（2026-10-13 建模）：
                    # 0.2 显名规则与人工复核定位依赖
                    **({"shape_name": pd["shape_name"]}
                       if pd.get("shape_name") else {}),
                })
                # ★ pin bbox 用**铜箔（ETCH/TOP）**尺寸 —— 阻焊/钢网比铜箔大，
                #   拿它们推 bbox 会把焊盘虚胖，3.3 重叠检查成片假 FAIL
                #   （2026-10-12 金标准对拍实锤：msop10/qfn13 等）
                if pd["layer"] == "ETCH/TOP" and size[0] > 0 and size[1] > 0:
                    etch_size = [size[0], size[1]]
                if size[0] > max_size[0]:
                    max_size[0] = size[0]
                if size[1] > max_size[1]:
                    max_size[1] = size[1]

            # pin bbox：优先 etch（铜箔），无 etch 时退回最大 pad 尺寸
            bbox_size = etch_size or (
                [max_size[0], max_size[1]]
                if (max_size[0] > 0 or max_size[1] > 0) else None
            )
            if xy and bbox_size:
                pin_bbox = [
                    [xy[0] - bbox_size[0] / 2, xy[1] - bbox_size[1] / 2],
                    [xy[0] + bbox_size[0] / 2, xy[1] + bbox_size[1] / 2],
                ]
            else:
                pin_bbox = None

            pins_out.append({
                "number": p["number"],
                "name": pad_name,
                "xy": xy,
                "relxy": xy,  # extracta 只给绝对坐标，relxy 用同一值兜底
                "rotation": rot,
                "rel_rotation": None,
                "bbox": pin_bbox,
                "size": bbox_size if pin_bbox else None,
                "pads": pads_attached,
            })

        # ---- 组装 layers / texts ----
        layers = geom_data["layers"]
        # 合并两路文字：几何视图（PIN_NUMBER 等）+ REF DES 视图（位号 #REF）
        texts: List[Dict[str, Any]] = list(geom_data["texts"])
        _seen = {(t.get("text"), t.get("layer"), tuple(t.get("xy") or []))
                 for t in texts}
        for t in refdes_texts:
            key = (t.get("text"), t.get("layer"), tuple(t.get("xy") or []))
            if key not in _seen:
                _seen.add(key)
                texts.append(t)

        # 位号汇总：CLS 为 REF DES 的文字（正常只有一条，且内容应为 #REF）
        ref_des = [t for t in texts
                   if str(t.get("cls", "")).strip().upper() == "REF DES"]

        # ---- 缺口清单：extracta 明明能读、这次却没读到的东西 ----
        #   ★ 这些**不**进 _is_extracta_result_complete 的判据（那套只管几何，
        #     缺几何才值得回退到 SkillBridge）；这里只如实上报，不静默。
        gaps: List[str] = []
        if not ref_des:
            why = "（无位号：图纸确实没放 REF DES 文字）"
            if refdes_error:
                why = f"（第 4 路解析出错：{refdes_error}）"
            gaps.append(f"ref_des_text 未读到 {why}")

        # 补全默认空层，确保 rule_checker 能 .get() 到
        for key in ("silkscreen_top", "silkscreen_bottom",
                    "assembly_top", "assembly_bottom",
                    "place_bound_top", "place_bound_bottom"):
            layers.setdefault(key, [])

        # ---- design_bbox：优先用 extracta 的绘图区范围 ----
        # 与 SkillBridge 的 design.b_box 语义一致（都是整个绘图区），
        # 内容 bbox 仅作为兜底（extent 缺失时）。
        design_bbox_raw = None
        e = header.get("extent")
        if e and None not in e:
            design_bbox_raw = [[e[0], e[1]], [e[2], e[3]]]
        else:
            all_bboxes = []
            for p in pins_out:
                if p.get("bbox"):
                    all_bboxes.append(p["bbox"])
            for els in layers.values():
                for el in els:
                    if el.get("bbox"):
                        all_bboxes.append(el["bbox"])
            design_bbox_raw = _merge_bboxes(all_bboxes)

        design_bbox = None
        if design_bbox_raw is not None:
            size_mm = _bbox_size(design_bbox_raw)
            center_mm = _bbox_center(design_bbox_raw)
            design_bbox = {
                "value": design_bbox_raw,
                "unit": header.get("units", "mm"),
                "value_mm": design_bbox_raw,
                "size_mm": size_mm,
                "center_mm": center_mm,
            }

        # ---- 最终返回 ----
        symbol_name = dra.stem
        result = {
            "source_file": str(dra.resolve()),
            "symbol_name": symbol_name,
            "units": header.get("units", "mm"),
            "units_raw": header.get("units_raw"),
            "design_bbox": design_bbox,
            "pins": pins_out,
            "texts": texts,
            "ref_des": ref_des,     # ★ REF DES 位号文字（正常= [{text:"#REF", ...}]）
            "layers": layers,
            "raw_pin_count": len(pins_out),
            "_source": "extracta",  # 标记数据来源，便于调试
            "_gaps": gaps,          # ★ 静默缺口的显式清单（空列表 = 无缺口）
        }
        # ★ mil 图纸统一归一成 mm（见 _normalize_result_units 的背景说明）
        return _normalize_result_units(result, header.get("units", "mm"))

    finally:
        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass