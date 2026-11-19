# -*- coding: utf-8 -*-
"""orchestration/pipeline.py —— 任务状态机：步骤序列 + 每步前置/后置断言

## 它解决什么

「先做什么、动手前必须先成立什么、做完必须留下什么」过去只写在 SKILL 文档里，
靠模型自觉执行。本模块把它变成**代码里的步骤与断言**：

    preflight → env-check → extract → check → report

规则本身**不写在这里**（在 `playbook.json`）。本模块只负责：按 playbook 的 gate 去问，
按 `consequence`（deny / silent / recoverable）决定能不能继续，并把结论交回上层。

## 三条纪律（与 orchestration/README.md 一致）

1. **只上报，不替调用方拒绝执行**：歧义 → `action="confirm"` + 候选列表，绝不抛异常；
2. **不静默跳过**：需要通道/单位的步骤拿不到事实时，产出 `error_code`，而不是「大概没问题」；
3. **层方向**：只 import 标准库 + `services`。**不 import observability** ——
   本模块只**产出** step 名与事件字典，落盘由上层 `mcp_server/middleware.py` 统一做。

## step 名（与 observability 的埋点字段同名）

    preflight / env-check / extract / check / report

❕ 落盘时必须 `telemetry.record(step=<名字>, tool="")`：
`observability/stats.py::step_stats()` 会丢弃带 `tool` 的事件（`if not step or ev.get("tool"): continue`）。

## 用法

    python -m orchestration.pipeline Data/footprint --recursive
    python -m orchestration.pipeline --validate-playbook
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "STEP_PREFLIGHT", "STEP_ENV_CHECK", "STEP_EXTRACT", "STEP_CHECK", "STEP_REPORT",
    "STEP_NAMES", "STEPS", "Step", "GateResult", "StepOutcome", "PipelineResult",
    "ERR_CHANNEL_NOT_READY", "ERR_CHANNEL_BUSY", "ERR_AMBIGUOUS_INPUT",
    "ERR_NO_CANDIDATES", "ERR_UNITS_UNKNOWN", "ERR_NO_DATA",
    "PLAYBOOK_PATH", "load_playbook", "validate_playbook", "evaluate_gate",
    "summarize_findings", "run",
]

# ── step 名（也是埋点字段的取值；改名等于改观测口径） ──────────────────────
STEP_PREFLIGHT = "preflight"
STEP_ENV_CHECK = "env-check"
STEP_EXTRACT = "extract"
STEP_CHECK = "check"
STEP_REPORT = "report"
STEP_NAMES = (STEP_PREFLIGHT, STEP_ENV_CHECK, STEP_EXTRACT, STEP_CHECK, STEP_REPORT)

# ── 失败码（SKILL 侧按码分支，不按人话分支） ─────────────────────────────
ERR_CHANNEL_NOT_READY = "CHANNEL_NOT_READY"
#: 通道被别的会话占着（**通道没坏**，等或稍后重试即可）—— 与 NOT_READY 的分工见
#: services/channel_guard.py 的 ChannelBusyError。码值必须与那里一致，不许各写一份。
ERR_CHANNEL_BUSY = "CHANNEL_BUSY"
ERR_AMBIGUOUS_INPUT = "AMBIGUOUS_INPUT"
ERR_NO_CANDIDATES = "NO_CANDIDATES"
ERR_UNITS_UNKNOWN = "UNITS_UNKNOWN"
ERR_NO_DATA = "NO_DATA"

#: 后果类别 → 允许的动作。deny 之后再给结论就是错的；silent 可以被看见，但必须写在报告里
CONSEQUENCE_ACTION = {"deny": "stop", "silent": "confirm", "recoverable": "proceed"}

PLAYBOOK_PATH = Path(__file__).resolve().parent / "playbook.json"


@dataclass(frozen=True)
class Step:
    """一个步骤：名字（=埋点 step）+ 前置/后置断言（=playbook 的 gate id）。"""
    name: str
    title: str
    pre: Tuple[str, ...] = ()
    post: Tuple[str, ...] = ()
    local: bool = True          # False = 必须由上层调用工具才能做
    needs_channel: bool = False


#: 标准封装检查流程。顺序本身就是约定：先探目录、再探通道，然后才是取数与判定
STEPS: Tuple[Step, ...] = (
    Step(STEP_PREFLIGHT, "预检输入目录",
         pre=("G-candidates-present",),
         post=("G-ambiguous-src", "G-stale-report", "G-noise-as-artifact")),
    Step(STEP_ENV_CHECK, "勘探通道与图纸单位",
         pre=("G-channel-ready",), post=("G-units",), needs_channel=True),
    Step(STEP_EXTRACT, "读 .dra / 读规格书", post=("G-candidates-present",),
         local=False, needs_channel=True),
    Step(STEP_CHECK, "跑规则（rule_checker）", local=False),
    Step(STEP_REPORT, "出报告（结论 + 证据）", local=False),
)


@dataclass
class GateResult:
    gate_id: str
    ok: bool
    consequence: str
    message: str
    evidence: Dict[str, object] = field(default_factory=dict)
    #: playbook 里显式写的失败动作（例如歧义 → confirm 而不是 stop）。
    #: 空 = 按后果类别默认（deny→stop / silent→confirm / recoverable→proceed）
    on_fail_action: str = ""


@dataclass
class StepOutcome:
    name: str
    title: str
    status: str            # done | needs-confirm | failed | pending
    action: str = "proceed"
    gates: List[GateResult] = field(default_factory=list)
    note: str = ""


@dataclass
class PipelineResult:
    folder: str
    action: str
    steps: List[StepOutcome]
    next_step: Optional[str]
    events: List[dict]
    preflight: Optional[dict] = None
    env: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "schema": "layout-forge-pipeline/1",
            "folder": self.folder,
            "action": self.action,
            "next_step": self.next_step,
            "steps": [{"name": s.name, "title": s.title, "status": s.status,
                       "action": s.action, "note": s.note,
                       "gates": [g.__dict__ for g in s.gates]} for s in self.steps],
            "events": self.events,
        }


# ── playbook ─────────────────────────────────────────────────────────────
def load_playbook(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else PLAYBOOK_PATH
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def validate_playbook(playbook: Optional[dict] = None) -> List[str]:
    """检查 playbook 的硬约定，返回问题列表（空 = 合格）。

    约定（orchestration/README.md 第 4 条）：**每条规则/门禁都必须同时有检测方式与后果类别**，
    否则不许进表。少一条就等不到「这条规则失守会怎样」的答案。
    """
    pb = playbook if playbook is not None else load_playbook()
    problems: List[str] = []
    seen: Dict[str, str] = {}
    for section in ("rules", "gates"):
        for item in pb.get(section) or []:
            rid = str(item.get("id", ""))
            if not rid:
                problems.append(f"{section}: 有条目没有 id")
                continue
            if rid in seen:
                problems.append(f"{rid}: 在 {seen[rid]} 与 {section} 里重复出现")
            seen[rid] = section
            if not item.get("detect"):
                problems.append(f"{rid}: 缺「检测方式」")
            cons = item.get("consequence")
            if cons not in CONSEQUENCE_ACTION:
                problems.append(f"{rid}: 缺/非法「后果类别」（{cons!r}，合法值 {sorted(CONSEQUENCE_ACTION)}）")
            if not item.get("evidence"):
                problems.append(f"{rid}: 缺「证据字段」")
    return problems


def _gates(playbook: dict) -> Dict[str, dict]:
    return {str(g["id"]): g for g in playbook.get("gates") or []}


# ── 门禁判定（纯函数：只读 ctx，不碰磁盘、不起 Allegro） ────────────────
def _pre(ctx: dict) -> dict:
    pre = ctx.get("preflight")
    if not isinstance(pre, dict):
        raise ValueError("G-* 判定需要 ctx['preflight']")
    return pre


def _units_of_candidates(pre: dict) -> int:
    if pre.get("mode") == "batch":
        units = pre.get("units") or []
        return sum(len(u.get("pdf_files") or []) + len(u.get("dra_files") or []) for u in units) if units \
            else (pre.get("pdf_count", 0) + pre.get("dra_count", 0))
    return pre.get("pdf_count", 0) + pre.get("dra_count", 0)


def _g_ambiguous_src(ctx: dict) -> GateResult:
    pre = _pre(ctx)
    ok = not pre.get("ambiguous")
    cands = [(u.get("folder"), u.get("pdf_files"), u.get("dra_files")) for u in pre.get("suspects") or []] \
        or [(pre.get("folder"), pre.get("pdf_files"), pre.get("dra_files"))]
    return GateResult("G-ambiguous-src", ok, "deny",
                      "目录里不止一个候选：先与用户确认用哪一份，再动手"
                      if not ok else "候选唯一",
                      {"verdict": pre.get("verdict"), "action": pre.get("action"),
                       "candidates": cands})


def _g_candidates_present(ctx: dict) -> GateResult:
    pre = _pre(ctx)
    n = _units_of_candidates(pre)
    ok = n > 0
    return GateResult("G-candidates-present", ok, "deny",
                      f"输入目录里找到 {n} 个候选文件" if ok
                      else "输入目录里没有 PDF 也没有 .dra —— 没有对象，谈不到结论",
                      {"verdict": pre.get("verdict"), "candidates": n,
                       "mode": pre.get("mode")})


def _g_stale_report(ctx: dict) -> GateResult:
    pre = _pre(ctx)
    from orchestration.preflight import is_noise           # 单一真源，绝不自己拼规则
    bad = [f for f in (pre.get("pdf_files") or []) + (pre.get("dra_files") or []) if is_noise(f)]
    noisy = pre.get("noise_files") or []
    return GateResult("G-stale-report", not bad, "deny",
                      "候选里没有历史报告/运行痕迹冒充" if not bad
                      else f"候选里混进了噪声文件：{bad}",
                      {"noise_files": noisy, "candidates_that_are_noise": bad})


def _g_noise_as_artifact(ctx: dict) -> GateResult:
    pre = _pre(ctx)
    noisy = pre.get("noise_files") or []
    return GateResult("G-noise-as-artifact", True, "silent",
                      f"已剔除 {len(noisy)} 个噪声文件（必须在报告里写明）" if noisy
                      else "无噪声文件",
                      {"noise_files": noisy})


def _env(ctx: dict) -> dict:
    env = ctx.get("env")
    if not isinstance(env, dict):
        raise ValueError("环境类 G-* 判定需要 ctx['env']")
    return env


def _g_channel_ready(ctx: dict) -> GateResult:
    env = _env(ctx)
    connected = bool(env.get("connected"))
    err = env.get("error") or ""
    return GateResult("G-channel-ready", connected, "deny",
                      "通道可用" if connected else f"通道不可用（{ERR_CHANNEL_NOT_READY}）：{err or '未勘探'}",
                      {"connected": connected, "error": err,
                       "error_code": "" if connected else ERR_CHANNEL_NOT_READY})


_KNOWN_UNITS = ("millimeters", "millimeter", "mm", "mils", "mil", "inches", "inch", "thou")


def _g_units(ctx: dict) -> GateResult:
    env = _env(ctx)
    unit = str(env.get("units") or "unknown")
    ok = unit.lower() in _KNOWN_UNITS
    return GateResult("G-units", ok, "silent",
                      f"图纸单位 = {unit}" if ok
                      else f"图纸单位不明（{unit}）——数值结论会差 25.4 倍，先确认单位",
                      {"units": unit, "error_code": "" if ok else ERR_UNITS_UNKNOWN})


_EVALUATORS: Dict[str, Callable[[dict], GateResult]] = {
    "G-ambiguous-src": _g_ambiguous_src,
    "G-candidates-present": _g_candidates_present,
    "G-stale-report": _g_stale_report,
    "G-noise-as-artifact": _g_noise_as_artifact,
    "G-channel-ready": _g_channel_ready,
    "G-units": _g_units,
}


def evaluate_gate(gate_id: str, ctx: dict, playbook: Optional[dict] = None) -> GateResult:
    """判定一个 gate。

    - 没有本地实现方的 gate **不静默通过**：报 `未实现`，由上层/套件执行；
    - `on_fail_action` 取自 playbook（策略写在规则表里，不写死在流程代码里）。
    """
    spec = _gates(playbook or load_playbook()).get(gate_id, {})
    fn = _EVALUATORS.get(gate_id)
    if fn is None:
        res = GateResult(gate_id, True, spec.get("consequence", "recoverable"),
                         f"{gate_id} 在本模块没有本地判定实现（检测方式：{spec.get('detect', {})}），"
                         f"需由上层/套件执行", {"unimplemented": True})
    else:
        res = fn(ctx)
    res.on_fail_action = str(spec.get("on_fail_action") or "")
    return res


# ── 结论归类 ─────────────────────────────────────────────────────────────
def summarize_findings(findings: Sequence[dict], playbook: Optional[dict] = None) -> dict:
    """把规则产出按「后果类别」归类，并**点名**引用了已失效 rule_id 的 finding。

    案例里出现过 `6.5` 这种「代码已删、案例还在引用」的 rule_id
    （`services/rule_checker.py:1329` 注明 2026-10-12 删除）。这类 finding 不许静默计入统计。
    """
    pb = playbook if playbook is not None else load_playbook()
    rules = {str(r["id"]): r for r in pb.get("rules") or []}
    stale = {str(s["id"]) for s in pb.get("stale_rule_ids") or []}
    out: Dict[str, List[str]] = {"deny": [], "silent": [], "recoverable": []}
    unknown: List[str] = []
    stale_hits: List[str] = []
    for f in findings:
        rid = str(f.get("rule_id") or "")
        if rid in rules:
            out.setdefault(rules[rid]["consequence"], []).append(rid)
        elif rid in stale:
            stale_hits.append(rid)
        elif rid in ("", "-", "CONV"):
            out["recoverable"].append(rid or "?")
        else:
            unknown.append(rid)
    out["unknown_rule_ids"] = sorted(set(unknown))
    out["stale_rule_ids"] = sorted(set(stale_hits))
    return out


def _action_of(gates: Sequence[GateResult]) -> Tuple[str, str]:
    """按后果类别（或 playbook 显式给的失败动作）取最严的动作 + 一句话原因。"""
    worst, why = "proceed", ""
    for g in gates:
        if g.ok:
            continue
        act = g.on_fail_action or CONSEQUENCE_ACTION.get(g.consequence, "confirm")
        if act == "stop":
            return "stop", g.message
        if act == "confirm" and worst == "proceed":
            worst, why = "confirm", g.message
    return worst, why


# ── 主流程 ───────────────────────────────────────────────────────────────
def _default_env_probe() -> dict:
    """默认通道探针：services.footprint_builder.inspect_environment()。

    延迟 import：本模块的本地步骤不该因为 fitz/skillbridge 缺失就整体不可用。
    """
    from services.footprint_builder import inspect_environment   # noqa: WPS433 (延迟 import 是刻意的)
    return inspect_environment()


def run(folder, recursive: bool = False, env_probe: Optional[Callable[[], dict]] = None,
        stop_after: Optional[str] = None) -> PipelineResult:
    """跑到第一个需要上层工具的步骤为止，每步都留下前置/后置断言的结论。

    :param folder: 目标目录
    :param recursive: 批量模式（每个子目录一个单元）
    :param env_probe: 覆盖通道探针（测试用；默认 `inspect_environment()`）
    :param stop_after: 跑到该 step 名之后就停（`--until`）
    """
    from orchestration.preflight import preflight

    result = PipelineResult(folder=str(folder), action="proceed", steps=[],
                            next_step=None, events=[])
    ctx: dict = {}
    halted = False

    for step in STEPS:
        if halted:
            result.steps.append(StepOutcome(step.name, step.title, "pending",
                                            note="上一步没有放行，未执行"))
            continue
        if stop_after and result.steps and result.steps[-1].name == stop_after:
            result.steps.append(StepOutcome(step.name, step.title, "pending"))
            continue

        gates: List[GateResult] = []
        if step.name == STEP_PREFLIGHT:
            ctx["preflight"] = preflight(str(folder), recursive=recursive)
            result.preflight = ctx["preflight"]
            gates = [evaluate_gate(gid, ctx) for gid in step.pre + step.post]
        elif step.name == STEP_ENV_CHECK:
            probe = env_probe or _default_env_probe
            try:
                ctx["env"] = probe()
            except Exception as exc:                      # 探针炸了也是「没数据」，不是「OK」
                ctx["env"] = {"connected": False, "error": f"{type(exc).__name__}: {exc}"}
            result.env = ctx["env"]
            gates = [evaluate_gate(gid, ctx) for gid in step.pre + step.post]

        action, why = _action_of(gates)
        if step.name == STEP_PREFLIGHT:
            # 预检自己也会给 action（missing_pdf 等 = stop）——两边的 stop 取更严的那个
            own = ctx["preflight"].get("action")
            if own == "stop":
                action, why = "stop", ctx["preflight"].get("hint", "")
            elif own == "confirm" and action == "proceed":
                action, why = "confirm", ctx["preflight"].get("hint", "")

        status = {"proceed": "done", "confirm": "needs-confirm", "stop": "failed"}[action]
        result.steps.append(StepOutcome(step.name, step.title, status, action, gates, why))
        result.events.append(_event(step, action, gates))
        result.action = action

        if action in ("confirm", "stop"):
            # 「先与用户确认」是一个**终点**：不能一边说拿不准一边继续往下取数
            halted = True
            result.next_step = None
            continue

        if not step.local:
            result.next_step = step.name
            for later in STEPS[STEPS.index(step) + 1:]:
                result.steps.append(StepOutcome(later.name, later.title, "pending"))
            break

    return result


def _event(step: Step, action: str, gates: Sequence[GateResult]) -> dict:
    """产出给上层落盘的事件。**不**在这里写遥测（本层禁止 import observability）。

    `error_code` 取第一个失败 gate 的证据里的码；`degraded` 收集所有未通过 gate 的 id。
    """
    failed = [g for g in gates if not g.ok]
    codes = [str(g.evidence.get("error_code")) for g in failed if g.evidence.get("error_code")]
    err = codes[0] if codes else ""
    if not err and failed:
        err = {"G-ambiguous-src": ERR_AMBIGUOUS_INPUT,
               "G-candidates-present": ERR_NO_CANDIDATES}.get(failed[0].gate_id, ERR_NO_DATA)
    return {
        "step": step.name,
        "ok": not failed,
        "action": action,
        "error_code": "" if not failed else err,
        "degraded": [g.gate_id for g in gates if not g.ok and g.consequence != "deny"],
    }


def main(argv=None) -> int:
    # 与 Tools/deploy/setup_agent.py 同样的理由：默认 stdout 在中文 Windows 上是 GBK，
    # 打印 ✓/✗ 会 UnicodeEncodeError 把好好的结论变成异常栈。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="任务状态机：步骤序列 + 每步前置/后置断言")
    ap.add_argument("folder", nargs="?", help="目标目录")
    ap.add_argument("--recursive", action="store_true", help="批量模式：逐个子目录判定")
    ap.add_argument("--until", help="跑到该 step 之后停（preflight/env-check/…）")
    ap.add_argument("--no-env", action="store_true", help="跳过通道勘探（只做本地预检）")
    ap.add_argument("--validate-playbook", action="store_true", help="只检查 playbook.json 的硬约定")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    if args.validate_playbook:
        problems = validate_playbook()
        if args.json:
            print(json.dumps({"problems": problems}, ensure_ascii=False, indent=2))
        else:
            print("playbook 校验：" + ("通过" if not problems else f"{len(problems)} 个问题"))
            for p in problems:
                print("  - " + p)
        return 1 if problems else 0

    if not args.folder:
        ap.error("需要目录，或 --validate-playbook")

    probe = None
    if args.no_env:
        probe = lambda: {"connected": False, "error": "跳过勘探（--no-env）"}   # noqa: E731
    res = run(args.folder, recursive=args.recursive, env_probe=probe,
              stop_after=args.until)
    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
        return 0 if res.action != "stop" else 1

    tag = {"proceed": "OK  ", "confirm": "确认", "stop": "停  "}[res.action]
    print(f"[{tag}] {res.folder}   下一步 = {res.next_step or '（本地步骤走完了）'}")
    for s in res.steps:
        mark = {"done": "✓", "failed": "✗", "needs-confirm": "?", "pending": "…"}[s.status]
        print(f"  {mark} {s.name:<9} {s.title}  [{s.status}/{s.action}]")
        if s.note:
            print(f"      {s.note}")
        for g in s.gates:
            if not g.ok:
                print(f"      !! {g.gate_id}（{g.consequence}）{g.message}")
    return 0 if res.action != "stop" else 1


if __name__ == "__main__":
    sys.exit(main())
