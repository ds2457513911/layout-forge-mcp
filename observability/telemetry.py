# -*- coding: utf-8 -*-
"""
observability/telemetry.py —— 遥测写入（JSONL）

写什么：一次任务里每一步的耗时、成功与否、降级码。
落到哪：`services.paths.RUNS_DIR/<YYYY-MM-DD>.jsonl`（**产物，不进 git**）。

★ 三条硬约束
  1. **绝不抛异常**：`record_event()` 内部吞掉一切异常，写失败只返回 False。
     观测失败不能拖垮主流程（和 `paths.collect_run_logs()` 同一条纪律）。
  2. **只落字段名与 hash**：`hash_args()` 只产出 `["folder_path","precision"]` + sha1 前 12 位；
     参数**值**（客户料号、尺寸数值）不进遥测文件。
  3. **降级必须显式**：没抛异常但结果不可信时，在 `track()` 里调 `rec.degrade(code, detail)`
     —— 让「静默回落」变成能统计的字段，而不是靠人回忆。

事件字段（schema v1；改字段要同步 `eval/suites/tool_contract` 的基线）：
  schema / ts / run_id / step / tool / ok / ms
  / error_code / error_type / error_msg / error_source / args_keys / args_hash / degraded[]
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from services import paths

#: 事件结构版本（加字段才 +1；删/改字段要同步 tool_contract 基线）
SCHEMA_VERSION = 1

#: 外部（CI / 一次任务编排）可以预设 run_id，让「一次任务」跨进程串起来
RUN_ID_ENV = "LAYOUT_FORGE_RUN_ID"

#: 错误信息与降级说明的落盘长度上限（防止把整篇堆栈写进 JSONL）
MAX_MSG = 300
MAX_DETAIL = 200

#: 异常类型 → 稳定错误码（顺序敏感：先精确后宽泛）
_ERROR_CODES: Sequence[Tuple[str, str]] = (
    ("FileNotFoundError", "NOT_FOUND"),
    ("NotADirectoryError", "NOT_FOUND"),
    ("PermissionError", "PERMISSION"),
    ("TimeoutError", "TIMEOUT"),
    ("TimeoutExpired", "TIMEOUT"),
    ("ModuleNotFoundError", "DEP_MISSING"),
    ("ImportError", "DEP_MISSING"),
    ("ConnectionRefusedError", "CHANNEL_DOWN"),
    ("ConnectionError", "CHANNEL_DOWN"),
    ("OSError", "IO_ERROR"),
    ("ValueError", "BAD_INPUT"),
    ("KeyError", "BAD_INPUT"),
    ("TypeError", "BAD_INPUT"),
    ("AssertionError", "INVARIANT"),
    ("ZeroDivisionError", "INTERNAL"),
)

_run_id: Optional[str] = None


# ============================================================
# run_id / 落盘路径
# ============================================================
def new_run_id(now: Optional[datetime] = None) -> str:
    """生成 run_id：`YYYYmmdd-HHMMSS-<6 位随机>`（可读 + 不撞车）。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def current_run_id() -> str:
    """本次进程的 run_id（首次调用时确定；环境变量优先）。"""
    global _run_id
    if _run_id is None:
        _run_id = os.environ.get(RUN_ID_ENV) or new_run_id()
    return _run_id


def set_run_id(run_id: str) -> None:
    """显式指定当前 run_id（一次任务跨多个进程时用）。"""
    global _run_id
    _run_id = str(run_id) if run_id else None


def runs_dir() -> str:
    """遥测目录（顺带幂等建目录；失败也不抛）。"""
    d = paths.RUNS_DIR
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def events_path(day: Optional[str] = None) -> str:
    """某一天的事件文件路径，默认今天（事件**按天分文件**，便于按窗口读）。"""
    return os.path.join(runs_dir(), f"{day or datetime.now().strftime('%Y-%m-%d')}.jsonl")


def recent_event_files(days: int = 7, today: Optional[datetime] = None) -> List[str]:
    """最近 `days` 天（含今天）的事件文件路径，**存在的**按日期升序返回。"""
    today = today or datetime.now()
    out: List[str] = []
    for i in range(max(int(days), 1) - 1, -1, -1):
        p = events_path((today - timedelta(days=i)).strftime("%Y-%m-%d"))
        if os.path.isfile(p):
            out.append(p)
    return out


# ============================================================
# 参数脱敏
# ============================================================
def hash_args(args: Optional[Mapping[str, Any]]) -> Tuple[List[str], str]:
    """
    参数 → `(字段名列表, 值指纹)`。

    ★ 只回字段名与整参数集的 sha1 前 12 位：能回答「同样的调用是否重复出现」，
    **不能**还原出料号与尺寸（那是客户资料，不该进遥测）。

    :return: (["folder_path", "precision"], "9f2c1a7b3d40")；无参数 → ([], "")
    """
    if not args:
        return ([], "")
    try:
        keys = sorted(str(k) for k in args.keys())
    except Exception:                       # noqa: BLE001
        return ([], "")
    try:
        canonical = {str(k): args[k] for k in sorted(args.keys(), key=str)}
        blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
    except Exception:                       # noqa: BLE001
        digest = ""
    return (keys, digest)


