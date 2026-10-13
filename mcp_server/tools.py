# -*- coding: utf-8 -*-
"""
mcp_server/tools.py —— MCP 工具定义

约定：
  每个 tool 函数只做三件事：
    1. 参数校验 / 归一化
    2. 调用 services 层
    3. 包装返回

  严禁在 tool 里写业务逻辑（循环、文件扫描、JSON 清洗）。

工具列表（v4.1）：
  - list_subfolders / list_folder_files         批量侦察
  - locate_land_pattern_page / render_pdf_page  定位 & 渲染
  - pdf_probe / render_pdf_region / measure_region  ★ 读规格书三件套（2026-10-13 加）
  - read_full_footprint                         拿 pins/layers 供 AI 语义检查
  - check_footprint_by_rules                    主用：检查 + 存档 + 渲染 markdown
  - validate_land_pattern_json                  校验 AI 输出的 JSON

v4.1 变更：
  - check_footprint_by_rules 新增 compact 参数（默认 True）：
      · compact=True  → 返回 conclusion / summary / markdown / report_path
                        / save_error / render_error，丢掉 items / failed
                        / warned / footprint_data（省 LLM 输入 token）
      · compact=False → 保留完整 items / failed / warned / footprint_data
                        （调试用）
      · 边界：save_report=False 时，compact 自动降级为 False（否则返回无用）
      · 错误分支：数据提取失败时，无论 compact 传什么，都返回完整错误信息
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from mcp_server import config
from services import allegro_service, pdf_service
from services import footprint_extractor, rule_checker, report_renderer
from services import footprint_builder, geometry_solver, library_install
from services import case_library, spec_library
from services.json_utils import coerce_numeric, extract_json_body
from services.validation import validate_land_pattern


# ============================================================
# 模块级辅助：保存 checklist JSON 报告
# ============================================================
def _write_checklist_json(
    result: dict,
    dra_file_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
) -> dict:
    """
    把一次清单检查的结果写成 JSON 文件。

    从 check_footprint_by_rules 内部调用。

    :param result: check_footprint_by_rules 的结果（含 conclusion/summary/items）
    :param dra_file_path: .dra 路径；None 时从 result.footprint_data 推断
    :param output_dir: 输出目录；None 时用 .dra 所在目录
    :param filename: 文件名；None 时带时间戳
    :return: {"ok": bool, "path": str, "error": str}
    """
    if not isinstance(result, dict):
        return {"ok": False, "path": "", "error": "result 不是 dict"}

    # 推断输出目录
    if output_dir is None:
        if dra_file_path is None:
            fp_data = result.get("footprint_data") or {}
            dra_file_path = fp_data.get("source_file")
        if dra_file_path:
            output_dir = Path(dra_file_path).parent
        else:
            return {
                "ok": False,
                "path": "",
                "error": "无法推断输出目录，请显式传 output_dir 或 dra_file_path",
            }

    out_dir = Path(output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "path": str(out_dir), "error": f"创建目录失败: {e}"}

    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 2026-10-12 待修清单 #1：文件名带 symbol_name，防止旧报告张冠李戴
        # （同一目录下多件连续检查时，纯时间戳名容易认错件）
        sym = str(result.get("footprint_data", {}).get("symbol_name") or "")
        sym = re.sub(r"[^0-9A-Za-z_-]+", "_", sym).strip("_")
        stem = f"{sym}_" if sym else ""
        filename = f"checklist_report_{stem}{ts}.json"

    out_path = out_dir / filename

    fp_data = result.get("footprint_data") or {}
    report = {
        "report_version": "3.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dra_file": fp_data.get("source_file"),
        "symbol_name": fp_data.get("symbol_name"),
        "component_type": result.get("component_type"),
        "conclusion": result.get("conclusion"),
        "summary": result.get("summary"),
        "items": result.get("items"),
        "failed": result.get("failed"),
        "warned": result.get("warned"),
    }

    try:
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        return {"ok": False, "path": str(out_path), "error": f"写文件失败: {e}"}

    return {"ok": True, "path": str(out_path.resolve()), "error": ""}


def register(mcp: FastMCP) -> None:
    """把所有 tool 注册到给定的 FastMCP 实例。"""

    # ============================================================
    # Tool 0: 列出父文件夹下的子文件夹（批量模式侦察用）
    # ============================================================
    @mcp.tool()
    def list_subfolders(parent_path: str) -> dict:
        """
        列出父文件夹下的所有子文件夹，供批量模式侦察。

        :param parent_path: 父文件夹路径
        :return: {"parent": str, "subfolders": [str], "count": int}
        """
        p = Path(parent_path)
        subs = allegro_service.list_subfolders(p)
        return {
            "parent": str(p.resolve()),
            "subfolders": subs,
            "count": len(subs),
        }

    # ============================================================
    # Tool 1: 列出文件夹内容
    # ============================================================
    @mcp.tool()
    def list_folder_files(folder_path: str) -> dict:
        """
        列出封装文件夹里的 PDF / .dra / .pad 文件。

        这是 AI 决策的第一步：了解文件夹里有什么，再决定后续怎么处理。

        :param folder_path: 目标文件夹路径
        :return: {
            "folder": str,
            "pdf": str | None,
            "dra_files": [str],
            "pad_files": [str],
            "error": str | None
        }
        """
        return allegro_service.list_folder_files(Path(folder_path))

    # ============================================================
    # Tool 2: 定位 land pattern 页
    # ============================================================
    @mcp.tool()
    def locate_land_pattern_page(pdf_file_path: str) -> dict:
        """
        定位 PDF 规格书中包含 land pattern（推荐焊盘）的页码。

        先用强关键词（LAND PATTERN、RECOMMENDED PAD 等）匹配；
        失败则用弱关键词（PAD、DIMENSION、SOLDER 等）兜底。

        :param pdf_file_path: 规格书 PDF 路径（完整路径或 stem 均可）
        :return: {
            "page": 页码(1-based) 或 null,
            "matched": [命中的关键词],
            "score": 匹配得分,
            "total_pages": 总页数,
            "method": "primary" | "fallback" | "no_match"
        }
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"error": f"找不到 PDF: {pdf_file_path}", "page": None}
        return pdf_service.locate_land_pattern_page(pdf)

    # ============================================================
    # Tool 2.1: 渲染 PDF 页
    # ============================================================
    @mcp.tool()
    def render_pdf_page(
        pdf_file_path: str,
        page: int,
        render_engine: str = "pymupdf",
    ) -> dict:
        """
        把 PDF 指定页渲染成 PNG 图片，返回图片的本地路径。

        用途：当客户端不支持读 MCP resource `datasheet://...` 时，
        AI 可以通过这个 tool 拿到图片路径，再用自己的图片分析能力读图。

        :param pdf_file_path: PDF 路径（完整路径或 stem）
        :param page: 人眼 1-based 页码
        :param render_engine: "pymupdf"（默认）
        :return: {
            "ok": bool,
            "image_path": str,       # 图片的本地绝对路径
            "out_dir": str,
            "engine": str,
            "error": str,
        }
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"ok": False, "error": f"找不到 PDF: {pdf_file_path}"}
        img_path = pdf_service.get_or_render_image(
            pdf_path=pdf,
            page=page,
            out_root=config.RENDER_OUTPUT_ROOT,
            engine=render_engine,
        )
        if img_path is None:
            return {"ok": False, "error": "渲染失败"}
        return {
            "ok": True,
            "image_path": str(img_path),
            "out_dir": str(img_path.parent),
            "engine": render_engine,
            "error": "",
        }

    # ============================================================
    # Tool 3: 全量读取 Allegro 封装（拿 pins/layers 供 AI 语义检查）
    # ============================================================
    @mcp.tool()
    def read_full_footprint(
        dra_file_path: str,
        pad_file_paths: list[str] | None = None,
    ) -> dict:
        """
        从 .dra 全量提取封装数据（不做规则判断）。

        主流程用途：把 pins / layers / symbol_name 交给 AI 做语义检查
        （命名语义、pin number、极性标识），产出 semantic_result 供
        check_footprint_by_rules 使用。

        调试用途：需要看 .dra 原始分层数据时直接调用。

        :param dra_file_path: .dra 文件完整路径
        :param pad_file_paths: 可选的 .pad 文件路径列表
        :return: {
            "source_file": str,
            "symbol_name": str,
            "units": "mm" | "mil",
            "design_bbox": {...},
            "pins": [...],       # 每个 pin 的 number/xy/bbox/pads
            "texts": [...],
            "layers": {...},     # assembly_top / silkscreen_top / place_bound_top 等
            "raw_pin_count": int,
        }
        """
        return footprint_extractor.read_full_footprint(
            dra_path=dra_file_path,
            pad_paths=pad_file_paths or [],
            workspace_id=config.SKILLBRIDGE_WORKSPACE_ID,
        )

    # ============================================================
    # Tool 3b: 跑规则检查 + 存档 + 渲染 markdown（主用）
    # ============================================================
    @mcp.tool()
    def check_footprint_by_rules(
        dra_file_path: str,
        theoretical_payload: dict | None = None,
        pad_file_paths: list[str] | None = None,
        custom_rules: dict | None = None,
        semantic_result: dict | None = None,
        save_report: bool = True,
        compact: bool = True,
    ) -> dict:
        """
        从 .dra 提取封装数据，跑完整的数值规则检查，并自动保存 JSON 报告、
        渲染固定格式的 Markdown 报告。

        覆盖 Excel 规范的 6 大项（数值部分）：
          - 1. 封装命名规范（前缀、小数点、数字）
          - 2. 焊盘尺寸（与命名一致性、阻焊/钢网外扩）
          - 3. 间距与原点（pitch、pitch vs datasheet、最小间距、原点居中）
          - 5. Place_Bound（存在、覆盖、外扩量）
          - 6. Assembly / Silkscreen（存在、1 脚标识、重叠）

        返回里的 `markdown` 字段是最终报告，AI 应**原样贴出**。

        :param dra_file_path: .dra 文件完整路径
        :param theoretical_payload: 可选，datasheet 理论参数（用于 pitch 对比）
        :param pad_file_paths: 可选的 .pad 文件路径列表
        :param custom_rules: 可选，自定义规则（覆盖默认）
        :param semantic_result: AI 产出的语义检查结果，结构：
            {
              "naming": {"type_match": bool, "pin_count_match": bool,
                         "dimension_match": bool, "pitch_match": bool},
              "pin_number": {"dra_pin_count": int, "datasheet_pin_count": int,
                             "count_match": bool, "pin_numbers_match": bool},
              "polarity": {"required": bool, "pin1_marker_assembly": bool,
                           "pin1_marker_silkscreen": bool}
            }
            缺字段或 None 时，渲染层会跳过对应行，不崩。
        :param save_report: 是否保存 JSON + 渲染 markdown；默认 True
        :param compact: 是否精简返回（默认 True）
            - True  → 只返回 conclusion / summary / markdown / report_path
                      / save_error / render_error
            - False → 额外返回 items / failed / warned / footprint_data
            - save_report=False 时，compact 强制为 False（否则返回无意义）
        :return: 见上方字段说明。
        """
        # ---------- 边界：save_report=False 时 compact 无意义，强制降级 ----------
        # 原因：没有 markdown、没有 report_path，再丢掉 items 就只剩空壳
        if not save_report:
            compact = False

        # 1. 提取数据
        fp_data = footprint_extractor.read_full_footprint(
            dra_path=dra_file_path,
            pad_paths=pad_file_paths or [],
            workspace_id=config.SKILLBRIDGE_WORKSPACE_ID,
        )
        if fp_data.get("error"):
            # 数据提取失败：无论 compact 传什么，都返回完整错误信息
            return {
                "conclusion": "FAIL",
                "error": fp_data["error"],
                "footprint_data": fp_data,
                "markdown": "",
                "report_path": "",
                "save_error": "",
                "render_error": "",
            }

        # 2. 跑规则检查
        result = rule_checker.run_numeric_checks(
            footprint_data=fp_data,
            theoretical=theoretical_payload,
            rules=custom_rules,
        )

        # 3. 附上原始提取数据（存档需要它，存档完成后可能被裁掉）
        result["footprint_data"] = fp_data

        # 4. 存档 + 渲染（容错：任一失败不影响检查结果返回）
        markdown = ""
        report_path = ""
        save_error = ""
        render_error = ""

        if save_report:
            # 4a. 保存 JSON 报告
            try:
                save_res = _write_checklist_json(result, dra_file_path=dra_file_path)
                report_path = save_res.get("path", "")
                save_error = save_res.get("error", "")
            except Exception as e:
                save_error = str(e)

            # 4b. 渲染 markdown
            try:
                report_name = Path(report_path).name if report_path else None
                markdown = report_renderer.render_markdown(
                    numeric_result=result,
                    semantic_result=semantic_result,
                    report_filename=report_name,
                )
            except Exception as e:
                render_error = str(e)

        # 5. 组装返回
        if compact:
            # 精简模式：只留必要字段
            return {
                "conclusion": result.get("conclusion"),
                "component_type": result.get("component_type"),
                "summary": result.get("summary"),
                "markdown": markdown,
                "report_path": report_path,
                "save_error": save_error,
                "render_error": render_error,
            }

        # 完整模式：保留全部字段
        result["markdown"] = markdown
        result["report_path"] = report_path
        result["save_error"] = save_error
        result["render_error"] = render_error
        return result

    # ============================================================
    # Tool 4: 校验 AI 输出的 JSON
    # ============================================================
    @mcp.tool()
    def validate_land_pattern_json(raw_json: str) -> dict:
        """
        校验 AI 输出的 land pattern JSON 是否符合预期 schema，并规范化。

        建议在 AI 自己读图提取参数之后、调 check_footprint_by_rules 之前
        先调这个 tool 做守门员，避免格式漂移导致比对失败。

        :param raw_json: AI 输出的原始 JSON 字符串
        :return: {"valid": bool, "errors": [str], "normalized": dict}
        """
        try:
            parsed = extract_json_body(raw_json)
        except Exception as e:
            return {
                "valid": False,
                "errors": [f"JSON 解析失败: {e}"],
                "normalized": None,
            }
        parsed = coerce_numeric(parsed)
        valid, errors, normalized = validate_land_pattern(parsed)
        return {"valid": valid, "errors": errors, "normalized": normalized}

    # ============================================================
    # ============  以下为 Allegro 封装生成工具  ============
    # ============================================================
    #
    # 前置条件（全部）：
    #   1. Allegro PCB Editor 正在运行
    #   2. SkillBridge 服务已启动（端口见 config.SKILLBRIDGE_WORKSPACE_ID，默认 7777）
    #   3. 已经在 Allegro 里打开一个 **Package symbol** 类型的 .dra 图纸
    #      （File -> New -> Drawing Type = Package symbol；不要用 wizard）
    #   4. 该 drawing 的单位已设定（Setup -> Design Parameters -> Design -> User Units）
    #
    # 单位约定：
    #   本层所有工具的参数**统一使用毫米(mm)**。内部会自动按当前 drawing 的
    #   design units 换算。所以调用方不需要关心 drawing 是 mm 制还是 mil 制。
    #
    # 推荐调用顺序：
    #   inspect_allegro_environment  -> 体检
    #   change_drawing_extents       -> 扩范围（避免 SPMHA1-70）
    #   create_padstack              -> 建焊盘
    #   add_footprint_pin            -> 放引脚（可多次）
    #   add_footprint_outline        -> 丝印/Assembly/Place_Bound（可多次）
    #   add_footprint_text           -> 位号（必做，否则 Create Symbol 报 SPMHCS-1）
    #   save_footprint_drawing       -> 存盘
    #
    #   或者直接用 build_footprint_from_params 一次跑完。
    # ============================================================

    # ------------------------------------------------------------
    # Tool 5: 环境体检
    # ------------------------------------------------------------
    @mcp.tool()
    def inspect_allegro_environment() -> dict:
        """
        检查 Allegro/SkillBridge 连通性与当前 drawing 状态。

        **所有生成类工具的第一步都应先调这个**。它会告诉你：
          - 能不能连上 Allegro
          - 当前 drawing 的单位是什么（mm / mil）
          - 当前 drawing 是不是 Package symbol（放 pin 的硬要求）
          - 有哪些警告会影响后续操作

        :return: {
            "success": bool, "connected": bool,
            "units": "mm"|"mil"|"unknown",
            "design_name": str|None,
            "is_package_symbol": bool|None,
            "warnings": [str], "steps": [...], "error": str|None
        }
        """
        return footprint_builder.inspect_environment()

    # ------------------------------------------------------------
    # Tool 6: 调整 drawing 范围
    # ------------------------------------------------------------
    @mcp.tool()
    def change_drawing_extents(
        x1: float = -50.0,
        y1: float = -50.0,
        x2: float = 50.0,
        y2: float = 50.0,
        union_with_current: bool = True,
    ) -> dict:
        """
        设置 drawing 的 extents（有效范围）。

        ★ 放 pin 之前强烈建议先调这个。新建的 .dra 图纸 extents 常常不包含
        原点，导致 axlDBCreatePin 报 SPMHA1-70 (Pin is outside of the extents)。

        ★★ 默认做「并集」（2026-09-29 修正）：Allegro 的
        axlDBChangeDesignExtents **拒绝缩小范围**（新范围比现有内容小就返回 nil）。
        所以本工具默认取「请求范围 ∪ 当前内容范围 + 余量」，
        保证只会扩大、幂等、不会因为 drawing 里已有图元而失败。
        需要精确设定（含收缩）时传 union_with_current=False。

        :param x1: 左下角 X，单位 mm
        :param y1: 左下角 Y，单位 mm
        :param x2: 右上角 X，单位 mm
        :param y2: 右上角 Y，单位 mm
        :param union_with_current: 是否与当前内容范围取并集（默认 True）
        :return: {"success": bool, "unit": str, "extent": [x1,y1,x2,y2],
                  "steps": [...], "error": str|None}
        """
        return footprint_builder.change_extents(
            x1, y1, x2, y2, union_with_current=union_with_current
        )

    # ------------------------------------------------------------
    # Tool 6.5: 把图纸「摆正」——范围 + 视图都以原点为中心
    # ------------------------------------------------------------
    @mcp.tool()
    def frame_drawing_view(
        half: float | None = None,
        margin: float = 2.0,
        extent_scale: float = 1.3,
        save: bool = False,
    ) -> dict:
        """
        把 drawing 的**图纸范围**和**视图窗口**都摆成以原点为中心，
        解决「生成的封装配打开后总缩在画布左下角」。

        ★★★ 根因（2026-09-30 实测）★★★
        `.dra` 会把**上一帧视图窗口**一起存盘。官方 `sym_template.dra`
        自带一个"原点落在左下角"的视图；我们的流程是
        「复制模板 → 打开 → 画几何 → 存盘」，视图原样跟着走 ——
        所以每份产出打开都是左下角。

        两个被实测推翻的直觉：
          ✗ 改 `extents` 不能改视图（只在缩小时夹一下，变大不放宽）
          ✗ `axlZoomCenter` 在视图已含原点时什么都不做

        正确做法 = `axlZoomPoints` 显式设视图 + `axlZoomBbox(nil)` 读回闭环
        （zoomPoints 是底左锚定 + 按窗口宽高比外扩，需按读回的中心偏移
        平移请求矩形再设一次；实测 2~3 次收敛到中心误差 0）。

        ★ 正常流程**不需要手动调** —— `build_footprint_from_params` 的
          `frame_view=True`（默认）会在存盘前自动做一次。

        :param half: 视图半宽 mm；None = 由几何外包框 + margin 自动算
        :param margin: half=None 时留的余量 mm（默认 2.0）
        :param extent_scale: 图纸范围 = 视图半宽 × 该系数（默认 1.3）
        :param save: 设完是否立刻存盘（默认 False）
        :return: {"success", "unit", "half", "extent", "view", "view_center",
                  "iterations", "steps", "warnings", "error"}
        """
        return footprint_builder.frame_drawing(
            half=half, margin=margin, extent_scale=extent_scale, save=save
        )

    # ------------------------------------------------------------
    # Tool 7: 建 padstack
    # ------------------------------------------------------------
    @mcp.tool()
    def create_padstack(
        name: str,
        layers: list[dict],
        drill: dict | None = None,
        save_to_disk: bool = True,
        on_exists: str = "error",
    ) -> dict:
        """
        在当前 drawing 中创建 padstack（并默认落盘为 .pad 文件）。

        注意：Allegro **不会覆盖同名 padstack**，重名时 axlDBCreatePadStack
        会静默失败（返回 nil 且不带错误信息）。本工具因此会先做一次重名
        检测，把含糊失败变成明确提示。策略由 on_exists 控制。

        :param name: padstack 名，如 "R1D4X1D2"。
                     命名惯例：R=矩形, C=圆形, O=椭圆, OB=长圆；
                     数字用 d 代替小数点，如 1.4 -> 1d4。
                     名字里不要带路径或扩展名。
        :param layers: 层定义列表，每项：
                       {
                         "layer":  "TOP",          # TOP/SOLDERMASK_TOP/PASTEMASK_TOP/BOTTOM...
                         "figure": "RECTANGLE",    # RECTANGLE/SQUARE/CIRCLE/OBLONG/OVAL/SHAPE
                         "size":   [1.4, 1.2],     # [宽, 高]，单位 mm
                         "type":   "REGULAR"       # 可选：REGULAR/THERMAL/ANTIPAD
                       }
                       常见组合（SMD 元件）：
                         TOP 1.4x1.2 + SOLDERMASK_TOP 1.5x1.3 + PASTEMASK_TOP 1.4x1.2
                       注意 SQUARE 要求宽高相等，否则会失败。
        :param drill: 可选钻孔（通孔元件才需要）：
                      {"diameter": 0.3, "plated": True}
                      或异形孔：{"size": [0.6, 0.3], "offset": [0, 0], "drill_type": "OBLONG"}
        :param save_to_disk: 建完是否立即落盘成 .pad 文件（默认 True）
        :param on_exists: 同名 padstack 已存在时的策略：
                          "error"（默认）直接返回明确错误；
                          "skip"  跳过重建、按复用处理；
                          "build" 不检测、照常构建（Allegro 侧会失败）。

        :return: {
            "success": bool, "padstack_name": str, "unit": str,
            "pad_file": str|None, "skipped": bool, "steps": [...], "error": str|None
        }
        """
        return footprint_builder.create_padstack(
            name=name,
            layers=layers,
            drill=drill,
            save_to_disk=save_to_disk,
            on_exists=on_exists,
        )

    # ------------------------------------------------------------
    # Tool 8: 放 pin
    # ------------------------------------------------------------
    @mcp.tool()
    def add_footprint_pin(
        padstack_name: str,
        number: str,
        x: float,
        y: float,
        rotation: float | None = None,
        text_offset: list[float] | None = None,
    ) -> dict:
        """
        在当前 drawing 中放置一个 pin（引脚）。

        ★ 硬约束：
          - 只能在 Package symbol / Mechanical symbol 图纸里用
          - pin 必须落在 drawing extents 范围内，否则报 SPMHA1-70
            建议先调 change_drawing_extents
          - padstack 必须已存在（先调 create_padstack）

        :param padstack_name: 已创建的 padstack 名，如 "R1D4X1D2"
        :param number: 引脚编号，字符串，如 "1"（不能含非法字符如 "!"）
        :param x: 引脚中心 X，单位 mm（相对 drawing 原点）
        :param y: 引脚中心 Y，单位 mm
        :param rotation: 可选，旋转角度（度）
        :param text_offset: 可选，编号文字的偏移 [dx, dy]，单位 mm

        :return: {"success": bool, "pin_number": str, "unit": str,
                  "steps": [...], "error": str|None}
        """
        return footprint_builder.add_pin(
            padstack_name=padstack_name,
            number=number,
            x=x,
            y=y,
            rotation=rotation,
            text_offset=text_offset,
        )

    # ------------------------------------------------------------
    # Tool 9: 画轮廓（丝印 / Assembly / Place_Bound）
    # ------------------------------------------------------------
    @mcp.tool()
    def add_footprint_outline(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        target: str = "silkscreen",
        width: float = 0.15,
        filled: bool = False,
    ) -> dict:
        """
        画一个矩形轮廓，用于丝印 / Assembly / Place_Bound。

        :param x1: 左下角 X，单位 mm
        :param y1: 左下角 Y，单位 mm
        :param x2: 右上角 X，单位 mm
        :param y2: 右上角 Y，单位 mm
        :param target: "silkscreen"  -> PACKAGE GEOMETRY/SILKSCREEN_TOP
                       "assembly"    -> PACKAGE GEOMETRY/ASSEMBLY_TOP
                       "place_bound" -> PACKAGE GEOMETRY/PLACE_BOUND_TOP（填充矩形）
        :param width: 线宽，单位 mm（place_bound 忽略此参数）
        :param filled: 仅 place_bound 有效，是否填充

        :return: {"success": bool, "target": str, "layer": str,
                  "created": int, "expected": int, "steps": [...], "error": str|None}
        """
        return footprint_builder.add_outline(
            x1=x1, y1=y1, x2=x2, y2=y2,
            target=target, width=width, filled=filled,
        )

    # ------------------------------------------------------------
    # Tool 10: 加文字（位号等）
    # ------------------------------------------------------------
    @mcp.tool()
    def add_footprint_text(
        text: str,
        x: float,
        y: float,
        target: str = "silkscreen",
        text_block: str = "1",
        rotation: float = 0.0,
    ) -> dict:
        """
        加一段文字。★ 位号（REFDES）是必加项 —— 缺它 File→Create Symbol
        会报 SPMHCS-1 (Symbol is missing a refdes)。

        :param text: 文字内容。位号建议写 "U*" 或 "REF**"（* 会被自动编号）
        :param x: 锚点 X，单位 mm
        :param y: 锚点 Y，单位 mm
        :param target: "silkscreen" | "assembly"
        :param text_block: Allegro text block 名，如 "1"/"2"（见 Setup->Text Sizes）
        :param rotation: 旋转角度（度）

        :return: {"success": bool, "text": str, "layer": str,
                  "steps": [...], "error": str|None}
        """
        return footprint_builder.add_text(
            text=text, x=x, y=y,
            target=target, text_block=text_block, rotation=rotation,
        )

    # ------------------------------------------------------------
    # Tool 11a0: ★ 全自动新建 Package symbol 图纸（模板复制法）
    # ------------------------------------------------------------
    @mcp.tool()
    def new_symbol_drawing(
        target_name: str,
        template_path: str | None = None,
        overwrite: bool = True,
        units: str | None = "millimeters",
        accuracy: int | None = 4,
    ) -> dict:
        """
        ★★★ 全自动新建一张 **Package symbol** 类型图纸（零弹框）★★★

        等价于 `File → New → Drawing Type = Package symbol`，全程无 UI。

        ★ 为什么不用 `axlKillDesign()`：实测它建出的是 **BOARD** 类型，
          而 `axlSetSymbolType` 会拒绝执行、`axlSetDesignType` **函数不存在**
          → AXL 没有"直接建 symbol 图纸"的 API。

        ★ 本工具走【当前版本模板复制】：
            找一张已是 Package symbol 且为 **17.2 当前版本**（按主版本号判，
            不是精确匹配魔数串 —— `all375891` 和官方库的 `allv17-08/...` 同为 17.2）
            的 `.dra` → `shutil.copyfile` 复制成目标名 → `axlOpenDesign` 打开
          → 类型天然是 SYMBOL；当前版本格式，**不会弹版本升级框**。

        ★ 防弹框四处保障：
          1. 复制前 `os.remove` 目标 + `.lck`（否则弹 File Exists. Overwrite?）
          2. 模板**强制校验版本**，老模板直接报错拒绝，绝不冒险弹升级框
          3. 打开时传 `?mode "wf"`
          4. ★ **污染校验**：打开后数 `pins`/`text`/`padstacks`。
             退路模板（工作目录扫到的 `probe_test.dra` 之类探针脏图）非空就拒绝
             —— 否则模板自带的 pin 会跟新建的撞号，最终编译中止。
             官方模板只记 warning 不阻断。

        ★★ 5. **单位/精度归一化**（2026-09-29 加）：
          官方 `sym_template.dra` 是 **mils / 精度 1**（整数 mil = 25.4 µm 步长），
          而金标准 `.dra` 实测是 **millimeters / 精度 0.0001**（0.1 µm 步长）。
          不切单位 → 所有 mm 坐标被量化到整数 mil：
              1.1 mm  → 43 mil = 1.0922 mm   （±7.8 µm 系统偏差）
              0.85 mm → 33 mil = 0.8382 mm
          这是「建出的件与金标准总差 ~0.01 mm」的根因。
          → 打开图纸后、**画任何图元之前**切单位（文档明写会 rerun DRC）。
          ★ 该 API 返回值是 drcCount/nil，**不能当成败判据**（DRC 关闭时恒 nil）
            → 内部一律复读 `axlDBGetDesignUnits()` 校验，失败会进 `warnings`。

        :param target_name: 新图纸名（可含目录，可带/不带 .dra）
            ★ **相对名默认落 `Tools/test/`**（目录约定见 `services/paths.py`）；
              给绝对路径则尊重你指定的位置。
        :param template_path: 模板 .dra 路径；None 时自动挑选（官方模板优先）
        :param overwrite: 目标已存在时是否覆盖
        :param units: 建完切到此单位（默认 `"millimeters"`）；None = 保持模板原单位。
            ★ 默认值就是金标准口径，**一般不要改**。
        :param accuracy: 精度位数（默认 4）；None = 保持原精度。
        :return: {"success": bool, "design": str|None, "dra_path": str|None,
                  "template": str|None, "logs": dict, "steps": [...],
                  "warnings": [...], "error": str|None}
          `steps` 里有一条 `set_units`，其 `value` 形如 `mil -> mm（accuracy=4）`。
        ★ 跑完会**自动**把 .jrl/.log 等运行痕迹归集到 `Tools/log`（`logs` 字段）。
        """
        return footprint_builder.new_symbol_drawing(
            target_name=target_name,
            template_path=template_path,
            overwrite=overwrite,
            units=units,
            accuracy=accuracy,
        )

    # ------------------------------------------------------------
    # Tool 11a: ★ 新建空图纸（axlKillDesign，产出 BOARD 类型）
    # ------------------------------------------------------------
    @mcp.tool()
    def new_footprint_drawing(
        set_units: str | None = None,
        set_accuracy: int | None = None,
        extents_mm: list[float] | None = None,
    ) -> dict:
        """
        ★ 新建一张全新的空图纸（`axlKillDesign()`，零弹窗）。

        ★ 限制：建出的是 **BOARD** 类型。要 **Package symbol** 请用
          `new_symbol_drawing()`。

        适合：建普通 board 图纸，或把当前图纸**重置为空**（清掉污染图元）。

        :param set_units:  可选，建完设单位："millimeters"/"mils"/"inches"
        :param set_accuracy: 可选，建完设精度位数（mm 用 4，mils 用 0~2）；
            ★ 单改精度传 `set_accuracy`、`set_units=None` 即可
              （文档支持 `axlDBChangeDesignUnits(nil 2)` 这种用法）。
        :param extents_mm: 可选，建完设范围 [x1,y1,x2,y2]，单位 mm
        :return: {"success": bool, "design": str|None, "steps": [...], "error": str|None}
          `steps` 里 `set_units` 的 `value` 会带复读结果。
        """
        return footprint_builder.new_drawing(
            set_units=set_units, set_accuracy=set_accuracy,
            extents_mm=extents_mm,
        )

    # ------------------------------------------------------------
    # Tool 11c: ★ 清空图纸图元（解决图元叠加污染）
    # ------------------------------------------------------------
    @mcp.tool()
    def clear_footprint_drawing(
        include_pins: bool = True,
        include_text: bool = True,
        include_geometry: bool = True,
    ) -> dict:
        """
        ★ 清空当前图纸里的图元（保留图纸本体、单位、范围不变）。

        ★ 为什么需要：Allegro 的图元创建是**叠加**不是**替换** ——
          同一张图纸反复生成封装会累积重复 pin，最终 `export_psm` 会报
          `WARNING(SPMHA1-298): duplicate pin numbers` +
          `ERROR(SPMHA1-291): aborted`。

          在生成新封装前先清一遍即可根治。

        分层删除（两层 API 互补）：
          - 几何层内容 → `axlDeleteByLayer`（高效）
          - pin / text  → `axlDeleteObject`（★ axlDeleteByLayer 官方明确不删 pin）
                       且**先 mapcar 拍快照再删**，避免边遍历边改集合

        :param include_pins:    是否删 pin（默认 True）
        :param include_text:    是否删文字（含旧位号，默认 True）
        :param include_geometry: 是否删线/矩形/形状等几何（默认 True）
        :return: {"success": bool, "deleted": [...], "steps": [...], "error": str|None}
        """
        return footprint_builder.clear_drawing(
            include_pins=include_pins,
            include_text=include_text,
            include_geometry=include_geometry,
        )

    # ------------------------------------------------------------
    # Tool 11: 保存 drawing
    # ------------------------------------------------------------
    @mcp.tool()
    def save_footprint_drawing(path: str | None = None) -> dict:
        """
        保存当前 drawing。

        :param path: 可选，另存到指定路径；None 时存回原位
        :return: {"success": bool, "path": str|None, "steps": [...], "error": str|None}
        """
        return footprint_builder.save_drawing(path=path)

    # ------------------------------------------------------------
    # Tool 11b: ★ 出 .psm（编译封装符号）
    # ------------------------------------------------------------
    @mcp.tool()
    def export_psm(
        symbol_name: str | None = None,
        symbol_type: str | None = None,
    ) -> dict:
        """
        ★ 把当前图纸编译成 `.psm` 封装符号（= 界面里的 File → Create Symbol）。

        内部调用 `axlCompileSymbol`。**这是 Allegro 出 .psm 的正路**，
        早期"AXL 出不了 .psm"的结论已被推翻（2026-09-29 实测）。

        ★ 前置条件（Allegro 会做 edit check）：
          - 当前必须是 Package symbol 图纸（symbol editor）
          - 图纸里**必须有位号文字**，否则报 SPMHCS-1
          - 图纸最好已保存过

        ★ 注意：`symbol_name` 传 None 会用当前图纸名；
          **绝不会传 nil** —— 那会弹出「另存为」对话框把通道卡死。

        ★ 产物保护：目标 `.psm` 已存在时必须先挪开（否则弹「File Exists」
          卡死通道），但**不删除** —— 移入项目 `.trash/` 隔离区。
          编译成功 → 隔离件留作备份（`previous_psm_backup`）；
          编译失败 → **自动原样搬回**，不会把上一次的成功产物弄丢。

        ★★★ 「弹框阻断」是不是真问题？2026-09-29 在线实测定论 ★★★
          连续两次调用（含"目标 .psm 已存在"的覆盖场景）耗时 **0.6~0.7 秒、
          全程无对话框**。原因：本 MCP 的会话进/出会自动设/恢复
          `NOCONFIRM` / `noconfirm_savedb` 变量族，叠加"编译前把旧产物移入
          隔离区"，双重保险已经压住了覆盖确认框。
          → 弹框**不是**主要矛盾。历史上真正的阻断源一直是 Edit Check 报错，
            原因全写在 `<symbol>.log` 里 —— 现在由返回值的 `log_diagnosis`
            字段直接翻译成人话 + 处方，不必再猜。

          两条已确认的 Edit Check 硬门槛（任一不满足都会返 nil）：
            ERROR(SPMHCS-1)   -> 缺位号（位号必须在 REF DES/* 层）
            ERROR(SPMHA1-298) -> pin 编号重复

        ★ 产物落点：`create symbol` 把 `.psm` 输出到 Allegro 的**当前工作
          目录**，未必等于 `.dra` 所在目录（实测踩过）。现在有两道保险：
            ① **入口断言**（步骤 `assert_working_dir`）：编译前断言 CWD 等于
               图纸目录，不一致就 `axlShell("cd …")` 纠偏。★ 为什么是前置而
               不是"编译后刷新"—— 后置刷新只能改 CWD，救不回已经写错位置
               的文件，防错位的那一刀必须在编译前落下。
            ② **快照证据**（步骤 `compile_evidence`）：编译前后各拍一次
               `.psm` 快照，落点以"真实变动过的那个文件"为准。
          兜底认返回的 `psm_path`，别按 `.dra` 目录去猜。

        ★★★ 成功判据＝**证据**，不是返回值（2026-09-29 实测定论）★★★
          `axlCompileSymbol` **恒返回 nil**（字符串形式与 SKILL 符号形式
          都是 nil），但编译确实成功、`.psm` 真的落盘。旧代码拿返回值当
          判据，会把成功误判为失败，进而回滚隔离件、**把刚产出的 `.psm`
          用旧备份盖掉**。现在三条证据任一成立即成功，返回值的 `evidence`
          字段会写明本次靠哪条：
            ① 返回值非 nil
            ② 编译前后 `.psm` 快照出现新文件 / mtime 变化
            ③ 日志含 "Create symbol completed" 且无 ERROR

        ★ 跑完会**自动**把编译日志 `<symbol>.log`、`allegro.jrl` 等
          运行痕迹归集到 `Tools/log`（见返回值的 `logs` 字段）。

        :param symbol_name: 编译后的符号名；None 时用当前图纸名
        :param symbol_type: "package" / "mechanical" / "format" /
                            "shape" / "flash"；None 时用当前图纸类型
        :return: {"success": bool, "symbol": str|None, "psm_path": str|None,
                  "psm_exists": bool, "psm_size": int|None,
                  "previous_psm_backup": str|None,
                  "log_diagnosis": {"log_file","entries","summary"},
                  "note": str|None, "logs": dict,
                  "steps": [...], "error": str|None}
        """
        return footprint_builder.export_psm(
            symbol_name=symbol_name, symbol_type=symbol_type
        )

    # ------------------------------------------------------------
    # Tool 11c: ★ 影子库落位（产物归档，纯文件操作）
    # ------------------------------------------------------------
    @mcp.tool()
    def install_shadow_library(
        symbol_name: str | None = None,
        pn: str | None = None,
        artifacts: list[str] | None = None,
        pad_names: list[str] | None = None,
        src_dirs: list[str] | None = None,
        lib_root: str | None = None,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """
        ★ 把产物（`.dra` / `.psm` / `.pad`）归集进**影子库**
          `..\\Data\\footprint\\<PN>\\`（攻坚清单 P2-8 库落位）。

        ★ 现状：`padpath` 全项目**没有任何配置**（ini/env/代码都查过），
          生成出来的封装原本**没有落脚点**，只能躺在 `Tools\\test\\` 当测试件 ——
          下一轮 `new_symbol_drawing` 一扫还可能被当成模板。
          而 `..\\Data\\footprint\\<PN>\\` 早已是事实上的影子库
          （里面就躺着 .dra/.psm/.pad/.log/tolerance_report.json）。

        ★★ 四条硬约束：
          1. **默认绝不覆盖**（`overwrite=False`）：影子库里的件**未必是
             生成物，可能是人工金标准**。实测 `Data/footprint/3S48000163/
             nb_xtal4_3d2x2d5x0d7.dra` 的 md5 与 `Tools/test/
             golden_3S48000163.dra` **完全相同** —— 那正是逐值对拍用的
             参照物。默认覆盖会让"顺手归档"把参照物换成生成件，
             之后所有对拍都变成自己跟自己比，**且毫无提示**。
             → 默认只装新的，已存在的记进 `collisions` 原样不动；
               要替换必须显式 `overwrite=True`（旧件先移入 `.trash/`
               隔离区，校验通过才算替换完成，随时可恢复）。
          2. **ASCII 路径守卫**（P2-9）：目标路径含中文等非 ASCII
             字符 → **直接拒绝**，不"尽力而为"；
          3. **不碰 Allegro 的搜索路径**：只做文件落位，不写 env、
             不改 padpath/psmpath（挂载那步需单独验证 API，本轮不做）；
          4. **拷完逐个校验** size + sha1。

        ★ 本工具**不需要 Allegro 会话** —— 纯文件操作，不占 SkillBridge 通道。
          想先看会装什么、一个字节都不动，用 `dry_run=True`。

        :param symbol_name: 符号名（用于按名找 .dra/.psm）
        :param pn:          影子库里的料号子目录名；None 用 symbol_name
        :param artifacts:   显式文件清单（给了就不按名搜索）
        :param pad_names:   显式 padstack 名；None 时扫源目录的所有 .pad
        :param src_dirs:    产物所在目录；None 时用 Tools/test + Tools
        :param lib_root:    影子库根；None 时用 <Layout>/Data/footprint
        :param overwrite:   **默认 False**：已存在就跳过并记进 `collisions`
        :param dry_run:     True 只报告"会装什么"，一个字节都不动
        :return: {"success": bool, "lib_root": str, "target_dir": str,
                  "installed": [...], "collisions": [...], "skipped": [...],
                  "missing": [...], "auto_collected_pads": [...],
                  "warnings": [...], "error": str|None}
        """
        return library_install.install_to_shadow_library(
            symbol_name=symbol_name, pn=pn, artifacts=artifacts,
            pad_names=pad_names, src_dirs=src_dirs, lib_root=lib_root,
            overwrite=overwrite, dry_run=dry_run,
        )

    @mcp.tool()
    def list_shadow_library(lib_root: str | None = None) -> dict:
        """
        列出影子库里已归档的料号及其产物（只读，便于人工核对）。

        :param lib_root: 影子库根；None 时用 <Layout>/Data/footprint
        :return: {"root": str, "entries": [{"pn","path","files":[...]}],
                  "error": str|None}
        """
        return library_install.list_shadow_library(lib_root=lib_root)

    # ------------------------------------------------------------
    # Tool 11.5: 器件参数 → 成品几何（P0-2 求解器，纯离线）
    # ------------------------------------------------------------
    @mcp.tool()
    def solve_footprint_geometry(
        symbol_base: str,
        pad: dict,
        array: dict,
        body: dict,
        dim_source: str = "drawing",
        dim_sources: dict | None = None,
        dim_tables: dict | None = None,
        dim_tols: dict | None = None,
        pin_count: int | None = None,
        component_type: str | None = None,
        mount: str = "smd",
        pin1_corner: str = "bottom-left",
        body_tol: dict | None = None,
        has_recommended_value: bool = False,
        body_max: dict | None = None,
        use_max_for_geometry: bool | None = None,
        pitch: float | list[float] | None = None,
        suffix: str = "",
        silk_style: str = "auto",
        silk_width: float | None = None,
        silk_min_clearance: float = 0.06,
        refdes_text: str = "#REF",
        refdes_targets: list[str] | str | None = None,
        refdes_place: str = "body_center",
        refdes_dx: float = 0.0,
        refdes_dy: float = 0.0,
        refdes_x: float | None = None,
        refdes_y: float | None = None,
        refdes_justify: str | None = "left",
        symbol_name: str | None = None,
        symbol_name_hint: str | None = None,
        body_center: list[float] | None = None,
        place_bound_expand_mm: float | None = None,
        pins_override: list[dict] | None = None,
        pin_names: list[str] | None = None,
        pin1_mark_axis: str | None = None,
        extra_padstacks: list[dict] | None = None,
        extra_spec: dict | None = None,
    ) -> dict:
        """
        【参数 → 几何】把器件参数算成**可直接执行**的成品几何。

        这是 `build_footprint_from_params` 前面缺的那一环：
        后者是执行器（只认算好的坐标与矩形），本工具是编译器。
        **纯离线**，不连 Allegro、不写文件 —— 结果先给 AI / 人复核，再拿去建。

        ★ 所有数字都按 `Data/checklist/PCB_Footprint建库规范.xlsx` +
          `services/rule_checker.py::DEFAULT_RULES` 推导，
          返回值 `derivations` 里逐条给出「值 / 公式 / 出处」，可审计。

        四条最容易写错的规则（本工具已内置）：
          1. **尺寸取值看「来源」**（用户口径 2026-09-29）：
             图上标注了具体值 → 取标注值本身（`3.2±0.1` 取 3.2，不加偏差）；
             图上没标、需查表 → 取表 MAX（无 MAX 用 TYP + 告警）。
             例：3S48000163 p5 全页尺寸都标在图上 → Assembly 3.2×2.5、
             Place_Bound 3.9×3.2（与人工金标准一致）
          2. **Place_Bound 外扩基准是「器件本体」，不是焊盘阵列**
             （本体 3.2×2.5 + 0.35×2 = 3.9×3.2 ✓；按焊盘算会得 4.4×3.8 ✗）
          3. **丝印 = 本体轮廓「逐边避让裁剪」，不是整框**
             （金标准在线反解：本体 4 条边各挖掉与「焊盘(含阻焊) + 0.20」
               冲突的区间；金标准最终只剩上下两条短横线 —— 左右两条边整条
               压在焊盘上，被完全挖掉。1 脚标识是**三角形**）
          4. **pin 编号**：2×2 方阵为逆时针绕行，起点由 `pin1_corner` 决定；
             左右两列(rows>2, cols=2)走 DIP 走向（左列自下而上、右列自上而下）

        :param symbol_base: 封装类型段，如 "XTAL" / "SOP" / "BGA"
        :param pad:  {"shape":"rect|circle|square|oblong", "size":[1.4,1.2]}  单位 mm
        :param array: {"rows":2,"cols":2,"pitch_x":2.2,"pitch_y":1.7}
                      rows = 沿 y 的个数，cols = 沿 x 的个数
        :param body: {"x":3.2,"y":2.5,"z":0.7}
                     **图上标注的值**（[规范 一] 命名**永远**用中值，不随口径变）

        ★★ **尺寸来源口径**（用户裁决 2026-09-29）—— 决定「画实体」取哪个值：
          1. **图上标注了具体值** → 取**标注值本身**
             （`3.2±0.1` 取 **3.2**，**不再**加上偏差 —— 这是与旧口径
               最大的差别：旧口径「没 recommended 就中值+公差」已废除）
          2. **图上没标、需要查表** → 取表里的 **MAX**
             （表里只有 MIN/TYP 没有 MAX → 取 TYP 并**告警**，要求人工复核）
        命名**不受**影响（永远用中值）；受影响的只有 Assembly / Place_Bound
        等实体几何。

        :param dim_source: 整件默认来源："drawing"（默认，图上标注）/"table"（查表）
        :param dim_sources: 逐维覆盖，如 {"body.x":"table","pad.y":"drawing"}
                           真实的规格书常常是混合的：本体标在图上、焊盘只给表
        :param dim_tables: 逐维的表，如 {"body.x":{"min":3.0,"typ":3.2,"max":3.4}}
                           ★ 逐维**显式**声明 "table" 却没给表 → 直接失败（spec 写错了）
        :param dim_tols: 逐维公差 {"body.x":0.1}，**仅**配合 use_max_for_geometry 用
        :param body_tol: 【遗留】本体公差。新口径下**不再影响取值**，只作记录
        :param has_recommended_value: 【已失效】新口径下「图上有具体值」= 取标注值，
                                      有无 "recommended" 都不再改变结果
        :param body_max: 【遗留】本体上偏差绝对值，仅配合 use_max_for_geometry
        :param pin_count: 交叉校验用；None 时取 rows×cols
        :param component_type: "ic" / "chip" / "connector"；None 时按 symbol_base 自动判定
        :param mount: "smd" / "tht"
        :param pin1_corner: 1 脚位置 "bottom-left"（默认）/ "bottom-right" /
                            "top-right" / "top-left" —— **务必对照规格书 TOP VIEW**
        :param use_max_for_geometry: 【遗留开关】True = 回到 2026-09-29 之前的旧口径
                                     （图上带公差时取**上偏差**）。默认 None/False
        :param pitch: 命名里的 pitch 段（可选，如 0.5 或 [0.5, 1.27]）
        :param suffix: 命名尾段（特殊属性，如 "NP"）
        :param silk_style: "auto"（默认 = 本体轮廓 + 逐边避让裁剪，即金标准形态）/
                           "frame"（强制本体整框，会压焊盘）/ "none"
        :param silk_width: 丝印线宽 mm；None 时取 max(0.10, 0.15)
        :param silk_min_clearance: 兜底包框时的间隙 mm（默认 0.06）
        :param refdes_text: 位号文字，默认 `"#REF"` —— Cadence 的位号占位符。
                            ★ 2026-09-30 用户裁决：`Data\\footprint\\*/` 下
                            **14/14 个人工模板**都用 `#REF`，故默认从 `"U*"` 改成 `"#REF"`
        :param refdes_targets: 位号放哪层，默认 `["refdes_assembly"]`
                            = REF DES/ASSEMBLY_TOP（14/14 模板都在这层）。
                            也可给 `"refdes"`（REF DES/SILKSCREEN_TOP）或两层都放
        :param refdes_place: "body_center"（默认，居中于本体）/
                            "outside"（旧行为：放本体外侧下方，与焊盘不重叠）
        :param refdes_dx: 相对本体中心的 x 偏移（默认 0）。
                          人工件 3S48000163 实测是 **-0.15**
        :param refdes_dy: 相对本体中心的 y 偏移（默认 0）。
                          人工件 3S48000163 实测是 **+0.04**
        :param refdes_x: 绝对 x 坐标覆盖（给了就以它为准，最高优先级；
                         用于逐值复现某件人工金标准）
        :param refdes_y: 绝对 y 坐标覆盖（同上）
        :param refdes_justify: 文字对齐，默认 "left"（模板实测）；None = 不传该参数

        :param symbol_name: 直接指定**成品符号名**（最高优先级），如
                            `"NB_CSTJ421_AJ35_6P"`。不给则按规范自动命名
        :param symbol_name_hint: 自动命名时的**类型段提示**（影响自动名中间那段）

        ---- 下面这组是「连接器 / 异形件」专用，阵列类器件用不到 ----
        ★ 2026-10-09 补齐：此前本工具**不透传**这几个字段，导致连接器类
          （CSTJ 那种：脚位不成阵列、本体中心不在原点、多种 padstack）
          在 MCP 层算不出来，只能退回 CLI。现在全部打通。

        :param body_center: 本体中心在**成品坐标系**里的坐标 [x, y]（不是偏移量）。
                            连接器常见：原点取本体上边缘 → 本体中心必然不在原点，
                            如 `[-0.15, -6.10]`。不给则默认 [0, 0]
        :param place_bound_expand_mm: Place_Bound 相对本体的**单边外扩量** mm。
                            默认按规则推；当焊盘外伸、自动值盖不住时显式给，如 1.60
        :param pins_override: 逐脚显式坐标，绕过阵列推导。
                            `[{"number":"1","x":-4.14,"y":-2.75,
                               "padstack":"R1D42X1D4","pad_size":[1.42,1.40]}, …]`
                            ★ 给了它，`array` 只作 pad_bbox 兜底记录；
                              `pin_count` 必须与实际条数一致，否则报错
        :param pin_names: 各脚用途名（可选，仅记录，不影响几何）
        :param pin1_mark_axis: 1 脚标识（三角）朝向，"x" / "y"，默认 "y"
        :param extra_padstacks: 主 padstack 之外的**额外焊盘定义**列表。
                            多种焊盘共存的件必需（如 CSTJ 的 OB1D2X2D2 /
                            OB1D2X2D0 / R1D42X1D4 三种）。每项格式同
                            `build_footprint_from_params` 的 padstack
        :param extra_spec: ★ 逃生舱。上面没显式列出的 solver spec 字段
                            （如 pin1_mark_center / refdes_text_block / silk_pad_clearance）
                            通通可以塞进这个 dict 原样透传。
                            与显式参数冲突时**显式参数优先**。
                            加了它之后，solver 以后新增字段**不用再改本工具签名**

        :return: {
            "success": bool,
            "symbol_name": str, "padstack": {...}, "pins": [...],
            "silk": [{"kind","lines":[[[x,y],[x,y]],…],"width"}, ...],
            "assembly": [本体矩形, 1脚三角],     # ★ 与金标准 assembly 元素数一致
            "body": rect, "place_bound": rect, "refdes": {...},
            "pad_rects": [...],                  # 每脚焊盘(含阻焊)矩形
            "silk_info": {...}, "pad_bbox": {...},
            "derivations": [{"item","value","formula","source"}, ...],
            "warnings": [str], "error": str|None
        }

        ★ `derivations` 里每个 `dim.<键>` 条目都会写明**这个尺寸是怎么取的**
          （`来源=drawing；图上标注值 3.2` / `来源=table；表 MAX`），可审计。

        ★ 拿到的结果可直接喂 `build_footprint_from_params`
          （用 `geometry_solver.to_build_args(solve(...))` 转一下最省事）：
             padstack / pins / silk / body / place_bound / refdes
          外加 symbol_name（配合 fresh_symbol=True 新建干净图纸）。

        ★ `pad` 支持 `{"rotation": 90}` —— 如果规格书焊盘是宽>高、而实物
          却把 padstack 转了 90° 放（金标准那份就是），用它能复现实物。
        """
        # ★ 逃生舱先落底，后面的显式参数覆盖它（显式优先）
        spec: dict = dict(extra_spec) if extra_spec else {}
        spec.update({
            "symbol_base": symbol_base,
            "pad": pad,
            "array": array,
            "body": body,
            "mount": mount,
            "pin1_corner": pin1_corner,
            "silk_style": silk_style,
            "silk_min_clearance": silk_min_clearance,
            "refdes_text": refdes_text,
            "refdes_place": refdes_place,
            "refdes_dx": refdes_dx,
            "refdes_dy": refdes_dy,
            "suffix": suffix or "",
            "dim_source": (dim_source or "drawing"),
        })
        if refdes_targets:
            spec["refdes_targets"] = refdes_targets
        if refdes_x is not None:
            spec["refdes_x"] = refdes_x
        if refdes_y is not None:
            spec["refdes_y"] = refdes_y
        if refdes_justify:
            spec["refdes_justify"] = refdes_justify
        if dim_sources:
            spec["dim_sources"] = dim_sources
        if dim_tables:
            spec["dim_tables"] = dim_tables
        if dim_tols:
            spec["dim_tols"] = dim_tols
        if pin_count is not None:
            spec["pin_count"] = pin_count
        if component_type:
            spec["component_type"] = component_type
        if body_tol:
            spec["body_tol"] = body_tol
        if has_recommended_value:
            spec["has_recommended_value"] = True
        if body_max:
            spec["body_max"] = body_max
        if use_max_for_geometry is not None:
            spec["use_max_for_geometry"] = bool(use_max_for_geometry)
        if pitch is not None:
            spec["pitch"] = pitch
        if silk_width is not None:
            spec["silk_width"] = silk_width
        if symbol_name:
            spec["symbol_name"] = symbol_name
        if symbol_name_hint:
            spec["symbol_name_hint"] = symbol_name_hint
        # ---- 连接器 / 异形件专用（2026-10-09 补齐）----
        if body_center is not None:
            spec["body_center"] = list(body_center)
        if place_bound_expand_mm is not None:
            spec["place_bound_expand_mm"] = float(place_bound_expand_mm)
        if pins_override:
            spec["pins_override"] = [dict(p) for p in pins_override]
        if pin_names:
            spec["pin_names"] = list(pin_names)
        if pin1_mark_axis is not None:
            spec["pin1_mark_axis"] = pin1_mark_axis
        if extra_padstacks:
            spec["extra_padstacks"] = [dict(e) for e in extra_padstacks]
        return geometry_solver.solve(spec)

    # ------------------------------------------------------------
    # Tool 12: 一次建完整封装（顶层接口）
    # ------------------------------------------------------------
    @mcp.tool()
    def build_footprint_from_params(
        padstack: dict,
        pins: list[dict],
        silk: dict | list[dict] | None = None,
        body: dict | list[dict] | None = None,
        place_bound: dict | list[dict] | None = None,
        refdes: dict | None = None,
        auto_extent: bool = True,
        frame_view: bool = True,
        save: bool = True,
        on_exists: str = "skip",
        fresh_symbol: bool = False,
        symbol_name: str | None = None,
        template_path: str | None = None,
        extra_padstacks: list[dict] | None = None,
        assembly: list[dict] | None = None,
    ) -> dict:
        """
        一次调用建出完整封装：padstack + 所有 pin + 丝印 + 本体 + Place_Bound + 位号。

        这是顶层接口，对标 KiCad MCP 的 create_footprint。
        内部会按顺序调用上面各个单步工具，任何一步失败都会记录到 warnings 里，
        不会静默跳过。

        :param padstack: {
            "name": "R1D4X1D2",
            "layers": [
              {"layer":"TOP",            "figure":"RECTANGLE", "size":[1.4,1.2]},
              {"layer":"SOLDERMASK_TOP", "figure":"RECTANGLE", "size":[1.5,1.3]},
              {"layer":"PASTEMASK_TOP",  "figure":"RECTANGLE", "size":[1.4,1.2]}
            ],
            "drill": null            # 通孔才需要，如 {"diameter":0.3}
          }
        :param pins: [
            {"number":"1","x":-1.1,"y": 0.85},
            {"number":"2","x": 1.1,"y": 0.85},
            {"number":"3","x":-1.1,"y":-0.85},
            {"number":"4","x": 1.1,"y":-0.85}
          ]   单位 mm
        :param silk:  {"x1":-1.7,"y1":-1.35,"x2":1.7,"y2":1.35,"width":0.15}
                      ★ 也接受**列表**（多段轮廓）：「外框 + 1 脚标识」这类
                      拆分形态由 `solve_footprint_geometry` 直接产出。
        :param body:  {"x1":-1.6,"y1":-1.25,"x2":1.6,"y2":1.25,"width":0.1}
                      （同样接受列表）
        :param place_bound: {"x1":-1.95,"y1":-1.6,"x2":1.95,"y2":1.6}
                      （同样接受列表）
        :param assembly: ★★ **推荐**。solver 产出的 Assembly 层图元列表，
                      即 `solve_footprint_geometry` 返回里的 `assembly` 字段
                      （本体矩形 + 1 脚三角标），逐项画到
                      PACKAGE GEOMETRY/ASSEMBLY_TOP。
                      ★ 给了它就代替 `body` —— 直接把 solve 结果里的
                      `assembly` 原样传进来即可，**不要**手工挑 `body` 字段
                      （那会丢掉 1 脚三角标，规则检查第 6.3 项必 FAIL）。
        :param refdes: {"text":"U*","x":0.0,"y":-2.0,"text_block":"1"}
            ★ 可选 `targets`（列表）一次放多层 —— 取值 "refdes"
              （REF DES/SILKSCREEN_TOP，默认）/ "refdes_assembly"
              （REF DES/ASSEMBLY_TOP）。例：
              `{"text":"#REF","x":0,"y":0,"targets":["refdes_assembly"]}`
              —— 人工金标准 `3S48000163` 就只在装配层放一条居中的 `#REF`。
              ★ 位号必须落在 **REF DES 类** 层，放 `PACKAGE GEOMETRY/*`
                Allegro **不认**，编译会报 SPMHCS-1。
        :param auto_extent: 自动按引脚范围扩大 drawing extents（默认 True）
        :param frame_view: ★ 存盘前把「图纸范围 + 视图窗口」摆成以原点为中心
                          （默认 True）—— 否则打开时封装缩在画布**左下角**。
                          `.dra` 会存"上一帧视图"，模板那帧的原点在左下角。
                          置 False 只影响"打开时的取景"，不影响几何。
        :param save: 结束时保存 drawing（默认 True）
        :param on_exists: 同名 padstack 已存在时的策略。默认 "skip" —— 顶层
                          流程常在已有 drawing 上反复迭代，沿用单步工具默认的
                          "error" 会让重跑必然失败。严格场景显式传 "error"。
        :param fresh_symbol: ★★★ **是否先自动新建一张干净的 Package symbol
                          图纸**（默认 False = 在当前图纸上生成）。
                          传 True → 内部用"当前版本模板复制"法新建，**根治
                          图元叠加污染**，全程无弹框。
                          ★ 这是"全自动生成封装"的推荐姿势。
        :param symbol_name: fresh_symbol=True 时的新图纸名（建议用封装名）
        :param template_path: 模板 .dra；None 时自动挑选（官方模板优先）
                              ★ fresh_symbol=True 且名字是相对名时，新图纸默认落 `Tools/test/`

        :return: {
            "success": bool, "padstack_name": str, "padstack_reused": bool,
            "pad_file": str|None, "unit": str,
            "pin_count": int, "created_pins": int,
            "warnings": [str], "logs": dict, "steps": [...], "error": str|None
        }
        """
        return footprint_builder.build_footprint(
            padstack=padstack,
            pins=pins,
            silk=silk,
            body=body,
            place_bound=place_bound,
            refdes=refdes,
            auto_extent=auto_extent,
            frame_view=frame_view,
            save=save,
            on_exists=on_exists,
            fresh_symbol=fresh_symbol,
            symbol_name=symbol_name,
            template_path=template_path,
            extra_padstacks=extra_padstacks,
            assembly=assembly,
        )

    # ============================================================
    # Tool 24: save_spec —— ★ 把「读图提参」的成果存档（下次零读图）
    # ============================================================
    @mcp.tool()
    def save_spec(
        name: str,
        spec_json: str,
        family: str | None = None,
        source_pdf: str | None = None,
        notes: str | None = None,
        overwrite: bool = True,
    ) -> dict:
        """
        ★ 把一份 spec（读规格书解析出的参数 JSON）存进 spec 库 `knowledge/specs/`。

        建封装成功后**顺手调一次**：下次遇到同封装的不同料号（如 LMBR120/140/160
        同为 SOD-323HE）直接 `load_spec` 复用，一次图都不用读；要返工重建时也
        不用手工重拼 JSON。

        :param name: 符号名，建议用封装名（如 `NB_LMBR140ET1G_2P`）
        :param spec_json: ★ spec 的 **JSON 字符串**（不是 dict，与
                          validate_land_pattern_json 同一约定）。
                          就是喂给 `solve_footprint_geometry` 的那套字段
                          （pad / array / body / mount / pins_override /
                          extra_padstacks / place_bound_expand_mm ...）
        :param family: 封装族（如 `SOD-323HE` / `SOT-23` / `FPC-0.5`），用于同族查找
        :param source_pdf: 来源规格书路径，便于回查
        :param notes: ★★ 最值钱的字段：提取方法论 + **踩过的坑**
                      （例："F=1.90 是两 pad 中心距不是总长；B/2+C+D/2 交叉验证"）
                      下次同族遇到同样陷阱，读这一句就不用再推理一遍
        :param overwrite: 同名已存在时是否覆盖（默认 True）

        :return: {"ok": bool, "name", "file", "index_entry", "error"}
        """
        try:
            spec = json.loads(spec_json)
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"spec_json 不是合法 JSON: {e}"}
        if not isinstance(spec, dict):
            return {"ok": False, "error": "spec_json 解析后必须是 object（dict）"}
        return spec_library.save_spec(
            name, spec, family=family, source_pdf=source_pdf,
            notes=notes, overwrite=overwrite)

    # ============================================================
    # Tool 25: load_spec —— ★ 零读图复用
    # ============================================================
    @mcp.tool()
    def load_spec(name: str) -> dict:
        """
        从 spec 库取回一份 spec（**直接能喂 `solve_footprint_geometry` 的字段**）。

        拿它之后通常这么用：
            load_spec("NB_LMBR140ET1G_2P")
              → 改 symbol_base / symbol_name（新料号）
              → solve_footprint_geometry(...) 或 build_footprint_from_params(...)

        :param name: 符号名（大小写不敏感）。不确定全名时先用 `find_spec` 搜。
        :return: {"found": bool, "name", "spec": {...}}；
                 未找到时附 `"hint"` 列出库里已有的名字，避免盲猜。
        """
        spec = spec_library.load_spec(name)
        if spec is None:
            return {"found": False, "name": name,
                    "hint": [e.get("name") for e in spec_library.list_specs()]}
        return {"found": True, "name": name, "spec": spec}

    # ============================================================
    # Tool 26: find_spec —— 按「几何指纹」找同封装 / 同族
    # ============================================================
    @mcp.tool()
    def find_spec(
        mount: str | None = None,
        pin_count: int | None = None,
        pitch_x: float | None = None,
        pitch_y: float | None = None,
        body_x: float | None = None,
        body_y: float | None = None,
        pad_x: float | None = None,
        pad_y: float | None = None,
        tol: float = 0.05,
        keyword: str | None = None,
    ) -> dict:
        """
        找库里有没有「同封装 / 同族」的 spec —— **判断能不能少读图**。

        两种用法（可同时给）：
          A. 给几何字段 → 按**指纹**匹配：
             (mount, pin_count, pitch_x, pitch_y, body_x, body_y, pad_x, pad_y)
             · 八项全等（容差内）      → `exact`：**同封装**，直接 load_spec 复用
             · mount+pad+body 同、差脚数/pitch → `family`：同族，只读差异
          B. 给 `keyword` → 按名字 / 族 / 来源 PDF 子串搜（人工导航用）

        ★ 典型场景：新料号先量几个数（pitch / body / pad）来查一次，
          `exact` 命中就**完全不用读规格书**。

        :param tol: 数值比较容差（mm，默认 0.05）
        :return: {"exact": [...], "family": [...], "keyword": [...],
                  "query": {...}, "total": 库内总数}
        """
        return spec_library.find_spec(
            mount=mount, pin_count=pin_count, pitch_x=pitch_x, pitch_y=pitch_y,
            body_x=body_x, body_y=body_y, pad_x=pad_x, pad_y=pad_y,
            tol=tol, keyword=keyword)

    # ============================================================
    # Tool 27: list_specs —— 库里有什么
    # ============================================================
    @mcp.tool()
    def list_specs() -> dict:
        """
        列出 spec 库里已存档的全部器件（名字 / 族 / 来源 PDF / 指纹 / 存档时间）。

        建新封装**之前**看一眼，能省掉重复读图。

        :return: {"count": int, "specs": [{name, family, source_pdf,
                  fingerprint, saved_at, file}]}
        """
        items = spec_library.list_specs()
        return {"count": len(items), "specs": items,
                "dir": spec_library.SPEC_DIR}

    # ============================================================
    # Tool 29~31: 案例库 —— 检测裁定沉淀 + 同族预警
    # ============================================================
    @mcp.tool()
    def save_case(
        name: str,
        tag: str | None = None,
        family: str | None = None,
        source_pdf: str | None = None,
        spec_json: str | None = None,
        check_summary_json: str | None = None,
        report_path: str | None = None,
        findings_json: str | None = None,
        lessons: list[str] | None = None,
        verdict: str | None = None,
        overwrite: bool = True,
    ) -> dict:
        """
        ★ 把一次检测的**人工裁定**存进案例库 `knowledge/cases/`。

        检查完成、和 layout 工程师确认完「哪条是真问题、哪条是误报」之后
        **顺手调一次**：同族件下次检测前 `find_case` 自动召回本案例，
        裁定一次、全族受益。

        :param name: 封装名（如 `NB_WSON8_8P`）
        :param tag: 可选标签，区分同一封装的多条案例（如 `散热盘pitch裁定`）
        :param spec_json: 该件的 spec JSON 字符串（有则自动算几何指纹）
        :param check_summary_json: 检查报告 summary 的 JSON 字符串
                                   （{"total","pass","fail","warn","na"}）
        :param report_path: 报告 JSON 路径，便于回查全文
        :param findings_json: ★ 人工裁定列表 JSON 字符串，每条
                              {"rule_id","issue","verdict","reason"}，
                              verdict ∈ "false_positive"/"true_positive"/"needs_human"
        :param lessons: ★ 教训/预警列表（最值钱），如
                        "中心有散热盘时 pitch 判据必须剔除散热盘再算"
        :param verdict: 整案结论 "pass"/"pass_with_notes"/"fail"
        :param overwrite: 同名已存在时是否覆盖（默认 True）
        :return: {"ok": bool, "name", "file", "error"}
        """
        try:
            spec = json.loads(spec_json) if spec_json else None
            summary = json.loads(check_summary_json) if check_summary_json else None
            findings = json.loads(findings_json) if findings_json else None
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"JSON 参数解析失败: {e}"}
        return case_library.save_case(
            name, tag=tag, family=family, source_pdf=source_pdf,
            spec=spec if isinstance(spec, dict) else None,
            check_summary=summary if isinstance(summary, dict) else None,
            report_path=report_path,
            findings=findings if isinstance(findings, list) else None,
            lessons=lessons, verdict=verdict, overwrite=overwrite)

    @mcp.tool()
    def find_case(
        spec_json: str | None = None,
        keyword: str | None = None,
        tol: float = 0.05,
        include_payload: bool = False,
    ) -> dict:
        """
        ★ 查案例库：该封装 / 同族件有哪些已知坑与人工裁定 —— **检查前先查**。

        用法与 `find_spec` 同源（同一套几何指纹）：
            检测开始时把目标件的 spec（或指纹字段）传进来
            → exact = 同封装案例；family = 同族案例
            → 把召回案例里的 lessons / findings 作为预警上下文，
              解读报告时先对照历史裁定，避免把已知误报再报一遍。

        :param spec_json: 目标件的 spec JSON 字符串（与 keyword 至少给一个）
        :param keyword: 名字/族/来源 PDF 子串搜（人工导航用）
        :param tol: 数值比较容差（mm，默认 0.05）
        :param include_payload: True 时附案例全文
        :return: {"exact": [...], "family": [...], "keyword": [...],
                  "query": {指纹}, "total": int}
        """
        try:
            spec = json.loads(spec_json) if spec_json else None
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"spec_json 不是合法 JSON: {e}"}
        return case_library.find_case(
            spec=spec if isinstance(spec, dict) else None,
            keyword=keyword, tol=tol, include_payload=include_payload)

    @mcp.tool()
    def list_cases() -> dict:
        """
        列出案例库全部案例（名字 / 标签 / 族 / 来源 PDF / 结论 / 教训数 / 存档时间）。

        检测前浏览一眼，了解库里有哪类经验。
        :return: {"count": int, "cases": [...], "dir": 案例库路径}
        """
        items = case_library.list_cases()
        return {"count": len(items), "cases": items,
                "dir": case_library.CASE_DIR}

    # ============================================================
    # Tool 28: render_pdf_contact_sheet —— ★ 扫描件定位尺寸页的兜底
    # ============================================================
    @mcp.tool()
    def render_pdf_contact_sheet(
        pdf_file_path: str,
        cols: int = 4,
        thumb_width: int = 210,
        max_pages: int | None = None,
    ) -> dict:
        """
        ★ 把整份规格书拼成**一张缩略图**，一次扫完定位「哪一页是尺寸/焊盘图」。

        **什么时候用它**：`locate_land_pattern_page` 返回 `page: null`、
        尤其是带 `hint` 说「文本层为空」时（纯扫描件，关键词匹配无解）。

        ★ 为什么这么干：这类 PDF 逐页读整页太贵（11 页 = 11 张大图）。
          拼成一张后**读一次**即可定位，再单独渲染目标页读数字。
          实测（LMBR140ET1G.pdf，11 页纯扫描件）：1 张 840×945 拼图就定位到 p3。

        :param pdf_file_path: 规格书 PDF 路径（完整路径或 stem 均可）
        :param cols: 每行几页（默认 4）
        :param thumb_width: 单页缩略图宽（像素，默认 210；字看不清就调 300+）
        :param max_pages: 最多拼多少页（None = 全部）

        :return: {"ok": bool, "image_path": str, "pages": int, "grid": str,
                  "page_index": {"3": [x, y, w, h]}, "note": str}
                 拿到页码后调 `render_pdf_page(page=N)` 读尺寸数字。
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"ok": False, "error": f"找不到 PDF: {pdf_file_path}"}
        return pdf_service.build_contact_sheet(
            pdf, config.RENDER_OUTPUT_ROOT, cols=cols,
            thumb_width=thumb_width, max_pages=max_pages)

    # ============================================================
    # Tool 32: pdf_probe —— ★ 开跑前判形态（零成本）
    # ============================================================
    @mcp.tool()
    def pdf_probe(pdf_file_path: str) -> dict:
        """
        ★ 读规格书的第一步：判这份 PDF 是哪种形态，决定用哪条读法（零成本，只读元数据）。

        **为什么先做这一步**：判错读法的代价不对称 ——
          对 CAD 矢量图做文本提取、对扫描件提文本层，都是纯白工；
          按形态选对读法，能整条省掉无效路径。

        :param pdf_file_path: 规格书 PDF 路径（完整路径或 stem 均可）
        :return: {"ok", "pages", "bitmaps", "text_chars", "drawings",
                  "form": "scan"|"printed"|"cad_vector"|"thin_text",
                  "read_hint": str, "error"}
                 form 是启发式建议、可能误判；三条读法对任何 PDF 都可用
                 （区域裁剪是通用兜底），误判只影响"先试哪条路"。
        ★ 本工具失败时返回 `ok:false`，调用方应**回退旧流程**，不要中断整个检查。
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"ok": False, "error": f"找不到 PDF: {pdf_file_path}"}
        return pdf_service.probe_pdf(pdf)

    # ============================================================
    # Tool 33: render_pdf_region —— ★ 区域高 dpi 重渲染（读尺寸数字主力）
    # ============================================================
    @mcp.tool()
    def render_pdf_region(
        pdf_file_path: str,
        page: int,
        rect: list,
        dpi: int = 600,
    ) -> dict:
        """
        ★ 按 PDF 坐标裁一块区域、高 dpi 重渲染 —— **读尺寸数字的主力手段**。

        **为什么需要它**：`render_pdf_page` 只能整页渲染（固定 150 dpi），
          小号尺寸数字在低分辨率下会读错（实测 200 dpi 把 `0.5` 读成 `0.7`）。
          本工具缩的是**视野**而非像素密度，可保住 600–1200 dpi。

        :param pdf_file_path: 规格书 PDF 路径（完整路径或 stem 均可）
        :param page: 人眼 1-based 页码
        :param rect: [x0, y0, x1, y1] PDF 显示坐标 pt（与 page.rect 同系，已含 rotation）
        :param dpi: 渲染 dpi（默认 600；亚毫米标注/公差带用 900–1200；上限 1200）
        :return: {"ok", "image_path", "size": [w, h], "rect", "dpi", "error"}
                 拿到 image_path 后用多模态能力读数字。
        ★ 本工具失败时返回 `ok:false`，调用方回退旧流程。
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"ok": False, "error": f"找不到 PDF: {pdf_file_path}"}
        return pdf_service.render_region(
            pdf, page, rect, config.RENDER_OUTPUT_ROOT, dpi=dpi)

    # ============================================================
    # Tool 34: measure_region —— ★ 矢量量距（交叉校验，非主读数）
    # ============================================================
    @mcp.tool()
    def measure_region(
        pdf_file_path: str,
        page: int,
        rect: list,
    ) -> dict:
        """
        ★ 量取区域内矢量图元的包围盒（PDF pt），用于**与读图结果交叉校验**。

        典型用法：把几何量出的比值（如 ①→②:②→⑤ = 1.770）与图上标注的比值
        （2.80:4.95 = 1.768）对拍，吻合则读数可信。

        ★ 不作主读数（焊盘与器件本体轮廓常粘连，直接量距会并成一块）；
          pt→mm 必须自行标定（标题栏 SCALE 不可信）。

        :param pdf_file_path: 规格书 PDF 路径（完整路径或 stem 均可）
        :param page: 人眼 1-based 页码
        :param rect: [x0, y0, x1, y1] PDF 显示坐标 pt
        :return: {"ok", "unit", "n_total", "n_in_rect", "boxes", "note", "error"}
        ★ 本工具失败时返回 `ok:false`，调用方回退旧流程。
        """
        pdf = pdf_service.resolve_pdf(pdf_file_path, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return {"ok": False, "error": f"找不到 PDF: {pdf_file_path}"}
        return pdf_service.measure_region(pdf, page, rect)