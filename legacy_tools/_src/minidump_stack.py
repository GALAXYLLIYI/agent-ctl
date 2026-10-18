# -*- coding: utf-8 -*-
"""minidump_stack.py —— 从 minidump 里还原调用栈（按模块+偏移表示，不需要符号）

思路：没有符号也能做——
  1. 取崩溃线程的 RSP / RIP（x64 CONTEXT 固定偏移）
  2. 读该线程栈内存（minidump 里带着栈）
  3. 把栈上的每个 8 字节按"是不是落在某个模块区间"来翻译
  4. 连续命中模块的地址就是一条粗略调用链

这能回答：**是谁把坏指针递给了 ucrtbase？**
"""
import os
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"MDMP":
        return None, None
    _, _, nstreams, dirrva, _, _, _ = struct.unpack_from("<4sIIIIIQ", d, 0)
    streams = {}
    for i in range(nstreams):
        stype, dsize, rva = struct.unpack_from("<III", d, dirrva + i * 12)
        streams.setdefault(stype, (dsize, rva))
    return d, streams


def modules(d, streams):
    mods = []
    if 4 not in streams:
        return mods
    _, rva = streams[4]
    n, = struct.unpack_from("<I", d, rva)
    off = rva + 4
    for _ in range(n):
        base, size, csum, mtds, namerva = struct.unpack_from("<QIIII", d, off)
        try:
            nl, = struct.unpack_from("<I", d, namerva)
            nm = d[namerva + 4: namerva + 4 + nl].decode("utf-16-le", errors="replace")
        except Exception:
            nm = "?"
        mods.append((base, size, nm))
        off += 108
    return mods


def ranges(d, streams):
    """返回 [(start, size, rva), ...]：dump 里带了哪些内存"""
    out = []
    if 5 in streams:                      # MemoryListStream
        _, rva = streams[5]
        n, = struct.unpack_from("<I", d, rva)
        off = rva + 4
        for _ in range(n):
            start, dsize, drva = struct.unpack_from("<QII", d, off)
            out.append((start, dsize, drva))
            off += 16
    if 9 in streams:                      # Memory64ListStream
        _, rva = streams[9]
        n, base_rva = struct.unpack_from("<QQ", d, rva)
        off = rva + 16
        cur = base_rva
        for _ in range(n):
            start, dsize = struct.unpack_from("<QQ", d, off)
            out.append((start, dsize, cur))
            cur += dsize
            off += 16
    return out


def threads(d, streams):
    out = []
    if 3 not in streams:
        return out
    _, rva = streams[3]
    n, = struct.unpack_from("<I", d, rva)
    off = rva + 4
    for _ in range(n):
        tid, susp, priocls, prio = struct.unpack_from("<IIII", d, off)
        teb, = struct.unpack_from("<Q", d, off + 16)
        st_start, st_size, st_rva = struct.unpack_from("<QII", d, off + 24)
        ctx_size, ctx_rva = struct.unpack_from("<II", d, off + 40)
        out.append(dict(tid=tid, stack_start=st_start, stack_size=st_size, stack_rva=st_rva,
                        ctx_rva=ctx_rva, ctx_size=ctx_size))
        off += 48
    return out


def mod_of(mods, a):
    for base, size, nm in mods:
        if base <= a < base + size:
            return nm, a - base
    return None, 0


def readmem(d, rngs, addr, size):
    """读内存；区间不够就【有多少读多少】，别因为越界整段放弃。"""
    for start, dsize, rva in rngs:
        if start <= addr < start + dsize:
            off = addr - start
            avail = dsize - off
            take = min(size, avail)
            if take <= 0:
                return None
            return d[rva + off: rva + off + take]
    return None


def main(path):
    d, streams = load(path)
    if not d:
        print("不是有效 minidump")
        return
    mods = modules(d, streams)
    rngs = ranges(d, streams)
    ths = threads(d, streams)

    # 找崩溃线程：**优先用异常流自带的 ThreadContext**（这才是崩溃那一刻的真实上下文）
    exc_addr = None
    exc_tid = None
    exc_ctx = None
    if 6 in streams:
        _, rva = streams[6]
        exc_tid, = struct.unpack_from("<I", d, rva)
        e = rva + 8
        exc_addr, = struct.unpack_from("<Q", d, e + 16)
        # MINIDUMP_EXCEPTION_STREAM: ThreadId(4)+align(4)+EXCEPTION(152) = 160，之后是 ThreadContext 描述符
        csize, crva = struct.unpack_from("<II", d, rva + 160)
        if csize and crva:
            exc_ctx = (crva, csize)

    print("=" * 72)
    print("文件: %s" % os.path.basename(path))
    print("  内存区间 %d 段   线程 %d 个   模块 %d 个" % (len(rngs), len(ths), len(mods)))

    target = None
    # ① 首选：异常流里的上下文
    if exc_ctx:
        crva, csize = exc_ctx
        try:
            rip, = struct.unpack_from("<Q", d, crva + 0xF8)
            rsp, = struct.unpack_from("<Q", d, crva + 0x98)
            rbp, = struct.unpack_from("<Q", d, crva + 0xA0)
            target = dict(tid=exc_tid, rip=rip, rsp=rsp, rbp=rbp, stack_start=0, stack_size=0x10000)
            print("  [上下文来源] 异常流 ThreadContext（崩溃那一刻）")
        except Exception as ex:
            print("  ! 读异常流上下文失败: %s" % ex)

    # ② 退路：线程表里 RIP == 异常地址的那个
    if target is None:
        for t in ths:
            if t["ctx_rva"] and t["ctx_size"]:
                try:
                    rip, = struct.unpack_from("<Q", d, t["ctx_rva"] + 0xF8)
                    rsp, = struct.unpack_from("<Q", d, t["ctx_rva"] + 0x98)
                    t["rip"], t["rsp"] = rip, rsp
                    if exc_addr is not None and rip == exc_addr:
                        target = t
                except Exception:
                    pass
    if target is None:
        cands = [t for t in ths if "rsp" in t and t.get("stack_size")]
        target = max(cands, key=lambda t: t["stack_size"]) if cands else None
        if target:
            print("  [!] 退路：用了栈最大的线程（可能不是崩溃线程）")

    if not target:
        print("  ! 取不到线程上下文")
        return

    nm, off = mod_of(mods, target["rip"])
    print("  崩溃线程 tid=%d" % target["tid"])
    print("  RIP = 0x%016X  ->  %s +0x%X" % (target["rip"], nm or "(未识别)", off))
    print("  RSP = 0x%016X" % target["rsp"])

    # 沿栈扫描：把落在模块内的地址翻成 模块+偏移
    print("")
    print("  ── 栈回溯（按模块表示；同模块连续出现只留首个）──")
    stack = readmem(d, rngs, target["rsp"], min(target.get("stack_size", 0) or 0x8000, 0x8000))
    if not stack:
        print("     ! 栈内存不在 dump 里（minidump 只带了部分内存）")
        print("     dump 里有这些区间：")
        for s, sz, _ in sorted(rngs)[:12]:
            print("        0x%016X  %d 字节" % (s, sz))
        return
    last = None
    n = 0
    for i in range(0, len(stack) - 7, 8):
        v, = struct.unpack_from("<Q", stack, i)
        nm, off = mod_of(mods, v)
        if nm:
            key = nm
            if key != last:
                print("    [+0x%04X] 0x%016X  %s +0x%X" % (i, v, nm, off))
                last = key
                n += 1
                if n >= 24:
                    break
        else:
            last = None


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
