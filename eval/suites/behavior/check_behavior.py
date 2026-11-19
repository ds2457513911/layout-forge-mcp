#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
behavior 行为夹具 —— 「SKILL 的 S 系硬规则真的可执行吗」考卷（L3 编排与控制层）。

为什么需要它（L5 度量与评测层 → 考 L3）
    `allegro-footprint-check` 的 S 系硬规则是**写在文档里的约束**：
      · S1 目标消歧：目录里有多份 .dra / 多份 PDF → **禁止默认拿第一个就跑**，必须列候选让用户确认；
      · S2 禁复用旧报告：本次结论只能来自本次运行，目录里的 `checklist_report_*.json`
        是历史产物（**不是证据**）；
      · S4 先探通道：凡是要触 Allegro 的工具，先探通道；探针失败就如实说「未连接」，
        禁止盲调后把超时误读成「工具挂了」。
    文档约束靠「模型自觉」是守不住的（违反了也看不出来）。本套件用**可控的临时目录**把这些
    场景造出来，逼工具层表态：该报 `ambiguous` 就不能给一个「我猜的」答案，该说没通道就不能沉默。

判据（B 段）
    | 夹具 | 场景 | 通过条件 |
    |--|--|--|
    | B1 | S1 多份 .dra / 多份 PDF | `classify_folder` 返回 `ambiguous_dra`/`ambiguous_pdf` + `action=confirm`，
    |    |                              | 且 `dra_files`/`pdf_files` **列出全部候选**（不许只回一个） |
    | B2 | S2 目录里有历史报告 | 旧报告必须落进 `noise_files`（**不算证据**）；目录里只有旧报告时必须 `stop` |
    | B3 | S1 噪声（`AUTOSAVE.dra`） | 噪声不计入候选：1 真 + 1 AUTOSAVE → 必须判 `ok`，AUTOSAVE 落 `noise_files` |
    | B4 | S4 通道先探 | `services.footprint_builder.inspect_environment()` 返回结构化体检；
    |    |                 | `connected=False` 时 `error` 必须非空（**不许沉默**），且不得抛异常 |
    | B5 | L3 状态机 | `playbook.json` 满足硬约定（每条规则都有检测方式 + 后果类别 + 证据字段）；
    |    |            | 步骤引用的 gate 都存在；一对 PDF/.dra + 通道正常 → 放行到 `extract`；
    |    |            | 歧义 → `confirm` 且**停在 preflight**；通道不通 → `stop` + `CHANNEL_NOT_READY`；
    |    |            | 已删除/未知的 `rule_id` 必须被点名（不许静默计入统计） |
    | B6 | D4 通道锁 | 同进程第二线程**按超时**报 `CHANNEL_BUSY`（不无限排队）；跨进程撞锁报 BUSY 且带持有人 |
    |    |            | 持锁进程 `taskkill /F` 后 1.5s 内可重取锁；建图租约撞上立刻返回持有人（不排队）；
    |    |            | 会话 `open()` 持锁 / `close()` 放锁，嵌套 `with` 不漏锁 |

★ 三条纪律
    1. **不用真实库做夹具**：全部在系统 TEMP 里现场造临时目录（跑完即删），不碰 `Data/`。
    2. **不静默跳过**：B4 / B6 需要装了 `skillbridge` 的解释器；找不到就报 ERROR（含修复提示），
       而不是「没依赖所以算通过」。
    3. **夹具只读生产代码**：本套件 import `orchestration.preflight` / `orchestration.pipeline` /
       `services.footprint_builder`，绝不修改它们；失败就是生产代码的失败。
       B5 用**假探针**喂通道状态，因此不依赖 Allegro 是否在跑。

用法
    python eval/suites/behavior/check_behavior.py            # 跑全部夹具
    python eval/suites/behavior/check_behavior.py --json
    python eval/suites/behavior/check_behavior.py --python <带 skillbridge 的解释器>

