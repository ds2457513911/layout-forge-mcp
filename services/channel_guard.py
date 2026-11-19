# -*- coding: utf-8 -*-
"""
services/channel_guard.py —— SkillBridge 通道锁 + 建图租约（两条不变量的执行者）

## 为什么需要它

D4（issue #3）实测定论：**通道是单占用资源**，而工具层是并发的。

  · 跨进程：第二进程连上来会被 Allegro 侧 `SingleTcpServer.verify_request` 直接拒
    （`skillbridge/client/python_server.py:71-81`，`request_queue_size=0`），
    所以**跨进程不会串话** —— 但当时的报错写成了「通道无法对齐」并建议 `pyKillServer()`，
    把「别人正占着」误报成「通道坏了」。
  · 进程内：fastmcp 4.0.10 把同步工具丢进 anyio 线程池（默认 40 线程），
    `Workspace.open` 有进程级缓存（`skillbridge/client/workspace.py:279-287`），
    两个并发 tools/call 拿到**同一个 `Channel`**；而 `Channel.send()` 是
    「发一条 + 收一条」的无锁原子操作（`skillbridge/client/channel.py:159-161`），
    于是 A 的回包被 B 读走 —— 这才是真竞态。

所以本模块提供两条不变量的**唯一实现**：

| 不变量 | 载体 | 语义 |
|--|--|--|
| 通道级：同一时刻只一个会话持有通道 | `channel_lock()` | **等**（默认 30s），超时抛 `ChannelBusyError` 并带持有人 |
| 任务级：建图长任务正在跑就只报告 | `TaskLease` | **不排队**，直接返回 `busy` + 持有人 |

## 两层锁，缺一不可

1. **进程内 `threading.RLock`**：必须是可重入的 —— `footprint_builder` 里存在
   嵌套 `with AllegroSession(...)`（如 `build_footprint` 内部再取会话），
   普通 `Lock` 会自己把自己锁死。
2. **跨进程 `msvcrt.locking`**：锁 `Tools/log/skillbridge.lock` 的第 0 字节。
   ★ `msvcrt` **不可重入**（同进程开第二个句柄也拿 `EACCES`，D4 已实测），
   所以文件锁只在 RLock 深度 **0→1** 时取、**1→0** 时放；嵌套层只加计数。

非 Windows 退化为「只加进程内锁」：本仓只在 Windows + Allegro 上跑，
测试若在别的平台上跑，通道锁本来也无意义。

## 持有人信息从哪来

锁文件本身只用来被锁（进程 `taskkill /F` 后由系统释放，所以**崩溃不会留下死锁**）；
人可读的持有人写在旁边的 `skillbridge.lock.holder`（JSON：pid / 起始时间 / 任务名 / 图纸名）。
撞锁时读它，就能在工具返回值里直接告诉调用方「谁在占、占了多久、在干什么」，
而不是让人去猜 `pyKillServer()`。

## 常量从哪来

**超时与路径都不是本模块自己拍的值**：默认路径取自 `services/paths.LOG_DIR`（目录单一真源），
超时既可由调用方传参（`mcp_server/config.py` 是真源），也可用环境变量
`FORGE_CHANNEL_LOCK_TIMEOUT` 覆盖（部署时改环境比改代码快）。
本模块**只依赖标准库 + services.paths**，绝不 import `mcp_server`（架构 A1：层方向单向）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from services import paths as P

logger = logging.getLogger("layout-forge")

# ============================================================
# 错误码（与 orchestration/pipeline.py 的 ERR_* 同一套词表）
# ============================================================
#: 通道被别的会话占着（等锁超时）—— 区别于「通道坏/没起」
CHANNEL_BUSY = "CHANNEL_BUSY"
#: 通道连不上（Allegro 没开 / 服务没起 / 端口不对）
CHANNEL_NOT_READY = "CHANNEL_NOT_READY"

#: 锁文件名（与 lease 同目录：Tools/log/）
LOCK_FILENAME = "skillbridge.lock"
HOLDER_FILENAME = "skillbridge.lock.holder"
LEASE_FILENAME = "build_footprint.lease"

#: 等锁默认上限（秒）；调用方可覆盖，环境变量也可覆盖
CHANNEL_LOCK_TIMEOUT_S = float(os.environ.get("FORGE_CHANNEL_LOCK_TIMEOUT", "30"))

#: 轮询间隔：50ms 足够细（撞锁后最坏多等 50ms），又不会把 CPU 转满
_POLL_S = 0.05


# ============================================================
# 路径
# ============================================================
def default_lock_path() -> str:
    """通道锁文件路径（`Tools/log/skillbridge.lock`）。"""
    return os.path.join(P.LOG_DIR, LOCK_FILENAME)


def default_holder_path() -> str:
    """持有人信息文件路径（锁文件旁边，**不加锁**，随时可读）。"""
    return os.path.join(P.LOG_DIR, HOLDER_FILENAME)


def default_lease_path() -> str:
    """任务级租约文件路径（`Tools/log/build_footprint.lease`）。"""
    return os.path.join(P.LOG_DIR, LEASE_FILENAME)


# ============================================================
# 小工具
# ============================================================
def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _atomic_write(path: str, text: str) -> None:
    """原子写（临时文件 + os.replace）：本机就地截断写会偶发 PermissionError。"""
    _ensure_dir(path)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_holder(holder_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读持有人信息；文件不存在 / 内容坏掉一律返回 None（**绝不抛**，它是诊断信息）。"""
    path = holder_path or default_holder_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """进程还活着吗。Windows 上不能用 os.kill(pid, 0)（会真发信号），走 OpenProcess。"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wt.HANDLE
            k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
            k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = wt.DWORD()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:  # noqa: BLE001 - 判断不了就当活着，宁可不抢别人的锁
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _holder_payload(task: str, drawing: Optional[str] = None) -> Dict[str, Any]:
    return {
        "pid": os.getpid(),
        #: 线程 id：同进程的不同线程各算一个独立持有者（建图任务级 lease 靠它区分重入）
        "tid": threading.get_ident(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": task or "allegro-session",
        "drawing": drawing,
        "workspace_id": None,
    }


def describe_holder(holder: Optional[Dict[str, Any]]) -> str:
    """持有人 → 一句人话（工具返回值与错误消息共用，避免两处措辞漂移）。"""
    if not holder:
        return "（无持有人信息：可能是别的进程刚释放，或持有人没来得及写信息）"
    pid = holder.get("pid")
    bits = [f"pid={pid}" if pid is not None else "pid=?"]
    if holder.get("task"):
        bits.append(f"任务={holder['task']}")
    if holder.get("drawing"):
        bits.append(f"图纸={holder['drawing']}")
    if holder.get("started_at"):
        bits.append(f"开始于 {holder['started_at']}")
    return " / ".join(bits)


# ============================================================
# 错误类型
# ============================================================
class ChannelBusyError(ConnectionError):
    """
    通道被**别的会话**占着（不是坏了）。

    ★ 继承 `ConnectionError` 是刻意的：`footprint_builder` 里 15 处
      `except ConnectionError` 负责把通道故障转成 `success=False` 的返回值，
      撞锁也属于这一类（工具不该抛异常给 MCP 层）。区别只在**字段**：
      本类带 `error_code="CHANNEL_BUSY"` 与 `holder`，调用方按码分支，不按人话分支。
    """

    error_code = CHANNEL_BUSY

    def __init__(self, holder: Optional[Dict[str, Any]], timeout_s: float,
                 task: str = "", drawing: Optional[str] = None):
        self.holder = holder or {}
        self.timeout_s = timeout_s
        self.task = task
        self.drawing = drawing
        mine = f"本次任务={task}" if task else "本次任务未命名"
        if drawing:
            mine += f"（图纸={drawing}）"
        super().__init__(
            f"SkillBridge 通道正被占用：等了 {timeout_s:.0f}s 仍未拿到锁。\n"
            f"持有者：{describe_holder(self.holder)}\n"
            f"{mine}\n"
            f"★ 这不是通道坏了，**不要**执行 pyKillServer()；"
            f"通道是单占用资源（同一时刻只允许一个会话连 Allegro）。\n"
            f"怎么办：等持有者跑完再重试；若确认它已卡死，结束那个进程即可 —— "
            f"锁在进程退出后由系统自动释放（崩溃也不会留下死锁）。"
        )


class ChannelNotReadyError(ConnectionError):
    """
    通道「连不上 / 对不齐」——服务没起来，或回复队列积压。

    与 ChannelBusyError 的分工是「谁的错」：

      * CHANNEL_BUSY      → 通道没坏，只是别人正在用：等或稍后重试；
      * CHANNEL_NOT_READY → 通道本身不可用（服务没起 / 队列积压）：要人处理。

    ★ 同样继承 ConnectionError：footprint_builder 里 15 处
      except ConnectionError 不用改就能接住，返回值再按 error_code 分支。
    """

    error_code = CHANNEL_NOT_READY

    #: 与 ChannelBusyError 同形的接口，调用方可统一取字段
    holder = None
    timeout_s = 0.0
    task = ""
    drawing = None

    def __init__(self, message: str):
        super().__init__(message)


# ============================================================
# 通道锁
# ============================================================
#: 进程内可重入锁：同一线程的嵌套 `with AllegroSession(...)` 不会自锁
_RLOCK = threading.RLock()

#: 当前嵌套深度与最外层持有的文件句柄（只有持 `_RLOCK` 的线程会改）
_STATE: Dict[str, Any] = {"depth": 0, "fd": None, "lock_path": "", "holder_path": ""}


def _try_lock_fd(fd: int) -> bool:
    """尝试独占锁文件的第 0 字节；拿到返回 True。非 Windows 直接算拿到。"""
    if os.name != "nt":
        return True
    import msvcrt

    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock_fd(fd: int) -> None:
    if os.name != "nt":
        return
    import msvcrt

    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _open_lock_fd(path: str) -> int:
    """打开（必要时创建）锁文件并定位到第 0 字节。lock 的区域必须有字节存在。"""
    _ensure_dir(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        pass
    return fd


class ChannelGuard:
    """通道占用权的持有者。`acquire()` / `release()` 可直接用，也可走 `channel_lock()`。"""

    def __init__(self, task: str = "", drawing: Optional[str] = None,
                 lock_path: Optional[str] = None,
                 holder_path: Optional[str] = None,
                 timeout_s: Optional[float] = None):
        self.task = task or "allegro-session"
        self.drawing = drawing
        self.lock_path = lock_path or default_lock_path()
        self.holder_path = holder_path or default_holder_path()
        self.timeout_s = CHANNEL_LOCK_TIMEOUT_S if timeout_s is None else float(timeout_s)
        #: 本次真正拿到的最外层持有人信息（嵌套层沿用最外层的）
        self.holder: Optional[Dict[str, Any]] = None

    # ---------- 取 / 放 ----------
    def acquire(self) -> "ChannelGuard":
        # ★ 进程内的第二个线程也必须**超时**，不能无限排队：MCP 工具层是并发的
        #   （anyio 线程池默认 40 线程），排队会让调用方一直挂着不返回。
        #   RLock 可重入 → 同一线程的嵌套会立刻拿到（depth>0 分支）。
        deadline = time.time() + self.timeout_s
        if not _RLOCK.acquire(timeout=self.timeout_s):
            raise ChannelBusyError(read_holder(self.holder_path), self.timeout_s,
                                   task=self.task, drawing=self.drawing)
        try:
            if _STATE["depth"] > 0:
                # 同一线程的嵌套：只加计数（msvcrt 不可重入，文件锁仍在最外层手里）
                _STATE["depth"] += 1
                self.holder = read_holder(_STATE["holder_path"]) or self.holder
                return self

            fd = _open_lock_fd(self.lock_path)
            got = _try_lock_fd(fd)
            while not got:
                if time.time() >= deadline:
                    os.close(fd)
                    other = read_holder(self.holder_path)
                    # 先放 RLock 再抛：否则同进程的其它线程会被一个失败者挡住
                    _RLOCK.release()
                    raise ChannelBusyError(other, self.timeout_s,
                                           task=self.task, drawing=self.drawing)
                time.sleep(_POLL_S)
                got = _try_lock_fd(fd)

            _STATE.update({"depth": 1, "fd": fd,
                           "lock_path": self.lock_path, "holder_path": self.holder_path})
            self.holder = _holder_payload(self.task, self.drawing)
            try:
                _atomic_write(self.holder_path,
                              json.dumps(self.holder, ensure_ascii=True, indent=2))
            except OSError:
                # 持有人信息只是诊断用，写不进去不该让会话起不来
                logger.debug("写通道持有人信息失败：%s", self.holder_path)
            logger.debug("通道锁已获取：%s（%s）", self.lock_path, describe_holder(self.holder))
            return self
        except ChannelBusyError:
            raise
        except BaseException:
            # 任何意外都不许把 RLock 留在手里（否则整个进程的通道永久卡死）
            try:
                _RLOCK.release()
            except RuntimeError:
                pass
            raise

    def release(self) -> None:
        """放锁。**只由持锁线程调用**；嵌套层只减计数，最外层才真正解锁。"""
        try:
            if _STATE["depth"] <= 0:
                return
            _STATE["depth"] -= 1
            if _STATE["depth"] > 0:
                return
            fd = _STATE.get("fd")
            hp = _STATE.get("holder_path") or self.holder_path
            _STATE.update({"fd": None, "lock_path": "", "holder_path": ""})
            if fd is not None:
                try:
                    # ★ 顺序：先删持有人信息，再解锁 —— 反过来的话，
                    #   下一个拿到锁的瞬间可能读到上一位的残留信息。
                    try:
                        os.remove(hp)
                    except OSError:
                        pass
                    _unlock_fd(fd)
                finally:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        finally:
            try:
                _RLOCK.release()
            except RuntimeError:
                pass

    # ---------- 诊断 ----------
    @staticmethod
    def is_held() -> bool:
        """本进程（当前线程）是否正持有通道锁。"""
        return _STATE["depth"] > 0


@contextmanager
def channel_lock(task: str = "", drawing: Optional[str] = None,
                 lock_path: Optional[str] = None,
                 holder_path: Optional[str] = None,
                 timeout_s: Optional[float] = None) -> Iterator[ChannelGuard]:
    """
    通道锁上下文：**等锁**（默认 30s），超时抛 `ChannelBusyError`（带持有人）。

    用法：
        with channel_lock(task="inspect_allegro_environment"):
            ws = Workspace.open(...)
            ...

    与 `AllegroSession.open()/close()` 的关系：会话层已经帮你套好了这一层，
    直接用它就行；本函数是给「不走 AllegroSession 的裸通道调用」留的口子。
    """
    guard = ChannelGuard(task=task, drawing=drawing, lock_path=lock_path,
                         holder_path=holder_path, timeout_s=timeout_s)
    guard.acquire()
    try:
        yield guard
    finally:
        guard.release()


# ============================================================
# 任务级租约（建图长任务）
# ============================================================
_LEASE_LOCK = threading.Lock()


class TaskLease:
    """
    建图长任务的**任务级**租约。

    语义（D4 定案）：**只报告，不排队**。已有 lease 时立刻返回持有人，不等待 ——
    因为建图是几十秒级的长任务，让它排队只会让调用方超时；让调用方自己决定
    「等一会儿再试 / 换一张图 / 停手」。

    用法：
        lease = TaskLease(task="export_psm", drawing="NB_XTAL")
        holder = lease.try_acquire()      # None = 拿到；dict = 别人在跑
        if holder:
            return _result(False, busy=True, error_code=CHANNEL_BUSY, holder=holder, ...)
        try:
            ...
        finally:
            lease.release()
    """

    def __init__(self, task: str, drawing: Optional[str] = None,
                 lease_path: Optional[str] = None):
        self.task = task or "build"
        self.drawing = drawing
        self.path = lease_path or default_lease_path()
        self.holder: Optional[Dict[str, Any]] = None
        self._owned = False

    def try_acquire(self) -> Optional[Dict[str, Any]]:
        """拿到 → 返回 None；已有别的活人在跑 → 返回持有人 dict（不排队）。"""
        with _LEASE_LOCK:
            if self._owned:
                return None
            for _ in range(3):
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                except FileExistsError:
                    other = read_holder(self.path)
                    # 同进程**同线程**重入：算拿到（防御未来出现嵌套建图）；
                    # 同进程不同线程 = 两个并发工具是真的撞了，必须报 busy
                    if (other and other.get("pid") == os.getpid()
                            and other.get("tid") == threading.get_ident()):
                        self._owned = True
                        return None
                    # 陈旧的租约（进程已死没来得及删）：清掉再来一次
                    if other is not None and not _pid_alive(int(other.get("pid") or 0)):
                        try:
                            os.remove(self.path)
                        except OSError:
                            pass
                        continue
                    return other or {"pid": None, "task": "unknown"}
                else:
                    payload = _holder_payload(self.task, self.drawing)
                    try:
                        os.write(fd, json.dumps(payload, ensure_ascii=True,
                                                indent=2).encode("utf-8"))
                    finally:
                        os.close(fd)
                    self.holder = payload
                    self._owned = True
                    return None
            return read_holder(self.path) or {"pid": None, "task": "unknown"}

    def release(self) -> None:
        """删除租约文件。正常结束与**进程内**异常路径都要调（异常退出靠 pid 探活兜底）。"""
        with _LEASE_LOCK:
            if not self._owned:
                return
            self._owned = False
            try:
                os.remove(self.path)
            except OSError:
                pass

    def __enter__(self) -> "TaskLease":
        holder = self.try_acquire()
        if holder is not None:
            raise ChannelBusyError(holder, 0.0, task=self.task, drawing=self.drawing)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
