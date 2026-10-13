# -*- coding: utf-8 -*-
"""
services/pdf_service.py —— PDF 操作服务

职责：
  - 在文件夹里找 PDF
  - 把用户给的 PDF 引用（全路径 / stem / 文件名）解析成绝对路径
  - 定位 land pattern 页（复用 job_find_land_pattern_page）
  - 渲染指定页为图片（复用 lib_datasheet_parser._render_page）

注意：
  lib_datasheet_parser._render_page 目前是下划线开头的内部函数，
  但渲染能力是 MCP 层需要的公共能力，暂时直接复用。
  未来建议在 lib_datasheet_parser 里暴露公共的 render_page()。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from services.pdf_locate import find_land_pattern_page
from services.pdf_renderer import _render_page


# ---------- 文件发现 ----------
def find_first_pdf(folder: Path) -> Optional[Path]:
    """
    在文件夹根目录下找第一个 PDF（按文件名排序，排除 ~$ 临时文件）。

    :param folder: 目标文件夹
    :return: PDF 绝对路径，找不到返回 None
    """
    if not folder.is_dir():
        return None
    pdfs = sorted(
        p for p in folder.glob("*.pdf")
        if p.is_file() and not p.name.startswith("~$")
    )
    return pdfs[0] if pdfs else None


# ---------- 路径解析 ----------
def resolve_pdf(pdf_ref: str, search_roots: list[Path]) -> Optional[Path]:
    """
    把用户给的 PDF 引用解析成绝对路径。

    支持三种形式：
      1. 完整路径（如 C:\\a\\b\\xxx.pdf）—— 直接返回
      2. 文件名（如 xxx.pdf）—— 在 search_roots 下递归找
      3. stem（如 xxx）—— 在 search_roots 下递归找 <stem>.pdf

    :param pdf_ref: PDF 引用字符串
    :param search_roots: 搜索根目录列表
    :return: PDF 绝对路径，找不到返回 None
    """
    if not pdf_ref:
        return None

    # 形式 1：直接路径
    p = Path(pdf_ref)
    if p.is_file() and p.suffix.lower() == ".pdf":
        return p.resolve()

    # 形式 2 / 3：在 search_roots 下搜索
    for root in search_roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob(f"{pdf_ref}.pdf"):
            if candidate.is_file():
                return candidate.resolve()
        for candidate in root.rglob(f"{pdf_ref}*.pdf"):
            if candidate.is_file():
                return candidate.resolve()
    return None


# ---------- 定位 ----------
def locate_land_pattern_page(pdf_path: Path) -> dict:
    """
    定位 PDF 中的 land pattern 页。

    ★ 2026-10-09 补：文本方案**失配时给可操作的下一步**，而不是干巴巴返回 None。
      实测 13 份规格书：强命中 4、弱命中 4、no_match 5 —— 失配的 5 份都是
      文本层稀薄的国产件规格书（两 FPC / TF 卡座 / HDMI / LMBR），
      其中 LMBR 是**纯位图扫描件**（文本 0 字符 + 矢量 0 条），任何文本方案都无解。

    :param pdf_path: PDF 绝对路径
    :return: find_land_pattern_page 的原始返回 +（失配时）`hint` / `text_chars`
    """
    res = find_land_pattern_page(pdf_path)
    if res.get("page") is None:
        text_chars = _count_text_chars(pdf_path)
        res["text_chars"] = text_chars
        if text_chars == 0:
            why = "该 PDF 文本层为空（纯扫描件/位图页），关键词匹配必然失败"
        else:
            why = f"PDF 有 {text_chars} 字符文本，但没有命中 land pattern 关键词"
        res["hint"] = (
            f"{why}。→ 改用 `render_pdf_contact_sheet` 生成**缩略图拼版**，"
            f"AI 扫一眼即可定位尺寸页（实测 11 页的扫描件，1 张拼图就够，"
            f"比逐页读整页省 5~6 张图）。"
        )
    return res


def _count_text_chars(pdf_path: Path) -> int:
    """统计整份 PDF 的文本字符数（0 = 纯扫描件；-1 = 读不了）。"""
    try:
        import pymupdf                                  # noqa: PLC0415
        total = 0
        with pymupdf.open(str(pdf_path)) as doc:
            for page in doc:
                total += len(page.get_text() or "")
        return total
    except Exception:                                   # noqa: BLE001
        return -1


# ---------- 渲染 ----------
def render_page(
    pdf_path: Path,
    page: int,
    out_root: Path,
    engine: str = "pymupdf",
) -> dict:
    """
    把 PDF 指定页渲染成图片。

    :param pdf_path: PDF 绝对路径
    :param page: 人眼 1-based 页码
    :param out_root: 输出根目录
    :param engine: "pymupdf"（默认，整页渲染）或 "marker"（版面分析）
    :return: {
        "ok": bool,
        "engine": str,
        "image_path": str | None,
        "md_path": str | None,
        "stderr": str,
        "stdout": str,
    }
    """
    return _render_page(pdf_path, page, out_root, engine=engine)


def get_or_render_image(
    pdf_path: Path,
    page: int,
    out_root: Path,
    engine: str = "pymupdf",
) -> Optional[Path]:
    """
    先检查缓存，命中则直接返回；否则渲染后返回图片路径。

    用于 resource 层频繁读同一页时避免重复渲染。

    :return: 图片绝对路径，渲染失败返回 None
    """
    # 缓存检查（仅 pymupdf 输出路径可预测）
    if engine == "pymupdf":
        cached = out_root / pdf_path.stem / "pymupdf_pages" / f"page_{page}.png"
        if cached.is_file():
            return cached

    res = render_page(pdf_path, page, out_root, engine=engine)
    if res.get("ok") and res.get("image_path"):
        return Path(res["image_path"])
    return None


# ---------- 缩略图拼版（contact sheet） ----------
def build_contact_sheet(
    pdf_path: Path,
    out_root: Path,
    *,
    cols: int = 4,
    thumb_width: int = 210,
    max_pages: Optional[int] = None,
    dpi: int = 72,
) -> dict:
    """
    ★ 把整份 PDF 的所有页拼成**一张缩略图**，供 AI 一次扫完定位关键页。

    为什么需要它：
      `locate_land_pattern_page` 靠文本关键词打分，对**纯扫描件**（文本层 0 字符、
      矢量图形 0 条）必然 no_match —— 而这种规格书恰恰只能靠眼睛看。
      逐页读整页太贵（11 页 = 11 张整页图），拼成一张后**读一次就够**。

    实测（LMBR140ET1G.pdf，11 页纯扫描件，2026-10-09）：
      原：翻 6 张整页 + 1 张放大裁剪
      现：1 张拼图（840×945）即定位到 p3「尺寸表 + Suggested solder pad layout」

    :param pdf_path: PDF 绝对路径
    :param out_root: 输出根目录（落 `<out_root>/<stem>/contact_sheet.png`）
    :param cols: 每行几页（默认 4）
    :param thumb_width: 单页缩略图宽（像素，默认 210 → 整图约 840 宽）
    :param max_pages: 最多拼多少页（None = 全部）
    :param dpi: 缩略图渲染 dpi（72 足够看清版面结构，尺寸数字需再读原页）

    :return: {"ok": bool, "image_path": str|None, "pages": int, "grid": str,
              "page_index": {页码: [x, y, w, h]}（便于 AI 说"看第 3 页"时回读原页）,
              "error": str|None}
    """
    try:
        import pymupdf                                    # noqa: PLC0415
        from PIL import Image, ImageDraw                  # noqa: PLC0415
    except ImportError as e:                              # pragma: no cover
        return {"ok": False, "error": f"缺少依赖: {e}", "image_path": None}

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:                                # noqa: BLE001
        return {"ok": False, "error": f"打不开 PDF: {e}", "image_path": None}

    try:
        n = len(doc)
        if max_pages:
            n = min(n, int(max_pages))
        if n <= 0:
            return {"ok": False, "error": "PDF 没有页", "image_path": None}

        # 先渲染第 1 页定尺寸（A4 竖版比例）
        pm0 = doc[0].get_pixmap(dpi=dpi)
        th = max(1, int(thumb_width * pm0.height / pm0.width))
        rows = (n + cols - 1) // cols
        label_h = 18
        sheet = Image.new("RGB", (cols * thumb_width, rows * (th + label_h)),
                          "white")
        draw = ImageDraw.Draw(sheet)
        page_index: dict = {}

        for i in range(n):
            pm = doc[i].get_pixmap(dpi=dpi)
            im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
            im = im.resize((thumb_width, th), Image.LANCZOS)
            r, c = divmod(i, cols)
            x, y = c * thumb_width, r * (th + label_h)
            sheet.paste(im, (x, y))
            draw.text((x + 4, y + th + 3), f"p{i + 1}", fill="black")
            page_index[str(i + 1)] = [x, y, thumb_width, th]

        out_dir = Path(out_root) / Path(pdf_path).stem
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "contact_sheet.png"
        sheet.save(str(out_path))
    except Exception as e:                                # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "image_path": None}
    finally:
        try:
            doc.close()
        except Exception:                                 # noqa: BLE001
            pass

    return {
        "ok": True,
        "image_path": str(out_path),
        "pages": n,
        "grid": f"{cols} x {rows}",
        "size": [sheet.width, sheet.height],
        "page_index": page_index,
        "note": ("缩略图只够定位「哪一页」，读尺寸数字请用 render_pdf_page "
                 "渲染该页原图（必要时再裁剪放大）"),
        "error": None,
    }


# ---------- ★ 判形态（pdf_probe） ----------
# 形态判定只需三个数：位图数 / 文本字符数 / 矢量图元数。判错读法的代价不对称：
# 对 CAD 图做文本提取、对扫描件提文本层，都是纯白工；反之区域裁剪对任何 PDF 都可用。
def probe_pdf(pdf_path: Path) -> dict:
    """
    探测 PDF 形态，用于**开跑前决定读法**（零成本，只读元数据）。

    :param pdf_path: PDF 绝对路径
    :return: {"ok", "pages", "bitmaps", "text_chars", "drawings",
              "drawings_capped", "form", "read_hint", "error"}

    form 判据（用 17 份真实规格书校准）：
      - scan       位图 ≥1 且文本 0 字符      → 缩略图拼版定位 → 区域裁剪读数
      - printed    文本 ≥200 字符             → 文本层提表 + 区域裁剪读图
      - cad_vector 文本 <200 且矢量图元 ≥200  → 跳过定位，直接区域高 dpi 读数
      - thin_text  其余                        → 关键词定位 → 区域裁剪读数

    ★ form 是**启发式建议**、可能误判；三条读法对任何 PDF 都可用（区域裁剪是通用兜底），
      误判只影响"先试哪条路"，不影响最终结果。
    """
    try:
        import pymupdf                                     # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "PyMuPDF 未安装"}

    try:
        with pymupdf.open(str(pdf_path)) as doc:
            pages = len(doc)
            bitmaps = 0
            text_chars = 0
            for page in doc:
                bitmaps += len(page.get_images(full=True))
                text_chars += len(page.get_text() or "")
            # 矢量图元数只在"文本稀薄"时才需要统计，
            # 避免在 100+ 页的印刷规格书上白跑 get_drawings（很贵）。
            drawings = 0
            drawings_capped = False
            if text_chars < 200:
                for page in doc:
                    drawings += len(page.get_drawings())
                    if drawings >= 20000:                  # 上限保护
                        drawings_capped = True
                        break
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if bitmaps >= 1 and text_chars == 0:
        form = "scan"
        hint = "纯位图/扫描件：先 contact_sheet 定位页，再 render_pdf_region 读数"
    elif text_chars >= 200:
        form = "printed"
        hint = "印刷规格书：可提文本层表格；land pattern 走 render_pdf_region 读数"
    elif drawings >= 200:
        form = "cad_vector"
        hint = ("CAD 矢量出图：尺寸被 plot 成字形轮廓、文本层拿不到；"
                "跳过定位，直接 render_pdf_region 高 dpi 读数")
    else:
        form = "thin_text"
        hint = ("薄文本层：关键词定位可能失配，失配则走 contact_sheet；"
                "读数用 render_pdf_region")

    return {"ok": True, "pages": pages, "bitmaps": bitmaps,
            "text_chars": text_chars, "drawings": drawings,
            "drawings_capped": drawings_capped,
            "form": form, "read_hint": hint, "error": ""}


# ---------- ★ 区域高 dpi 重渲染（render_pdf_region） ----------
# 与"整页缩小拼版"的区别：缩的是**视野**，不是**像素密度**。
# 拼版把 N 页塞一张图、单页稀释到 72 dpi；区域裁剪保住 600–1200 dpi。
# 输出命名空间刻意与 get_or_render_image 的 <stem>/pymupdf_pages/ 分开，避免缓存互相覆盖。
def render_region(
    pdf_path: Path,
    page: int,
    rect: tuple,
    out_root: Path,
    dpi: int = 600,
) -> dict:
    """
    按 PDF 坐标裁一块区域，高 dpi 重渲染成 PNG（★ 读尺寸数字的主力手段）。

    :param pdf_path: PDF 绝对路径
    :param page: 人眼 1-based 页码
    :param rect: (x0, y0, x1, y1) PDF 显示坐标 pt（与 page.rect 同系，已含 rotation）
    :param out_root: 输出根目录（调用方传 config.RENDER_OUTPUT_ROOT）
    :param dpi: 渲染 dpi；常规尺寸 600 起，亚毫米标注/公差带用 900–1200
    :return: {"ok", "image_path", "size": [w, h], "rect", "dpi", "error"}
    """
    try:
        import pymupdf                                     # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "PyMuPDF 未安装"}

    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except Exception:                                      # noqa: BLE001
        return {"ok": False, "error": f"rect 需为 4 个数: {rect!r}"}

    dpi = max(72, min(int(dpi), 1200))                     # ★ 上限保护，防超大图

    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if page < 1 or page > len(doc):
                return {"ok": False, "error": f"页码越界: {page}/{len(doc)}"}
            pg = doc[page - 1]
            clip = pymupdf.Rect(x0, y0, x1, y1) & pg.rect   # 夹到页面内，防越界裁空
            if clip.is_empty:
                return {"ok": False,
                        "error": f"rect 与页面无交集: {rect} vs {pg.rect}"}
            pm = pg.get_pixmap(dpi=dpi, clip=clip)
            out_dir = Path(out_root) / Path(pdf_path).stem / "regions"
            out_dir.mkdir(parents=True, exist_ok=True)
            tag = (f"p{page}_{clip.x0:.0f}-{clip.y0:.0f}-"
                   f"{clip.x1:.0f}-{clip.y1:.0f}@{dpi}")
            out = out_dir / f"{tag}.png"
            pm.save(str(out))
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": True, "image_path": str(out),
            "size": [pm.width, pm.height],
            "rect": [clip.x0, clip.y0, clip.x1, clip.y1],
            "dpi": dpi, "error": ""}


# ---------- ★ 矢量量距（measure_region，档 2 交叉校验） ----------
# 用途：量出区域内图元的包围盒，**与读图结果对拍**（如几何比 ①→②:②→⑤ 是否等于标注比）。
# 图纸等比时几何比值可用，但 **pt/mm 必须自行标定**（标题栏 SCALE 常年不可信）。
# ★ 不作主读数：焊盘与器件本体轮廓在几何上常粘连，直接量距会并成一块。
def measure_region(
    pdf_path: Path,
    page: int,
    rect: tuple,
    max_boxes: int = 200,
) -> dict:
    """
    量取区域内矢量图元的包围盒（PDF pt），供与读图结果交叉校验。

    :param pdf_path: PDF 绝对路径
    :param page: 人眼 1-based 页码
    :param rect: (x0, y0, x1, y1) PDF 显示坐标 pt
    :param max_boxes: 最多返回多少个包围盒（默认 200，防返回爆炸）
    :return: {"ok", "unit", "n_total", "n_in_rect", "boxes", "note", "error"}
    """
    try:
        import pymupdf                                     # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "PyMuPDF 未安装"}

    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except Exception:                                      # noqa: BLE001
        return {"ok": False, "error": f"rect 需为 4 个数: {rect!r}"}

    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if page < 1 or page > len(doc):
                return {"ok": False, "error": f"页码越界: {page}/{len(doc)}"}
            pg = doc[page - 1]
            clip_disp = pymupdf.Rect(x0, y0, x1, y1)
            # ★ 坐标系陷阱（2026-10-13 实测，本样本 rotation=270 / 842x595 vs 595x842）：
            #     page.get_pixmap(clip=...) 吃的是**显示坐标**（与 page.rect 同系）；
            #     page.get_drawings() 返回的却是**未旋转坐标**（与 mediabox 同系）。
            #   旋转页不做这次转换 → 与任何图元都不相交（原型实测 n_in_rect = 0）。
            clip_draw = clip_disp * pg.derotation_matrix
            if clip_draw.is_empty:
                return {"ok": False, "error": f"rect 无效（与页面无交集）: {clip_disp}"}
            all_ds = pg.get_drawings()
            # 用「相交」而非「包含」：图元包围盒常跨出区域，
            # 用 contains 会把它们全部滤掉。
            inside = [d for d in all_ds
                      if not d["rect"].is_empty
                      and d["rect"].intersects(clip_draw)]
            boxes = []
            for d in inside[:max_boxes]:
                # 从「未旋转坐标」转回「显示坐标」，与 rect 参数口径一致
                r = d["rect"] * pg.rotation_matrix
                boxes.append([round(r.x0, 3), round(r.y0, 3),
                              round(r.x1, 3), round(r.y1, 3),
                              round(r.x1 - r.x0, 3), round(r.y1 - r.y0, 3)])
            n_total = len(all_ds)
            n_in_rect = len(inside)
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "unit": "pt",
        "space": "display（与 page.rect / render_pdf_region 的 rect 同系）",
        "n_total": n_total,
        "n_in_rect": n_in_rect,
        "boxes": boxes,
        "note": ("★ 仅供参考、不作主读数：① 焊盘与器件本体轮廓可能粘连，"
                 "直接量距会并成一块；② pt→mm 必须自行标定（标题栏 SCALE 不可信，"
                 "用一条已知标注的两条界线反推）；③ 适合做「几何比值 vs 标注比值」"
                 "的交叉校验。"),
        "error": "",
    }
