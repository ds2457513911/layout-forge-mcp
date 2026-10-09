# -*- coding: utf-8 -*-
"""
services/skillbridge_writer.py —— 通过 SkillBridge 在 Allegro 中执行 SKILL

与 skillbridge_reader.py 互补：
  - reader: 读 .dra / 提取数据（只读）
  - writer: 建 padstack / pin / 丝印 / 存盘（写入）

设计要点（借鉴 probe 实测经验）：
  1. SkillBridge 的 ws["FuncName"] 调用等价于 Allegro CIW 里直接跑 SKILL。
  2. 不能假设调用一定成功 —— Allegro 侧的 SKILL 报错会以异常或 nil 形式返回，
     所以每个动作都要 errset 语义的包装（这里用 Python 侧 try + 返回值校验）。
  3. 所有尺寸数值的单位 = 当前 drawing 的 design units（mm 或 mil）。
     调用方必须自己确保单位正确，本模块只负责"发出去"和"看返回值"。
  4. Allegro 对非 ASCII 路径不友好：涉及文件路径一律走临时 ASCII 目录。

重要：本模块不做业务编排，只做"执行 + 结果归一化"。编排见 footprint_builder.py。
"""
from __future__ import annotations

import ast
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

try:
    from skillbridge import Workspace
except ImportError:  # pragma: no cover
    raise ImportError("请先安装 skillbridge: pip install skillbridge")


logger = logging.getLogger("layout-forge")


# Allegro .dra 的 design units 常量
UNIT_MM = "millimeters"
UNIT_MIL = "mils"

MIL_TO_MM = 0.0254

