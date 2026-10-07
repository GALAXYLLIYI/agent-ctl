# -*- coding: utf-8 -*-
"""pe_export_lookup.py —— 给一个 "模块+偏移"，查出它落在哪个【导出函数】里

不需要符号文件：PE 的导出表里有函数名和 RVA，把崩溃偏移夹在两个导出之间即可命名。
用法: python pe_export_lookup.py <dll路径> <偏移十六进制...>
"""
import struct
import sys


def rva_to_off(data, sections, rva):
    for va, vsize, rawptr, rawsize in sections:
        if va <= rva < va + max(vsize, rawsize):
            return rawptr + (rva - va)
    return None


def parse_pe(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:2] != b"MZ":
        raise ValueError("不是 PE")
    e_lfanew, = struct.unpack_from("<I", d, 0x3C)
    if d[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("PE 签名不对")
    coff = e_lfanew + 4
    nsec, = struct.unpack_from("<H", d, coff + 2)
    opt_size, = struct.unpack_from("<H", d, coff + 16)
    opt = coff + 20
    magic, = struct.unpack_from("<H", d, opt)
    pe32plus = (magic == 0x20B)
    # 数据目录
    dd_off = opt + (112 if pe32plus else 96)
    exp_rva, exp_size = struct.unpack_from("<II", d, dd_off)
    # 节表
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        o = sec_off + i * 40
        va, vsize, rawptr, rawsize = struct.unpack_from("<IIII", d, o + 12)
        sections.append((va, vsize, rawptr, rawsize))
    return d, sections, exp_rva, exp_size


def exports(d, sections, exp_rva):
    off = rva_to_off(d, sections, exp_rva)
    if off is None:
        return []
    nnames, = struct.unpack_from("<I", d, off + 24)
    addr_rva, = struct.unpack_from("<I", d, off + 28)
    names_rva, = struct.unpack_from("<I", d, off + 32)
    ords_rva, = struct.unpack_from("<I", d, off + 36)
    ao = rva_to_off(d, sections, addr_rva)
    no = rva_to_off(d, sections, names_rva)
    oo = rva_to_off(d, sections, ords_rva)
    if ao is None or no is None or oo is None:
        return []
    out = []
    for i in range(nnames):
        nrva, = struct.unpack_from("<I", d, no + i * 4)
        oi, = struct.unpack_from("<H", d, oo + i * 2)
        frva, = struct.unpack_from("<I", d, ao + oi * 4)
        if frva == 0:
            continue
        so = rva_to_off(d, sections, nrva)
        if so is None:
            continue
        e = d.index(b"\0", so)
        nm = d[so:e].decode("ascii", errors="replace")
        out.append((frva, nm))
    out.sort()
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    path = sys.argv[1]
    d, sections, exp_rva, exp_size = parse_pe(path)
    exps = exports(d, sections, exp_rva)
    print("=" * 70)
    print("模块: %s" % path)
    print("  导出函数 %d 个；崩溃偏移将按'最近的、RVA 更小的那个导出'命名" % len(exps))
    for arg in sys.argv[2:]:
        target = int(arg, 16)
        best = None
        for rva, nm in exps:
            if rva <= target:
                if best is None or rva > best[0]:
                    best = (rva, nm)
        print("")
        print("  偏移 +0x%X :" % target)
        if best:
            rva, nm = best
            print("     落在导出函数  %s  (导出 RVA 0x%X, 距函数入口 +0x%X)" % (nm, rva, target - rva))
        else:
            print("     ! 在所有导出之前，可能是内部函数（未导出）")
        # 找下一个导出，给出函数长度上界
        nxt = None
        for rva, nm in exps:
            if rva > target:
                nxt = (rva, nm)
                break
        if nxt:
            print("     下一个导出是  %s (RVA 0x%X)，故本函数长度 ≈ 0x%X" % (nxt[1], nxt[0], nxt[0] - (best[0] if best else 0)))
        # 打印该处的机器码前 16 字节
        fo = rva_to_off(d, sections, target)
        if fo:
            b = d[fo:fo + 16]
            print("     该处机器码: " + " ".join("%02X" % x for x in b))
            # 极简提示：x64 常见 mov eax,[rcx+xx] = 8B 41 xx
            if len(b) >= 3 and b[0] == 0x8B:
                modrm = b[1]
                reg = (modrm >> 3) & 7
                rm = modrm & 7
                regs = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi"]
                if rm == 1 and b[2] < 0x80:
                    print("     ⇒ 像 `mov %s, [%s+0x%X]` —— 即【对寄存器指向的结构体取偏移 0x%X 的成员】"
                          % (regs[reg], regs[1] if (modrm >> 6) == 0 else "?", b[2], b[2]))
                    print("       若那个寄存器是 0，就会读到地址 0x%X ← **与本崩溃的访问地址吻合**" % b[2])


if __name__ == "__main__":
    main()
