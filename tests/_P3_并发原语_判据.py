# -*- coding: utf-8 -*-
r"""并发原语 · 判据（2026-10-25 · 工单 W2）

被测：src/agentctl/sync.py（LockedDict / SerialByActor / ensure_single_writer）。
纯离线、纯进程内：只用线程与内存计数器，不碰桌面、不写盘、临时产物无。

本判据**只钉行为、不改行为**：
  ① 单线程 API 冒烟：update_fn 返回 new 且落盘；items() 是快照不是活视图；
     fn 里同线程重入读字典不死锁（RLock 语义，用带超时的旁路线程验，不 hang）；
  ② 并发不丢更新：8 线程各 +1 × 1000 ⇒ 精确 8000（丢更新即红）；
     counting 期间 GIL 切换间隔压到 100µs（跑完还原），逼非原子实现真交错；
  ③ SerialByActor：锁实例稳定；同一 actor 串行（临界区无交错 + 计数器精确）；
     不同 actor 并发不互斥（最多 2 个同时在临界区 + 总耗时 ≈ 单路）；
  ④ 装饰器：functools.wraps 保留函数名/文档字符串；actor 关键字/位置两种传法
     都串行；不同 actor 并发重叠。

跑法：python -X utf8 tests\_P3_并发原语_判据.py
      python -X utf8 tests\_P3_并发原语_判据.py --inject   # 必红
      --inject 干的事：把 update_fn 换成「先 get 再 set」的非原子实现
      （各操作都拿锁、但组合不原子 —— 正是 core 四处缓存现在的隐患形态），
      再用**同一组并发断言**重跑 —— 丢更新就会红。
退出码：0 绿；1 红。
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import sync                                       # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


class _Probe:
    """临界区探针：owner 侦测「有人没出来就被插进来」（串行性），max_inside 侦测「最多几人同时在」（并发性）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.owner = None
        self.violations = 0
        self.inside = 0
        self.max_inside = 0

    def enter(self, who):
        with self._lock:
            if self.owner is not None:
                self.violations += 1
            self.owner = who
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)

    def leave(self):
        with self._lock:
            self.owner = None
            self.inside -= 1


