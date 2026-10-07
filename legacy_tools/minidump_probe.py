# -*- coding: utf-8 -*-
"""minidump_probe.py —— 不装 WinDbg 也能读出 minidump 的关键信息

读三样：
  1. 异常码（如 0xC0000005 = 访问违规）
  2. 出错的指令地址，以及它落在【哪个模块】里  ← 最关键
  3. 0xC0000005 时：是读还是写、访问了哪个地址
"""
import os
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXC_NAMES = {
    0xC0000005: "EXCEPTION_ACCESS_VIOLATION（访问违规）",
    0xC0000006: "EXCEPTION_IN_PAGE_ERROR（页错误）",
    0xC000001D: "ILLEGAL_INSTRUCTION（非法指令）",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO（除零）",
    0xC0000096: "PRIVILEGED_INSTRUCTION（特权指令）",
    0xC00000FD: "STACK_OVERFLOW（栈溢出）",
    0xC0000409: "STACK_BUFFER_OVERRUN（/GS 检测到栈破坏）",
    0xC0000374: "HEAP_CORRUPTION（堆损坏）",
    0x80000003: "BREAKPOINT（断点）",
    0xE06D7363: "C++ 异常（未捕获）",
    0xC0000135: "DLL_NOT_FOUND（缺 DLL）",
    0xC0000139: "ENTRYPOINT_NOT_FOUND（入口点缺失）",
}


def parse(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"MDMP":
        return None
    sig, ver, nstreams, dirrva, checksum, tds, flags = struct.unpack_from("<4sIIIIIQ", d, 0)
    streams = {}
    for i in range(nstreams):
        stype, dsize, rva = struct.unpack_from("<III", d, dirrva + i * 12)
        streams.setdefault(stype, (dsize, rva))

    out = {"file": os.path.basename(path), "size": len(d)}

    # --- ExceptionStream (type 6)
    if 6 in streams:
        _, rva = streams[6]
        thread_id, _al = struct.unpack_from("<II", d, rva)
        e = rva + 8
        code, eflags = struct.unpack_from("<II", d, e)
        exc_addr, = struct.unpack_from("<Q", d, e + 16)
        nparams, = struct.unpack_from("<I", d, e + 24)
        params = struct.unpack_from("<15Q", d, e + 32)
        out["exc_code"] = code
        out["exc_flags"] = eflags
        out["exc_addr"] = exc_addr
        out["exc_params"] = list(params[:min(nparams, 15)])
        out["thread_id"] = thread_id

    # --- SystemInfoStream (type 7): 版本 + 进程架构
    if 7 in streams:
        _, rva = streams[7]
        arch, level, rev, ncpu, ptype, major, minor, build = struct.unpack_from("<HHHBBIII", d, rva)
        out["arch"] = {0: "x86", 9: "x64", 5: "ARM", 12: "ARM64"}.get(arch, str(arch))
        out["os"] = "%d.%d.%d" % (major, minor, build)

    # --- ModuleListStream (type 4)
    mods = []
    if 4 in streams:
        _, rva = streams[4]
        n, = struct.unpack_from("<I", d, rva)
        off = rva + 4
        for i in range(n):
            base, size, csum, mtds, namerva = struct.unpack_from("<QIIII", d, off)
            try:
                nlen, = struct.unpack_from("<I", d, namerva)
                name = d[namerva + 4: namerva + 4 + nlen].decode("utf-16-le", errors="replace")
            except Exception:
                name = "?"
            mods.append((base, size, mtds, name))
            off += 108
    out["modules"] = mods

    # --- 定位出错地址属于哪个模块
    if "exc_addr" in out:
        a = out["exc_addr"]
        hit = None
        for base, size, mtds, name in mods:
            if base <= a < base + size:
                hit = (name, base, size, mtds, a - base)
                break
        out["fault_module"] = hit
    return out


def fmt(path):
    r = parse(path)
    if not r:
        print("  不是有效 minidump: %s" % path)
        return
    print("=" * 70)
    print("文件: %s   (%d 字节)" % (r["file"], r["size"]))
    if "os" in r:
        print("  系统/架构: Windows %s  %s" % (r["os"], r.get("arch", "?")))
    code = r.get("exc_code")
    if code is not None:
        nm = EXC_NAMES.get(code, "")
        print("  异常码   : 0x%08X  %s" % (code, nm))
        print("  出错地址 : 0x%016X" % r["exc_addr"])
        if code == 0xC0000005 and r.get("exc_params"):
            op = {0: "读取", 1: "写入", 8: "执行"}.get(r["exc_params"][0], str(r["exc_params"][0]))
            tgt = r["exc_params"][1] if len(r["exc_params"]) > 1 else 0
            print("  访问类型 : %s" % op)
            print("  访问地址 : 0x%016X" % tgt)
    hit = r.get("fault_module")
    if hit:
        name, base, size, mtds, off = hit
        print("  ★崩溃模块: %s" % name)
        print("      模块基址 0x%016X  大小 %d  偏移 +0x%X" % (base, size, off))
    else:
        print("  ★崩溃模块: 不在模块列表内（可能是 JIT/动态代码或已卸载模块）")
    print("  已加载模块数: %d" % len(r.get("modules", [])))
    # 列出非系统的可疑模块（游戏/显卡/覆盖层）
    sus = [m for m in r.get("modules", []) if any(
        k in m[3].lower() for k in ("bf6", "battlefield", "ea", "javelin", "ac", "nv", "d3d", "dxgi",
                                    "overlay", "vbox", "gameviewer", "todesk", "obs", "rtss"))
    ]
    if sus:
        print("  相关模块（前 18 个）:")
        for base, size, mtds, name in sus[:18]:
            print("     0x%016X  %s" % (base, name))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    for p in args:
        fmt(p)