def classify(exc: BaseException) -> str:
    """异常 → 稳定错误码（`eval/` 与 `stats` 都按这个字符串聚合）。"""
    name = type(exc).__name__
    for cls_name, code in _ERROR_CODES:
        if name == cls_name:
            return code
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "CANCELLED"
    return "INTERNAL"


# ============================================================
# 写事件
# ============================================================
def record_event(event: Mapping[str, Any]) -> bool:
    """
    追加一条事件到今天的 JSONL。

    ★ **绝不抛异常**；写失败返回 False（调用方也不需要处理 —— 遥测不是主流程）。
    """
    try:
        rec: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "run_id": current_run_id(),
        }
        rec.update({str(k): v for k, v in event.items()})
        rec.setdefault("ok", True)
        rec.setdefault("ms", None)
        rec.setdefault("degraded", [])
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with open(events_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception:                       # noqa: BLE001  ★ 见文件头硬约束 1
        return False


def record(
    tool: str = "",
    step: str = "",
    ok: bool = True,
    ms: Optional[int] = None,
    error: Optional[BaseException] = None,
    args: Optional[Mapping[str, Any]] = None,
    degraded: Optional[Sequence[Any]] = None,
    **extra: Any,
) -> bool:
    """一次性记一条事件（没有上下文管理需求时用；有耗时统计需求请用 `track()`）。"""
    keys, digest = hash_args(args)
    ev: Dict[str, Any] = {
        "tool": tool,
        "step": step,
        "ok": bool(ok),
        "ms": ms,
        "args_keys": keys,
        "args_hash": digest,
    }
    if error is not None:
        ev["ok"] = False
        ev["error_code"] = classify(error)
        ev["error_type"] = type(error).__name__
        ev["error_msg"] = str(error)[:MAX_MSG]
    if degraded:
        ev["degraded"] = list(degraded)
    ev.update(extra)
    return record_event(ev)


class StepRecord(dict):
    """
    `track()` 交出来的记录。

    工具里发现「没报错但结果不可信」时（例如 PDF 有多个、holeType 回落、
    区域裁剪失败改用整页），**必须**调 `rec.degrade("AMBIGUOUS_PDF", "2 份 PDF")`
    —— 这就是把「静默失败」变成可统计字段的唯一入口。
    """

    def degrade(self, code: str, detail: str = "") -> None:
        item: Dict[str, str] = {"code": str(code)[:64]}
        if detail:
            item["detail"] = str(detail)[:MAX_DETAIL]
        self.setdefault("degraded", []).append(item)

    def annotate(self, **fields: Any) -> None:
        """补充业务字段（如 result_count、conclusion）——只放短标量。"""
        for k, v in fields.items():
            self[str(k)] = v

    def fail(self, code: str = "INTERNAL", detail: str = "", error_type: str = "") -> None:
        """登记「**没抛异常，但这次调用失败了**」——工具返回 `ok:false` 的场景。

        与 `degrade()` 的区别：`degrade` = 结果降级但还能用；`fail` = 这次调用没成。
        `error_source` 区分失败来源：`exception`（抛了异常）/ `returned`（返回值里 `ok:false`）。
        """
        self["ok"] = False
        self["error_code"] = str(code)[:64]
        self["error_source"] = "returned"
        if detail:
            self["error_msg"] = str(detail)[:MAX_MSG]
        if error_type:
            self["error_type"] = str(error_type)[:64]


@contextlib.contextmanager
def track(tool: str = "", step: str = "", args: Optional[Mapping[str, Any]] = None) -> Iterator[StepRecord]:
    """
    包住一次工具调用 / 一个步骤：

        with telemetry.track("check_footprint_by_rules", args=locals()) as rec:
            ...                      # 业务代码
            rec.degrade("EXTENTS_DROPPED", "3 处")

    成功、失败、异常都会落一条事件；**业务异常照原样继续抛**（这里绝不吞）。
    """
    keys, digest = hash_args(args)
    rec = StepRecord(tool=tool, step=step, ok=True, args_keys=keys, args_hash=digest, degraded=[])
    started = time.perf_counter()
    try:
        yield rec
    except BaseException as e:              # noqa: BLE001 —— 记完继续抛
        rec["ok"] = False
        rec["error_code"] = classify(e)
        rec["error_source"] = "exception"
        rec["error_type"] = type(e).__name__
        rec["error_msg"] = str(e)[:MAX_MSG]
        raise
    finally:
        rec["ms"] = int((time.perf_counter() - started) * 1000)
        record_event(rec)


def log_degraded(tool: str, code: str, detail: str = "", **extra: Any) -> bool:
    """没有 `track()` 上下文时单记一条降级事件（例如脚本里的兜底分支）。"""
    item = {"code": str(code)[:64]}
    if detail:
        item["detail"] = str(detail)[:MAX_DETAIL]
    return record(tool=tool, step="degraded", ok=True, degraded=[item], **extra)