#: 会话级"无人值守"开关：进入会话时自动设上 `NOCONFIRM` 变量族，退出时恢复现场。
#:
#: ★ 为什么默认开：本模块的所有调用都是**脚本化**的，而 AXL 里好几个关键 API
#:   （`axlCompileSymbol`、`axlPadstackToDisk` …）根本没有 `?noConfirm` 参数，
#:   它们的目标文件已存在时会弹「File Exists. Overwrite?」——
#:   在 SkillBridge 通道里**弹框 = 自杀**：调用永不返回，回复队列彻底错位。
#:   官方给的解药就是环境变量（`?mode "wf"` 只在 `axlOpenDesign*` 里被识别）。
#:
#: 关闭方式（用于需要人工逐步确认的调试）：
#:   1) `AllegroSession(auto_noconfirm=False)`
#:   2) 环境变量 `FORGE_NOCONFIRM=0`
AUTO_NOCONFIRM = (
    os.environ.get("FORGE_NOCONFIRM", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


# ============================================================
# 结果容器
# ============================================================
def _ok(**kw) -> Dict[str, Any]:
    """构造成功结果。"""
    return {"success": True, "error": None, **kw}


def _fail(msg: str, **kw) -> Dict[str, Any]:
    """构造失败结果。"""
    return {"success": False, "error": msg, **kw}


def _has_non_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _norm_units(raw: Any) -> str:
    """
    把 axlDBGetDesignUnits() 的返回值归一化成 'mm' / 'mil' / 'unknown'。

    实测返回：['millimeters', 4]  —— 列表，首元素是单位名，次元素是精度。
    也见过直接返回字符串的情况，两种都兼容。
    """
    if raw is None:
        return "unknown"

    s = str(raw).lower()
    if "millimeter" in s or "millimetre" in s:
        return "mm"
    if "mil" in s or "thou" in s:
        return "mil"
    return "unknown"


def _decode_skill_reply(raw: Any, translator: Any = None) -> Any:
    """
    把 skillbridge `_channel.send()` 的原始回复转成 Python 对象。

    ★★★ 协议真相（2026-09-28 读 python_server.il 源码后确认）★★★

    Allegro 侧 `__pyOnData` 对**每一行**输入单独求值，然后回一条：

        ipcWriteProcess(id, lsprintf("success %s\\n", pythonCode))
        ipcWriteProcess(id, lsprintf("failure %L\\n", errset.errset))

    即回复帧格式为：

        'success ["millimeters", 4]\\n'   -> 求值成功
        'success nil\\n'                  -> None
        'success Remote("__py_...")'      -> 远端对象引用
        'failure ("eval" 0 t nil (...))'  -> 求值失败

    所以**必须两步走**：
        1) 剥掉 `success ` / `failure ` 前缀（失败则抛 RuntimeError）
        2) 剩下部分用 translator.decode() 交给 skillbridge 自己的 eval 环境解析
           （它能识别 `Remote(...)` / `Table(...)` / `Symbol(...)` / `warning(...)`）

    ★ 缓冲错位的原因（血泪）：
        `__pyOnData` 里是 `foreach(line parseString(data "\\n") ...)` ——
        **一次 send 里的多行数据会产生多条回复**，而客户端 `readline()` 只读
        第一条。剩下的回复就积压下来，导致后续每次调用都读到"上一次的"
        结果（表现为 axlCurrentDesign() 返回上次 axlGetDrawingName() 的值）。
        因此 eval_skill 里的代码**必须是单行**，并且每次调用前后要排空缓冲。

    :param translator: skillbridge 的 Translator 实例；给了就用它做正规解码。
    """
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return None

    # ---- 1) 剥状态前缀（容错：某些版本/中间层可能不带前缀）----
    status, _, payload = text.partition(" ")
    if status == "failure":
        raise RuntimeError(f"Allegro 侧求值失败: {payload or '(无详情)'}")
    if status == "success":
        payload = payload.strip()
    else:
        # 没有前缀：可能是被 printf 调试输出污染的帧，退化为整串当载荷
        payload = text

    if not payload:
        return None

    # ---- 2) 正规解码 ----
    if translator is not None and hasattr(translator, "decode"):
        try:
            return translator.decode(payload)
        except Exception:
            # decode 需要 eval 环境；失败就退到下面的字面量解析
            pass

    # ---- 3) 字面量解析（处理 translator 缺失或失败的情况）----
    # SKILL 的 nil / t 不是 Python 字面量，先手工归一
    low = payload.lower()
    if low == "nil":
        return None
    if low == "t":
        return True

    try:
        return ast.literal_eval(payload)
    except (ValueError, SyntaxError):
        # 含 Remote(...) 之类无法字面解析的内容，原样返回字符串
        return payload


# ============================================================
# 连接管理
# ============================================================
class AllegroSession:
    """
    Allegro SkillBridge 会话上下文管理器。

    用法：
        with AllegroSession() as s:
            print(s.units())
            s.call("axlDBCreatePadStack", name, None, pad_list, True)

    注意：SkillBridge 对同一个 workspace_id 重复 open 是允许的，
    但为了安全，每个 with 块结束后都会 close。

    ★ 进入会话时会自动设上 `NOCONFIRM` 变量族（抑制弹框），
      退出时**恢复现场** —— 详见 `AUTO_NOCONFIRM` 与 `_apply_noconfirm()`。
    """

    def __init__(self, workspace_id: Optional[str] = None,
                 auto_noconfirm: Optional[bool] = None):
        from mcp_server import config

        self.workspace_id = workspace_id or config.SKILLBRIDGE_WORKSPACE_ID
        self._ws = None
        #: None = 用模块级默认（AUTO_NOCONFIRM）
        self.auto_noconfirm = AUTO_NOCONFIRM if auto_noconfirm is None \
            else bool(auto_noconfirm)
        #: {变量名: 进入会话时的旧值}；None 表示"没做自动开关"
        self._nc_previous: Optional[Dict[str, bool]] = None

    # ---------- 生命周期 ----------
    def __enter__(self) -> "AllegroSession":
        self.open()
        self._apply_noconfirm()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # ★ 顺序要紧：先恢复变量（此时通道还活着），再关连接
        self._restore_noconfirm()
        self.close()

    def open(self) -> None:
        try:
            self._ws = Workspace.open(workspace_id=self.workspace_id)
        except Exception as e:
            raise ConnectionError(
                f"无法连接 Allegro SkillBridge (workspace_id={self.workspace_id})。\n"
                f"请确认：1) Allegro 正在运行；2) SkillBridge 服务已启动；"
                f"3) 端口 {self.workspace_id} 未被占用。\n"
                f"原始错误: {e}"
            ) from e

        # ★ 连接后做一次"通道对齐"自检。
        # Allegro 侧 ipcWriteProcess 的回复走一条固定深度约 15 帧的管道，
        # 一旦有回复没被读走就会滞留在管道里，表现为后续调用拿到旧结果：
        #     axlCurrentDesign() 返回上上次 axlGetDrawingName() 的值
        # 正常情况下这个自检第 1 轮就通过；若失败会抛带修复指引的错误，
        # 避免后续所有调用都在静默错位中运行。
        self._handshake()

    def _handshake(self, lag: int = 16, timeout_s: float = 20.0) -> int:
        """
        握手对齐：把 Allegro 回复管道的**固定滞后**填平。

        ★★★ 最终根因（2026-09-28 逐条发 80 探针实测确认）★★★

        Allegro 侧 `ipcWriteProcess` 到 Python 服务端 `stdin` 之间有一条
        **固定深度约 15 帧的管道缓冲**。表现为：

            发第 k 条命令 -> 读回来的第 k 条回复是 **第 (k-15) 条命令**的结果

        实证：连发 `__P000__`..`__P079__`，逐个读，
              recv[20] == '__P005__'、recv[40] == '__P025__'、recv[79] == '__P064__'
              → 滞后恒为 15 帧。

        再补一组实测（同上日期）：滞后恒为 15，发第 k 条命令读回来的是
        第 (k-15) 条的结果 —— `sent=ZZ037 -> recv=ZZ022`（37-22 = 15）。

        ★ 为什么"发一批读一批"的三阶段方案也不行：
          客户端 `Channel.send()` 是「发一条 + 收一条」的原子操作，
          队列长度（已发 - 已收）**守恒**，任何对称发送都无法缩短滞后。
          真正能清空队列的只有重启 Allegro 侧的 SkillBridge 服务。

        所以本函数的定位是**诊断 + 快速对齐**：
          - 若队列本来就干净 → 第 1 轮即读到自己的探针，返回 1；
          - 若队列有积压   → 抛 ConnectionError，提示用户重启服务。

        :param lag: 允许的最大尝试轮数基准（实测滞后 15，故默认 16）
        :return: 实际消耗的对齐轮数
        :raises ConnectionError: 尝试完毕仍对不齐，说明 Allegro 侧队列已积压，
                 需在 CIW 里 pyKillServer() 后重启服务
        """
        import time

        deadline = time.time() + timeout_s
        base = int(time.time() * 1000) % 1000000

        # ★ 策略：连续做「发一条带唯一序号的探针 -> 读一条」。
        #   若读回来的就是本轮那条 —— 通道已对齐（滞后被前面的实验顶平了）。
        #   若始终读不到，说明 Allegro 侧积压了陈旧回复，超过 lag 轮后放弃。
        rounds = 0
        limit = lag + 8
        while rounds < limit and time.time() < deadline:
            rounds += 1
            token = f"__forge_sync_{base}_{rounds}__"
            try:
                raw = self._send_raw(f'"{token}"')
            except SkillCallError:
                continue
            if token in str(raw):
                logger.debug("SkillBridge 通道对齐完成，消耗 %d 轮", rounds)
                return rounds

        raise ConnectionError(
            f"SkillBridge 通道无法对齐：连续 {limit} 轮发送的探针都读不回自己的结果，"
            f"说明 Allegro 侧 ipcWriteProcess 队列里积压了大量陈旧回复"
            f"（通常是之前的调试/异常调用留下的）。\n"
            f"请在 Allegro CIW 里执行：\n"
            f"    pyKillServer()\n"
            f"    load(\"<skillbridge>/server/python_server.il\")\n"
            f"    pyStartServer ?id \"7777\"\n"
            f"重启服务后队列即被清空。"
        )

    def _send_raw(self, code: str) -> str:
        """裸发一条单行 SKILL，返回原始回复帧（不抛语义错误）。"""
        if self._ws is None:
            raise RuntimeError("session 未打开")
        channel = getattr(self._ws, "_channel", None)
        if channel is None or not hasattr(channel, "send"):
            raise SkillCallError("eval_skill", RuntimeError("无可用求值通道"))
        try:
            return channel.send(code)
        except Exception as e:
            raise SkillCallError("eval_skill", e) from e

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ---------- NOCONFIRM 变量族（弹框总开关） ----------
    def _apply_noconfirm(self) -> None:
        """
        进入会话时设上 `NOCONFIRM` 变量族，**并把旧值记下来**以便退出时恢复。

        两步、共 2 次往返（不是 2×N 次）：
          ① `mapcar(axlGetVariable ...)` 一次读回全部旧值
          ② `foreach(axlSetVariable ...)` 一次全部设成 `t`

        ★ 本函数**绝不抛异常** —— 变量开关只是"锦上添花"，
        设不上不该让整个会话起不来（顶多退回"有弹框"的老世界）。
        """
        if not self.auto_noconfirm:
            self._nc_previous = None
            return

        from services import skill_snippets as S

        names = list(S.AUTO_NOCONFIRM_VARS)
        prev: Optional[Dict[str, bool]] = None
        try:
            raw = self.eval_skill(S.build_get_variables(names))
            prev = S.build_normalize_var_reply(raw, names)
            if len(prev) != len(names):
                # 拿不到完整旧值 → 退化为"全部按未设处理"，
                # 退出时统一 unset —— 方向是**恢复默认（会弹框）**，安全侧。
                prev = {n: False for n in names}
        except Exception as e:                          # noqa: BLE001
            logger.debug("NOCONFIRM 旧值读取失败，退出时将统一清除: %s", e)
            prev = {n: False for n in names}
        self._nc_previous = prev

        try:
            self.eval_skill(S.build_set_variables(names))
            logger.debug("已设上 NOCONFIRM 变量族: %s", ", ".join(names))
        except Exception as e:                          # noqa: BLE001
            logger.debug("NOCONFIRM 变量设置失败（继续跑，可能仍有弹框风险）: %s", e)

    def _restore_noconfirm(self) -> None:
        """
        退出会话时把变量恢复原状（该设回的设回，该清掉的清掉）。

        ★ 照抄 Cadence 论坛版主 eDave `AFn_OpenDesign` 的保存-恢复范式。
        ★ 同样**绝不抛异常** —— 恢复失败只记日志，绝不能把正常返回搞成异常。
        """
        prev = self._nc_previous
        if not prev:
            return

        from services import skill_snippets as S

        try:
            self.eval_skill(S.build_restore_variables(prev))
            logger.debug("已恢复 NOCONFIRM 变量族原状")
        except Exception as e:                          # noqa: BLE001
            logger.debug("NOCONFIRM 恢复失败（通道可能已失效，忽略）: %s", e)
        finally:
            self._nc_previous = None

    def restart_server(
        self,
        il_path: Optional[str] = None,
        python_exe: Optional[str] = None,
        workspace_id: Optional[str] = None,
        force_tcp: bool = True,
        reconnect_timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        ★★★ 让 Allegro 侧「自己重启」SkillBridge 服务（2026-09-29 实测确认）★★★

        为什么需要这个
        ----------------------------------------------------------------
        服务一旦被杀（无论是 pyKillServer 还是通道错位），Python 侧的 socket
        就断了 —— **再也没法用 Python 发命令去启动它**。所以「重启」必须是
        **一条原子复合表达式**，在服务还活着的时候一次性发出去：

            prog1( pyKillServer() load("<il>")
                   pyStartServer ?id "<id>" ?python "<pyw>" ?forceTcp t )

        `prog1` 顺序求值、返回最后一值，正好把「杀 → 重载 → 启动」串成一条。

        ★ 关键：这是 fire-and-forget
        ----------------------------------------------------------------
        `pyKillServer()` 一执行服务就死了，**这条命令的回复永远收不到**，
        客户端必然超时/断连 —— 这属**预期行为**，不代表失败。
        因此本方法：
          1. 发送时忽略超时/断连异常；
          2. 然后**轮询重连**，直到新服务起来或超时。

        机制依据（读 skillbridge/server/python_server.il 源码）：
        ----------------------------------------------------------------
            defun(pyKillServer ()   ; 杀服务，pyStartServer.ipc = nil
            defun(pyReloadScript () ; pyKillServer() + load(...)   ← 只重载，不启动
            defun(pyStartServer (@key (id "default") (logLevel "INFO")
                                      (singleMode nil) (timeout nil)
                                      (python "python") (forceTcp nil) "ttggtg")
                ; ★ ?python 默认值是 "python"，PATH 里未必有 → 必须显式传绝对路径

        :param il_path:      python_server.il 的绝对路径；None 时自动定位
        :param python_exe:   pythonw.exe 的绝对路径；None 时自动定位
        :param workspace_id: 目标 workspace（默认用本 session 的）
        :param force_tcp:    强制 TCP 模式（本项目 python_server.py 已改为 TCPServer）
        :param reconnect_timeout_s: 重启后等待重连的总秒数
        :return: {"sent": bool, "reconnected": bool, "seconds": float,
                  "il": str, "python": str, "error": str|None}
        """
        import time
        import socket as _socket

        t_start = time.time()
        wid = workspace_id or self.workspace_id
        il = il_path or self._guess_python_server_il()
        pyw = python_exe or self._guess_pythonw()

        result: Dict[str, Any] = {
            "sent": False, "reconnected": False, "seconds": 0.0,
            "il": il, "python": pyw, "error": None,
        }
        if not il:
            result["error"] = "找不到 python_server.il，请显式传 il_path"
            return result
        if not pyw:
            result["error"] = "找不到 pythonw.exe，请显式传 python_exe"
            return result

        # ---- 0) ★ 硬前提：必须已经连得上，否则命令根本发不出去 ----
        #   服务一旦被杀，Allegro 侧就没有 __pyOnData 接收器了，
        #   Python 侧再怎么发都是往空气里喊 —— 只能人工在 CIW 里敲。
        if self._ws is None:
            try:
                self.open()
            except Exception as e:
                result["error"] = (
                    "服务当前已断，无法自动重启 —— 自动重启的硬前提是"
                    "「服务还活着时把命令发出去」。请在 Allegro CIW 里手工执行：\n"
                    f'    load("{il}")\n'
                    f'    pyStartServer ?id "{wid}" ?python "{pyw}"'
                    + (" ?forceTcp t" if force_tcp else "")
                    + f"\n(原始错误: {type(e).__name__}: {e})"
                )
                result["seconds"] = round(time.time() - t_start, 2)
                return result

        # ---- 1) 发复合重启命令（单行！eval_skill 强制单行）----
        combo = (
            f'prog1(pyKillServer() '
            f'load("{il}") '
            f'pyStartServer ?id "{wid}" ?python "{pyw}"'
            + (" ?forceTcp t" if force_tcp else "")
            + ")"
        )
        t0 = t_start
        try:
            self._send_raw(combo)
            result["sent"] = True
        except Exception as e:
            # ★ 预期内：服务被杀了，回复收不到
            result["sent"] = True  # 命令已发出，只是没收到回执
            result["error"] = f"发送后未收到回执（预期内）: {type(e).__name__}"
            logger.debug("restart_server: 命令已发出但未收到回执: %s", e)

        # 旧连接作废
        self.close()

        # ---- 2) 轮询重连 ----
        deadline = t0 + reconnect_timeout_s
        last_err = None
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                self._ws = Workspace.open(workspace_id=wid)
                # 连通性验证
                probe = self._send_raw("1+1")
                if str(probe).strip():
                    result["reconnected"] = True
                    break
            except Exception as e:
                last_err = e
                self._ws = None

        if not result["reconnected"] and last_err is not None:
            result["error"] = f"重连失败: {type(last_err).__name__}: {last_err}"

        result["seconds"] = round(time.time() - t0, 2)
        return result

    # ---------- 路径推断（restart_server 用）----------
    @staticmethod
    def _fix_windows_case(path: str) -> str:
        """
        ★ 把 Windows 路径的「实际大小写」纠正回来（2026-09-29 实测需要）。

        Python 从 `skillbridge.__file__` 拿到的常是小写的 `.../lib/site-packages/...`，
        而磁盘上真实目录是 `Lib`。Windows 文件系统不区分大小写所以打开没问题，
        但**传给 Allegro 的 SKILL `load()` 会按字面找**，大小写不符可能加载失败。

        做法：逐级向下，用 os.listdir 找到真实名字再拼回去。
        """
        import os as _os
        if not path:
            return path
        drive, rest = _os.path.splitdrive(path)
        parts = [p for p in rest.replace("\\", "/").split("/") if p]
        cur = drive + _os.sep if drive else _os.sep
        for i, part in enumerate(parts):
            cand = _os.path.join(cur, part)
            try:
                names = _os.listdir(cur)
            except Exception:
                return path
            hit = next((n for n in names if n.lower() == part.lower()), None)
            if hit:
                cur = _os.path.join(cur, hit)
            else:
                cur = cand
        return cur.replace("\\", "/")

    @staticmethod
    def _guess_python_server_il() -> Optional[str]:
        """定位 skillbridge 的 python_server.il（优先本 venv）。"""
        import os
        cands = []
        # 1) 当前解释器所在 venv 的 site-packages
        try:
            import skillbridge
            base = os.path.dirname(os.path.abspath(skillbridge.__file__))
            cands.append(os.path.join(base, "server", "python_server.il"))
        except Exception:
            pass
        # 2) 进程 sys.prefix
        try:
            import sys as _sys
            cands.append(os.path.join(
                _sys.prefix, "Lib", "site-packages",
                "skillbridge", "server", "python_server.il"))
        except Exception:
            pass
        for c in cands:
            if c and os.path.exists(c):
                return AllegroSession._fix_windows_case(c)
        return None

    @staticmethod
    def _guess_pythonw() -> Optional[str]:
        """定位 pythonw.exe（无声窗口，避免弹黑框）。"""
        import os
        import sys as _sys
        for name in ("pythonw.exe", "python.exe"):
            for rel in (("Scripts", name), (name,)):
                c = os.path.join(_sys.prefix, *rel)
                if os.path.exists(c):
                    return AllegroSession._fix_windows_case(c)
        return None

    def __bool__(self) -> bool:
        return self._ws is not None

    # ---------- 底层调用 ----------
    def func(self, name: str):
        """取到 SKILL 函数对象（ws["axlDBCreatePadStack"] 这种）。"""
        if self._ws is None:
            raise RuntimeError("session 未打开，请先 open() 或使用 with 语句")
        return self._ws[name]

    def call(self, name: str, *args, **kwargs) -> Any:
        """
        调用一个 SKILL 函数。异常会被包装成 SkillCallError 抛出。

        调用方一般不需要直接用这个，用下面语义化的方法更好。
        """
        try:
            return self.func(name)(*args, **kwargs)
        except Exception as e:
            raise SkillCallError(name, e) from e

    def eval_skill(self, code: str) -> Any:
        """
        直接把一段 SKILL 表达式交给 Allegro 求值。

        :param code: 单条 SKILL 表达式字符串，例如
                     'axlDBGetDesignUnits()'
        :return: Allegro 返回的对象（SkillBridge 已转成 Python 对象）

        ★★★ 语法硬约束（2026-09-28 实测确认，血泪教训）★★★

        SKILL 里 `func(args)` 与 `(func args)` 是**两种不同的东西**：

            axlDBGetDesignUnits()      -> 函数调用  ✓ 正确
            (axlDBGetDesignUnits())    -> eval: not a function  ✗ 报错

        `(f a)` 形式的语义是"先求值 f，再把结果当函数套用"，而 AXL 函数
        不能作为值取出，所以报 "not a function"。
        凡是在 eval 通道里跑的代码，**必须用 `func(args)` 形式**。

        另一条：**不存在 `ws["skill"]` 这个函数**（Allegro 报
        `eval: undefined function skill`）。skillbridge 的通用求值入口是
        私有的 `_channel.send()`；`ws["Name"](args)` 那条路是给带参数
        序列化的 RPC 用的，不适用于"发一段代码"。

        ★★★ 单行铁律（2026-09-28 读 python_server.il 后确认）★★★

        Allegro 侧 `__pyOnData` 会 `foreach(line parseString(data "\\n") ...)`
        逐行求值，**每行回一条消息**。而客户端每次只 `readline()` 读一条，
        多出来的就积压 —— 之后每次调用都会读到"上一次的"结果。
        所以这里**强制要求 code 是单行**，含换行直接报错。
        """
        if self._ws is None:
            raise RuntimeError("session 未打开")

        # 单行铁律：多行会让 Allegro 产生多条回复，造成缓冲错位
        if "\n" in code or "\r" in code:
            raise SkillCallError(
                "eval_skill",
                ValueError(
                    "eval_skill 只接受单行 SKILL 代码。Allegro 侧按 \\n 拆分逐行"
                    "求值并各回一条消息，多行会导致回复积压、后续调用读到上一次"
                    "的结果。请把代码压成一行（用空格分隔表达式）。"
                ),
            )

        # 主通道：skillbridge 的底层 send（等价于 CIW 里贴一行 SKILL）
        channel = getattr(self._ws, "_channel", None)
        if channel is not None and hasattr(channel, "send"):
            translator = getattr(self._ws, "_translator", None)
            raw = self._send_raw(code)
            try:
                return _decode_skill_reply(raw, translator)
            except Exception as e:
                raise SkillCallError("eval_skill", e) from e

        # 兜底：旧版 skillbridge 可能提供 ws.skill()
        fn = getattr(self._ws, "skill", None)
        if callable(fn):
            try:
                return fn(code)
            except Exception as e:
                raise SkillCallError("skill", e) from e

        raise SkillCallError(
            "eval_skill",
            RuntimeError("当前 skillbridge 版本没有任何可用的 SKILL 求值通道"),
        )

    # ---------- 语义化查询 ----------
    def units(self) -> str:
        """当前 design 的单位：'mm' / 'mil' / 'unknown'。"""
        try:
            raw = self.call("axlDBGetDesignUnits")
        except SkillCallError:
            return "unknown"
        return _norm_units(raw)

    def design_name(self) -> Optional[str]:
        """当前打开的 drawing 名（不含扩展名）。失败返回 None。"""
        try:
            return self.call("axlCurrentDesign")
        except SkillCallError:
            try:
                return self.eval_skill("axlCurrentDesign()")
            except SkillCallError:
                return None

    def is_package_symbol(self) -> Optional[bool]:
        """
        判断当前 drawing 是不是 package symbol。

        ★ 实测（2026-09-28）：正确做法是
              axlDesignType(axlDBGetDesign())
        返回 'SYMBOL' / 'BOARD' 等符号。

        为什么不用 `axlDBGetDesign()->drawing_type`：
          在 `_channel.send` 通道里，`->` 属性访问**取不到值**（返回 nil），
          因为 dbid 是远端对象，属性穿透不被支持。必须用函数式 API
          （axlDesignType / axlDBGetDesignUnits 这类）来取值。

        取不到时返回 None（不确定，不要当成 False）。
        """
        try:
            raw = self.eval_skill("axlDesignType(axlDBGetDesign())")
        except SkillCallError:
            return None
        if raw is None:
            return None

        s = str(raw).strip().strip("'\"").lower()
        if not s or s == "none":
            return None
        # SYMBOL 涵盖 package symbol / mechanical symbol；
        # BOARD 是普通板图，放 pin 无效。
        if "symbol" in s:
            return True
        if "board" in s:
            return False
        return None


class SkillCallError(Exception):
    """SKILL 调用失败。保留函数名与原始异常，便于上层定位。"""

    def __init__(self, func_name: str, original: Exception):
        self.func_name = func_name
        self.original = original
        super().__init__(f"SKILL 调用失败: {func_name}() -> {original!r}")


# ============================================================
# 临时 ASCII 工作目录
# ============================================================
class TempWorkDir:
    """
    创建一个纯 ASCII 的临时工作目录。

    Allegro 对含中文的路径处理不可靠（.dra / .pad 的文件名、路径都可能出问题），
    所以凡是生成文件的操作，都先在临时 ASCII 目录里做完，再拷回目标位置。
    """

    def __init__(self, prefix: str = "forge_tmp_"):
        self._prefix = prefix
        self.path: Optional[str] = None

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp(prefix=self._prefix)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self.path and os.path.isdir(self.path):
            shutil.rmtree(self.path, ignore_errors=True)


def ascii_safe_copy(src: str, dst_dir: str, fallback_name: str) -> str:
    """
    把 src 复制到 dst_dir，确保目标文件名是 ASCII。

    :return: 目标文件的完整路径
    """
    base = os.path.basename(src)
    if _has_non_ascii(base):
        base = fallback_name
    dst = os.path.join(dst_dir, base)
    shutil.copy2(src, dst)
    return dst


# ============================================================
# 单位换算辅助
# ============================================================
def to_unit(value_mm: float, target_unit: str) -> float:
    """
    把 mm 数值换算成目标单位。

    :param value_mm:    以 mm 表示的数值
    :param target_unit: 'mm' 或 'mil'
    """
    if target_unit == "mil":
        return value_mm / MIL_TO_MM
    return value_mm


def from_unit(value: float, source_unit: str) -> float:
    """把 source_unit 数值换算成 mm。"""
    if source_unit == "mil":
        return value * MIL_TO_MM
    return value
