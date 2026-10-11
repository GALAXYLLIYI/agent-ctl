# -*- coding: utf-8 -*-
"""闸门跨进程互斥 —— 共享模块（D18 从老线 gate.py 抽出，**唯一定义处**）

为什么单独成文件：老线 `gate.py` 与 `auth_popup.py` **共用同一份 `auth_pass.json`**
（两者 AUTHDIR 都是「本文件所在目录 / `_auth`」；新线 `agentctl.gate` 的 HOME 实测解析到 pointer 层，
指向同一个目录）。既然共用数据，就必须**共用同一把锁** —— 各锁各的等于没锁。

★ 纪律：`gate.py` 与 `auth_popup.py` 一律 **import 本模块**，**不许复制**一份锁逻辑过去
（T14 整单就是在消灭双份漂移，复制等于造新债）。

锁文件：`<authdir>/gate.lock`（与 D16 起的路径一致，两侧自动同源）。
姿势：Windows 用 `msvcrt.locking` 非阻塞重试 + 硬超时；超时**拒绝**（fail-closed）而不是放行 ——
等不到锁说明有别的进程卡住，宁可这一下不放行，也不能在状态不确定时越权。

历史：D16（2026-10-09）在老线 gate.py 里首次引入；D18（2026-10-10）抽到这里，
并把 `auth_popup.check_pass` 的读-改-写也收进同一把锁。
"""
import contextlib
import json
import os
import tempfile
import time

LOCK_TIMEOUT = 10.0
IS_WIN = os.name == "nt"
if IS_WIN:
    import msvcrt

LOCK_NAME = "gate.lock"


class LockTimeout(Exception):
    """等锁超时。调用方应按自己的契约翻译（gate → GateDenied；auth_popup → 拒绝并说明）。"""


def lock_path(authdir):
    """锁文件路径。两侧都用它，保证同一把锁。"""
    return os.path.join(authdir, LOCK_NAME)


# ────────────────────── 原子写（同一类并发文件原语，故同放这里）──────────────────────
# 为什么需要它：原来 gate.py 与 auth_popup.py **各有一份** `_save`，而且已经开始漂移
#   （一份有 makedirs、一份没有），临时名都写死 `path + ".tmp"`。
#   实测（D16 残留量化）：两个写者共用同一临时名 ⇒ 3000 次写里 1198 次异常
#   （WinError 32 共享冲突 893 + PermissionError 285 + FileNotFoundError 2），
#   并曾把 auth_pass.json 写成**非法 JSON**（有效对象 + 尾巴，`Extra data: line 13`）。
# 现在两件事一起治：
#   ① 临时名每次唯一（mkstemp）—— 消掉临时文件互踩；
#   ② 目标文件的 replace **有界重试** —— 两个写者同时替换同一个目标时，
#      Windows 会给出 ERROR_ACCESS_DENIED(Win5)；这是**瞬时**冲突，重试即可。
#      重试用尽仍失败就**抛出**（绝不静默成功）。
REPLACE_TRIES = 60
REPLACE_DELAY = 0.004


def _replace(tmp, path):
    for i in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_DELAY)


def save_json(path, obj):
    """原子写 JSON：唯一临时名 + 有界重试替换 + 失败清理。两个模块共用这一处。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp-", dir=d or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)          # 别留垃圾；失败也不能盖住原异常
        except OSError:
            pass
        raise


@contextlib.contextmanager
def exclusive(authdir, timeout=None):
    """跨进程互斥。用法：`with exclusive(AUTHDIR): 读 → 判定 → 写回`"""
    if timeout is None:
        timeout = LOCK_TIMEOUT
    path = lock_path(authdir)
    os.makedirs(authdir, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    t0 = time.time()
    got = False
    try:
        while True:
            try:
                if IS_WIN:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise LockTimeout(
                        "闸门锁等待超时（%gs）—— 有别的进程卡住了" % timeout)
                time.sleep(0.005)
        yield
    finally:
        try:
            if got:
                os.lseek(fd, 0, 0)
                if IS_WIN:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)
