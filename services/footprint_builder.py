# -*- coding: utf-8 -*-
"""
services/footprint_builder.py —— 封装构建编排层

职责：
  把"用户/AI 给的参数"变成"一串 SKILL 调用"，并收集每一步的结果。

分层原因：
  - skill_snippets.py  只负责**生成 SKILL 表达式**（纯函数，可无 Allegro 单测）
  - skillbridge_writer 只负责**执行**（连接、发命令、取结果）
  - 本模块负责**编排**（顺序、依赖、单位换算、结果归一化）

核心设计原则（借鉴 KiCad MCP 的 create_footprint 接口形态）：
  一次调用给出完整参数 -> 一次调用返回完整结果。
  中间过程用 steps 列表逐条报告，失败不会静默。

单位约定：
  **本模块对外统一用 mm**。内部根据当前 drawing 的 design units 自动换算。
  这样调用方（AI）永远不需要关心 drawing 是 mm 制还是 mil 制。
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from services import paths as P
from services import skill_snippets as S
from services.skillbridge_writer import (
    AllegroSession,
    SkillCallError,
    to_unit,
)

logger = logging.getLogger("layout-forge")


# ============================================================
# 结果构造
# ============================================================
def _result(ok: bool, **kw) -> Dict[str, Any]:
    return {"success": ok, **kw}


def _step_entry(
    name: str,
    expr: str,
    ok: bool,
    value: Any = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """单步执行记录。"""
    entry = {
        "step": name,
        "ok": ok,
        "expr": expr,
    }
    if value is not None:
        entry["value"] = _safe_repr(value)
    if error:
        entry["error"] = error
    return entry


def _safe_repr(v: Any) -> str:
    """把 Allegro 返回的对象转成可读字符串（截断，避免日志爆炸）。"""
    try:
        s = repr(v)
    except Exception:
        s = f"<unrepr {type(v).__name__}>"
    return s if len(s) <= 300 else s[:300] + "..."


def _is_errset_failure(value: Any) -> Optional[str]:
    """
    判断一个 errset 包装调用的返回值是否代表失败。

    Allegro 的 errset 语义：
        成功 -> 返回 (结果)         即单元素列表
        失败 -> 返回 (nil 错误信息)  即两元素列表且首元素为 nil
    SkillBridge 把它转成 Python 的 list/tuple。

    :return: 失败时返回错误信息字符串；成功返回 None
    """
    if value is None:
        # errset 本身被外层的错误吞掉了
        return "调用返回 nil（可能被 Allegro 拒绝）"

    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return "返回空列表"
        first = value[0]
        if first is None:
            msg = value[1] if len(value) > 1 else "未知错误"
            return f"errset 捕获错误: {msg}"
        return None  # 有值 = 成功

    # 非列表：直接当作成功返回值（部分 API 直接返回 dbid/t）
    return None


def _is_creation_ok(raw: Any) -> bool:
    """
    判断 axlDBCreateLine / axlDBCreateRectangle 这类图元创建是否成功。

    ★★★ 返回值形态（2026-09-29 实测 + 文档核实）★★★

    文档签名：
        axlDBCreateLine( l_points [f_width] [t_layer] [t_netname] )
            ⇒ l_result / nil
        axlDBCreateRectangle( l_bBox [g_fill] [t_layer] [t_netname] )
            ⇒ l_result / nil

    实测返回（经 skillbridge 翻译）：
        axlDBCreateLine    -> [[<remote object>], None]     即 ((dbid) drc_flag)
        axlDBCreateRectangle -> [<remote object>, None]     即 (dbid nil)（有 DRC 时第二项非 nil）

    所以**不能**简单地 `if raw:` —— 那个判断在结构变了以后就静默失效。
    这里按"结构里能否找到对象引用"判定：

        [[obj], None]  -> True
        [obj, None]    -> True
        [None]         -> False   （创建失败）
        None           -> False

    ★ 也接受裸 dbid/remote（老版本或直接返回对象的情况）。
    """
    if raw is None:
        return False
    if isinstance(raw, str):
        # 字符串形态：'None' / 'nil' 视为失败
        return raw.strip().lower() not in ("", "none", "nil")
    if isinstance(raw, (list, tuple)):
        if len(raw) == 0:
            return False
        # l_result 里第一项是结果容器；容器里的无 None 元素才算成功
        head = raw[0]
        if head is None:
            return False
        if isinstance(head, (list, tuple)):
            return any(x is not None for x in head)
        return True
    # 裸对象（dbid / remote object / 数字等）
    return True


def _snapshot_psms(
    dirs: Sequence[Optional[str]],
) -> Dict[str, Tuple[float, int]]:
    """
    给候选目录下所有 `.psm` 拍快照：`{规范化路径: (mtime, size)}`。

    ★ 存在的意义（2026-09-29，`export_psm` 的核心修复）★★★
        `axlCompileSymbol` **恒返回 nil** —— 实测字符串形式
        （`axlCompileSymbol(?symbol "NAME" ?type "package")`）与 SKILL 符号形式
        （`?symbol 'NAME`）**都是 nil**，但编译**其实成功**、
        `<name>.psm` **确实落盘**（8840 B）。
        若拿返回值当成功判据，就会把一次成功的编译误判为失败，
        进而触发 `_rollback_quarantine()` —— **把刚产出的 `.psm` 用旧备份盖掉**。

        → 改成：编译前后各拍一次快照，出现过**新的 / mtime 变了的** `.psm`
          即为成功。文件系统的 mtime 是 Allegro 骗不了证据。

    mtime 用 `st_mtime`（float，Windows 上 NTFS 精度 100ns）——足以区分
    "同一次编译内被重写"。
    """
    snap: Dict[str, Tuple[float, int]] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for nm in names:
            if not nm.lower().endswith(".psm"):
                continue
            p = os.path.join(d, nm).replace("\\", "/")
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[p] = (st.st_mtime, st.st_size)
    return snap


def _creation_failure_hint(raw: Any = None) -> str:
    """
    图元创建失败时给出的排查提示。

    ★ 血泪教训（2026-09-29）：allegro 的图元创建**失败时不报错、只返回 nil**，
    最常见的两个原因：

      1) **drawing 处于异常状态** —— 例如前面某个操作中途失败/被中断，
         或 extents 还没设置好。这种情况下连"完全合法"的图元也建不出来。
         修复：先 axlDBChangeDesignExtents 给一个大范围，再重试。

      2) **点坐标落在 drawing extents 之外** —— 尤其新建 .dra 的
         extents 常常很小甚至不含原点。

    注意：**不要**把这个失败归因于"层名写错了" —— 实测层名
    "PACKAGE GEOMETRY/SILKSCREEN_TOP" 等在本机是有效的
    （axlLayerGet 能取到对象）。
    """
    return (
        "图元创建返回 nil（Allegro 不报错）。请依次排查："
        "1) 先调 change_drawing_extents 把范围放大（extents 过小/未初始化是最常见原因）；"
        "2) 确认坐标落在 extents 内；"
        "3) 若之前有操作中途失败，重开一次 SkillBridge 服务（pyKillServer → pyStartServer）"
        "把 drawing 恢复到干净状态再重试。"
    )


# ============================================================
# 文件隔离：把"删除"改成"可恢复地搬走"
# ============================================================
#: 隔离区目录（★ 目录约定见 `services/paths.py`；保留模块级别名以便测试 monkeypatch）
_TRASH_DIR = P.TRASH_DIR


def _quarantine_file(path: str, reason: str = "") -> Optional[str]:
    """
    ★ 把文件**移入项目 `.trash/` 隔离区**，而不是 `os.remove` 掉。

    ★★★ 为什么不用 os.remove ★★★
        `export_psm` 编译前必须把旧 `.psm` 挪开 —— 否则 Allegro 会弹
        「File Exists. Overwrite?」把 SkillBridge 通道卡死。
        但**直接删**会带来灾难性后果：编译一旦失败（图纸污染 / 缺位号），
        上一次的成功产物就永久没了。
        2026-09-29 真实踩过：`.psm` 成功产出过一次（8640 B），
        重跑时被"覆盖保护"的 `os.remove` 删掉，然后编译失败 → 产物彻底丢失。
        → 所以统一改成「搬走 + 记住路径」，失败时可原样搬回。

    :param path: 要隔离的文件路径
    :param reason: 写进隔离文件名的原因标签（便于日后追溯）
    :return: 隔离后的新路径（正斜杠）；**失败返回 None**
        ★ 调用方拿到 None 时**必须中止操作**，绝不能退化成"删掉算了"。
    """
    try:
        if not os.path.isfile(path):
            return None
        src = os.path.abspath(path)
        os.makedirs(_TRASH_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.basename(src)
        tag = f".{reason}" if reason else ""
        dst = os.path.join(_TRASH_DIR, f"{base}{tag}_{stamp}")
        n = 1
        while os.path.exists(dst):          # 同一秒内多次隔离也不覆盖
            dst = os.path.join(_TRASH_DIR, f"{base}{tag}_{stamp}_{n}")
            n += 1
        shutil.move(src, dst)
        logger.info("已隔离文件: %s -> %s", src, dst)
        return dst.replace("\\", "/")
    except Exception as e:                  # noqa: BLE001 — 隔离失败必须让调用方知道
        logger.warning("隔离文件失败 %s: %s", path, e)
        return None


def _restore_quarantined(orig: str, quarantined: str) -> bool:
    """
    把隔离区里的文件搬回原位（编译失败时的回滚动作）。
    """
    try:
        if not quarantined or not os.path.isfile(quarantined):
            return False
        d = os.path.dirname(os.path.abspath(orig))
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.exists(orig):
            # 原位已冒出半成品（编译产出的残片）→ 先把它也隔离走，再还原
            _quarantine_file(orig, reason="superseded")
        shutil.move(quarantined, orig)
        logger.info("已还原: %s -> %s", quarantined, orig)
        return True
    except Exception as e:                  # noqa: BLE001
        logger.warning("还原失败 %s -> %s: %s", quarantined, orig, e)
        return False


def _collect_logs_step(
    steps: List[Dict[str, Any]],
    *src_dirs: Optional[str],
) -> Dict[str, Any]:
    """
    ★ best-effort 把运行痕迹（`.jrl` / `.log` / `master.tag` / `probe_result.txt`）
    归集到 `Tools/log`，并把结果记进 `steps`。

    ★★ 设计原则：**日志归集永远不能拖累主流程**。
       - 不抛异常（内部已吞掉）
       - `allegro.jrl` 会被运行中的 Allegro 攥住 → 搬不动是正常现象，不算失败
       - 所以这一步恒定记 `ok=True`，把跳过的数量写在 `value` 里
    """
    try:
        r = P.collect_run_logs(*src_dirs)
    except Exception as e:                  # noqa: BLE001
        steps.append(_step_entry("collect_logs", "collect_run_logs", False,
                                 error=str(e)))
        return {}
    moved = r.get("moved") or []
    skipped = r.get("skipped") or []
    note = f"归集 {len(moved)} 个 -> {r.get('log_dir')}"
    if skipped:
        note += f"（{len(skipped)} 个被占用/跳过：Allegro 运行中攥着的 allegro.jrl 等）"
    steps.append(_step_entry("collect_logs", "collect_run_logs()", True, value=note))
    return r


# ============================================================
# 编译日志解析 —— 把 Allegro 错误码翻译成"人话 + 处方"
# ============================================================
#: `<name>.log` 里会出现的错误码 → (人话解释, 处方)
#:
#: ★★★ 为什么必须有这一层（2026-09-29 实测定论）★★★
#:   `axlCompileSymbol` 失败时**只返回 nil**，不告诉你为什么 → 极易误判为
#:   "被弹框阻断"，然后去跟弹框搏斗，方向全错。
#:   真相：弹框是**罕见**的（覆盖已有 .psm 时才出现，且已有双重规避）；
#:   真正的阻断源一直是 Edit Check 报错，原因全在 `<name>.log` 里。
#:   实测证据（本项目 Tools/log/）：
#:       x.log              -> ERROR(SPMHCS-1)  missing a refdes
#:       nb_...log,2        -> WARNING(SPMHA1-298) duplicate pin numbers
#:                             ERROR(SPMHA1-291) create symbol has been aborted
#:       nb_...log (15:32)  -> "Create symbol completed."   ← 数据对了就成功
_COMPILE_LOG_CODES: Dict[str, Tuple[str, str]] = {
    "SPMHCS-1": (
        "图纸里没有位号（refdes）文字",
        '用 add_text(target="refdes") 往 REF DES/SILKSCREEN_TOP 放一段文本'
        "（习惯写 U*）。注意：层名写成 PACKAGE GEOMETRY/* 时 Allegro **不认**是位号。",
    ),
    "SPMHA1-298": (
        "存在重复的 pin number",
        "逐个核对 pin 编号；编号重复时 Edit Check 不通过，Create symbol 直接被中止。",
    ),
    "SPMHA1-301": (
        "Create symbol 的 DRC 校验未通过",
        "看同批次 WARNING 的具体条目 —— 绝大多数情况就是重复 pin number。",
    ),
    "SPMHA1-291": (
        "Create symbol 被中止（由上面列出的错误引起）",
        "先修掉同批次里列出的具体错误，再重试编译。这不是弹框问题。",
    ),
    "SPMHA1-70": (
        "图形超出 drawing extents",
        "先调 change_drawing_extents 把范围放大，再编译。",
    ),
    "SPMHGE-268": (
        "create_sym 内部报错（allegro.jrl 里的外层症状）",
        "具体原因在同目录的 <symbol>.log —— 通常是 SPMHCS-1 或 SPMHA1-29x。",
    ),
    "SPMHGE-7": (
        "有错误发生（allegro.jrl 里的外层症状）",
        "同上，去看 <symbol>.log 的具体条目。",
    ),
}

#: 匹配 "ERROR(SPMHCS-1): Symbol is missing a refdes."
_LOG_LINE_RE = re.compile(
    r"^\s*(ERROR|WARNING)\s*\(\s*([A-Za-z0-9][A-Za-z0-9\-]*)\s*\)\s*:\s*(.*?)\s*$"
)


def _parse_symbol_log_text(text: str) -> List[Dict[str, Any]]:
    """
    把编译日志文本解析成结构化条目。**纯函数，可离线单测。**

    只认 `ERROR(CODE): msg` / `WARNING(CODE): msg` 两种行，
    其余（表头、`Create symbol started.`、空行）一律忽略。

    :return: [{"severity","code","message","meaning","hint"}, ...]
    """
    entries: List[Dict[str, Any]] = []
    for ln in (text or "").splitlines():
        m = _LOG_LINE_RE.match(ln)
        if not m:
            continue
        sev = m.group(1).upper()
        code = m.group(2).upper()
        msg = m.group(3)
        meaning, hint = _COMPILE_LOG_CODES.get(code, (None, None))
        entries.append({
            "severity": sev,
            "code": code,
            "message": msg,
            "meaning": meaning,
            "hint": hint,
        })
    return entries


def _summarize_symbol_log(
    entries: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """把条目压成一句人话；没有 ERROR 也没有 WARNING 时返回 None。"""
    if not entries:
        return None
    errs = [e for e in entries if e.get("severity") == "ERROR"]
    pool = errs or list(entries)
    head = "编译未通过：" if errs else "编译有告警："
    return head + "；".join(
        f"{e.get('code')}（{e.get('meaning') or e.get('message')}）"
        for e in pool
    )


def _scan_symbol_compile_log(
    dirs: Sequence[Optional[str]],
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """
    找 `<symbol>.log` 并把里面的 ERROR / WARNING 解析成结构化条目。

    ★ 这是"失败可诊断"的地基：`axlCompileSymbol` 返 nil 时，
      唯一能告诉你为什么的就是这个文件。

    搜索顺序：
      1) 传入的候选目录（通常是 drawing 目录 + 其父目录）
      2) `Tools/log`（日志被 `collect_run_logs` 归集后的落点）

    文件名候选：`<symbol>.log` 与 `<symbol>` 原样大小写。
    同名多份时取 **mtime 最新** 的那个。

    ★ 除了 ERROR/WARNING，还负责给出**成功证据**（2026-09-29 补）：
      `axlCompileSymbol` **恒返回 nil**，返回值不能当成功判据；
      真正的证据是 Allegro 自己写进日志的这两行：
          Create symbol started.
          Create symbol completed.
      → `started` / `completed` 两个布尔字段就是从这里抠出来的，
        供 `export_psm` 做"以证据为准"的判定。

    :param dirs:   候选目录（可含 None，会被过滤）
    :param symbol: 符号名；可传完整路径（内部取 basename 去扩展名）
    :return: {"log_file": str|None,
              "entries": [{"severity","code","message","meaning","hint"}],
              "raw_errors": [str],
              "summary": str|None,
              "started": bool, "completed": bool, "log_tail": str}
    """
    out: Dict[str, Any] = {
        "log_file": None, "entries": [], "raw_errors": [], "summary": None,
        "started": False, "completed": False, "log_tail": "",
    }
    if not symbol:
        return out

    stem = os.path.basename(str(symbol).replace("\\", "/"))
    if stem.lower().endswith(".log"):
        stem = stem[:-4]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    if not stem:
        return out

    cand_dirs: List[str] = []
    for d in list(dirs) + [P.LOG_DIR]:
        if d and os.path.isdir(d) and d not in cand_dirs:
            cand_dirs.append(d)

    # ★ 精确名 + "带时间戳的归集版"
    #   `collect_run_logs` 会把日志改名成 `<stem>_20260929-155731.log`，
    #   只按精确名找会漏掉归集后的那份 → 必须前缀匹配。
    stem_low = stem.lower()
    candidates: List[str] = []
    for d in cand_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for nm in names:
            low = nm.lower()
            if not low.endswith(".log"):
                continue
            if low == f"{stem_low}.log" or low.startswith(f"{stem_low}_"):
                candidates.append(os.path.join(d, nm))
    if not candidates:
        return out

    latest = max(candidates, key=lambda p: os.path.getmtime(p))
    out["log_file"] = latest.replace("\\", "/")

    try:
        with open(latest, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError as e:                       # noqa: BLE001
        out["summary"] = f"日志读取失败: {e}"
        return out

    entries = _parse_symbol_log_text(text)
    out["entries"] = entries
    out["raw_errors"] = [
        f"{e['severity']}({e['code']}): {e['message']}" for e in entries
    ]
    out["summary"] = _summarize_symbol_log(entries)

    # ★ 成功证据（不受 ERROR/WARNING 解析影响，纯粹做子串匹配）
    low = text.lower()
    out["started"] = "create symbol started" in low
    out["completed"] = "create symbol completed" in low
    out["log_tail"] = text[-800:]
    return out


# ============================================================
# padstack 重名检测（内存优先 + 磁盘双保险）
# ============================================================
def _drawing_dir(s: AllegroSession) -> Optional[str]:
    """
    取当前 drawing 所在目录。取不到返回 None。

    用 `axlGetDrawingName()` 拿完整路径再取 dirname —— 实测可用：
        'C:/.../Tools/probe_test.dra' -> 'C:/.../Tools'
    """
    import os

    try:
        full = s.eval_skill("axlGetDrawingName()")
    except SkillCallError:
        pass
    else:
        if full:
            d = os.path.dirname(str(full))
            if d and os.path.isdir(d):
                return d

    # 兜底：RPC 通道
    try:
        full = s.call("axlGetDrawingName")
        if full:
            d = os.path.dirname(str(full))
            if d and os.path.isdir(d):
                return d
    except SkillCallError:
        pass

    return None


def _padstack_files(s: AllegroSession) -> Optional[set]:
    """
    列出当前 drawing 目录下所有 `<name>.pad` 文件的名字（小写、去扩展名）。

    这是**磁盘侧**的存在性视图，只作双保险用。

    ★ 为什么不能只靠它（2026-09-28 踩坑）：
        Allegro 的 padstack 落盘成 `<name>.pad`，但**内存中的 drawing
        可以持有磁盘上没有的 padstack**（例如手工把 .pad 删了、
        或 padstack 建了但没落盘）。实测就因此漏判过一次：
        磁盘 `set()` 为空，而 `axlDBGetDesign()->padstacks` 有 7 个，
        其中 `R1D4X1D2` 真实存在 -> 建新 padstack 报含糊失败。
        所以必须叠加**内存侧**探测（见 `_padstack_names_mem`）。

    :return: 小写名集合；无法确定目录时返回 None（调用方应跳过磁盘判定）
    """
    import os

    d = _drawing_dir(s)
    if not d:
        return None

    names = set()
    try:
        for fn in os.listdir(d):
            if fn.lower().endswith(".pad"):
                names.add(os.path.splitext(fn)[0].lower())
    except OSError:
        return None
    return names


def _padstack_names_mem(s: AllegroSession) -> Optional[set]:
    """
    列出当前 drawing **内存中**所有 padstack 的名字（小写）。

    实现：`mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)`

    ★ 早期误判更正：曾以为"AXL 没有列出 padstack 的 API"，实际是
      `->` 属性穿透 + `mapcar` 在 eval 通道里都能正常工作（实测见
      skill_snippets.build_padstack_name_list 的 docstring）。

    :return: 小写名集合；查询失败返回 None
    """
    try:
        raw = s.eval_skill(S.build_padstack_name_list())
    except SkillCallError:
        return None

    if raw is None:
        # 空 design 也可能返回 None；用数量再确认一次，避免把"查不到"当"没有"
        try:
            n = s.eval_skill("length(axlDBGetDesign()->padstacks)")
            if n in (0, "0"):
                return set()
        except SkillCallError:
            pass
        return None

    if isinstance(raw, (list, tuple)):
        return {str(x).strip().lower() for x in raw if x is not None}

    # 非列表（老版本/异常返回）：退化为字符串处理
    return {str(raw).strip().lower()}


def _padstack_exists(s: AllegroSession, name: str) -> Dict[str, Any]:
    """
    判断某个 padstack 是否已存在于当前 drawing 中 —— **三层探测**。

    :return: {
        "exists": bool|None,      # None = 判定不了
        "via":    str,            # 判定依据
        "detail": str,
    }

    三层（短路求值，第一个能判定的就返回）：

      1) **内存全量名单**（最可信）
            mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)
         一次往返拿到全部名字，能同时服务"重名检测"和"重名枚举报告"。

      2) **单名内存探测**
            axlLoadPadstack("<name>") -> remote object / nil
         第 1 层失败时的精确补刀。注意它**大小写不敏感**（实测）。

      3) **磁盘 .pad 文件**（双保险）
            <drawing_dir>/<name>.pad
         前两层都查不到时才用；只能作为"存在"的正向证据 ——
         磁盘没有**不能**推出内存没有。
    """
    low = name.strip().lower()

    # ---- 第 1 层：内存全量名单 ----
    mem = _padstack_names_mem(s)
    if mem is not None:
        return {
            "exists": low in mem,
            "via": "memory_list",
            "detail": f"design 内存中共有 {len(mem)} 个 padstack",
        }

    # ---- 第 2 层：单名探测 ----
    try:
        obj = s.eval_skill(S.build_padstack_exists_check(name))
    except SkillCallError:
        obj = None
    else:
        if obj is not None:
            return {
                "exists": True,
                "via": "axlLoadPadstack",
                "detail": "axlLoadPadstack 返回了对象引用",
            }
        # 返回 nil：是真的不存在，还是查不了？用一次 errset 区分。
        # 这里直接采信 nil = 不存在（axlLoadPadstack 对不存在的名字就是 nil，
        # 不会报错，实测确认）。
        return {
            "exists": False,
            "via": "axlLoadPadstack",
            "detail": "axlLoadPadstack 返回 nil",
        }

    # ---- 第 3 层：磁盘 ----
    files = _padstack_files(s)
    if files is not None and low in files:
        return {
            "exists": True,
            "via": "file_system",
            "detail": f"drawing 目录下存在 {name}.pad",
        }

    return {
        "exists": None,
        "via": "none",
        "detail": "三层探测都无法给出结论",
    }


# ============================================================
# 单位换算辅助
# ============================================================
def _mm_to_design(mm: float, unit: str) -> float:
    """把 mm 换算成当前 design 的单位数值。"""
    return to_unit(mm, unit)


def _convert_sequence_mm_to_design(values: Sequence[float], unit: str) -> List[float]:
    return [_mm_to_design(v, unit) for v in values]


# ============================================================
# 单个动作：建 padstack
# ============================================================
def create_padstack(
    name: str,
    layers: Sequence[Dict[str, Any]],
    drill: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[str] = None,
    save_to_disk: bool = True,
    on_exists: str = "error",
) -> Dict[str, Any]:
    """
    在**当前打开的 drawing** 中创建一个 padstack。

    :param name:   padstack 名，如 "R1D4X1D2"
                   （Allegro 会存成 <name>.pad，故名字里不要带路径和扩展名）
    :param on_exists: 同名字 padstack 已存在时的策略：
                     - "error"（默认）：直接返回失败，并给出明确提示
                     - "skip"： 不重建，直接返回成功（padstack_name 保持原名）
                     - "build": 不检测，照常构建（Allegro 行为：失败）
    :param layers: 层定义列表，每项：
                     {
                       "layer":  "TOP",              # 层名
                       "figure": "RECTANGLE",        # RECTANGLE/SQUARE/CIRCLE/OBLONG/OVAL/SHAPE
                       "size":   [1.4, 1.2],         # [w, h]，**单位 mm**
                       "type":   "REGULAR"           # 可选，默认 REGULAR
                     }
    :param drill:  可选钻孔：
                     {
                       "diameter": 0.3,              # mm（圆孔）
                       "plated":   True,
                       "size":     [0.6, 0.3],       # 可选，异形孔；给了就忽略 diameter
                       "offset":   [0, 0]            # 可选，相对焊盘中心，mm
                     }
    :param save_to_disk: 建完是否立即落盘成 .pad 文件（默认 True）
    :param workspace_id: SkillBridge workspace，默认读 config

    :return: {
        "success": bool, "padstack_name": str, "unit": str,
        "steps": [...], "pad_file": str|None, "error": str|None,
        "skipped": bool,        # 仅 on_exists="skip" 命中时出现
        "exists_via": str,      # 同上，重名判定依据（memory_list/axlLoadPadstack/file_system）
    }

    ★ 重名检测为**三层探测**（见 _padstack_exists）：
        1) 内存全量名单 mapcar(lambda((ps) ps->name) axlDBGetDesign()->padstacks)
        2) 单名探测   axlLoadPadstack("<name>")
        3) 磁盘文件   <drawing_dir>/<name>.pad
      只用磁盘扫描会漏判（内存持有、磁盘已删的情况实测踩过）。
    """
    steps: List[Dict[str, Any]] = []

    if not layers:
        return _result(False, error="layers 不能为空", steps=steps)

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))

            if unit == "unknown":
                return _result(
                    False,
                    error="无法确定当前 drawing 的单位。请确认 Allegro 里已打开一个 drawing。",
                    steps=steps,
                )

            # ---- 重名前置检测 ----
            # Allegro 的 axlDBCreatePadStack 遇重名不会覆盖，只会静默失败，
            # 且 errset 拿不到错误信息（实测只返回 (nil)）。
            # 所以这里主动查一次，把"含糊失败"变成"明确提示"。
            #
            # ★ 三层探测：内存全量名单 -> axlLoadPadstack 单名 -> 磁盘 .pad
            #   （早期只用磁盘扫描，漏判了"磁盘没有但内存持有"的情况）
            if on_exists in ("error", "skip"):
                probe = _padstack_exists(s, name)
                steps.append(
                    _step_entry(
                        "check_exists",
                        S.build_padstack_exists_check(name),
                        True,
                        value=f"{probe['detail']}（via={probe['via']}）",
                    )
                )
                if probe["exists"] is None:
                    steps.append(
                        _step_entry(
                            "check_exists_warn", "<probe inconclusive>", True,
                            value="三层探测均无法判定，按“不存在”继续",
                        )
                    )
                elif probe["exists"]:
                    if on_exists == "skip":
                        steps.append(
                            _step_entry(
                                "skip_existing",
                                f"<{name} already exists>", True,
                                value=f"按 on_exists=skip 跳过重建（via={probe['via']}）",
                            )
                        )
                        return _result(
                            True,
                            padstack_name=name,
                            unit=unit,
                            steps=steps,
                            pad_file=_locate_pad_file(s, name),
                            error=None,
                            skipped=True,
                            exists_via=probe["via"],
                        )
                    return _result(
                        False,
                        error=(
                            f"padstack `{name}` 已存在于当前 drawing 中"
                            f"（判定依据：{probe['via']}）。"
                            f"Allegro 不会覆盖同名 padstack。"
                            f"请改用其它名字，或先删除旧的 padstack，"
                            f"或传 on_exists='skip' 直接复用。"
                        ),
                        padstack_name=name,
                        unit=unit,
                        steps=steps,
                    )

            # ---- 构造 pad 层表达式（mm -> design units）----
            layer_exprs: List[str] = []
            for i, ld in enumerate(layers):
                if "layer" not in ld or "figure" not in ld or "size" not in ld:
                    return _result(
                        False,
                        error=f"layers[{i}] 缺少必需字段 (layer/figure/size): {ld}",
                        steps=steps,
                    )
                w = _mm_to_design(float(ld["size"][0]), unit)
                h = _mm_to_design(float(ld["size"][1]), unit)
                expr = S.build_pad_layer(
                    layer=str(ld["layer"]),
                    figure=str(ld["figure"]),
                    size=(w, h),
                    pad_type=str(ld.get("type", "REGULAR")),
                )
                layer_exprs.append(expr)
                steps.append(_step_entry(f"build_layer[{i}]", expr, True))

            # ---- 钻孔 ----
            drill_expr: Optional[str] = None
            if drill:
                if drill.get("size"):
                    sz = drill["size"]
                    drill_expr = S.build_drill(
                        diameter=0.0,
                        plated=bool(drill.get("plated", True)),
                        drill_type=str(drill.get("drill_type", "OBLONG")),
                        size=(
                            _mm_to_design(float(sz[0]), unit),
                            _mm_to_design(float(sz[1]), unit),
                        ),
                        offset=tuple(
                            _mm_to_design(float(v), unit)
                            for v in drill.get("offset", [0, 0])
                        ),
                    )
                else:
                    drill_expr = S.build_drill(
                        diameter=_mm_to_design(float(drill.get("diameter", 0.3)), unit),
                        plated=bool(drill.get("plated", True)),
                    )
                steps.append(_step_entry("build_drill", drill_expr, True))

            # ---- 真正建 padstack ----
            create_expr = S.build_create_padstack_guarded(name, layer_exprs, drill_expr)
            try:
                raw = s.eval_skill(create_expr)
            except SkillCallError as e:
                steps.append(_step_entry("create_padstack", create_expr, False, error=str(e)))
                return _result(False, error=str(e), steps=steps)

            err = _is_errset_failure(raw)
            if err:
                steps.append(
                    _step_entry(
                        "create_padstack", create_expr, False,
                        value=raw, error=err,
                    )
                )
                return _result(
                    False,
                    error=(
                        f"axlDBCreatePadStack 失败: {err}。"
                        f"常见原因：1) padstack `{name}` 已存在（Allegro 不覆盖，"
                        f"重名时会返回 nil 但**不带错误信息**）；"
                        f"2) drawing 不是 Package symbol；"
                        f"3) figure 与 size 不兼容（如 SQUARE 要求 w==h）。"
                    ),
                    padstack_name=name,
                    unit=unit,
                    steps=steps,
                )

            steps.append(_step_entry("create_padstack", create_expr, True, value=raw))

            # ---- 落盘 ----
            pad_file = None
            if save_to_disk:
                disk_expr = S.build_padstack_to_disk(name)
                try:
                    draw = s.eval_skill(disk_expr)
                    ok = bool(draw)
                    steps.append(
                        _step_entry(
                            "padstack_to_disk", disk_expr, ok,
                            value=draw,
                            error=None if ok else "axlPadstackToDisk 返回 false",
                        )
                    )
                    if ok:
                        # 落盘后文件在 drawing 目录下，命名为 <name>.pad（小写）
                        pad_file = _locate_pad_file(s, name)
                        if pad_file:
                            steps.append(
                                _step_entry("locate_pad", f"<scan for {name}.pad>", True, pad_file)
                            )
                except SkillCallError as e:
                    steps.append(
                        _step_entry("padstack_to_disk", disk_expr, False, error=str(e))
                    )

            return _result(
                True,
                padstack_name=name,
                unit=unit,
                steps=steps,
                pad_file=pad_file,
                error=None,
            )

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("create_padstack 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


def _locate_pad_file(s: AllegroSession, name: str) -> Optional[str]:
    """
    在 drawing 所在目录找刚生成的 .pad 文件。

    Allegro 落盘时会把名字**转小写**（实测：R1D4X1D2 -> r1d4x1d2.pad）。
    所以这里大小写都要找。
    """
    import os

    try:
        design = s.call("axlCurrentDesign")
    except SkillCallError:
        return None
    if not design:
        return None

    # 优先从 design 的完整路径推目录
    candidates: List[str] = []
    try:
        full = s.call("axlGetDrawingName")
        if full:
            candidates.append(os.path.join(os.path.dirname(str(full)), f"{name.lower()}.pad"))
            candidates.append(os.path.join(os.path.dirname(str(full)), f"{name}.pad"))
    except SkillCallError:
        pass

    # 兜底：当前工作目录
    for c in (f"{name.lower()}.pad", f"{name}.pad"):
        candidates.append(os.path.abspath(c))

    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ============================================================
# 单个动作：放 pin
# ============================================================
def add_pin(
    padstack_name: str,
    number: str,
    x: float,
    y: float,
    rotation: Optional[float] = None,
    text_offset: Optional[Sequence[float]] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    在当前 drawing 中放一个 pin。

    :param x, y:       pin 锚点坐标，**单位 mm**（相对 drawing 原点）
    :param rotation:   可选，旋转角度（度）
    :param text_offset: 可选，pin 编号文字的偏移 [dx, dy]，单位 mm
    :return: {"success": bool, "steps": [...], "error": str|None}
    """
    steps: List[Dict[str, Any]] = []

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)

            dx, dy = (0.0, 0.0)
            if text_offset:
                dx = _mm_to_design(float(text_offset[0]), unit)
                dy = _mm_to_design(float(text_offset[1]), unit)

            ptxt = S.build_pin_text(number=str(number), offset=(dx, dy), text=str(number))
            expr = S.build_create_pin(
                padstack_name,
                _mm_to_design(x, unit),
                _mm_to_design(y, unit),
                ptxt,
                rotation,
            )

            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("create_pin", expr, False, error=str(e)))
                return _result(False, error=str(e), steps=steps)

            err = _is_errset_failure(raw)
            if err:
                hint = ""
                if "extent" in err.lower():
                    hint = (
                        "  ← SPMHA1-70: pin 落在 drawing extents 之外。"
                        "建议先调 change_extents 扩大范围。"
                    )
                steps.append(_step_entry("create_pin", expr, False, value=raw, error=err))
                return _result(False, error=f"axlDBCreatePin 失败: {err}{hint}", steps=steps)

            steps.append(_step_entry("create_pin", expr, True, value=raw))
            return _result(True, pin_number=str(number), unit=unit, steps=steps, error=None)

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("add_pin 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 单个动作：画轮廓（丝印 / Assembly / Place_Bound）
# ============================================================
def add_outline(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    target: str = "silkscreen",
    width: float = 0.15,
    filled: bool = False,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    画一个矩形轮廓。

    :param x1,y1,x2,y2: 对角两点，**单位 mm**
    :param target: "silkscreen" | "assembly" | "place_bound"
    :param width:  线宽 mm（place_bound 忽略）
    :param filled: 仅 place_bound 有效 —— 是否填充
    """
    steps: List[Dict[str, Any]] = []

    LAYER_MAP = {
        "silkscreen": "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "assembly": "PACKAGE GEOMETRY/ASSEMBLY_TOP",
        "place_bound": "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
    }
    layer = LAYER_MAP.get(target)
    if layer is None:
        return _result(
            False,
            error=f"未知 target: {target}（可选: {list(LAYER_MAP)}）",
            steps=steps,
        )

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)

            X1 = _mm_to_design(x1, unit)
            Y1 = _mm_to_design(y1, unit)
            X2 = _mm_to_design(x2, unit)
            Y2 = _mm_to_design(y2, unit)
            W = _mm_to_design(width, unit)

            if target == "place_bound":
                exprs = [S.build_filled_rect(X1, Y1, X2, Y2, layer, fill=filled)]
            else:
                # ★ 一次调用画完「闭合折线」= **1 个图元**（不是 4 条独立线）。
                #   [金标] 金标准的 assembly 本体框实测就是 `n_segs=4` 的**一个对象**
                #   → 逐段画会让「Assembly 元素数」等检查项对不上，故走这条。
                exprs = [S.build_rect_polyline(X1, Y1, X2, Y2, layer, W)]

            created = 0
            step_name = "create_rect" if target == "place_bound" else "create_line"
            for e in exprs:
                try:
                    raw = s.eval_skill(e)
                    if _is_creation_ok(raw):
                        created += 1
                        steps.append(_step_entry(step_name, e, True, value=raw))
                    else:
                        steps.append(
                            _step_entry(step_name, e, False, error=_creation_failure_hint(raw))
                        )
                except SkillCallError as ex:
                    steps.append(_step_entry(step_name, e, False, error=str(ex)))

            ok = created == len(exprs)
            return _result(
                ok,
                target=target,
                layer=layer,
                created=created,
                expected=len(exprs),
                unit=unit,
                steps=steps,
                error=None if ok else (
                    f"只成功 {created}/{len(exprs)} 条。"
                    f"{_creation_failure_hint(None)}"
                ),
            )

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("add_outline 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 单个动作：画线段（丝印任意形状 —— 1 脚标识三角 / 缺口 / 短横线）
# ============================================================
def add_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    target: str = "silkscreen",
    width: float = 0.10,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    画一条**任意线段**。

    `add_outline` 只能画矩形；丝印的 1 脚标识（三角形 / 缺口 / 箭头）
    与"焊盘之间的短横线"都需要任意线段，故补此工具。

    ★ 实测依据（2026-09-29 在线读 `Data\\footprint\\3S48000163` 人工金标准的
      `PACKAGE GEOMETRY/SILKSCREEN_TOP`）：金标准丝印正是由
      **2 条短横线 + 1 个 4 段三角形**构成，全部是 `path`/line，不是矩形。

    :param x1,y1,x2,y2: 两端点，**单位 mm**
    :param target: "silkscreen" | "assembly"
    :param width:  线宽 mm（[规范 四] 丝印线宽 >= 0.10）
    """
    steps: List[Dict[str, Any]] = []

    LAYER_MAP = {
        "silkscreen": "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "assembly": "PACKAGE GEOMETRY/ASSEMBLY_TOP",
    }
    layer = LAYER_MAP.get(target)
    if layer is None:
        return _result(False, error=f"未知 target: {target}（可选: {list(LAYER_MAP)}）",
                       steps=steps)

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)

            expr = S.build_line(
                _mm_to_design(x1, unit), _mm_to_design(y1, unit),
                _mm_to_design(x2, unit), _mm_to_design(y2, unit),
                layer, _mm_to_design(width, unit),
            )
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as ex:
                steps.append(_step_entry("create_line", expr, False, error=str(ex)))
                return _result(False, error=str(ex), steps=steps)

            ok = _is_creation_ok(raw)
            hint = None if ok else _creation_failure_hint(raw)
            steps.append(_step_entry("create_line", expr, ok,
                                     value=raw if ok else None, error=hint))
            return _result(ok, target=target, layer=layer, unit=unit,
                           from_to=[[round(x1, 4), round(y1, 4)],
                                    [round(x2, 4), round(y2, 4)]],
                           width=width, steps=steps, error=hint)

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("add_line 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


def add_polyline(
    points: Sequence[Sequence[float]],
    target: str = "silkscreen",
    width: float = 0.10,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    画一条**折线**（n 个点 → n-1 段），**只生成 1 个图元**。

    ★ 与「连续调 N 次 `add_line`」的区别（2026-09-29 金标准对拍发现）：
        金标准丝印层只有 3 个图元：1 个 4 段三角 + 2 条单段短横线。
        三角若拆成 3 次 `add_line`，图元数变 5 →
        「Silkscreen 复杂 path 数」「Assembly 元素数」两条检查项都对不上。
      → 所以凡是首尾相连的线段，一律用本函数**一次画完**。

    :param points: [[x1,y1],[x2,y2],...]（**单位 mm**，绝对坐标）
    :param target: "silkscreen" | "assembly"
    :param width:  线宽 mm
    """
    steps: List[Dict[str, Any]] = []
    LAYER_MAP = {
        "silkscreen": "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "assembly": "PACKAGE GEOMETRY/ASSEMBLY_TOP",
    }
    layer = LAYER_MAP.get(target)
    if layer is None:
        return _result(False, error=f"未知 target: {target}（可选: {list(LAYER_MAP)}）",
                       steps=steps)
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2:
        return _result(False, error=f"折线至少需要 2 个点，收到 {len(pts)} 个",
                       steps=steps)

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)

            dpts = [(_mm_to_design(x, unit), _mm_to_design(y, unit)) for x, y in pts]
            expr = S.build_polyline(dpts, layer, _mm_to_design(width, unit))
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as ex:
                steps.append(_step_entry("create_polyline", expr, False, error=str(ex)))
                return _result(False, error=str(ex), steps=steps)

            ok = _is_creation_ok(raw)
            hint = None if ok else _creation_failure_hint(raw)
            steps.append(_step_entry("create_polyline", expr, ok,
                                     value=raw if ok else None, error=hint))
            return _result(ok, target=target, layer=layer, unit=unit,
                           point_count=len(pts), segments=len(pts) - 1,
                           points=[[round(x, 4), round(y, 4)] for x, y in pts],
                           width=width, steps=steps, error=hint)

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("add_polyline 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 单个动作：加文字（位号 / 值 / 1 脚标识）
# ============================================================
def add_text(
    text: str,
    x: float,
    y: float,
    target: str = "silkscreen",
    text_block: str = "1",
    rotation: float = 0.0,
    justify: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    加一段文字。位号（REFDES）是必加项 —— 缺它 Create Symbol 会报 SPMHCS-1。

    ★★★ 层名对应关系（2026-09-29 逐条 `axlLayerGet` 实测确认）★★★
        "silkscreen"      -> PACKAGE GEOMETRY/SILKSCREEN_TOP
        "assembly"        -> PACKAGE GEOMETRY/ASSEMBLY_TOP
        "refdes"          -> REF DES/SILKSCREEN_TOP
        "refdes_assembly" -> REF DES/ASSEMBLY_TOP   ← ★ 位号默认用这个

        ✗ `PACKAGE GEOMETRY/PLACE_TOP` 实测返 None（层不存在）—— 别用。

    ★★★ 致命坑：位号放错层的后果 ★★★
        直接跑 `axlCompileSymbol` 出 `.psm` 时会报：
            ERROR(SPMHCS-1): Symbol is missing a refdes.
            ERROR(SPMHA1-291): Create symbol has been aborted.
        即使图纸里**确实有** `U*` 这段文字，只要它不在 `REF DES/*` 层，
        Allegro 就不认它是 refdes。

    ★ 位号放哪一层（2026-09-30 用户裁决 + 模板实测）
        `Data\\footprint\\*/` 下 14/14 个人工模板都放
        **REF DES/ASSEMBLY_TOP + `#REF`**，所以默认层改成 `refdes_assembly`。
        （`refdes`→丝印层仍保留可用，只是不再是默认。旧版默认是丝印层 `U*`。）

    :param target: "silkscreen" / "assembly" / "refdes" / "refdes_assembly"
    :param justify: "left" / "center" / "right"；None 时不传该参数。
                    ★ 人工模板位号是 LEFT 对齐。
    """
    steps: List[Dict[str, Any]] = []

    LAYER_MAP = {
        "silkscreen": "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "assembly": "PACKAGE GEOMETRY/ASSEMBLY_TOP",
        # ★★★ 位号（refdes）必须放 REF DES 类层，否则 Create Symbol 报
        #   ERROR(SPMHCS-1): Symbol is missing a refdes.
        #   ★ 2026-09-29 实测：`PACKAGE GEOMETRY/PLACE_TOP` **无效**（axlLayerGet 返 None），
        #     正确的是 `REF DES/SILKSCREEN_TOP`。
        #   ★ 2026-09-30：模板库 14/14 用装配层，故默认改 `refdes_assembly`。
        "refdes": "REF DES/SILKSCREEN_TOP",
        "refdes_assembly": "REF DES/ASSEMBLY_TOP",
    }
    layer = LAYER_MAP.get(target)
    if layer is None:
        return _result(False, error=f"未知 target: {target}", steps=steps)

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", True, unit))
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)

            expr = S.build_text(
                text=str(text),
                x=_mm_to_design(x, unit),
                y=_mm_to_design(y, unit),
                layer=layer,
                text_block=text_block,
                rotation=rotation,
                justify=justify,
            )
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("create_text", expr, False, error=str(e)))
                return _result(False, error=str(e), steps=steps)

            ok = _is_creation_ok(raw)
            steps.append(
                _step_entry(
                    "create_text", expr, ok,
                    value=raw,
                    error=None if ok else (
                        "axlDBCreateText 返回 nil。" + _creation_failure_hint(raw)
                    ),
                )
            )
            return _result(ok, text=str(text), layer=layer, unit=unit, steps=steps,
                           error=None if ok else "创建文字失败")

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("add_text 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 单个动作：调整 extents（避免 SPMHA1-70）
# ============================================================
def change_extents(
    x1: float = -50.0,
    y1: float = -50.0,
    x2: float = 50.0,
    y2: float = 50.0,
    workspace_id: Optional[str] = None,
    union_with_current: bool = True,
    margin: float = 2.0,
) -> Dict[str, Any]:
    """
    扩大/设置 drawing extents。

    ★ 放 pin 之前建议先调这个 —— 新建 .dra 的 extents 常常不含原点。

    ★★ 为什么默认"允许并集"（2026-09-29 实测 + 2026-10-08 修正）★★
        `axlDBChangeDesignExtents` 的文档明确说：**若新范围比现有对象小**
        （有东西落在新范围外），返回 nil 失败。
        实测正是如此：drawing 里已有 ±20 的图元时，
        设 ±3.1 会被拒绝 -> 报 "axlDBChangeDesignExtents 返回 nil"。

    ★★★ 2026-10-08 修正：并集从「无条件」改为「**精确设定失败才并集**」★★★

        旧实现**无条件**先读 `axlDBGetDesign()->bBox` 做并集。问题是：

          **空图纸（刚由模板复制出来的新 symbol）的 `->bBox` 返回的不是
          「内容外包框」，而是「设计 extents（模板页框）」。**

        实测（CSTJ-421）：新 symbol 图纸 `->bBox` = `(0,0)-(1117.6,863.6)`
        （= 官方模板的 44000×34000 mil 页框）→ 并集把范围撑成
        `(-6.55,-13.7)-(1119.6,865.6)`，日志里看着荒谬，
        还会**掩盖"究竟哪个方向需要扩"**。

        正确做法（与 Cadence 官方示例一致，`14dsnctl.html`）：
            extents = axlExtentDB('obstacle)
            axlDBChangeDesignExtents(extents)        ; 直接精确设定
        只有**精确设定失败时**，才说明内容真的落在请求范围外 ——
        此时 `->bBox` 才是真实的内容框，用它并集重试才有意义。

        幂等性依然成立：同一参数连调两次结果一致。

    :param x1..y2: 期望的对角范围，**单位 mm**
    :param union_with_current: 精确设定失败时，是否与当前**内容** bBox
                               取并集后重试（默认 True）
    :param margin: 取并集时额外留的余量（mm）
    """
    steps: List[Dict[str, Any]] = []

    try:
        with AllegroSession(workspace_id) as s:
            unit = s.units()

            def _set(bx1: float, by1: float, bx2: float, by2: float):
                """发一次 axlDBChangeDesignExtents，返回 (expr, raw, err)。"""
                e = S.build_extent_set(
                    _mm_to_design(bx1, unit), _mm_to_design(by1, unit),
                    _mm_to_design(bx2, unit), _mm_to_design(by2, unit),
                )
                try:
                    return e, s.eval_skill(e), None
                except SkillCallError as ex:
                    return e, None, str(ex)

            # ---- 1) 先精确设定（空图纸时这一步就成功，不会引入页框污染）----
            expr, raw, call_err = _set(x1, y1, x2, y2)
            if call_err:
                steps.append(_step_entry("change_extents", expr, False, error=call_err))
                return _result(False, error=call_err, steps=steps)
            ok = bool(raw)
            steps.append(
                _step_entry("change_extents", expr, ok, value=raw,
                            error=None if ok else "axlDBChangeDesignExtents 返回 nil")
            )

            # ---- 2) 失败 → 有对象落在范围外 → 读**内容** bBox 并集重试 ----
            if not ok and union_with_current:
                cur = _current_bbox_mm(s, unit)
                if cur:
                    cx1, cy1, cx2, cy2 = cur
                    ux1, uy1 = min(x1, cx1 - margin), min(y1, cy1 - margin)
                    ux2, uy2 = max(x2, cx2 + margin), max(y2, cy2 + margin)
                    steps.append(
                        _step_entry(
                            "union_with_current",
                            "<merge with current content bBox>", True,
                            value=f"精确设定被拒（有对象在范围外）"
                                  f" -> 内容 bBox={cur}"
                                  f" -> 并集=({ux1},{uy1})-({ux2},{uy2})",
                        )
                    )
                    expr2, raw2, err2 = _set(ux1, uy1, ux2, uy2)
                    if err2:
                        steps.append(
                            _step_entry("change_extents", expr2, False, error=err2))
                    else:
                        ok2 = bool(raw2)
                        steps.append(
                            _step_entry("change_extents", expr2, ok2, value=raw2,
                                        error=None if ok2 else "并集后仍然返回 nil"))
                        if ok2:
                            ok = True
                            x1, y1, x2, y2 = ux1, uy1, ux2, uy2

            return _result(
                ok, unit=unit, steps=steps,
                extent=(x1, y1, x2, y2),
                error=None if ok else (
                    "设置 extents 失败。文档说明：若有对象落在新范围外、"
                    "或超出数据库整数范围，会返回 nil。"
                    "默认已做并集扩展，仍然失败时试试给更大的范围，"
                    "或传 union_with_current=False 精确设定。"
                ),
            )

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("change_extents 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


def _bbox_mm_from_raw(raw: Any, unit: str) -> Optional[Tuple[float, float, float, float]]:
    """
    把 `((x1 y1) (x2 y2))` 形态的 SKILL bBox 换算成 mm，
    归一化成 (minx, miny, maxx, maxy)。取不到返回 None。
    """
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        (ax, ay), (bx, by) = raw[0], raw[1]
        from services.skillbridge_writer import from_unit

        v = [from_unit(float(ax), unit), from_unit(float(ay), unit),
             from_unit(float(bx), unit), from_unit(float(by), unit)]
        return (min(v[0], v[2]), min(v[1], v[3]),
                max(v[0], v[2]), max(v[1], v[3]))
    except (TypeError, ValueError, IndexError):
        return None


def frame_drawing(
    half: Optional[float] = None,
    margin: float = 2.0,
    extent_scale: float = 1.3,
    save: bool = False,
    max_iter: int = 4,
    tol: float = 5e-3,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    r"""
    ★★★ 让图纸「打开就居中」—— 把**图纸范围 + 视图窗口**都摆成以原点对称 ★★★

    ------------------------------------------------------------------
    解决什么（2026-09-30，用户反馈）
    ------------------------------------------------------------------
    每次生成的封装，打开后都缩在整个画布的**左下角**。

    ------------------------------------------------------------------
    根因（实测结论，不是猜的）
    ------------------------------------------------------------------
    `.dra` **会把"上一帧视图窗口"一起存进文件**。官方 `sym_template.dra`
    自带一个"原点落在左下角"的视图；我们的流程是
    「复制模板 → 打开 → 画几何 → 存盘」，视图原样跟着走
    —— 于是**每一份产出**打开都是左下角。

    两条被实测**推翻**的直觉：

    1. ✗「改图纸范围（extents）就能改视图」—— 不成立。
       `axlDBChangeDesignExtents` 只在范围**缩小**时把视图夹一下
       （视图比新范围大 → 裁到新范围）；范围**变大时不会放宽视图**。
       实测：视图 ±4 时把范围设成 ±20，视图读数仍是 ±4。

    2. ✗「`axlZoomCenter` 能兜底居中」—— 不成立。
       视图已经包含原点时它返回 t 但**什么都不改**。

    正确做法是**显式设视图**，并且**读回来做闭环**：
        `axlZoomPoints` 是"**底左锚定 + 按窗口宽高比外扩**"的
        （请求 ±4 正方形 → 实得 `[[-4,-4],[5.5226,4]]`，中心偏到 +0.76），
        所以必须用 `axlZoomBbox(nil)` 读回真实窗口、算出中心偏移、
        **平移请求坐标再设一次**。实测 2 次迭代即可收敛到中心误差 0。

    ------------------------------------------------------------------
    结果
    ------------------------------------------------------------------
    以 3S48000163（本体 3.2×2.5）为例：
        几何外包框   ≈ ±1.95 × ±1.975 mm
        margin=2.0  → R ≈ 4.0 mm（向上取到 0.5 的整数倍）
        图纸范围     = ±5.2 mm（R × extent_scale，留出宽高比外扩的余量）
        视图窗口     = ±4.76 × ±4.0 mm，**中心精确在原点**
        → 封装落在画面正中，四周留白均匀，`Zoom World` 也正常。

    :param half:  视图半宽（mm）。None = 由几何外包框 + margin 自动算
    :param margin: half=None 时，在几何外包框外留的余量（mm，默认 2.0）
    :param extent_scale: 图纸范围 = 视图半宽 × 该系数（默认 1.3，
                  目的是让"视图（会被宽高比外扩）"仍然落在图纸范围之内）
    :param save:  设完是否立刻存盘（默认 False —— 通常紧接着还会
                  由 `build_footprint` / `export_psm` 存，不必存两次）
    :param max_iter: 视图居中闭环的最大迭代次数（默认 4）
    :param tol:   判定"已居中"的中心误差阈值（mm，默认 5µm）

    :return: {"success", "unit", "half", "extent", "view", "view_center",
              "iterations", "steps", "warnings", "error"}
    """
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []

    try:
        # ---- 1) 读几何外包框 + 画布宽高比，算视图半宽 ----
        # ★★★ 2026-09-30 定论（FPC-0510 才暴露，探针数据钉死映射规则）★★★
        #   `axlZoomPoints(nil 上右 下左)` 的真实映射：
        #     · res_y  = 请求 y（精确保持）
        #     · res_x1 = 请求 x1（左下锚定），但**被"图纸范围左界"钳住**
        #     · 宽度  = 请求高度 × 画布宽高比 aspect（≈窗口宽/高，实测 ~1.75）
        #   ⇒ 居中后的视图宽 = 2·R·aspect。**图纸范围半宽必须 ≥ R·aspect**，
        #     否则视图左端被范围左界钳住 → 中心偏右（实测 +2.46mm）。
        #   所以这里先读 aspect，再把 extent_scale 抬到 ≥ aspect，
        #   并按 aspect 构造请求矩形 → **一次调用即居中，无需迭代**。
        aspect = 1.0
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            if unit == "unknown":
                return _result(False, error="无法确定 drawing 单位", steps=steps)
            try:
                raw_geom = s.eval_skill(S.build_extent_db())
            except SkillCallError as e:
                steps.append(_step_entry("extent_db", S.build_extent_db(),
                                         False, error=str(e)))
                return _result(False, error=str(e), steps=steps)
            geom = _bbox_mm_from_raw(raw_geom, unit)
            steps.append(_step_entry(
                "extent_db", S.build_extent_db(), geom is not None,
                value=f"几何外包框(mm) = {geom}",
                error=None if geom else "axlExtentDB 未返回 bBox"))
            # 画布宽高比：读当前视图窗口算（若读得到）
            try:
                v0 = _bbox_mm_from_raw(
                    s.eval_skill(S.build_zoom_bbox("nil")), unit)
                if v0 and (v0[3] - v0[1]) > 1e-6 and (v0[2] - v0[0]) > 1e-6:
                    aspect = max(0.5, min(6.0,
                        (v0[2] - v0[0]) / (v0[3] - v0[1])))
                    steps.append(_step_entry(
                        "zoom_aspect", S.build_zoom_bbox("nil"), True,
                        value=f"画布宽高比 = {aspect:.4f}（视图 {v0}）"))
            except SkillCallError as e:
                steps.append(_step_entry(
                    "zoom_aspect", S.build_zoom_bbox("nil"), False, error=str(e)))

        if half is None:
            if geom is None:
                R = 5.0
                warnings.append("读不到几何外包框 —— 视图按默认 ±5.0mm 居中。")
            else:
                R = max(abs(geom[0]), abs(geom[2]),
                        abs(geom[1]), abs(geom[3])) + float(margin)
                R = math.ceil(R * 2.0 - 1e-9) / 2.0      # 向上取到 0.5 的整数倍
        else:
            R = float(half)
        if R <= 0:
            return _result(False, error=f"非法的视图半宽: {R}", steps=steps)

        # ---- 2) 图纸范围：以原点对称，且必须容下居中后的视图宽度 ----
        #   居中视图半宽 = R·aspect；图纸范围半宽 Re 必须 ≥ 它，否则视图
        #   左端被范围左界钳住。故 extent_scale 抬到 ≥ aspect（留 10% 余量）。
        eff_scale = max(float(extent_scale), aspect + 0.1)
        Re = round(R * eff_scale, 4)
        ext = change_extents(-Re, -Re, Re, Re,
                             workspace_id=workspace_id,
                             union_with_current=False, margin=0.0)
        for st in ext.get("steps", []):
            st2 = dict(st)
            st2["step"] = "extent_" + str(st2.get("step"))
            steps.append(st2)
        if not ext.get("success"):
            # 有对象落在新范围外时会失败 → 退回并集（只扩不缩）
            warnings.append(
                "精确设置图纸范围失败（可能有图元落在新范围外）"
                "—— 已退回「只扩不缩」的并集模式。")
            ext2 = change_extents(-Re, -Re, Re, Re,
                                  workspace_id=workspace_id,
                                  union_with_current=True, margin=R * 0.25)
            for st in ext2.get("steps", []):
                st2 = dict(st)
                st2["step"] = "extent_union_" + str(st2.get("step"))
                steps.append(st2)
        # 记下实际生效的图纸范围（若并集扩过，会更大）
        Re_eff = Re
        if not ext.get("success") and ext2.get("success") and ext2.get("extent"):
            _ee = ext2["extent"]
            Re_eff = max(abs(_ee[0]), abs(_ee[2]), abs(_ee[1]), abs(_ee[3]))

        # ---- 3) 视图窗口：设 → 读回 → 修正（闭环）----
        # ---- 3) 视图窗口：按画布宽高比构造请求矩形，一次到位 ----
        # ★★★ 2026-09-30 定论（FPC-0510 6P 暴露，探针钉死映射规则）★★★
        #   `axlZoomPoints(nil 上右 下左)`：
        #     · res_y  = 请求 y（精确）
        #     · res_x1 = 请求 x1（左下锚定），宽度 = 请求高 × aspect
        #   ⇒ 请求矩形自己按 aspect 构造、且图纸范围已容下（见第 2 段），
        #     一次调用即精确居中。**保留一次读回校验**（不迭代，读回来
        #     只做数值判据 + 兜底一次修正）。
        view = None
        center = None
        iters = 0
        with AllegroSession(workspace_id) as s:
            unit = s.units()
            W = round(R * aspect, 4)          # 按画布宽高比构造请求矩形
            x1, y1, x2, y2 = -W, -R, W, R
            for i in range(2):                # 最多 2 次：一次到位 + 兜底修正
                iters = i + 1
                expr = S.build_zoom_points(
                    _mm_to_design(x1, unit), _mm_to_design(y1, unit),
                    _mm_to_design(x2, unit), _mm_to_design(y2, unit),
                )
                try:
                    raw = s.eval_skill(expr)
                except SkillCallError as e:
                    steps.append(_step_entry("zoom_points", expr, False,
                                             error=str(e)))
                    return _result(False, error=str(e), steps=steps,
                                   warnings=warnings)
                steps.append(_step_entry("zoom_points", expr, bool(raw),
                                         value=raw))
                if not raw:
                    warnings.append(f"axlZoomPoints 第 {iters} 次返回 nil。")
                    break

                time.sleep(0.4)                    # 让 Allegro 刷新视图
                try:
                    raw_box = s.eval_skill(S.build_zoom_bbox("nil"))
                except SkillCallError as e:
                    steps.append(_step_entry("zoom_bbox", S.build_zoom_bbox("nil"),
                                             False, error=str(e)))
                    break
                view = _bbox_mm_from_raw(raw_box, unit)
                steps.append(_step_entry(
                    "zoom_bbox", S.build_zoom_bbox("nil"), view is not None,
                    value=f"视图(mm) = {view}"))
                if view is None:
                    break

                cx = round((view[0] + view[2]) / 2.0, 6)
                cy = round((view[1] + view[3]) / 2.0, 6)
                center = (cx, cy)
                steps.append(_step_entry(
                    "zoom_center_check", "<view center vs origin>", True,
                    value=f"第 {iters} 次：中心 = ({cx:+.4f}, {cy:+.4f}) mm"))
                if abs(cx) <= tol and abs(cy) <= tol:
                    break
                # 兜底：左下锚定 → 反向平移剩余偏移再设一次（幅度 1% 微扰，
                # 避免「请求高 == 当前高」时 axlZoomPoints no-op）
                x1, y1, x2, y2 = -W - cx, -R - cy, W - cx, R - cy
                R = round(R * 1.01, 4)
                W = round(W * 1.01, 4)

            # ---- 4) 可选存盘 ----
            if save:
                r = save_drawing(workspace_id=workspace_id)
                for st in r.get("steps", []):
                    st2 = dict(st)
                    st2["step"] = "frame_save"
                    steps.append(st2)
                if not r.get("success"):
                    warnings.append(f"存盘失败: {r.get('error')}")

        centered = bool(center) and abs(center[0]) <= tol and abs(center[1]) <= tol
        if not centered:
            warnings.append(
                f"视图未完全居中（中心 {center}，阈值 {tol}mm）—— "
                f"通常是窗口宽高比变化导致，肉眼影响很小。")
        return _result(
            centered, unit=unit, half=R,
            extent=(-Re_eff, -Re_eff, Re_eff, Re_eff),
            view=view, view_center=center,
            iterations=iters, steps=steps, warnings=warnings,
            error=None if centered else f"视图中心偏离原点: {center}",
        )

    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps, warnings=warnings)
    except Exception as e:
        logger.exception("frame_drawing 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}",
                       steps=steps, warnings=warnings)


def _current_bbox_mm(
    s: AllegroSession, unit: str
) -> Optional[Tuple[float, float, float, float]]:
    """
    取当前 drawing 内容的外包框，换算成 mm。

    ★ 注意（实测）：`axlDBGetDesign()->bBox` 返回的是**绘图内容范围**
    （例如 [[-3.1,-2.85],[3.1,2.85]]），而 `axlDBGetDesign()->extents`
    在本机返回 None（该属性名不在 axlDBGetDesign 的属性表里）。
    所以这里读 bBox。

    :return: (x1,y1,x2,y2) 单位 mm；取不到返回 None
    """
    try:
        raw = s.eval_skill("axlDBGetDesign()->bBox")
    except SkillCallError:
        return None
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None

    try:
        (ax, ay), (bx, by) = raw[0], raw[1]
        from services.skillbridge_writer import from_unit

        vals = [
            from_unit(float(ax), unit), from_unit(float(ay), unit),
            from_unit(float(bx), unit), from_unit(float(by), unit),
        ]
        return (min(vals[0], vals[2]), min(vals[1], vals[3]),
                max(vals[0], vals[2]), max(vals[1], vals[3]))
    except (TypeError, ValueError, IndexError):
        return None


# ============================================================
# 单个动作：新建 / 清空图纸（全自动化，无需人工建图）
# ============================================================
def _dra_version_tag(path: str) -> Optional[str]:
    """
    读 .dra 文件 **offset 248** 处的版本标识串（"魔数"）。

    ★★★ 实测结构（2026-09-29，三种真实样本）★★★
        X.dra (17.2 自建)            @248  "all375891/28/..."
        0603rf_wv_12d.dra (官方符号)  @248  "allv17-08/24/.apdv16-02/22/.dbdv..."
        sym_template.dra (官方模板)   @248  "qvuv17-28/6/2dbdv16-512/5/..."

    ★ 规律：offset 248 是一个 **版本串链**，各段以 `/` 分隔，
      形如 `<prefix><major>-<rest>`，主版本号在第一个 `-` 之前。
      `all375891` 是特例 —— 它写成 `<prefix><数字串>` 无 `-`，
      其中圆整到 **375 == 37.5 == 17.2**（Cadence 内部版本号）。

    ★ 为什么不能"精确等于 all375891"（上一版 bug）：
      官方符号库 3277 个 `.dra` 全部被误判为老版本 —— 它们是
      `allv17-08/...`，同样是 17.2 格式，只是串写法不同。

    :return: offset 248 处的标识串（截断到第一个 NUL）；读不到返回 None
    """
    try:
        with open(path, "rb") as f:
            f.seek(248)
            raw = f.read(64)
    except OSError:
        return None
    if not raw:
        return None
    # 截到第一个不可打印字符
    out = bytearray()
    for b in raw:
        if 32 <= b < 127:
            out.append(b)
        else:
            break
    return out.decode("ascii", "replace") or None


#: 17.2 的主版本号（Cadence 内部把 17.2 记作 375 ≈ 37.5）
_CURRENT_MAJOR = 375


def _dra_major_version(path: str) -> Optional[int]:
    """
    从标识串解析出**主版本号**。

        "all375891/28/..."        -> 375   (17.2 当前版本)
        "allv17-08/24/.apd..."    -> 17    (17.2 官方符号 —— 需映射)
        "qvuv17-28/6/2..."        -> 17    (17.2 官方模板)
        "qvuv16-04..."            -> 16    (16.0 老版本 —— 会弹升级框)

    ★ 映射规则：Cadence 内部大版本 X.Y 有时记作 `X`，有时记作 `XY*25`
      （17.2 -> 375）。这里统一归一化为**大版本整数**：
        17.x -> 17、375 -> 17；16.x -> 16；15.x -> 15。

    :return: 大版本号（如 17）；无法解析返回 None
    """
    tag = _dra_version_tag(path)
    if not tag:
        return None

    head = tag.split("/", 1)[0]          # 取第一段

    # 情形 A：形如 allv17-08 / qvuv16-04  -> 提取 v 后面的数字
    import re
    m = re.search(r"[a-zA-Z]+(\d+)-", head)
    if m:
        return int(m.group(1))

    # 情形 B：形如 all375891 -> 提取尾部数字串再归一化
    m = re.search(r"(\d+)$", head)
    if m:
        digits = m.group(1)
        for w in (4, 3, 2):
            if len(digits) >= w:
                n = int(digits[:w])
                if n == 375:
                    return 17          # 17.2 的特殊内部编号
                if 10 <= n <= 30:
                    return n           # 直接就是大版本
        return None
    return None


def _is_current_version_dra(path: str) -> bool:
    """
    是否 **17.2 当前版本**格式 —— 只有这种模板打开时不会弹版本升级框。

    ★ 判定：解析出的主版本 == 17。
      （"all375891"、"allv17-08/..."、"qvuv17-28/..." 全部判 True；
        "qvuv16-04"、"dbdv15-71" 判 False）
    """
    return _dra_major_version(path) == 17


#: Cadence 官方 Package symbol 模板（17.2 自带，永远干净、永远是 SYMBOL 类型）
_OFFICIAL_SYM_TEMPLATE = (
    "D:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"
    "/share/pcb/pcb_lib/symbols/template/sym_template.dra"
)


def _find_official_symbol_template() -> Optional[str]:
    """
    定位 Cadence 自带的 **Package symbol 模板** `sym_template.dra`。

    ★ 这是最理想的模板来源：
        - 官方出品，**类型必定是 Package symbol**（不是 BOARD）
        - 随 17.2 安装，**版本必定是当前版本**（魔数 `qvuv17-28/...`）
        - 干净：不含任何图元，复制后就是一张空白封装图纸

    ★ 相比"扫工作目录找 .dra 赌它是 symbol"，这条路可靠得多。

    搜索顺序：
        1. 硬编码的默认安装路径
        2. 环境变量 `CDS_INST_DIR` 推断
        3. 在 `D:/`、`C:/` 常见根下浅层扫 `pcb_lib/symbols/template`

    :return: 模板绝对路径（正斜杠）；找不到返回 None
    """
    from pathlib import Path
    cands: List[str] = [_OFFICIAL_SYM_TEMPLATE]

    inst = os.environ.get("CDS_INST_DIR")
    if inst:
        cands.append(
            str(Path(inst) / "share/pcb/pcb_lib/symbols/template/sym_template.dra")
        )

    vroot = os.environ.get("CDS_INST_DIR")
    if vroot:
        cands.append(str(Path(vroot).parent / "share/pcb/pcb_lib/symbols/template/sym_template.dra"))

    for c in cands:
        c = str(c).replace("\\", "/")
        if os.path.isfile(c):
            return c
    return None


#: 可信的"图纸内容"属性
#: ★ `lines` / `shapes` / `rectangles` **不在** `axlDBGetDesign()` 的属性表里
#:   （读恒为 0，不代表没建成）→ 不能用来做污染判据。
_CONTENT_PROPS = ("pins", "text", "padstacks")


def _build_content_count_expr(prop: str) -> str:
    """
    生成"数某个内容属性有多少个元素"的 SKILL 表达式。
    实现落在 snippets 层（`S.build_content_count`），那边有离线语法自检覆盖。
    """
    return S.build_content_count(prop)


def _drawing_content_counts(s: AllegroSession) -> Dict[str, Optional[int]]:
    """
    读当前 drawing 的图元计数，用于判断**模板是否被污染**。

    :return: `{"pins": int|None, "text": int|None, "padstacks": int|None}`
             `None` = 该属性查询失败（★ 别把它当成"干净"，不要据此放行）
    """
    out: Dict[str, Optional[int]] = {}
    for prop in _CONTENT_PROPS:
        try:
            v = s.eval_skill(_build_content_count_expr(prop))
            out[prop] = int(v) if isinstance(v, (int, float)) else None
        except Exception as e:              # noqa: BLE001
            logger.debug("内容计数 %s 查询失败: %s", prop, e)
            out[prop] = None
    return out


def _allegro_working_dir(s: AllegroSession) -> Optional[str]:
    """
    取 Allegro 进程的**工作目录**（`getWorkingDir()`）。

    ★★ 为什么必须知道它（2026-09-29 实测踩中的大坑）★★
      `axlSaveDesign` 把图纸写到 **Allegro 的工作目录**，
      **完全不理会 `axlOpenDesign` 里给的那个目录**。

      Allegro 是从 `Tools/` 启动的，于是：
        建图         复制模板到 `Tools/test/NB_XXX.dra`           ✓
        `axlOpenDesign(".../Tools/test/NB_XXX.dra")` → 打开成功    ✓
        `axlSaveDesign` → **实际写到 `Tools/NB_XXX.dra`**          ✗（少一层 test）

      后果极具欺骗性：`new_symbol_drawing` 返回的 `dra_path` 是
      `Tools/test/...`，但那个文件**从头到尾没被写过**（md5 与官方
      `sym_template.dra` 一模一样）；回读它自然得到「0 pin 的空模板」，
      而真正的产物悄悄躺在 `Tools/` 下。

    :return: 正斜杠路径；查询失败返回 None
    """
    try:
        v = s.eval_skill("getWorkingDir()")
    except Exception as e:                      # noqa: BLE001
        logger.debug("getWorkingDir 查询失败: %s", e)
        return None
    return str(v).replace("\\", "/") if v else None


def _chdir_allegro(s: AllegroSession, target_dir: str) -> bool:
    """
    把 Allegro 的工作目录切到 `target_dir`，让后续 `axlSaveDesign` 落在正确位置。

    ★ 唯一走得通的办法是 `axlShell("cd <dir>")` —— 实测**真的会改**
      `getWorkingDir()`：`.../Tools` → `.../Tools/test` ✓，
      而且对 `cd` 这种内建命令**不弹框**（返回 t）。

      （试过且**不存在**的路：`setWorkingDir` / `axlSetWorkingDir` /
        `cdsSetWorkingDir` / `chdir` —— 全是 `undefined function`；
        `getWorkingDir()` 是存在且可用的只读版本。）

    :return: 是否切换成功 —— 以 **`getWorkingDir()` 复读**为准，不看返回值
    """
    import os as _os

    target_dir = str(target_dir).replace("\\", "/")
    if not _os.path.isdir(target_dir):
        return False
    cur = _allegro_working_dir(s)
    if cur and cur.rstrip("/").lower() == target_dir.rstrip("/").lower():
        return True
    try:
        s.eval_skill(f'axlShell("cd {target_dir}")')
    except Exception as e:                      # noqa: BLE001
        logger.debug("axlShell cd 失败: %s", e)
        return False
    after = _allegro_working_dir(s)
    return bool(after) and after.rstrip("/").lower() == target_dir.rstrip("/").lower()


def new_symbol_drawing(
    target_name: str,
    template_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    overwrite: bool = True,
    units: Optional[str] = "millimeters",
    accuracy: Optional[int] = 4,
) -> Dict[str, Any]:
    """
    ★★★ 全自动新建一张 **Package symbol** 类型图纸 ★★★

    等价于界面里的 `File → New → Drawing Type = Package symbol`，
    但**全程无 UI、无弹框**。

    ★★★ 为什么不能直接用 `axlKillDesign()` ★★★
        实测（2026-09-29）：
            axlKillDesign()              -> 建出 **BOARD** 类型（不是 SYMBOL）
            axlSetSymbolType("package")  -> 拒绝："can only be applied to
                                            symbol drawings"
            axlSetDesignType             -> **函数不存在**（文档写错了）
        所以「新建空图 + 改类型」这条路走不通 —— AXL 没有"建 symbol 图纸"的 API。

    ★★★ 因此采用【当前版本模板复制】法（已验证的可行路径）★★★
        1. 找一张**已是 Package symbol 且为当前版本**的 `.dra` 作为模板
        2. `shutil.copyfile` 复制成 `<target_name>.dra`（纯文件操作，无 UI）
        3. `axlOpenDesign(?design <副本> ?mode "wf")` 打开
           → 类型天然是 SYMBOL，不需要任何"改类型"调用
           → 当前版本格式，**不会弹版本升级框**

    ★ 模板选择规则（`template_path` 为 None 时自动挑选）：
        ① **Cadence 官方** `share/pcb/pcb_lib/symbols/template/sym_template.dra`
           —— 首选。类型必定是 Package symbol、版本必定当前、内容干净
        ② 退路：扫 `<工作目录>/*.dra`
        筛选条件：`_dra_major_version(p) == 17`
          ★ **按主版本号判**，不是精确匹配魔数串。`all375891` 和官方符号库的
            `allv17-08/...` 同为 17.2，精确匹配曾把 3277 个官方符号全判成"老版本"。
        若找不到 → **返回明确错误**，绝不拿老模板冒险弹版本升级框。

    ★ 污染校验（2026-09-29 新增）：
        打开后数 `pins` / `text` / `padstacks`。**退路模板非空就拒绝**
        （工作目录下常躺着 `probe_test.dra` 这类探针脏图，拿它建图会累积成
        `Duplicate pin number` → `Create symbol has been aborted.`）；
        官方模板只记 warning，不阻断（保住首选路径）。

    ★ 为什么必须先删旧文件：`axlOpenDesign` 打开**已存在**的文件时，
      即使 `?mode "wf"` 也会弹「File Exists. Overwrite?」框
      （`wf` 只管"丢弃未保存编辑"，**不管"文件是否已存在"**）。
      → 所以复制前先 `os.remove`，复制本身即覆盖。

    ★★ 单位切换（2026-09-29 新增，由用户裁决）★★
      官方 `sym_template.dra` 是 **mils / 精度 1**（整数 mil = 25.4 µm 步长），
      而金标准 `.dra` 实测是 **millimeters / 精度 0.0001**（0.1 µm 步长）。
      不切单位 → 所有 mm 坐标被量化到整数 mil：
          1.1 mm → 43 mil = 1.0922 mm   （±7.8 µm 系统性偏差）
          0.85 mm → 33 mil = 0.8382 mm
      这就是「建出的件与金标准总有 ~0.01 mm 级差异」的根因。
      → 打开图纸后、**画任何图元之前**调 `axlDBChangeDesignUnits`
        （文档 14dsnctl.html，官方示例恰为 `("millimeters" 4)`）。

    :param target_name: 新图纸名（不含 .dra 后缀）；也可含目录
    :param template_path: 模板 .dra 路径；None 时自动挑选
    :param overwrite: 目标已存在时是否覆盖（默认 True）
    :param units: 建好图纸后切到此单位；None = 保持模板原单位
                  （合理值："millimeters" / "mils"）
    :param accuracy: 精度位数；None = 保持原精度
                     （mm 建议 4；mils 建议 0~2）
    :return: {"success": bool, "design": str|None, "dra_path": str|None,
              "template": str|None, "steps": [...], "error": str|None}
    """
    import os
    import shutil

    steps: List[Dict[str, Any]] = []

    try:
        with AllegroSession(workspace_id) as s:
            # ---- 1) 当前工作目录（只用于汇报 / 退路模板扫描）----
            wdir = _drawing_dir(s) or P.TEST_DIR
            steps.append(_step_entry("current_drawing_dir", "axlGetDrawingName()",
                                     True, value=wdir))

            # ---- 2) 组织目标路径 ----
            #   ★ 项目约定（services/paths.py）：
            #     相对名  → 落 `Tools/test/`（测试图纸干活区）
            #     绝对路径 → 尊重调用方指定的位置
            name = str(target_name).strip()
            if name.lower().endswith(".dra"):
                name = name[:-4]
            if os.path.isabs(name):
                target_dra = name + ".dra"
            else:
                P.ensure_dirs()
                target_dra = os.path.join(P.TEST_DIR, name + ".dra")
            target_dra = P.as_posix(target_dra)
            steps.append(_step_entry("target_path", "resolve target", True,
                                     value=target_dra))

            # ---- 3) 选模板（★ 必须当前版本）----
            tpl = template_path
            tpl_source = "explicit"          # explicit | official | auto-scan
            if tpl:
                tpl = str(tpl).replace("\\", "/")
                if not os.path.isfile(tpl):
                    steps.append(_step_entry("pick_template", "explicit", False,
                                             error=f"模板不存在: {tpl}"))
                    return _result(False, steps=steps,
                                   error=f"指定的模板文件不存在：{tpl}")
                if not _is_current_version_dra(tpl):
                    steps.append(_step_entry(
                        "pick_template", "explicit", False,
                        error=f"模板版本过旧({_dra_version_tag(tpl)})，打开会弹升级框"))
                    return _result(
                        False, steps=steps,
                        error=f"模板 {os.path.basename(tpl)} 不是 17.2 当前版本格式"
                              f"（魔数 {_dra_version_tag(tpl)}），"
                              f"打开时会弹「release '16.0' will be updated...」升级框"
                              f"并卡死通道。请换一张当前版本的 Package symbol .dra。",
                    )
            else:
                # ★★★ 自动挑选顺序（优先级从高到低）★★★
                #   ① Cadence 官方 sym_template.dra —— 类型/版本都保证正确
                #   ② 工作目录下当前版本的 .dra —— 次优（可能是 BOARD）
                cands: List[str] = []
                official = _find_official_symbol_template()
                if official:
                    cands.append(official)
                    steps.append(_step_entry(
                        "pick_template", "official-sym-template", True,
                        value=official, error=None))
                else:
                    steps.append(_step_entry(
                        "pick_template", "official-sym-template", False,
                        error="未找到官方 sym_template.dra，回退到扫描工作目录",
                    ))

                try:
                    for fn in os.listdir(wdir):
                        if not fn.lower().endswith(".dra"):
                            continue
                        p = os.path.join(wdir, fn).replace("\\", "/")
                        if p.lower() == target_dra.lower():
                            continue          # 别拿目标自己当模板
                        if p.lower() in [c.lower() for c in cands]:
                            continue          # 去重
                        if _is_current_version_dra(p):
                            cands.append(p)
                except OSError as e:
                    if not cands:
                        return _result(False, steps=steps,
                                       error=f"扫描工作目录失败: {e}")

                if not cands:
                    steps.append(_step_entry(
                        "pick_template", "auto-scan", False,
                        error=f"{wdir} 下没有当前版本(17.x)的 .dra 可作模板"))
                    return _result(
                        False, steps=steps,
                        error=f"在 {wdir} 下没找到 17.2 当前版本的 .dra 模板，"
                              f"且未定位到官方 sym_template.dra。"
                              f"请用 template_path 显式指定一张 Package symbol 图纸。",
                    )
                # 官方模板优先，排在最前
                tpl = cands[0]
                tpl_source = "official" if (official and tpl == official) else "auto-scan"
                if len(cands) > 1:
                    steps.append(_step_entry("pick_template", "auto-scan", True,
                                             value=f"候选 {len(cands)} 个，取 {tpl}",
                                             error=None))

            # ---- 3.9) ★★★ 把 Allegro 工作目录切到图纸目录（2026-09-29 加）★★★
            #   为什么必做：`axlSaveDesign` 写到 **Allegro 工作目录**，
            #   不理会 `axlOpenDesign` 给的目录 → 产物会漏到 `Tools/` 而不是
            #   `Tools/test/`，而返回的 `dra_path` 却是后者（报告与实际不符）。
            #   详见 `_allegro_working_dir()` 的说明。不切成功就**明确告警**，
            #   绝不假装没事。
            _tdir = os.path.dirname(target_dra)
            _wd_before = _allegro_working_dir(s)
            _wd_ok = _chdir_allegro(s, _tdir)
            steps.append(_step_entry(
                "set_working_dir", f'axlShell("cd {_tdir}")', _wd_ok,
                value=f"{_wd_before} -> {_allegro_working_dir(s)}",
                error=None if _wd_ok
                else "未能切换 Allegro 工作目录 —— 存盘可能落到 Allegro 的启动目录",
            ))
            _wd_warn: List[str] = []
            if not _wd_ok:
                _wd_warn.append(
                    f"⚠️ 无法把 Allegro 工作目录切到 {_tdir} —— "
                    f"`axlSaveDesign` 会写到 Allegro 自己的工作目录"
                    f"（当前 {_wd_before}），图纸可能**不在**返回的 dra_path 上。")

            # ---- 4) ★★★ 生成"唯一临时名"（2026-09-30 修复「同名内存命中」）★★★
            #   实测根因：`axlOpenDesign` 打开一个**内存里已存在的同名设计**时，
            #   Allegro 直接激活内存副本、**不重读磁盘** ——
            #   表现是新建图的 pin / 丝印数量**整齐翻倍**：
            #       图纸已有 4 pin → 同名重建 → 再建 4 pin → .dra 里 8 pin
            #       （编号 1,2,3,4,1,2,3,4 各两套；assembly 2→4、丝印 3→6 …）
            #   且 Allegro **没有 "close design" API**
            #   （axlCloseDesign / axlDesignClose / axlDBCloseDesign 均
            #     undefined function，2026-09-30 实测），
            #   所以只能反过来绕：**用一个磁盘上必然不存在的随机名**打开模板，
            #   逼 Allegro 走"读盘"路径；校验/切单位完成后
            #   `axlSaveDesign` 再改成正式名。
            import uuid as _uuid_mod
            _tmp_stem = f"_forge_tmp_{_uuid_mod.uuid4().hex[:10]}"
            tmp_dra = P.as_posix(os.path.join(_tdir, _tmp_stem + ".dra"))

            # ---- 4.5) 删除目标 + 临时残留 + 残留锁文件 ----
            #   ★ axlOpenDesign 打开已存在文件会弹「File Exists. Overwrite?」
            #     临时文件用唯一名，实际不会被命中，删掉只是兜底。
            for victim in (target_dra, target_dra + ".lck",
                           tmp_dra, tmp_dra + ".lck"):
                if os.path.exists(victim):
                    if not overwrite and victim == target_dra:
                        return _result(False, steps=steps,
                                       error=f"目标已存在且 overwrite=False: {victim}")
                    try:
                        os.remove(victim)
                        steps.append(_step_entry("remove_old", f"os.remove({victim})",
                                                 True, value="removed"))
                    except OSError as e:
                        steps.append(_step_entry("remove_old", f"os.remove({victim})",
                                                 False, error=str(e)))
                        return _result(False, steps=steps,
                                       error=f"无法删除旧文件 {victim}: {e}")

            # ---- 5) 复制模板到**临时名** ----
            try:
                shutil.copyfile(tpl, tmp_dra)
                size = os.path.getsize(tmp_dra)
                steps.append(_step_entry("copy_template",
                                         f"copyfile({os.path.basename(tpl)} -> "
                                         f"{os.path.basename(tmp_dra)})",
                                         True, value=f"{size} bytes"))
            except OSError as e:
                steps.append(_step_entry("copy_template", "copyfile", False, error=str(e)))
                return _result(False, steps=steps, error=f"复制模板失败: {e}")

            # ---- 6) 打开**临时名**（★ 文件已存在，不会弹 Drawing Parameters 框）----
            expr = S.build_open_drawing(tmp_dra, "wf")
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("open_drawing", expr, False, error=str(e)))
                return _result(False, steps=steps, error=str(e))
            ok_open = bool(raw)
            steps.append(_step_entry("open_drawing", expr, ok_open, value=raw,
                                     error=None if ok_open else "axlOpenDesign 返回 nil"))
            if not ok_open:
                return _result(False, steps=steps, error="打开新图纸失败")

            # ---- 7) 校验类型真的是 SYMBOL ----
            dtype = dtype_full = None
            try:
                dtype = s.eval_skill("axlDesignType(nil)")
                dtype_full = s.eval_skill("axlDesignType(t)")
            except Exception:
                pass
            is_sym = isinstance(dtype, str) and dtype.upper() == "SYMBOL"
            steps.append(_step_entry(
                "verify_symbol", "axlDesignType(nil)", is_sym,
                value=f"{dtype} / {dtype_full}",
                error=None if is_sym
                else f"期望 SYMBOL，实际 {dtype}（模板可能不是 package symbol）",
            ))

            # ---- 7.5) ★ 单位 / 精度归一化（2026-09-29 加，用户裁决）----
            #   官方 sym_template.dra 是 **mils / 精度 1**（整数 mil），
            #   金标准 .dra 实测是 **millimeters / 精度 0.0001**。
            #   不切单位 → 所有 mm 坐标被量化到整数 mil：
            #       1.1 mm → 43 mil = 1.0922 mm   （±7.8 µm 系统偏差）
            #   必须在**画任何图元之前**切（文档明写会 rerun DRC）。
            _unit_warn: List[str] = []
            if units is not None or accuracy is not None:
                try:
                    _u_before = s.units()
                except Exception:                        # noqa: BLE001
                    _u_before = "unknown"
                _uexpr = S.build_change_units(units, accuracy)
                _u_err = None
                try:
                    s.eval_skill(_uexpr)
                except SkillCallError as e:
                    _u_err = str(e)
                except Exception as e:                   # noqa: BLE001
                    _u_err = f"{type(e).__name__}: {e}"
                # ★★ 返回值是 drcCount/nil，**不能用来判成败**
                #    （DRC 关闭时恒返回 nil）→ 一律复读单位校验。
                try:
                    _u_after = s.units()
                except Exception:                        # noqa: BLE001
                    _u_after = "unknown"
                _want = None
                if units:
                    _ul = str(units).lower()
                    _want = ("mm" if "milli" in _ul
                             else ("mil" if "mil" in _ul else _ul))
                _u_ok = (_u_err is None) and (_want is None or _u_after == _want)
                steps.append(_step_entry(
                    "set_units", _uexpr, _u_ok,
                    value=f"{_u_before} -> {_u_after}（accuracy={accuracy}）",
                    error=None if _u_ok
                    else (f"切单位后复读仍为 {_u_after}，期望 {_want}"
                          + (f"；调用异常：{_u_err}" if _u_err else "")),
                ))
                if not _u_ok:
                    _unit_warn.append(
                        f"⚠️ 未能把图纸单位切到 {units}/{accuracy}"
                        f"（复读 {_u_after}）—— 坐标会被量化到模板原精度，"
                        f"与金标准对拍会出现 ~0.01 mm 级系统偏差。")
            else:
                steps.append(_step_entry("set_units", "(skipped)", True,
                                         value="units=None 且 accuracy=None"))

            # ---- 7.9) ★★★ 另存为正式名（临时名 -> 目标名）★★★
            #   步骤 4 的收口：临时名只为绕开 axlOpenDesign 的「同名内存命中」。
            #   ★ 落点是 **Allegro 工作目录**（已在 3.9 切到图纸目录），
            #     所以传**裸名**即可落在 target_dra 旁边。
            _save_warn: List[str] = []
            _tgt_stem = os.path.basename(target_dra)[:-4]     # 去 .dra 后缀
            _save_expr = S.build_save_design(_tgt_stem)
            _save_err = None
            try:
                _save_ret = s.eval_skill(_save_expr)
            except Exception as e:                            # noqa: BLE001
                _save_err = f"{type(e).__name__}: {e}"
                _save_ret = None
            _saved_on_disk = os.path.isfile(target_dra)
            steps.append(_step_entry(
                "save_as", _save_expr, _saved_on_disk,
                value=f"ret={_save_ret!r}, 磁盘 {os.path.basename(target_dra)}"
                      f"{' ✓' if _saved_on_disk else ' ✗'}",
                error=None if _saved_on_disk
                else (f"另存后磁盘上仍没有 {target_dra}"
                      + (f"；调用异常：{_save_err}" if _save_err else "")),
            ))
            if not _saved_on_disk:
                _save_warn.append(
                    f"⚠️ 未能把临时图纸另存为 {target_dra} —— "
                    f"后续图元会落在临时设计上，产物可能不在预期路径。")
            else:
                # 临时文件已被"另存"接管，清掉磁盘残留（Allegro 可能仍占着）
                try:
                    if os.path.isfile(tmp_dra):
                        os.remove(tmp_dra)
                        steps.append(_step_entry("remove_tmp",
                                                 f"os.remove({tmp_dra})",
                                                 True, value="removed"))
                except OSError as e:
                    steps.append(_step_entry(
                        "remove_tmp", f"os.remove({tmp_dra})", False,
                        error=f"{e}（Allegro 可能仍持有句柄，"
                              f"下次 remove_old 会兜底）"))

            # ---- 8) ★ 污染校验：模板自带图元会把脏东西带进新图纸 ----
            #   为什么必须查：图元创建是**叠加**不是替换，模板里已有的 pin
            #   会与新建的 pin 撞号 → `Duplicate pin number` →
            #   `Create symbol has been aborted.`（2026-09-29 实测踩中）
            #   ★ 已知风险源：工作目录下的 `probe_test.dra` / `X.dra` 这类
            #     探针图纸，本身就被反复读写污染过。
            counts = _drawing_content_counts(s)
            polluted = [f"{k}={v}" for k, v in counts.items()
                        if isinstance(v, int) and v > 0]
            steps.append(_step_entry(
                "check_template_pollution",
                "count(pins/text/padstacks)",
                not polluted,
                value=str(counts),
                error=("模板自带图元：" + ", ".join(polluted)) if polluted else None,
            ))
            if polluted and tpl_source != "official":
                # 官方模板即便有残留也只警告（它是首选路径，不该因一次查询被否掉）；
                # 退路模板（工作目录扫描到的）一律拒绝 —— 大概率是探针脏图。
                moved = _quarantine_file(target_dra, reason="polluted-template")
                return _result(
                    False, dra_path=None, template=tpl, steps=steps,
                    warnings=_wd_warn,
                    error=f"模板 {os.path.basename(tpl)} 自带图元"
                          f"（{', '.join(polluted)}），用它建图会累积成"
                          f"「重复 pin 号 → 编译中止」。已把该副本隔离到 "
                          f"{moved or '.trash'}。"
                          f"请改用官方 sym_template.dra（首选），"
                          f"或用 template_path 显式指定一张空白 Package symbol 图纸。",
                )

            design = None
            try:
                design = s.eval_skill("axlCurrentDesign()")
            except Exception:
                pass

            # ---- 9) 归集本次运行甩出来的日志 ----
            logs = _collect_logs_step(steps, os.path.dirname(target_dra),
                                      P.TEST_DIR)

            return _result(is_sym, design=design, dra_path=target_dra,
                           template=tpl, steps=steps, logs=logs,
                           warnings=_wd_warn + _unit_warn + _save_warn,
                           error=None if is_sym
                           else f"图纸类型不是 SYMBOL 而是 {dtype}")
    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("new_symbol_drawing 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


def new_drawing(
    workspace_id: Optional[str] = None,
    set_units: Optional[str] = None,
    set_accuracy: Optional[int] = None,
    extents_mm: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """
    ★ 新建一张全新的空图纸（调 `axlKillDesign()`，零弹窗）

    ★★★ 重要限制（2026-09-29 实测）：`axlKillDesign()` 建出的是 **BOARD** 类型，
        不是 SYMBOL。且没有 API 能改类型：
            axlSetSymbolType("package") -> 拒绝（"can only be applied to
                                           symbol drawings"）
            axlSetDesignType            -> **函数不存在**
        → **要建 Package symbol 图纸，请用 `new_symbol_drawing()`**
          （走"当前版本模板复制"路线，见其 docstring）。

    本函数适合的场景：
        - 建一张普通 **board** 图纸
        - 只是想**把当前图纸重置为空**（清掉污染的图元）

    :param set_units:  可选，建完后设单位："millimeters" / "mils" / "inches"
    :param extents_mm: 可选，建完后设范围 [x1,y1,x2,y2]，单位 **mm**
    :return: {"success": bool, "design": str|None, "steps": [...], "error": str|None}
    """
    steps: List[Dict[str, Any]] = []
    try:
        with AllegroSession(workspace_id) as s:
            # ---- 1) 新建（★ 零参数，最安全）----
            expr = S.build_kill_design()
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("kill_design", expr, False, error=str(e)))
                return _result(False, error=str(e), steps=steps)
            ok = bool(raw)
            steps.append(
                _step_entry("kill_design", expr, ok, value=raw,
                            error=None if ok else "axlKillDesign 返回 nil（未开新图）")
            )
            if not ok:
                return _result(False, steps=steps, error="新建图纸失败")

            # ---- 2) 可选：设单位 / 精度 ----
            #   ★ 返回值是 drcCount/nil，**不能当成败判据**（DRC 关闭时恒 nil）
            #     → 复读 axlDBGetDesignUnits() 才算数。
            if set_units or set_accuracy is not None:
                uexpr = S.build_change_units(set_units, set_accuracy)
                try:
                    s.eval_skill(uexpr)
                    u_after = s.units()
                    _uwant = None
                    if set_units:
                        _ul = str(set_units).lower()
                        _uwant = ("mm" if "milli" in _ul
                                  else ("mil" if "mil" in _ul else _ul))
                    u_ok = _uwant is None or u_after == _uwant
                    steps.append(_step_entry(
                        "set_units", uexpr, u_ok,
                        value=f"{u_after}（accuracy={set_accuracy}）",
                        error=None if u_ok else f"复读仍为 {u_after}，期望 {_uwant}"))
                except SkillCallError as e:
                    steps.append(_step_entry("set_units", uexpr, False, error=str(e)))

            # ---- 3) 可选：设范围 ----
            if extents_mm and len(extents_mm) >= 4:
                try:
                    eexpr = S.build_extent_set(*[float(v) for v in extents_mm[:4]])
                    eraw = s.eval_skill(eexpr)
                    steps.append(_step_entry("set_extents", eexpr, True, value=eraw))
                except Exception as e:
                    steps.append(_step_entry("set_extents", "build_extent_set", False,
                                             error=str(e)))

            # ---- 4) 回报新图纸名 ----
            design = None
            try:
                design = s.eval_skill("axlCurrentDesign()")
            except Exception:
                pass

            return _result(True, design=design, steps=steps, error=None)
    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("new_drawing 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


def clear_drawing(
    include_pins: bool = True,
    include_text: bool = True,
    include_geometry: bool = True,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    清空当前图纸里的**图元**（保留图纸本体、单位、范围）。

    与 `new_drawing()` 的区别：
      - `new_drawing()`   = 换一张新图纸（单位/范围可能继承后需重设）
      - `clear_drawing()` = 图纸不动，只把内容删干净

    ★ 典型用途：解决**图元叠加污染** ——
      Allegro 的图元创建是「叠加」不是「替换」，同一张图纸反复生成会累积
      重复 pin，最终 `axlCompileSymbol` 报 `duplicate pin numbers`。
      在 `build_footprint` 前先清一遍即可根治。

    分层策略（两层 API 互补）：
      - 几何（线/矩形/形状/文字层内容）→ `axlDeleteByLayer`（高效，按层删）
        ⚠️ 但官方明确 **PIN / VIA_CLASS / DRC_ERROR_CLASS 会被忽略**
      - pin / text → `axlDeleteObject`（补 axlDeleteByLayer 的盲区）
        ★ 必须**先 mapcar 拍快照再删**，否则边遍历边改集合会让游标失效

    :return: {"success": bool, "deleted": [...], "steps": [...], "error": str|None}
    """
    steps: List[Dict[str, Any]] = []
    deleted: List[Dict[str, Any]] = []
    try:
        with AllegroSession(workspace_id) as s:
            exprs = S.build_clear_drawing(
                include_pins=include_pins,
                include_text=include_text,
                include_geometry=include_geometry,
            )
            for e in exprs:
                tag = "delete_by_layer" if "axlDeleteByLayer" in e \
                    else ("delete_" + (e.split("axlDBGetDesign()->")[1].split(")")[0]
                                        if "axlDBGetDesign()->" in e else "obj"))
                try:
                    raw = s.eval_skill(e)
                    steps.append(_step_entry(tag, e, True, value=raw))
                    deleted.append({"step": tag, "count": raw})
                except SkillCallError as ex:
                    steps.append(_step_entry(tag, e, False, error=str(ex)))
            return _result(True, deleted=deleted, steps=steps, error=None)
    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("clear_drawing 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 单个动作：存盘
# ============================================================
def save_drawing(
    path: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    保存当前 drawing。path 为 None 时存回原位。

    ★ 内部固定传 `?noConfirm t`：不传的话 Allegro 会弹交互确认框，
      把 SkillBridge 连接卡死（实测踩过，见 build_save 的 docstring）。
    """
    steps: List[Dict[str, Any]] = []
    try:
        with AllegroSession(workspace_id) as s:
            expr = S.build_save(path)
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("save", expr, False, error=str(e)))
                return _result(False, error=str(e), steps=steps)
            ok = bool(raw) and raw is not None
            steps.append(
                _step_entry("save", expr, ok, value=raw,
                            error=None if ok else "axlSaveDesign 返回 nil")
            )
            return _result(ok, path=path, steps=steps,
                           error=None if ok else "保存失败")
    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("save_drawing 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 出 .psm：编译封装符号
# ============================================================
def export_psm(
    symbol_name: Optional[str] = None,
    symbol_type: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ★★★ 出 `.psm` —— 编译并落盘封装符号 ★★★

    等价于 Allegro 界面里的 `File → Create Symbol`。

    依赖 `axlCompileSymbol`（文档 14dsnctl.html 第 2341 行）：
        axlCompileSymbol( ?symbol t_name ?type t_type ) ⇒ t_symbolName /nil

    ★★★★★★ `axlCompileSymbol` **恒返回 nil**（2026-09-29 实测定论）★★★★★★
        文档说成功时返回符号名 —— **本机实际不返回**：
            ?symbol "NB_XTAL..." ?type "package"  → None
            ?symbol 'NB_XTAL...  ?type "package"  → None
        但编译**确实成功**：`.psm` 落盘（8840 B），日志写着
            Create Symbol of type: PACKAGE / Create symbol completed.
        → 返回值**不能**当成功判据。旧代码就是栽在这：
          把成功误判为失败 → 走 `_rollback_quarantine()` →
          **刚产出的 `.psm` 被上一次的备份盖掉**（"产物凭空消失"的真凶）。
        现在改成**以证据为准**（任一成立即成功）：
            ① 返回值非 nil
            ② 编译前后 `.psm` 快照出现"新文件 / mtime 变化"
            ③ 日志含 "Create symbol completed" 且无 ERROR
        返回值的 `evidence` 字段会列出本次到底靠哪条判成功的。

    ★★★ 致命陷阱（2026-09-29 实测踩中）★★★
        `?symbol` 传 **nil** 会弹出「另存为」对话框 → 通道卡死！
        务必显式给名字（或留空让它默认用当前图纸名）。

    ★★★ 旧产物保护（2026-09-29 修复）★★★
        目标 `.psm` 已存在时，`axlCompileSymbol` 会弹
        「File Exists. Overwrite?」，而它**没有 `?noConfirm` 开关**，
        API 层无法抑制 → 只能编译前把旧文件挪走。
        挪走后如果**直接删**，一旦编译失败（图纸污染 / 缺位号），
        上一次的成功产物就永久没了 —— 真实踩过（8640 B 的 `.psm` 就这么丢了）。
        → 现在统一**移入 `.trash/` 隔离区**：
            编译成功 → 隔离件留作备份，路径见返回值的 `previous_psm_backup`
            编译失败 → 自动原样搬回，绝不让产物凭空消失

    ★★★★★★ 「弹框阻断」实测定论（2026-09-29，在线跑通）★★★★★★
        连续两次调用（第二次目标 `.psm` 已存在 = 覆盖场景）
        耗时 **0.6s / 0.7s，全程无对话框**，产出 8644 B 的 `.psm`。
        → 弹框已被两层机制压住：
            ① 会话级 `NOCONFIRM` / `noconfirm_savedb` 变量族（自动设/恢复）
            ② 编译前把旧产物移入隔离区（根本不给它"已存在"的机会）
        → **弹框不是主要矛盾**。长期以来的真实阻断源是 Edit Check 报错，
          现已由 `_scan_symbol_compile_log()` 翻译进 `log_diagnosis`。

    前置条件（Allegro 会做 edit check，不满足会返回 nil）：
      1. 当前是 symbol editor（`axlDesignType(nil)` == "SYMBOL"）
      2. 图纸里**必须有位号文字**（`REF DES/*` 层），否则报 SPMHCS-1
      3. **pin 编号不能重复**，否则报 SPMHA1-298 并直接中止
      4. 图纸已保存过（有实际的文件名）

    ★ 产物落点 = Allegro 的**当前工作目录**，未必是 `.dra` 所在目录
      （2026-09-29 实测：`.dra` 在 `Tools/test/`，产物却落在 `Tools/`）。
      → 调用方必须认返回的 `psm_path`，不要按 `.dra` 目录去猜。
        构建链已由 `new_symbol_drawing` 的 `set_working_dir` 步骤把工作目录
        对齐到图纸目录，让 `.psm` 与 `.dra` 同处一地。

    :param symbol_name: 编译后的符号名；None 时用当前图纸名
    :param symbol_type: "package" / "mechanical" / "format" / "shape" / "flash"；
                        None 时用当前图纸类型
    :return: {"success": bool, "symbol": str|None, "psm_path": str|None,
              "psm_exists": bool, "psm_size": int|None,
              "evidence": [str],           # 本次靠哪条证据判成功
              "previous_psm_backup": str|None,
              "log_diagnosis": {"log_file","entries","raw_errors","summary",
                                "started","completed"},
              "note": str|None, "steps": [...], "error": str|None}
    """
    steps: List[Dict[str, Any]] = []
    try:
        with AllegroSession(workspace_id) as s:
            # ---- 0) 前置检查：必须是 symbol editor ----
            try:
                dtype = s.eval_skill("axlDesignType(nil)")
            except Exception as e:
                steps.append(_step_entry("check_editor", "axlDesignType(nil)",
                                         False, error=str(e)))
                return _result(False, error=str(e), steps=steps)
            is_symbol = isinstance(dtype, str) and dtype.upper() == "SYMBOL"
            steps.append(
                _step_entry("check_editor", "axlDesignType(nil)", is_symbol,
                            value=dtype,
                            error=None if is_symbol
                            else f"当前不是 symbol editor（{dtype}）")
            )
            if not is_symbol:
                return _result(
                    False, steps=steps,
                    error=f"axlCompileSymbol 只能在 symbol editor 里用，"
                          f"当前设计类型是 {dtype}",
                )

            # ---- 0b) 工作目录**前置断言**（★ 折进入口，不做后置刷新）----
            #   `create symbol` 把 `.psm` 写到 Allegro 的**工作目录**，
            #   不是 `.dra` 目录。CWD 一旦被别的操作改掉，产物就会**静默错位**
            #   （2026-09-29 实测踩过：`.dra` 在 `Tools/test/`，
            #    `.psm` 却落到 `Tools/`，而且返回的 `dra_path` 看着完全正常）。
            #
            #   ★ 为什么是"前置断言"而不是"编译后刷新一次"：
            #     后置刷新**只能改 CWD，救不回已经写错位置的文件**；
            #     防错位的那一刀必须在编译**之前**落下。
            #     成本 = 1 次 `getWorkingDir()` 往返，幂等、不弹框。
            #   ★ 兜底仍在 2b：最终落点以「编译前后 .psm 快照差异」为准，
            #     即使这里纠偏失败，也不会把错位当成成功。
            try:
                want_dir = _drawing_dir(s)
            except Exception:               # noqa: BLE001
                want_dir = None
            got_dir = _allegro_working_dir(s)
            same_wd = bool(
                want_dir and got_dir
                and os.path.normcase(str(got_dir).rstrip("/"))
                == os.path.normcase(str(want_dir).rstrip("/"))
            )
            wd_fixed = False
            if want_dir and not same_wd:
                wd_fixed = _chdir_allegro(s, want_dir)
                if wd_fixed:
                    got_dir = _allegro_working_dir(s) or got_dir
            steps.append(_step_entry(
                "assert_working_dir",
                "getWorkingDir() == drawing dir",
                same_wd or wd_fixed,
                value=f"cwd={got_dir} | dra={want_dir}"
                      + ("（已 cd 纠偏）" if wd_fixed else ""),
                error=None if (same_wd or wd_fixed)
                else "工作目录与图纸目录不一致，且 cd 纠偏失败 —— "
                     ".psm 可能落到别处（落点仍以快照证据为准）",
            ))

            # ---- 1) 当前图纸名（用于推断产出文件名）----
            dwg_name = None
            try:
                dwg_name = s.eval_skill("axlCurrentDesign()")
            except Exception:
                pass

            target = symbol_name or (dwg_name if isinstance(dwg_name, str)
                                     else None)
            typ = symbol_type or "package"

            # ---- 1b) drawing 目录（旧产物隔离、编译日志定位都要用）----
            #   ★ 产物目录**不可靠**：`create symbol` 把 .psm 输出到 Allegro 的
            #     当前工作目录，未必等于 .dra 所在目录（2026-09-29 实测：
            #     .dra 在 Tools/test/，产物却落在 Tools/）。
            #     所以这里同时留 drawing 目录与其父目录两个候选。
            #   ★ ddir 直接复用 0b 里已取到的 want_dir，省一次 SKILL 往返。
            ddir = want_dir
            _psm_dirs = [ddir, os.path.dirname(ddir) if ddir else None]

            # ---- 2) 编译 ----
            # ★★★ 覆盖保护（2026-09-29 实测踩中）★★★
            #   axlCompileSymbol 只有 ?symbol / ?type 两个参数，**没有 ?noConfirm**。
            #   若目标 .psm 已存在，Allegro 会弹：
            #       "xxx.psm: File Exists. Overwrite?  [Yes] [No]"
            #   这个模态框会把 SkillBridge 通道彻底卡死。
            #   唯一的规避办法是**编译前先把旧 .psm 挪开**。
            #   ★★★ 但**不能直接 `os.remove`** ★★★
            #     编译一旦失败（图纸污染 / 缺位号），上一次的成功产物就永久没了。
            #     2026-09-29 真实踩过：`.psm` 成功产出过一次（8640 B），
            #     重跑时被这里的 `os.remove` 删掉、随后编译失败 → 产物彻底丢失。
            #   → 改为**移入 `.trash/` 隔离区**：成功就留作备份，失败就搬回来。
            quarantined: Optional[Tuple[str, str]] = None
            old_psm: Optional[str] = None
            if target:
                try:
                    # 产物名可能取自 ?symbol，也可能取自 drawing 名 → 两个都查。
                    # 大小写也都要试：实测 ?symbol "NB_XTAL..." 落盘成小写
                    # "nb_xtal....psm"（Linux 风格小写化）。
                    bases: List[str] = []
                    for nm in (symbol_name, dwg_name):
                        if not isinstance(nm, str) or not nm:
                            continue
                        b = os.path.basename(str(nm).replace("\\", "/"))
                        if b.lower().endswith(".psm"):
                            b = b[:-4]
                        if "." in b:
                            b = b.rsplit(".", 1)[0]
                        for cb in (b, b.lower(), b.upper()):
                            ps = cb + ".psm"
                            if ps not in bases:
                                bases.append(ps)
                    for d in filter(None, _psm_dirs):
                        for ps in bases:
                            cand = os.path.join(d, ps)
                            if os.path.exists(cand):
                                old_psm = cand.replace("\\", "/")
                                break
                        if old_psm:
                            break
                except Exception as e:          # noqa: BLE001
                    logger.debug("export_psm: 定位旧 .psm 失败（继续尝试）: %s", e)

            if old_psm:
                moved = _quarantine_file(old_psm, reason="pre_compile")
                if not moved:
                    steps.append(_step_entry("quarantine_old_psm",
                                             f"quarantine {old_psm}", False,
                                             error="隔离旧产物失败，已中止编译"))
                    return _result(
                        False, steps=steps,
                        error=f"无法把旧产物 {old_psm} 移入隔离区，为免丢失已中止编译。"
                              f"（若直接删，编译失败就永久没了。）"
                              f"请先手工备份或移走该文件再重试。",
                    )
                quarantined = (old_psm, moved)
                steps.append(_step_entry("quarantine_old_psm",
                                         f"quarantine {os.path.basename(old_psm)}",
                                         True, value=f"{old_psm} -> {moved}"))

            def _record_failure_diag() -> Dict[str, Any]:
                """
                编译失败时读 `<symbol>.log`，把错误码翻译成人话并记进 steps。

                ★ 存在的意义：`axlCompileSymbol` 返 nil 时**不给原因**，
                  不读日志就只能猜 —— 实测证明"猜"会猜偏（曾长期误判为
                  "弹框阻断"，真凶其实是缺位号 / pin 编号重复）。
                """
                diag = _scan_symbol_compile_log(_psm_dirs, target)
                steps.append(_step_entry(
                    "scan_compile_log", "scan <symbol>.log",
                    bool(diag.get("entries")),
                    value=diag.get("log_file") or "(未找到日志文件)",
                    error=None if diag.get("entries")
                    else "未找到编译日志，或日志里没有 ERROR/WARNING 条目",
                ))
                return diag

            def _rollback_quarantine() -> None:
                """把隔离区的旧 .psm 原样搬回来（编译失败时绝不让产物凭空消失）。"""
                if not quarantined:
                    return
                back = _restore_quarantined(*quarantined)
                steps.append(_step_entry(
                    "restore_old_psm", "restore quarantined .psm", back,
                    value=quarantined[0],
                    error=None if back
                    else f"还原失败，备份仍在 {quarantined[1]}",
                ))

            # ★★★ 编译前快照（隔离动作已做完，此刻的目录状态才是"编译前"）★★★
            #   之后靠"快照差异"来证明编译是否真的产出了 .psm。
            pre_psm_snap = _snapshot_psms(_psm_dirs)

            expr = S.build_compile_symbol(symbol_name, typ)
            try:
                raw = s.eval_skill(expr)
            except SkillCallError as e:
                steps.append(_step_entry("compile_symbol", expr, False,
                                         error=str(e)))
                diag = _record_failure_diag()
                _rollback_quarantine()
                err = (str(e) + "（若为超时，说明弹了对话框 —— "
                       "检查是否误传了 ?symbol nil）")
                if diag.get("summary"):
                    err += f"｜编译日志：{diag['summary']}"
                if quarantined:
                    err += "（原有 .psm 已还原）"
                return _result(
                    False, steps=steps, log_diagnosis=diag,
                    previous_psm_backup=quarantined[1] if quarantined else None,
                    error=err,
                )

            # ---- 2b) 编译是否成功：**以证据为准** ----
            # ★★★ `axlCompileSymbol` 恒返回 nil（2026-09-29 实测定论）★★★
            #   字符串形式 ?symbol "NAME" ?type "package" → nil
            #   SKILL 符号形式 ?symbol 'NAME               → nil
            #   但编译**其实成功**、`.psm` **确实落盘**（8840 B）：
            #       Create Symbol of type: PACKAGE
            #       Name = nb_xtal4_3d2x2d5x0d7.psm
            #       Create symbol started. / Create symbol completed.
            #   旧代码拿返回值当判据 → 把成功的编译误判成失败 →
            #   触发 _rollback_quarantine()，**用旧备份把刚产出的 .psm 盖掉**。
            #   这是"产物凭空消失"的真凶之一。
            #
            #   新判据 = 三条**互相独立**的证据，任一成立即判成功：
            #     ① 返回值非 nil（Allegro 哪天回心转意了仍然认）
            #     ② 编译前后 `.psm` 快照出现"新文件 / mtime 变化"（最硬）
            #     ③ 编译日志含 "Create symbol completed" 且无 ERROR/WARNING
            raw_ok = _is_creation_ok(raw)
            post_psm_snap = _snapshot_psms(_psm_dirs)
            new_psms: List[str] = [
                p for p, sig in post_psm_snap.items()
                if pre_psm_snap.get(p) != sig
            ]
            new_psms.sort(key=lambda p: post_psm_snap[p][0], reverse=True)

            diag = _scan_symbol_compile_log(_psm_dirs, target)
            log_completed = bool(diag.get("completed"))
            log_clean = log_completed and not diag.get("entries")

            evidence: List[str] = []
            if raw_ok:
                evidence.append("返回值非 nil")
            if new_psms:
                evidence.append(f"{len(new_psms)} 个 .psm 新落盘/被更新")
            if log_clean:
                evidence.append("日志含 'Create symbol completed' 且无 ERROR")
            ok = bool(evidence)

            # 产物名：优先用**本次真实落盘的那个**（比 target 更可信）
            got_name = raw if isinstance(raw, str) else None
            if not got_name and new_psms:
                got_name = os.path.basename(new_psms[0])
            got_name = got_name or target

            steps.append(
                _step_entry("compile_symbol", expr, ok, value=raw,
                            error=None if ok
                            else "无任何成功证据：返回值 nil、目录里没出现新 "
                                 ".psm、日志里没有 'Create symbol completed'")
            )
            if evidence:
                steps.append(_step_entry(
                    "compile_evidence", "success evidence", True,
                    value="；".join(evidence)))

            if not ok:
                diag = _record_failure_diag()
                _rollback_quarantine()
                err = ("编译失败：没有任何成功证据（返回值 nil，"
                       "目录里也没出现新的 .psm，编译日志里没有完成记录）。"
                       "常见原因：缺位号文字、pin 编号重复、图纸未保存过。")
                if diag.get("summary"):
                    err += diag["summary"]
                    for e2 in diag.get("entries", []):
                        if e2.get("hint"):
                            err += f"｜处方：{e2['code']} → {e2['hint']}"
                else:
                    err += ("按经验依次排查："
                            "1) REF DES 层有没有位号文字（缺了报 SPMHCS-1）；"
                            "2) pin 编号是否重复（报 SPMHA1-298）；"
                            "3) 图纸是否已保存过。"
                            "★ 这三条都是**数据问题**，与弹框无关。")
                if quarantined:
                    err += "（原有 .psm 已还原）"
                return _result(
                    False, symbol=got_name, steps=steps, log_diagnosis=diag,
                    previous_psm_backup=quarantined[1] if quarantined else None,
                    error=err,
                )

            # ---- 3) 定位产出的 .psm ----
            #   优先级：**本次编译真实产出/更新的那个** > 返回值里的路径 >
            #           图纸目录推算
            psm_path = None
            if new_psms and os.path.exists(new_psms[0]):
                # 证据优先：快照差异里"确认被写过"的文件，比返回值可靠
                psm_path = new_psms[0]
            if psm_path is None and isinstance(raw, str) and raw.lower().endswith(".psm"):
                # 返回值就是路径，但可能是 Allegro 风格的反斜杠/大小写，
                # 用 _fix_windows_case 校正后确认存在
                cand = raw.replace("\\", "/")
                if os.path.exists(cand):
                    psm_path = cand
                else:
                    fixed = AllegroSession._fix_windows_case(cand)
                    if os.path.exists(fixed):
                        psm_path = fixed
                    else:
                        # 路径有效但文件还没落盘（极少见）—— 仍信任返回值
                        psm_path = fixed

            if psm_path is None:
                try:
                    ddir = _drawing_dir(s)
                    base = os.path.basename(got_name) if got_name else None
                    if ddir and base:
                        base = base if base.lower().endswith(".psm") \
                            else base + ".psm"
                        for cand in (
                            os.path.join(ddir, base),
                            os.path.join(os.path.dirname(ddir), base),
                        ):
                            if os.path.exists(cand):
                                psm_path = cand
                                break
                except Exception:
                    pass

            exists = bool(psm_path) and os.path.exists(psm_path)
            steps.append(
                _step_entry("locate_psm", "resolve .psm path", psm_path is not None,
                            value=psm_path,
                            error=None if psm_path
                            else "编译成功但未能定位 .psm 路径")
            )
            size = None
            if exists:
                try:
                    size = os.path.getsize(psm_path)
                except Exception:
                    size = None

            # ---- 4) 成功也要看日志：可能有 WARNING（如 duplicate pin number）----
            #   diag 已在 2b 里取过，直接复用，不重复扫盘。
            if diag.get("entries"):
                steps.append(_step_entry(
                    "scan_compile_log", "scan <symbol>.log", True,
                    value=diag.get("summary"),
                ))

            # ---- 5) 归集日志（编译日志 <symbol>.log、allegro.jrl 等）----
            try:
                ddir2 = _drawing_dir(s)
            except Exception:               # noqa: BLE001
                ddir2 = None
            logs = _collect_logs_step(steps, ddir2, os.path.dirname(ddir2)
                                      if ddir2 else None, P.TEST_DIR)

            # ---- 6) 产物落点提示 ----
            #   ★ create symbol 把 .psm 输出到 Allegro 的**当前工作目录**，
            #     未必等于 .dra 所在目录（2026-09-29 实测踩过：
            #     .dra 在 Tools/test/，产物却落在 Tools/）。
            #     调用方必须认 psm_path，不要按 .dra 目录去猜。
            note = None
            if psm_path and ddir2:
                try:
                    same = (os.path.normcase(os.path.dirname(psm_path))
                            == os.path.normcase(ddir2))
                except Exception:           # noqa: BLE001
                    same = True
                if not same:
                    note = (f"注意：.psm 落在 {os.path.dirname(psm_path)}，"
                            f"与 .dra 所在目录 {ddir2} 不同 —— "
                            f"create symbol 的输出目录是 Allegro 的工作目录。")

            return _result(True, symbol=got_name, psm_path=psm_path,
                           psm_exists=exists, psm_size=size, steps=steps,
                           logs=logs, log_diagnosis=diag, note=note,
                           evidence=evidence,
                           previous_psm_backup=quarantined[1] if quarantined else None)
    except ConnectionError as e:
        return _result(False, error=str(e), steps=steps)
    except Exception as e:
        logger.exception("export_psm 未预期异常")
        return _result(False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 环境探测：Allegro 是否可用、当前 drawing 状态
# ============================================================
def inspect_environment(workspace_id: Optional[str] = None) -> Dict[str, Any]:
    """
    检查 SkillBridge 连通性与当前 drawing 状态。

    这是所有生成操作前的"体检"，也是 AI 判断能不能动手的依据。
    """
    steps: List[Dict[str, Any]] = []
    try:
        with AllegroSession(workspace_id) as s:
            info: Dict[str, Any] = {"connected": True}

            unit = s.units()
            info["units"] = unit
            steps.append(_step_entry("units", "(axlDBGetDesignUnits)", unit != "unknown", unit))

            try:
                name = s.call("axlCurrentDesign")
                info["design_name"] = str(name) if name else None
                steps.append(_step_entry("current_design", "(axlCurrentDesign)", True, name))
            except SkillCallError as e:
                info["design_name"] = None
                steps.append(_step_entry("current_design", "(axlCurrentDesign)", False, error=str(e)))

            is_pkg = s.is_package_symbol()
            info["is_package_symbol"] = is_pkg

            # 关键警告
            warnings: List[str] = []
            if unit == "unknown":
                warnings.append(
                    "读不到 design 单位 —— 可能没有打开任何 drawing，"
                    "或 Allegro 会话未就绪。生成类操作会失败。"
                )
            if is_pkg is False:
                warnings.append(
                    "当前 drawing **不是** Package symbol。"
                    "axlDBCreatePin 只能用在 package/mechanical symbol 上。"
                )
            if is_pkg is None:
                warnings.append(
                    "无法判断 drawing 类型 —— 请人工确认是 Package symbol，"
                    "否则放 pin 会失败。"
                )
            info["warnings"] = warnings

            return _result(True, **info, steps=steps, error=None)

    except ConnectionError as e:
        return _result(
            False,
            connected=False,
            error=str(e),
            steps=steps,
        )
    except Exception as e:
        logger.exception("inspect_environment 未预期异常")
        return _result(False, connected=False, error=f"{type(e).__name__}: {e}", steps=steps)


# ============================================================
# 组合动作：一次建完整封装
# ============================================================
#: 轮廓参数：单个矩形，或多段矩形列表（多段用于"外框 + 1 脚标识"这类拆分）
#:   ★ 单个元素可以是**矩形** `{x1,y1,x2,y2,width}`，
#:     也可以是**折线** `{lines:[[p1,p2],[p2,p3],…], width}`。
OutlineSpec = Union[Dict[str, Any], Sequence[Dict[str, Any]]]


def _chain_points(segs: Sequence[Any], eps: float = 1e-6) -> Optional[List[List[float]]]:
    """
    把首尾相连的线段串成点表：`[[p1,p2],[p2,p3]]` -> `[p1,p2,p3]`。

    ★ 为什么需要（金标准对拍）：`axlDBCreateLine` 一次调用画 n 个点 = **1 个图元**。
      1 脚三角本应是一个 4 点闭合折线（=1 个图元），若逐段调用会变成 3 个图元，
      「Silkscreen 复杂 path 数」「Assembly 元素数」就对不上金标准了。

    :return: 点表；**串不起来（有断点/倒向）返回 None** —— 调用方退回逐段绘制。
    """
    pts: List[List[float]] = []
    for seg in segs:
        if not seg or len(seg) < 2:
            return None
        (x1, y1), (x2, y2) = seg[0], seg[1]
        p1 = [float(x1), float(y1)]
        p2 = [float(x2), float(y2)]
        if not pts:
            pts = [p1, p2]
            continue
        last = pts[-1]
        if abs(last[0] - p1[0]) <= eps and abs(last[1] - p1[1]) <= eps:
            pts.append(p2)
        elif abs(last[0] - p2[0]) <= eps and abs(last[1] - p2[1]) <= eps:
            pts.append(p1)
        else:
            return None
    return pts


def _outline_xy_points(items: Any) -> List[Tuple[float, float]]:
    """
    抽出一个轮廓描述里的**全部坐标点**（用于算 drawing extents）。

    支持与 `_draw_outlines` 一致的两种形态：
        ① 矩形：`{"x1":..,"y1":..,"x2":..,"y2":..}`
        ② 折线：`{"lines":[[[x1,y1],[x2,y2]], ...]}`

    ★★ 存在理由（2026-10-08，CSTJ-421 实测血泪）★★
        `build_footprint` 的 `auto_extent` 旧实现**只按引脚坐标**外扩。
        连接器类器件（如 CSTJ-421 音频插座）的 Place_Bound / 丝印 / 本体
        比引脚分布**还大** —— 实测 Place_Bound 下边缘 `-13.80` 落在
        extents 下界 `-13.70` 之外 **0.1mm** →
        `axlDBCreateRectangle` **静默返回 nil**（Allegro 不报错）……
        表现为「86 步只挂 1 步」，极难定位。

    实测证据（`probe_extent_limit`，在线）：
        · 矩形**和**线段都受 extents 约束 —— 界内 20mm 成功，界外 40mm 返回 nil
        · 把 extents 放大到 ±600 后，同一坐标立刻成功
    官方依据（`14dsnctl.html` → `axlDBChangeDesignExtents`）：
        "This may fail if an object falls outside the new extents"

    :return: [(x, y), ...]；非法/缺失的项静默跳过（外扩算宽一点无副作用）
    """
    pts: List[Tuple[float, float]] = []
    if not items:
        return pts
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, (list, tuple, set)):
        # 既不是矩形 dict、也不是序列 → 视为非法输入（不抛异常，见契约）
        return pts
    for it in items:
        if not isinstance(it, dict):
            continue
        segs = it.get("lines")
        if segs:
            for seg in segs:
                for p in (seg or []):
                    try:
                        pts.append((float(p[0]), float(p[1])))
                    except (TypeError, ValueError, IndexError):
                        continue
            continue
        try:
            pts.append((float(it["x1"]), float(it["y1"])))
            pts.append((float(it["x2"]), float(it["y2"])))
        except (KeyError, TypeError, ValueError):
            continue
    return pts


def _compute_auto_extents(
    pins: Sequence[Dict[str, Any]],
    silk: Any = None,
    body: Any = None,
    place_bound: Any = None,
    *,
    padstack: Optional[Dict[str, Any]] = None,
    extra_padstacks: Optional[Sequence[Dict[str, Any]]] = None,
    refdes: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, float, float]:
    """
    ★ 算出「能把本次要画的**全部图元**装下」的 drawing extents（mm）。

    ★★ 为什么必须覆盖全部几何（2026-10-08，CSTJ-421 实测血泪）★★
        旧实现只按**引脚坐标**外扩。连接器 / 插座这类器件
        「焊盘集中、本体与 Place_Bound 比焊盘分布更大」，
        范围会算偏小：
            CSTJ-421：引脚 y ∈ [-11.60,-2.75]、margin=2.10
                      → extents y ∈ [-13.70,-0.65]
                      Place_Bound 实际 y ∈ [-13.80,+1.60]
                      ← 下边缘差 **0.10mm**，落在范围外
        → `axlDBCreateRectangle` **静默返回 nil**（不报错、不弹框），
          整轮 87 步只有这 1 步失败，极难定位。

    ★ 实测（在线 `probe_extent_limit`）：
        · 矩形**和**线段都受 extents 约束：界内 20mm 成功、界外 40mm 返回 nil
        · 把 extents 放大到 ±600 后，同一坐标立刻成功
    ★ 官方依据（`14dsnctl.html` → `axlDBChangeDesignExtents`）：
        "This may fail if an object falls outside the new extents"

    ▸ margin 取 `max(2.0, 最大焊盘半尺寸 + 1.0)`：
      引脚是"点"，其铜皮还要向外伸半个焊盘，margin 必须盖住它。

    :return: (x1, y1, x2, y2)，**单位 mm**；空几何时退化为 ±2.0 的方框
    """
    xs: List[float] = []
    ys: List[float] = []
    for p in (pins or []):
        try:
            xs.append(float(p.get("x", 0.0)))
            ys.append(float(p.get("y", 0.0)))
        except (TypeError, ValueError, AttributeError):
            continue
    for _pts in (
        _outline_xy_points(silk),
        _outline_xy_points(body),
        _outline_xy_points(place_bound),
    ):
        for _px, _py in _pts:
            xs.append(_px)
            ys.append(_py)
    if refdes:
        # 位号锚点也算进来（文本可能略伸出锚点，下面的 margin 足以兜住）
        try:
            xs.append(float(refdes.get("x", 0.0)))
            ys.append(float(refdes.get("y", 0.0)))
        except (TypeError, ValueError):
            pass

    pad_w = 0.0
    try:
        for _pd in [padstack or {}] + list(extra_padstacks or []):
            for ld in (_pd.get("layers") or []):
                if str(ld.get("layer", "")).upper() == "TOP":
                    pad_w = max(pad_w, float(ld["size"][0]), float(ld["size"][1]))
                    break
        pad_w /= 2.0
    except (TypeError, ValueError, KeyError, IndexError):
        pad_w = 0.0

    margin = max(2.0, pad_w + 1.0)
    if not xs or not ys:
        return (-margin, -margin, margin, margin)
    return (
        min(xs) - margin, min(ys) - margin,
        max(xs) + margin, max(ys) + margin,
    )


def build_footprint(
    padstack: Dict[str, Any],
    pins: Sequence[Dict[str, Any]],
    silk: Optional[OutlineSpec] = None,
    body: Optional[OutlineSpec] = None,
    place_bound: Optional[OutlineSpec] = None,
    refdes: Optional[Dict[str, Any]] = None,
    auto_extent: bool = True,
    frame_view: bool = True,
    save: bool = True,
    on_exists: str = "skip",
    fresh_symbol: bool = False,
    symbol_name: Optional[str] = None,
    template_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    extra_padstacks: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    一次调用建出完整封装（padstack + pins + 丝印 + 本体 + place_bound + 位号）。

    这是对标 KiCad MCP 的 `create_footprint` 的顶层接口。

    :param on_exists: padstack 重名策略，见 create_padstack。
                     这里默认 **"skip"** —— 顶层流程通常是在已有 drawing 上
                     反复迭代，若沿用单个动作的默认 "error" 会让重跑必然失败。
                     需要严格时显式传 "error"。

    :param fresh_symbol: ★★★ **是否先自动新建一张干净的 Package symbol 图纸**。
                     默认 False（在当前图纸上生成）。
                     传 True 时，会先调 `new_symbol_drawing(symbol_name)`，
                     用"当前版本模板复制"法得到全新的 symbol 图纸
                     —— **彻底根治图元叠加污染**，且全程无弹框。

    :param symbol_name: `fresh_symbol=True` 时的新图纸名（不含 .dra）。
                     建议直接用封装名（如 "NB_XTAL4_3D2X2D5X0D7"）。

    :param template_path: 模板 .dra 路径；None 时自动扫描工作目录
                     （**强制要求 17.2 当前版本格式**，老模板会被拒绝）。

    :param padstack: {"name": "R1D4X1D2", "layers": [...], "drill": {...}}
    :param pins:     [{"number":"1","x":-1.1,"y":0.85}, ...]  （单位 mm）
    :param silk:     {"x1":..,"y1":..,"x2":..,"y2":..,"width":0.15}
                     ★ 也可传**列表**（多段）：丝印避让焊盘时拆成
                     「外框 + 1 脚标识」两段，见 `geometry_solver.solve()`
    :param body:     {"x1":..,"y1":..,"x2":..,"y2":..,"width":0.1}
                     （同样支持列表）
    :param place_bound: {"x1":..,"y1":..,"x2":..,"y2":..}（同样支持列表）
    :param refdes:   {"text":"#REF","x":0,"y":0,"text_block":"1","justify":"left"}
                     ★ 可选 `targets`（列表）一次放多层：
                       `target` 取值 "refdes_assembly"（**默认**，
                       REF DES/ASSEMBLY_TOP）/ "refdes"
                       （REF DES/SILKSCREEN_TOP）。
                       例：`{"text":"#REF","x":0,"y":0,
                             "targets":["refdes_assembly","refdes"]}`
                       —— 人工模板库 14/14 都只放 **装配层** 一条 `#REF`
                       （2026-09-30 用户裁决，默认已照此改）。
    :param auto_extent: 自动按引脚+轮廓范围扩 extents（默认 True）

    :param frame_view: ★ 建完把「图纸范围 + 视图窗口」摆成**以原点为中心**
                     （默认 True）—— 否则打开时封装会缩在画布**左下角**。
                     原理见 `frame_drawing()`：`.dra` 会存"上一帧视图"，
                     模板自带的那帧原点在左下角。
                     关掉它几何不受影响，只是打开时不在画面正中。

    :return: {
        "success": bool, "padstack_name": str, "unit": str,
        "pin_count": int, "created_pins": int,
        "pad_file": str|None, "steps": [...], "error": str|None
    }
    """
    all_steps: List[Dict[str, Any]] = []

    def _collect(r: Dict[str, Any], label: str) -> None:
        for st in r.get("steps", []):
            st2 = dict(st)
            st2["phase"] = label
            all_steps.append(st2)

    # ---- 0-pre) ★ 全自动新建干净 symbol 图纸（可选，治本）----
    if fresh_symbol:
        _nm = symbol_name or padstack.get("name") or "FP"
        _nd = new_symbol_drawing(
            target_name=_nm,
            template_path=template_path,
            workspace_id=workspace_id,
        )
        _collect(_nd, "new_symbol")
        if not _nd.get("success"):
            return _result(
                False, error=_nd.get("error"), steps=all_steps,
                warnings=[_nd.get("error")] if _nd.get("error") else [],
            )

    # ---- 0) 体检 ----
    env = inspect_environment(workspace_id)
    _collect(env, "env")
    if not env.get("success"):
        return _result(
            False,
            error=env.get("error"),
            steps=all_steps,
            warnings=env.get("warnings", []),
        )

    unit = env.get("units", "unknown")
    warnings = list(env.get("warnings", []))

    # ---- 0b) ★ 图纸污染检测（2026-09-29 加，防"静默叠加"）----
    #   图元创建是**叠加**不是替换。同一张图纸反复跑会在里面累积重复的
    #   pin / text / line，最终 axlCompileSymbol 报：
    #       WARNING(SPMHA1-298): Create symbol duplicate pin numbers.
    #       ERROR(SPMHA1-291): Create symbol has been aborted.
    #   这里提前发现并给出明确指引，而不是等编译时才发现。
    try:
        with AllegroSession(workspace_id) as _s:
            _npins = _s.eval_skill("length(axlDBGetDesign()->pins)")
            _ntext = _s.eval_skill("length(axlDBGetDesign()->text)")

            # ★ 工作目录守卫（2026-09-29 加）：`axlSaveDesign` 写的是
            #   **Allegro 工作目录**，跟图纸自己所在目录未必是同一个。
            #   不一致就纠正 + 告警，否则产物会"消失"到别处。
            _dd = _drawing_dir(_s)
            _wd = _allegro_working_dir(_s)
            if _dd and _wd and (
                os.path.normcase(os.path.normpath(_dd))
                != os.path.normcase(os.path.normpath(_wd))
            ):
                _fixed = _chdir_allegro(_s, _dd)
                warnings.append(
                    f"⚠️ Allegro 工作目录（{_wd}）与当前图纸目录（{_dd}）不一致。"
                    f"`axlSaveDesign` 写的是**工作目录** —— 不改的话 "
                    f".dra/.psm/.log 会落到图纸目录之外。"
                    + ("已自动切到图纸目录。" if _fixed
                       else "★ 自动切换失败 —— 请人工核对产物落点。"))
        _npins = int(_npins) if isinstance(_npins, (int, float)) else 0
        _ntext = int(_ntext) if isinstance(_ntext, (int, float)) else 0
        all_steps.append(_step_entry(
            "precheck", "count pins/text", True,
            value={"pins": _npins, "text": _ntext},
        ))
        if _npins > 0:
            warnings.append(
                f"⚠️ 当前图纸里已有 {_npins} 个 pin / {_ntext} 段文字 —— "
                f"继续生成会在其之上**叠加**（不是替换），"
                f"很可能导致 pin 编号重复、axlCompileSymbol 报 "
                f"SPMHA1-298/291 而失败。"
                f"**建议先在一张全新的 Package symbol 图纸上生成。**"
            )
    except Exception as _e:
        logger.debug("图纸污染检测跳过: %s", _e)

    # ---- 1) extents（避免 SPMHA1-70 / 图元被静默丢弃）----
    if auto_extent:
        # ★★ 2026-10-08 修正：外扩必须覆盖【全部将要画的几何】，不只是引脚 ★★
        #
        #   旧实现只取 `pins` 的 x/y。对**连接器 / 插座**这种
        #   「焊盘集中、本体与 Place_Bound 更大」的器件，范围会算**偏小**：
        #     CSTJ-421 实测：引脚 y ∈ [-11.60, -2.75]、margin=2.10
        #                     → extents y ∈ [-13.70, -0.65]
        #     Place_Bound 实际 y ∈ [-13.80, +1.60]  ← 下边缘差 **0.10mm**
        #   → `axlDBCreateRectangle` **静默返回 nil**（Allegro 不报错、不弹框），
        #     整轮只有这一条失败步，极难定位。
        #   ★ 实测（在线 `probe_extent_limit`）：
        #       · 矩形与线段**都**受 extents 约束：界内 20mm 成功、界外 40mm 返 nil
        #       · 把 extents 放大到 ±600 后，同一坐标立刻成功
        #   ★ 官方依据（`14dsnctl.html`）：
        #       "This may fail if an object falls outside the new extents"
        #   ★ 计算抽到 `_compute_auto_extents()`（纯函数）—— 便于离线自检回归。
        _ex1, _ey1, _ex2, _ey2 = _compute_auto_extents(
            pins, silk, body, place_bound,
            padstack=padstack, extra_padstacks=extra_padstacks, refdes=refdes,
        )
        ext = change_extents(_ex1, _ey1, _ex2, _ey2, workspace_id)
        _collect(ext, "extents")
        if not ext.get("success"):
            warnings.append(
                f"扩展 extents 失败（{ext.get('error')}）—— "
                f"放 pin 可能报 SPMHA1-70，但不阻断流程。"
            )

    # ---- 2) padstack ----
    ps = create_padstack(
        name=str(padstack.get("name", "")),
        layers=padstack.get("layers", []),
        drill=padstack.get("drill"),
        workspace_id=workspace_id,
        save_to_disk=True,
        on_exists=on_exists,
    )
    _collect(ps, "padstack")
    if not ps.get("success"):
        return _result(
            False,
            error=f"padstack 创建失败: {ps.get('error')}",
            steps=all_steps, warnings=warnings, unit=unit,
        )

    pad_name = ps["padstack_name"]
    pad_file = ps.get("pad_file")

    # ---- 2b) ★ 额外 padstack（2026-09-30 加，FPC 固定脚等第二尺寸焊盘）----
    #   pins 里可用 "padstack": "<名>" 引用；未指定的脚用主 padstack。
    extra_ps: List[Dict[str, Any]] = list(extra_padstacks or [])
    built_extra: Dict[str, str] = {}
    for ep in extra_ps:
        _en = str(ep.get("name", ""))
        if not _en or _en == pad_name or _en in built_extra:
            continue
        _r = create_padstack(
            name=_en,
            layers=ep.get("layers", []),
            drill=ep.get("drill"),
            workspace_id=workspace_id,
            save_to_disk=True,
            on_exists=on_exists,
        )
        _collect(_r, f"padstack[{_en}]")
        if not _r.get("success"):
            return _result(
                False,
                error=f"额外 padstack {_en} 创建失败: {_r.get('error')}",
                steps=all_steps, warnings=warnings, unit=unit,
            )
        built_extra[_en] = _r["padstack_name"]

    # ---- 3) pins ----
    created = 0
    for p in pins:
        _pn = str(p.get("padstack") or pad_name)
        if p.get("padstack") and _pn not in built_extra and _pn != pad_name:
            warnings.append(
                f"pin {p.get('number')} 引用的 padstack {_pn!r} 未在 "
                f"extra_padstacks 中定义 —— 已回退主 padstack {pad_name}。")
            _pn = pad_name
        r = add_pin(
            padstack_name=_pn,
            number=str(p.get("number", "")),
            x=float(p.get("x", 0.0)),
            y=float(p.get("y", 0.0)),
            rotation=p.get("rotation"),
            text_offset=p.get("text_offset"),
            workspace_id=workspace_id,
        )
        _collect(r, f"pin[{p.get('number')}]")
        if r.get("success"):
            created += 1
        else:
            warnings.append(f"pin {p.get('number')} 创建失败: {r.get('error')}")

    # ---- 4-6) 轮廓层：丝印 / Assembly / Place_Bound ----
    #   ★ silk / body / place_bound 均支持「单个矩形 dict」或「矩形 dict 列表」。
    #     列表用于多段轮廓：如丝印避让焊盘后拆成「外框 + 1 脚标识」两段
    #     （geometry_solver 的输出即是列表形式）。
    def _draw_outlines(items, target: str, default_width: float,
                       filled: bool = False) -> None:
        """
        画一组轮廓图元，支持两种形态混排：

          ① 矩形：  {"x1":..,"y1":..,"x2":..,"y2":..,"width":..}
          ② 折线：  {"lines":[[[x1,y1],[x2,y2]], [[x2,y2],[x3,y3]], ...],
                     "width":..}                       ← 走 add_line 逐段画

        ★ ② 是 2026-09-29 加的金标准形态所必需 —— 金标准丝印不是矩形，
          而是「2 条短横线 + 1 个三角形」，只能用任意线段画。
        """
        if not items:
            return
        if isinstance(items, dict):
            items = [items]
        for i, it in enumerate(items):
            w = float(it.get("width", default_width))
            label = f"{target}[{i + 1}]"

            # ---- ② 折线图元：首尾相连就**一次画完**（1 个图元）----
            segs = it.get("lines")
            if segs:
                chain = _chain_points(segs)
                if chain is not None and len(chain) >= 2:
                    r = add_polyline(chain, target=target, width=w,
                                     workspace_id=workspace_id)
                    _collect(r, label)
                    if not r.get("success"):
                        warnings.append(
                            f"{target} 第 {i + 1}/{len(items)} 个图元"
                            f"（折线 {len(chain)} 点）绘制失败: {r.get('error')}")
                    continue
                # 串不起来 → 退回逐段画（形状仍正确，只是图元数会变多）
                warnings.append(
                    f"{target} 第 {i + 1}/{len(items)} 个图元的 {len(segs)} 段"
                    f"首尾不连续 —— 已退回逐段绘制（图元数会多于设计意图）。")
                for j, seg in enumerate(segs):
                    if not seg or len(seg) < 2:
                        continue
                    (x1, y1), (x2, y2) = seg[0], seg[1]
                    r = add_line(
                        float(x1), float(y1), float(x2), float(y2),
                        target=target, width=w, workspace_id=workspace_id,
                    )
                    _collect(r, f"{label}.seg{j + 1}")
                    if not r.get("success"):
                        warnings.append(
                            f"{target} 第 {i + 1}/{len(items)} 个图元的"
                            f"第 {j + 1} 条线绘制失败: {r.get('error')}")
                continue

            # ---- ① 矩形图元 ----
            if "x1" not in it:
                warnings.append(
                    f"{target} 第 {i + 1} 个图元既无 `x1` 也无 `lines` —— 已跳过。")
                continue
            r = add_outline(
                it["x1"], it["y1"], it["x2"], it["y2"],
                target=target,
                width=w,
                filled=filled,
                workspace_id=workspace_id,
            )
            _collect(r, label)
            if not r.get("success"):
                warnings.append(
                    f"{target} 第 {i + 1}/{len(items)} 段绘制不完整: {r.get('error')}")

    _draw_outlines(silk, "silkscreen", 0.15)
    _draw_outlines(body, "assembly", 0.10)
    _draw_outlines(place_bound, "place_bound", 0.0, filled=True)

    # ---- 7) 位号 ----
    #   ★★★ 位号（refdes）必须落在 **REF DES 类** 层，否则 axlCompileSymbol 报
    #     ERROR(SPMHCS-1): Symbol is missing a refdes.
    #     （写在 PACKAGE GEOMETRY/* 里 Allegro **不认**。2026-09-29 实测确认。）
    #   模板库口径（2026-09-30 用户裁决）：`Data\footprint\*/` 下 14/14 个人工
    #   模板都是 **REF DES/ASSEMBLY_TOP + `#REF`**，所以默认层 = `refdes_assembly`、
    #   默认字 = `#REF`。仍可用 `targets` 一次放多层（含旧默认丝印层）。
    if refdes:
        rf_text = str(refdes.get("text", "#REF"))
        rf_x = float(refdes.get("x", 0.0))
        rf_y = float(refdes.get("y", 0.0))
        rf_tb = str(refdes.get("text_block", "1"))
        rf_jf = refdes.get("justify")
        _tgts = refdes.get("targets") or [refdes.get("target", "refdes_assembly")]
        if isinstance(_tgts, str):
            _tgts = [_tgts]
        for tgt in _tgts:
            r = add_text(
                text=rf_text,
                x=rf_x,
                y=rf_y,
                target=str(tgt),
                text_block=rf_tb,
                justify=(str(rf_jf) if rf_jf else None),
                workspace_id=workspace_id,
            )
            _collect(r, f"refdes@{tgt}")
            if not r.get("success"):
                warnings.append(
                    f"位号创建失败（{tgt}）: {r.get('error')} —— "
                    f"缺位号时 File→Create Symbol 会报 SPMHCS-1。"
                )

    # ---- 7.5) ★ 图纸范围 + 视图窗口：摆成以原点为中心（打开就居中）----
    #   为什么必须在**存盘之前**做：`.dra` 会把"上一帧视图窗口"一起存盘，
    #   模板自带的是"原点在左下角"的视图，不重设就会一路继承下去
    #   —— 这就是"封装永远缩在画布左下角"的根因（2026-09-30 实测）。
    #   详见 `frame_drawing()` 的 docstring。
    if frame_view:
        r = frame_drawing(workspace_id=workspace_id)
        _collect(r, "frame")
        for w in (r.get("warnings") or []):
            warnings.append(f"[居中] {w}")
        if not r.get("success"):
            warnings.append(
                f"图纸居中未完全成功: {r.get('error')} —— "
                f"几何不受影响，只是打开时可能不在画面正中。")

    # ---- 8) 存盘 ----
    if save:
        r = save_drawing(workspace_id=workspace_id)
        _collect(r, "save")
        if not r.get("success"):
            warnings.append(f"保存失败: {r.get('error')}")

    # ---- 9) 归集本次运行甩出来的日志 -> Tools/log ----
    logs = _collect_logs_step(all_steps, P.TEST_DIR)

    ok = created == len(pins)
    return _result(
        ok,
        padstack_name=pad_name,
        padstack_reused=bool(ps.get("skipped")),
        pad_file=pad_file,
        unit=unit,
        pin_count=len(pins),
        created_pins=created,
        warnings=warnings,
        steps=all_steps,
        logs=logs,
        error=None if ok else f"{len(pins) - created} 个 pin 创建失败",
    )