退出码：0 = 无 ERROR；1 = 有 ERROR。
纯 stdlib（B4 用子进程跑，所以本文件本身不需要依赖）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # layout-forge-mcp/
LAYOUT = ROOT.parent                                # Layout/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 找「带 skillbridge 的解释器」的顺序：环境变量 → 缓存区不受限副本 → 兄弟仓 venv → 当前解释器
UNCONFINED_PY = os.environ.get(
    "LAYOUT_FORGE_UNCONFINED_PY",
    str(Path.home() / "WorkBuddy/Layout/.workbuddy/cache/tools/venv-unconfined/Scripts/python.exe"),
)
SIBLING_PY = str(LAYOUT / "layout-check-mcp/.venv/Scripts/python.exe")

CHANNEL_SNIPPET = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
try:
    from services import footprint_builder as fb
except Exception as exc:                     # 依赖缺失也是「通道没准备好」的一种
    print(json.dumps({"import_error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)
try:
    res = fb.inspect_environment()
    print(json.dumps({"result": res}, ensure_ascii=False, default=str))
except Exception as exc:
    print(json.dumps({"raised": f"{type(exc).__name__}: {exc}"}))
"""


#: B6 探针（票 #10 / D4 的通道锁与建图租约）。由外层夹具放进**带 skillbridge 的解释器**跑，
#: 因为要 import services.skillbridge_writer 验证「锁跟着会话生命周期」。
#: ★ 所有锁/租约文件都落在夹具自己的临时目录：不碰 Tools/log，也不和真正在跑的 MCP 服务抢锁。
B6_SNIPPET = r"""
import json, os, subprocess, sys, threading, time
ROOT, TMP = sys.argv[1], sys.argv[2]
sys.path.insert(0, ROOT)
from services import channel_guard as G

LOCK = os.path.join(TMP, "b6.lock")
HOLDER = os.path.join(TMP, "b6.lock.holder")
LEASE = os.path.join(TMP, "b6.lease")
# 全部落到临时目录：不碰 Tools/log，也不和真正在跑的 MCP 服务抢同一把锁
G.default_lock_path = lambda: LOCK
G.default_holder_path = lambda: HOLDER
G.default_lease_path = lambda: LEASE

cases, errs = [], []

def rec(name, ok, extra=""):
    cases.append({"check": name, "ok": bool(ok), "extra": str(extra)[:240]})
    if not ok:
        errs.append("B6 " + name + ("：" + str(extra) if extra else ""))

# ---- 1) 同进程跨线程：必须按超时报 BUSY，不许无限排队 ----
g = G.ChannelGuard(task="b6-holder"); g.acquire()
out = {}
def _worker():
    try:
        G.ChannelGuard(task="b6-second", timeout_s=0.5).acquire()
        out["err"] = None
    except BaseException as exc:            # noqa: BLE001
        out["err"] = exc
t0 = time.time()
_th = threading.Thread(target=_worker); _th.start(); _th.join(20)
dt = time.time() - t0
rec("同进程第二线程报 CHANNEL_BUSY", isinstance(out.get("err"), G.ChannelBusyError),
    type(out.get("err")).__name__)
rec("同进程第二线程按超时返回（<5s）", dt < 5, round(dt, 2))
rec("BUSY 消息里带得出持有人", "b6-holder" in str(out.get("err")), str(out.get("err"))[:100])
g.release()

# ---- 2) 跨进程：子进程持锁 → 父进程 BUSY；taskkill 后 1.5s 内可重取 ----
child_code = (
    "import sys, time\n"
    "sys.path.insert(0, r'%s')\n"
    "from services import channel_guard as G\n"
    "g = G.ChannelGuard(task='b6-child', lock_path=r'%s', holder_path=r'%s')\n"
    "g.acquire()\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n" % (ROOT, LOCK, HOLDER)
)
child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, text=True)
rec("子进程持锁就绪", child.stdout.readline().strip() == "READY")
t0 = time.time()
try:
    G.ChannelGuard(task="b6-parent", timeout_s=0.6).acquire()
    rec("跨进程撞锁应报 BUSY", False, "没抛异常（等于让两个进程同时用通道）")
except G.ChannelBusyError as exc:
    rec("跨进程撞锁应报 BUSY", True)
    rec("持有人指向子进程", (exc.holder or {}).get("pid") == child.pid, exc.holder)
    rec("跨进程按超时返回（<5s）", time.time() - t0 < 5, round(time.time() - t0, 2))
except BaseException as exc:                # noqa: BLE001
    rec("跨进程撞锁应报 BUSY", False, type(exc).__name__)
subprocess.run(["taskkill", "/F", "/PID", str(child.pid)], capture_output=True, text=True)
child.wait(timeout=15)
t0 = time.time()
try:
    g_after = G.ChannelGuard(task="b6-after-kill", timeout_s=3.0); g_after.acquire()
    dt = time.time() - t0
    rec("进程被 taskkill /F 后 1.5s 内可重取锁", dt < 1.5, round(dt, 2))
    g_after.release()
except BaseException as exc:                # noqa: BLE001
    rec("进程被 taskkill /F 后 1.5s 内可重取锁", False, type(exc).__name__)

# ---- 3) 建图租约：撞上只报告不排队 ----
lease_child = subprocess.Popen([sys.executable, "-c", (
    "import sys, time\n"
    "sys.path.insert(0, r'%s')\n"
    "from services import channel_guard as G\n"
    "l = G.TaskLease(task='export_psm', drawing='NB_XTAL', lease_path=r'%s')\n"
    "assert l.try_acquire() is None\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n" % (ROOT, LEASE))], stdout=subprocess.PIPE, text=True)
rec("子进程持建图租约就绪", lease_child.stdout.readline().strip() == "READY")
t0 = time.time()
holder = G.TaskLease(task="build_footprint", drawing="OTHER", lease_path=LEASE).try_acquire()
dt = time.time() - t0
rec("撞租约立刻返回持有人（不排队）", holder is not None and dt < 1.0,
    "%s / %.3fs" % (holder, dt))
rec("租约持有人带得出图纸名", bool(holder and holder.get("drawing") == "NB_XTAL"), holder)
subprocess.run(["taskkill", "/F", "/PID", str(lease_child.pid)], capture_output=True, text=True)
lease_child.wait(timeout=15)
_after = G.TaskLease(task="after-kill", lease_path=LEASE)
rec("建图进程被杀后租约可接管", _after.try_acquire() is None)
_after.release()

# ---- 4) 会话层接线：假 Workspace 下 open 持锁 / close 放锁 / 嵌套不泄漏 ----
from services import skillbridge_writer as W
class _Ch:
    def send(self, code):
        return code.strip().strip('"')
class _WS:
    def __init__(self):
        self._channel = _Ch()
    def close(self):
        pass
class _Mod:
    @staticmethod
    def open(workspace_id=None):
        return _WS()
_orig = W.Workspace
W.Workspace = _Mod
try:
    sess = W.AllegroSession(task="b6-session", lock_timeout_s=0.5)
    sess.open()
    rec("会话 open 后持锁", G.ChannelGuard.is_held())
    rec("会话持有人任务名正确", (G.read_holder(HOLDER) or {}).get("task") == "b6-session",
        G.read_holder(HOLDER))
    sess.close()
    rec("会话 close 后放锁", not G.ChannelGuard.is_held())
    with W.AllegroSession(task="b6-outer", lock_timeout_s=0.5):
        with W.AllegroSession(task="b6-inner", lock_timeout_s=0.5):
            rec("嵌套 with 内仍持锁", G.ChannelGuard.is_held())
        rec("内层退出后外层仍持锁", G.ChannelGuard.is_held())
    rec("全退出后锁已释放", not G.ChannelGuard.is_held())
finally:
    W.Workspace = _orig

for path in (LOCK, HOLDER, LEASE):
    try:
        os.remove(path)
    except OSError:
        pass
print(json.dumps({"cases": cases, "errors": errs}, ensure_ascii=False))
"""


# ---------- 夹具脚手架 ----------
def _mk(tmp: Path, *names: str) -> Path:
    d = tmp / names[0]
    d.mkdir(parents=True, exist_ok=True)
    for n in names[1:]:
        (d / n).write_bytes(b"")           # 内容无关：预检只看名字与后缀
    return d


def _classify(folder: Path) -> dict:
    from orchestration import preflight as pf
    return pf.classify_folder(str(folder))


def _pick_interpreter(explicit: str | None) -> tuple[str | None, list[str]]:
    """返回 (可用解释器, 尝试过的清单)。"""
    tried = []
    cands = [explicit] if explicit else [UNCONFINED_PY, SIBLING_PY, sys.executable]
    for cand in cands:
        if not cand or not Path(cand).is_file():
            tried.append(f"{cand}（不存在）")
            continue
        try:
            p = subprocess.run([cand, "-c", "import skillbridge"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except Exception as exc:                       # noqa: BLE001
            tried.append(f"{cand}（无法执行：{exc}）")
            continue
        if p.returncode == 0:
            return cand, tried
        tried.append(f"{cand}（import skillbridge 失败）")
    return None, tried


# ---------- 夹具 B1–B4 ----------
def b1_ambiguous(tmp: Path) -> dict:
    """S1：多份 .dra / 多份 PDF 必须列候选 + confirm，不许默默选一个。"""
    errs, cases = [], []
    d1 = _mk(tmp, "b1_dra", "spec.pdf", "a.dra", "b.dra")
    d2 = _mk(tmp, "b1_pdf", "one.dra", "spec_a.pdf", "spec_b.pdf")
    for label, folder, want in (("多份 .dra", d1, "ambiguous_dra"), ("多份 PDF", d2, "ambiguous_pdf")):
        r = _classify(folder)
        cand = r.get("dra_files") if want == "ambiguous_dra" else r.get("pdf_files")
        cases.append({"dir": label, "verdict": r.get("verdict"), "action": r.get("action"),
                      "candidates": cand})
        if r.get("verdict") != want:
            errs.append(f"B1 [{label}] 期望 verdict={want}，实际 {r.get('verdict')}")
        if r.get("action") != "confirm":
            errs.append(f"B1 [{label}] 期望 action=confirm（先问用户），实际 {r.get('action')}")
        if not cand or len(cand) < 2:
            errs.append(f"B1 [{label}] 候选没列全：{cand}（★ 只回一个 = 默认拿第一个就跑）")
    return {"name": "B1", "rule": "S1 目标消歧", "cases": cases, "errors": errs}


def b2_stale_report(tmp: Path) -> dict:
    """S2：目录里的历史 checklist_report_*.json 不算证据，且只有它时必须 stop。"""
    errs, cases = [], []
    d1 = _mk(tmp, "b2_with_pair", "spec.pdf", "real.dra", "checklist_report_20260101_120000.json")
    r1 = _classify(d1)
    cases.append({"dir": "有一对 PDF/.dra + 旧报告", "verdict": r1.get("verdict"),
                  "noise_files": r1.get("noise_files")})
    if r1.get("verdict") != "ok":
        errs.append(f"B2 有一对 PDF/.dra 时应判 ok，实际 {r1.get('verdict')}")
    if "checklist_report_20260101_120000.json" not in (r1.get("noise_files") or []):
        errs.append("B2 旧报告没被标成噪声 → 会被当成本次证据（S2 的物证没落地）")

    d2 = _mk(tmp, "b2_only_report", "checklist_report_20260101_120000.json", "allegro.jrl")
    r2 = _classify(d2)
    cases.append({"dir": "只有旧报告", "verdict": r2.get("verdict"), "action": r2.get("action"),
                  "noise_files": r2.get("noise_files")})
    if r2.get("verdict") not in ("missing_pdf", "missing_dra", "empty"):
        errs.append(f"B2 只有旧报告时必须判缺料并 stop，实际 {r2.get('verdict')}")
    if r2.get("action") != "stop":
        errs.append(f"B2 只有旧报告时 action 应为 stop，实际 {r2.get('action')}")
    return {"name": "B2", "rule": "S2 禁复用旧报告", "cases": cases, "errors": errs}


def b3_autosave(tmp: Path) -> dict:
    """S1 噪声：AUTOSAVE.dra 不能算候选（金标准库真发生过 5 例）。"""
    errs, cases = [], []
    d = _mk(tmp, "b3_autosave", "spec.pdf", "real.dra", "AUTOSAVE.dra")
    r = _classify(d)
    cases.append({"dir": "1 真 + 1 AUTOSAVE", "verdict": r.get("verdict"),
                  "dra_files": r.get("dra_files"), "noise_files": r.get("noise_files")})
    if r.get("verdict") != "ok":
        errs.append(f"B3 去掉 AUTOSAVE 后应剩唯一 .dra、判 ok，实际 {r.get('verdict')}")
    if (r.get("dra_files") or []) != ["real.dra"]:
        errs.append(f"B3 候选应只剩 real.dra，实际 {r.get('dra_files')}")
    if "AUTOSAVE.dra" not in (r.get("noise_files") or []):
        errs.append("B3 AUTOSAVE.dra 没被标成噪声（会被取成「目录里第一个 .dra」）")
    return {"name": "B3", "rule": "S1 噪声剔除", "cases": cases, "errors": errs}


def b4_channel(explicit_py: str | None) -> dict:
    """S4：先探通道；结果必须结构化，连不上时 error 必须非空，且不许抛异常。"""
    errs, cases = [], []
    py, tried = _pick_interpreter(explicit_py)
    if py is None:
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases,
                "errors": ["B4 找不到带 skillbridge 的解释器 → 无法验证「先探通道」（**不静默跳过**）。"
                           "修复：重跑那条 robocopy 重建缓存区 venv-unconfined，或用 --python 指定。"
                           f" 试过：{'；'.join(tried)}"]}
    p = subprocess.run([py, "-c", CHANNEL_SNIPPET, str(ROOT)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (p.stdout or "").strip().splitlines()
    payload = {}
    if out:
        try:
            payload = json.loads(out[-1])
        except Exception:                                   # noqa: BLE001
            payload = {}
    cases.append({"python": py, "rc": p.returncode,
                  "import_error": payload.get("import_error"),
                  "raised": payload.get("raised"),
                  "result_keys": sorted((payload.get("result") or {}).keys())})
    if payload.get("import_error"):
        errs.append(f"B4 通道体检 import 失败：{payload['import_error']}（依赖缺失也要以明确错误暴露）")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    if payload.get("raised"):
        errs.append(f"B4 inspect_environment() 抛异常：{payload['raised']}（探针必须 self-contained）")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    res = payload.get("result") or {}
    if not isinstance(res, dict) or not res:
        errs.append(f"B4 体检没返回结构化结果：{out[-1] if out else '(无输出)'}")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    if not isinstance(res.get("connected"), bool):
        errs.append(f"B4 缺 `connected` 布尔字段：{sorted(res.keys())}")
    if res.get("connected") is False and not str(res.get("error") or "").strip():
        errs.append("B4 connected=False 但 error 为空 → 模型会把「没通道」误读成「工具挂了」")
    if res.get("connected") is True and res.get("units") not in ("mm", "mil", "unknown"):
        errs.append(f"B4 connected=True 时 units 取值异常：{res.get('units')!r}")
    cases[-1]["verdict"] = "channel-up" if res.get("connected") else "channel-down-explicit"
    return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}


# ---------- 夹具 B5 ----------
def b5_pipeline(tmp: Path) -> dict:
    """L3 状态机：playbook 硬约定 + 步骤放行/拦停的判定（不碰 Allegro，喂假探针）。

    这一夹具守的是「编排层自己」：规则表缺「检测方式/后果类别」、步骤引用了不存在的 gate、
    歧义时还继续往下取数、通道没探就放行 —— 任何一条都该在这里变红。
    """
    from orchestration import pipeline as pl

    errs, cases = [], []

    # 1) playbook 硬约定 + 步骤表自洽
    problems = pl.validate_playbook()
    cases.append({"check": "playbook 硬约定", "problems": problems})
    if problems:
        errs.append("B5 playbook 违反硬约定（每条规则都必须有检测方式 + 后果类别 + 证据字段）："
                    + "；".join(problems))

    known = {str(g["id"]) for g in pl.load_playbook().get("gates") or []}
    referenced = {gid for s in pl.STEPS for gid in (s.pre + s.post)}
    dangling = sorted(referenced - known)
    outside = sorted(known - referenced)
    cases.append({"check": "步骤引用的 gate 都在 playbook 里", "dangling": dangling,
                  "不在 pipeline 步骤里（由套件/部署侧守）": outside})
    if dangling:
        errs.append(f"B5 步骤引用了 playbook 里不存在的 gate：{dangling}")
    names = tuple(s.name for s in pl.STEPS)
    if names != pl.STEP_NAMES:
        errs.append(f"B5 步骤名与 STEP_NAMES 不一致：{names} vs {pl.STEP_NAMES}")

    # 2) 一对 PDF/.dra + 通道正常 → 放行到 extract
    up = lambda: {"connected": True, "units": "millimeters"}          # noqa: E731
    d_ok = _mk(tmp, "b5_ok", "spec.pdf", "real.dra")
    r_ok = pl.run(d_ok, env_probe=up)
    cases.append({"check": "一对 PDF/.dra + 通道正常", "action": r_ok.action, "next_step": r_ok.next_step})
    if r_ok.action != "proceed" or r_ok.next_step != pl.STEP_EXTRACT:
        errs.append(f"B5 正常输入应放行到 extract，实际 action={r_ok.action} next_step={r_ok.next_step}")

    # 3) 歧义 → confirm 且不许继续往下走（“拿不准还继续取数”正是要防的）
    d_amb = _mk(tmp, "b5_ambiguous", "real.dra", "a.pdf", "b.pdf")
    r_amb = pl.run(d_amb, env_probe=up)
    ran = [s.name for s in r_amb.steps if s.status in ("done", "needs-confirm")]
    fails = [g.gate_id for s in r_amb.steps for g in s.gates if not g.ok]
    cases.append({"check": "歧义输入", "action": r_amb.action, "已执行步骤": ran,
                  "未通过的 gate": fails})
    if r_amb.action != "confirm":
        errs.append(f"B5 歧义输入应 confirm（问用户），实际 {r_amb.action}")
    if ran != [pl.STEP_PREFLIGHT]:
        errs.append(f"B5 歧义时必须停在 preflight（后续步骤一律 pending），实际已执行 {ran}")
    if "G-ambiguous-src" not in fails:
        errs.append("B5 歧义时 G-ambiguous-src 必须判失败（否则等于默认拿了第一个）")

    # 4) 通道没探通 → stop + 明确失败码（不静默跳过）
    down = lambda: {"connected": False, "error": "CDSROOT 未配置"}     # noqa: E731
    r_down = pl.run(d_ok, env_probe=down)
    codes = [e.get("error_code") for e in r_down.events]
    cases.append({"check": "通道不可用", "action": r_down.action, "error_codes": codes})
    if r_down.action != "stop":
        errs.append(f"B5 通道不可用必须 stop，实际 {r_down.action}")
    if pl.ERR_CHANNEL_NOT_READY not in codes:
        errs.append(f"B5 通道不可用应给出 {pl.ERR_CHANNEL_NOT_READY}，实际 {codes}")

    # 5) 结论归类：引用了已删除的 rule_id 或被代码遗忘的 rule_id 都必须点名
    cls = pl.summarize_findings([{"rule_id": "2.7"}, {"rule_id": "6.5"}, {"rule_id": "9.9"}])
    cases.append({"check": "结论归类", "silent": cls["silent"],
                  "stale": cls["stale_rule_ids"], "unknown": cls["unknown_rule_ids"]})
    if "6.5" not in cls["stale_rule_ids"]:
        errs.append("B5 6.5 是已删除的 rule_id（rule_checker.py:1329），案例再引用它必须被点名")
    if "9.9" not in cls["unknown_rule_ids"]:
        errs.append("B5 代码里没有的 rule_id 不许静默计入统计")
    if "2.7" not in cls["silent"]:
        errs.append("B5 2.7 的后果类别是 silent，归类没按 playbook 走")
    return {"name": "B5", "rule": "L3 状态机 + playbook 硬约定", "cases": cases, "errors": errs}


# ---------- 夹具 B6 ----------
def b6_channel_guard(tmp: Path, explicit_py: str | None) -> dict:
    """D4（票 #10）：通道锁 + 建图租约 —— 撞锁必须**报出来**，不许两边都往下跑。

    守的四条不变量（D4 定案的落地判据）：
      1. 同一时刻一个通道只有一个会话持有；同进程第二个线程**按超时**报 CHANNEL_BUSY，不无限排队
         （fastmcp 把同步工具丢进 anyio 线程池，同进程并发是真的会发生的）；
      2. 跨进程撞锁同样报 BUSY，并把持有人（pid / 任务 / 时间）带出来；
      3. 持锁进程被 `taskkill /F` 后锁必须**自动回收**（1.5s 内可重取），否则一次崩溃要人手动删文件；
      4. 建图租约撞上**只报告不排队**：立刻返回 `busy` + 持有人，不去排队等前一个跑完。

    探针放进**带 skillbridge 的解释器**跑，因为第 4 段要 import services.skillbridge_writer；
    锁/租约文件全部落在夹具临时目录，不碰 Tools/log，也不和真正在跑的 MCP 服务抢同一把锁。
    """
    errs, cases = [], []
    py, tried = _pick_interpreter(explicit_py)
    if py is None:
        return {"name": "B6", "rule": "D4 通道锁 + 建图租约", "cases": cases,
                "errors": ["B6 找不到带 skillbridge 的解释器 → 无法验证通道锁（**不静默跳过**）。"
                           "修复：重跑那条 robocopy 重建缓存区 venv-unconfined，或用 --python 指定。"
                           f" 试过：{'；'.join(tried)}"]}
    p = subprocess.run([py, "-c", B6_SNIPPET, str(ROOT), str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    lines = (p.stdout or "").strip().splitlines()
    payload = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except Exception:                                   # noqa: BLE001
            payload = {}
    if not payload:
        errs.append("B6 探针没给出 JSON 结论 "
                    f"(rc={p.returncode})：{(p.stderr or '')[-600:]}")
        return {"name": "B6", "rule": "D4 通道锁 + 建图租约", "cases": cases, "errors": errs}
    cases.append({"python": py, "rc": p.returncode,
                  "checks": len(payload.get("cases") or []),
                  "failed": len(payload.get("errors") or [])})
    cases += payload.get("cases") or []
    errs += payload.get("errors") or []
    return {"name": "B6", "rule": "D4 通道锁 + 建图租约", "cases": cases, "errors": errs}


# ---------- 主流程 ----------
def run(explicit_py: str | None = None) -> dict:
    results, errors = [], []
    with tempfile.TemporaryDirectory(prefix="behavior_fixtures_") as tmpdir:
        tmp = Path(tmpdir)
        for fn in (b1_ambiguous(tmp), b2_stale_report(tmp), b3_autosave(tmp), b5_pipeline(tmp)):
            results.append(fn)
            errors += fn["errors"]
        # B6 要在临时目录里造锁文件，故留在 with 块内（子进程探针 + 假 Workspace）
        b6 = b6_channel_guard(tmp, explicit_py)
        results.append(b6)
        errors += b6["errors"]
    b4 = b4_channel(explicit_py)          # 子进程，自己管临时文件
    results.append(b4)
    errors += b4["errors"]
    return {"fixtures": results, "errors": errors,
            "error_count": len(errors),
            "fixture_count": len(results),
            "passed": sum(1 for r in results if not r["errors"])}


def _render(rep: dict) -> None:
    print("=" * 74)
    print("behavior 行为夹具（L3 规则可执行性）")
    print("=" * 74)
    for r in rep["fixtures"]:
        mark = "✓" if not r["errors"] else "✗"
        print(f"\n{mark} {r['name']}  {r['rule']}")
        for c in r["cases"]:
            print("   ", json.dumps(c, ensure_ascii=False)[:300])
        for e in r["errors"]:
            print("    [X]", e)
    print("\n" + "-" * 74)
    print(f"夹具 {rep['passed']}/{rep['fixture_count']} 通过；ERROR {rep['error_count']} 项")
    if not rep["errors"]:
        print("[✓] 全部通过（S1 歧义可见 / S2 旧报告不算证据 / S4 通道先探 / L3 状态机放行与拦停 / "
              "D4 撞锁报 CHANNEL_BUSY 且进程被杀后锁自动回收）")


def main(argv: list[str] | None = None) -> int:
    # 中文 Windows 的默认 stdout 是 GBK：打印 ✓ 会 UnicodeEncodeError，
    # 让「全部通过」变成栈回溯（评测脚本看起来像套件坏了）。自己保证 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="behavior 行为夹具（S1/S2/S4 硬规则 + D4 通道锁可执行性）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--python", default=None, help="带 skillbridge 的解释器（B4/B6 用）")
    args = ap.parse_args(argv)

    rep = run(args.python)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _render(rep)
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
