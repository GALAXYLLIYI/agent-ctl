# -*- coding: utf-8 -*-
r"""sync —— 进程内并发原语（纯标准库 · 2026-10-25 · 工单 W2）

背景：core.py 有四处模块级缓存（_SCREEN_SEEN / _SCREEN_FRAME_RESULT /
_CDP_PROC / _INTEGRITY_CACHE）在 ThreadingHTTPServer 下被并发访问；它们现在
靠 GIL 的「单次 dict 操作原子」侥幸安全，**读-改-写组合不安全**（两个线程同时
get → 各自改 → 各自 set，后写覆盖前写 = 丢更新）。

core.py 是受控文件（改它要重落复核账本）⇒ 本单**只做原语、不接 core**；
接线由主控做。本模块零第三方依赖、不碰桌面、不写盘、不联网。

三件东西：
    LockedDict          加锁字典：列出的每个方法都在 threading.RLock 内完成；
                        update_fn(key, fn) 是原子的「读-改-写」（丢更新即不可能）。
    SerialByActor       按 actor 名取锁：with serial.hold(actor): ...
                        同一 actor 串行、不同 actor 各走各的（不互斥）。
    ensure_single_writer(serial)
                        装饰器工厂：@ensure_single_writer(serial) 让同一 actor
                        的调用串行，functools.wraps 保留函数名/文档字符串。

口径与注意：
  ★ update_fn 的 fn 在锁内执行：old 缺键时为 **None**（约定，不猜类型）；
    fn 必须快、不许做慢 IO；RLock 允许同线程重入 ⇒ fn 里再读本字典是安全的。
  ★ SerialByActor.hold 返回该 actor 的 threading.Lock（**不可重入**）：
    同一线程嵌套 hold 同一 actor 会死锁 —— 需要嵌套就别这么写。
    锁实例一经创建**永不更换**（字典只增不删）⇒ 先拿引用再进入，安全。
  ★ ensure_single_writer 从被包函数的参数里**按名**取 actor（默认参数名
    "actor"）：关键字传参直接取；位置传参用 inspect.signature 绑定后取；
    取不到 ⇒ actor=None（所有无 actor 的调用共享一把锁，仍是串行的）。
"""
import collections
import functools
import inspect
import threading

__all__ = ["LockedDict", "SerialByActor", "ensure_single_writer"]


class LockedDict:
    """加锁字典：每个公开方法都在 threading.RLock 内完成。

    为什么 RLock 不是 Lock：update_fn 要在锁内调 fn，fn 若回头读本字典
    （同线程重入），Lock 会死锁、RLock 不会。
    """

    def __init__(self, initial=None):
        self._lock = threading.RLock()
        self._d = dict(initial) if initial else {}

    def __getitem__(self, key):
        with self._lock:
            return self._d[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._d[key] = value

    def __delitem__(self, key):
        with self._lock:
            del self._d[key]

    def get(self, key, default=None):
        with self._lock:
            return self._d.get(key, default)

    def setdefault(self, key, default=None):
        with self._lock:
            return self._d.setdefault(key, default)

    def pop(self, key, *args):
        with self._lock:
            return self._d.pop(key, *args)

    def update(self, other=None, **kwargs):
        with self._lock:
            self._d.update(other or {}, **kwargs)

    def items(self):
        """返回**快照**（list），不是活视图：调用方慢慢迭代也不会撞上并发写。"""
        with self._lock:
            return list(self._d.items())

    def __len__(self):
        with self._lock:
            return len(self._d)

    def __contains__(self, key):
        with self._lock:
            return key in self._d

    def update_fn(self, key, fn):
        """原子「读-改-写」：new = fn(old)，落回并返回 new。

        键不存在时 old = None（约定）。fn 在锁内执行：必须快、不许慢 IO；
        整个 get→fn→set 不被打断 ⇒ 并发下**不可能丢更新**。
        """
        with self._lock:
            new = fn(self._d.get(key))
            self._d[key] = new
            return new


class SerialByActor:
    """按 actor 名串行的锁登记处。

    hold(actor) 返回该 actor 专属的 threading.Lock（可 with）：
      - 同一 actor：互斥（排队进）；
      - 不同 actor：各用各的锁，**互不等待**；
      - 锁实例一经创建永不更换 ⇒ 并发取锁也不会拿到两把不同的锁
        （defaultdict 的 __missing__ 竞态被 _guard 锁挡住）。
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._locks = collections.defaultdict(threading.Lock)

    def hold(self, actor):
        """取该 actor 的锁（用 with serial.hold(actor): 进入临界区）。

        actor 就是字典键（建议 str；None 也行，所有 None 共享一把锁）。
        返回的 Lock **不可重入**：同一线程嵌套 hold 同一 actor 会死锁。
        """
        with self._guard:
            return self._locks[actor]


def ensure_single_writer(serial, actor_kw="actor"):
    """装饰器工厂：@ensure_single_writer(serial) 让同一 actor 的调用串行。

    actor 从被包函数的参数里按名取（actor_kw 指定参数名，默认 "actor"）：
      - 关键字传参直接取；
      - 位置传参用 inspect.signature 绑定后取；
      - 取不到 ⇒ actor=None（所有无 actor 的调用共享一把锁，仍是串行的）。
    functools.wraps 保留函数名/文档字符串/签名元数据。
    """

    def deco(fn):
        sig = inspect.signature(fn)
        has_actor = actor_kw in sig.parameters

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            actor = kwargs.get(actor_kw)
            if actor is None and has_actor and args:
                try:
                    bound = sig.bind_partial(*args)
                    actor = bound.arguments.get(actor_kw)
                except TypeError:
                    actor = None
            with serial.hold(actor):
                return fn(*args, **kwargs)

        return wrapper

    return deco