def run_counting(make_dict, tag=""):
    """8 线程 × 1000 次 update_fn(+1) ⇒ 精确 8000。返回实际值（供注入自证用）。

    ★ 期间把 GIL 切换间隔压到 100µs（跑完还原）：默认 5ms 切片下，坏实现的
    get→set 窗口只有几条字节码，可能整轮一次都不交错（实测就出过精确 8000）
    —— 压小间隔后，非原子实现必然真丢更新；而好实现必须照样精确 8000。
    """
    d = make_dict()
    NT, NI = 8, 1000
    barrier = threading.Barrier(NT, timeout=10)

    def worker():
        barrier.wait()
        for _ in range(NI):
            d.update_fn("n", lambda old: (old or 0) + 1)

    old_iv = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        ts = [threading.Thread(target=worker) for _ in range(NT)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        sys.setswitchinterval(old_iv)
    got = d.get("n")
    check(tag + "并发不丢更新：8 线程各 +1 × 1000 ⇒ 精确 8000", got == 8000, "实际 %r" % (got,))
    return got


print("== ① 单线程 API 冒烟 ==")
d = sync.LockedDict({"a": 1})
d["b"] = 2
check("构造/读写取值", d["a"] == 1 and d["b"] == 2)
check("get 带缺省", d.get("nope", 9) == 9)
check("setdefault 首次生效、再次不动", d.setdefault("c", 3) == 3 and d.setdefault("c", 99) == 3)
check("pop 有值弹值、缺键给缺省", d.pop("c") == 3 and d.pop("c", None) is None)
d.update({"x": 1}, y=2)
check("update(位置+关键字)/len/in", len(d) == 4 and "x" in d and "z" not in d)
check("update_fn 返回 new 并落盘", d.update_fn("k", lambda old: (old or 0) + 1) == 1 and d["k"] == 1)
snap = d.items()
d["zz"] = 1
check("items() 是快照不是活视图", all(kv[0] != "zz" for kv in snap) and ("zz", 1) in d.items())

result = {}


def _reentrant():
    d2 = sync.LockedDict()
    d2["m"] = 5
    try:
        result["v"] = d2.update_fn("m", lambda old: old + (1 if "m" in d2 else 0))
    except Exception as e:  # noqa: BLE001
        result["err"] = repr(e)


th = threading.Thread(target=_reentrant, daemon=True)
th.start()
th.join(5)
check("fn 里同线程重入读字典（RLock 语义，不 hang）", result.get("v") == 6, "实际 %r" % (result,))

print("== ② update_fn 并发：8 线程 × 1000 ⇒ 8000 ==")
run_counting(sync.LockedDict)

print("== ③ SerialByActor：锁稳定 / 同 actor 串行 / 异 actor 不互斥 ==")
s = sync.SerialByActor()
check("锁实例稳定：同 actor 拿到同一把，异 actor 不同把",
      s.hold("x") is s.hold("x") and s.hold("x") is not s.hold("y"))

# —— 同一 actor 串行：2 线程 × 60 段（每段 1ms），临界区里不许有第二个人；
probe_same = _Probe()


def same_actor_work(name):
    for _ in range(60):
        with s.hold("same"):
            probe_same.enter(name)
            time.sleep(0.001)
            probe_same.leave()


ts = [threading.Thread(target=same_actor_work, args=("t%d" % i,)) for i in range(2)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("同一 actor 串行：临界区无交错（violations == 0）", probe_same.violations == 0,
      "实际 %r" % (probe_same.violations,))

counter = [0]


def same_actor_count():
    for _ in range(1000):
        with s.hold("same2"):
            counter[0] += 1


ts = [threading.Thread(target=same_actor_count) for _ in range(2)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("同一 actor 串行：计数器精确 2000（无丢加）", counter[0] == 2000, "实际 %r" % (counter[0],))

# —— 不同 actor 并发不互斥：单路 4 段 × 50ms ≈ S；两路并发若互斥要 ≈ 2S，不互斥 ≈ S。
SECTION = 0.05
ROUNDS = 4
s2 = sync.SerialByActor()


def one_path():
    t0 = time.perf_counter()
    for _ in range(ROUNDS):
        with s2.hold("single"):
            time.sleep(SECTION)
    return time.perf_counter() - t0


single_sec = one_path()

s3 = sync.SerialByActor()
probe_par = _Probe()
start = threading.Barrier(2, timeout=10)


def parallel_work(name):
    start.wait()
    for _ in range(ROUNDS):
        with s3.hold(name):
            probe_par.enter(name)
            time.sleep(SECTION)
            probe_par.leave()


ts = [threading.Thread(target=parallel_work, args=(n,)) for n in ("甲", "乙")]
t0 = time.perf_counter()
for t in ts:
    t.start()
for t in ts:
    t.join()
both_sec = time.perf_counter() - t0
check("不同 actor 并发：最多 2 个同时在临界区（真重叠过）", probe_par.max_inside == 2,
      "实际 max_inside=%r" % (probe_par.max_inside,))
check("不同 actor 并发：两路总耗时 ≈ 单路（%.2fs vs %.2fs，若互斥要翻倍）"
      % (both_sec, single_sec),
      both_sec < single_sec * 1.6, "实际 both=%.3f single=%.3f" % (both_sec, single_sec))

print("== ④ ensure_single_writer：元数据保留 + 串行/并发语义 ==")
serial_d = sync.SerialByActor()
probe_d = _Probe()


@sync.ensure_single_writer(serial_d)
def touch(tag, actor=None):
    """原函数文档字符串"""
    probe_d.enter((actor, tag))
    time.sleep(0.001)
    probe_d.leave()
    return tag


check("函数名保留", touch.__name__ == "touch", "实际 %r" % (touch.__name__,))
check("文档字符串保留", touch.__doc__ == "原函数文档字符串", "实际 %r" % (touch.__doc__,))

# actor 关键字传参：同一 actor 串行
def dec_work_kw(i):
    touch(i, actor="u1")


ts = [threading.Thread(target=dec_work_kw, args=(i,)) for i in range(40)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("装饰器（actor 关键字）：同 actor 40×2 段无交错", probe_d.violations == 0,
      "实际 %r" % (probe_d.violations,))

# actor 位置传参：同样要按名绑到 actor 并串行
def dec_work_pos(i):
    touch(i, "u2")


ts = [threading.Thread(target=dec_work_pos, args=(i,)) for i in range(40)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("装饰器（actor 位置传参）：同 actor 40×2 段无交错", probe_d.violations == 0,
      "实际 %r" % (probe_d.violations,))

# 不同 actor：必须能重叠（专用慢段装饰函数，观测 50ms 临界区的真重叠）
probe_par2 = _Probe()
serial_p2 = sync.SerialByActor()


@sync.ensure_single_writer(serial_p2)
def touch_slow(tag, actor=None):
    """慢速档：并发重叠观测用"""
    probe_par2.enter((actor, tag))
    time.sleep(SECTION)
    probe_par2.leave()
    return tag


start2 = threading.Barrier(2, timeout=10)


def dec_par(name):
    start2.wait()
    for _ in range(ROUNDS):
        touch_slow(name, actor=name)


ts = [threading.Thread(target=dec_par, args=(n,)) for n in ("u1", "u2")]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("装饰器：不同 actor 并发重叠（max_inside == 2）", probe_par2.max_inside == 2,
      "实际 max_inside=%r" % (probe_par2.max_inside,))

print()
if "--inject" in sys.argv:
    print("== 注入：update_fn 换成「先 get 再 set」的非原子实现 —— 同一组并发断言必须抓到 ==")

    class _BadDict(sync.LockedDict):
        """坏实现：get 与 set 各自拿锁，但组合不原子 —— 正是 core 四处缓存现在的隐患形态。"""

        def update_fn(self, key, fn):
            old = self.get(key)
            new = fn(old)
            self[key] = new
            return new

    got = run_counting(_BadDict, tag="注入·")
    check("注入：坏实现确实非原子（真的丢过更新才算注入有效）", got != 8000,
          "坏实现居然精确 8000 —— 注入没生效，别信这次的绿")
    if FAILS:
        print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
        raise SystemExit(1)
    print("【红】注入模式必须红：坏实现没被同一组断言抓到 ⇒ 判据自证了，别信这次的绿")
    raise SystemExit(1)
if FAILS:
    print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
    raise SystemExit(1)
print("【绿】全部通过：update_fn 并发精确 8000；SerialByActor 同 actor 串行、异 actor 不互斥；"
      "装饰器保留元数据且两种传参都串行。")
