# -*- coding: utf-8 -*-
"""preflight.py —— 改文件前的自检（把手册里的高频教训做成可执行检查）

为什么有这个东西：
  2026-09-18 一天内重犯手册已写过的教训 5 次（PS 引号×3、LF→CRLF×1、漏 BOM×1）。
  结论：**光把教训写进手册没用，要让它自动跑。** 本脚本就是那些教训的自动化版本。

用法（改文件前先跑一遍）：
  python preflight.py stamp                      # ① 写时间戳前，取时钟（别估）
  python preflight.py path <路径>                # ② 目标路径还在吗（防被归档/搬走）
  python preflight.py file <文件>                # ③ EOL / BOM / 行数 / 大小
  python preflight.py anchor <文件> <锚点>        # ④ 锚点出现几次（防 .Replace 全替换）
  python preflight.py ps1 <文件>                 # ⑤ .ps1 有中文就必须带 BOM + 引号隐患扫描
  python preflight.py diff-eol <文件> <文件>      # ⑥ 两个文件 EOL 是否一致
"""
import os
import re
import sys
from datetime import datetime

# 输出被重定向/捕获时，Windows 下 Python 默认用 locale(GBK) 编码 → 调用方按 UTF-8 解码会乱码。
# 强制 UTF-8 输出（对应手册"输出被重定向时 Python 用 GBK 编码"那条已知坑）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def eol_stat(p):
    b = _read_bytes(p)
    crlf = b.count(b"\r\n")
    lf_only = len(re.findall(rb"(?<!\r)\n", b))
    if crlf and lf_only:
        eol = "MIXED(混用!)"
    elif crlf:
        eol = "CRLF"
    elif lf_only:
        eol = "LF"
    else:
        eol = "NO-NEWLINE"
    bom = "BOM" if b[:3] == b"\xef\xbb\xbf" else "无BOM"
    text = b.decode("utf-8", errors="replace")
    lines = text.count("\n") + 1
    return eol, bom, lines, len(b)


def cmd_stamp():
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("  ↑ 时间戳一律用这个值，不要凭感觉写")


def cmd_path(p):
    if os.path.exists(p):
        st = os.stat(p)
        kind = "目录" if os.path.isdir(p) else "文件"
        print("OK  %s 存在（%s，%d 字节）" % (p, kind, st.st_size))
        print("    改于 " + datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
    else:
        print("!!  %s 不存在" % p)
        print("    可能原因：被归档/搬走、路径写错、或大小写不符。")
        print("    对策：先别写，先确认正确路径（2026-09-18 有过归档搬走把产物清掉的先例）。")
        return 1
    return 0


def cmd_file(p):
    if not os.path.exists(p):
        return cmd_path(p)
    eol, bom, lines, size = eol_stat(p)
    print("%-52s" % os.path.basename(p))
    print("    EOL=%s  %s  行数=%d  字节=%d" % (eol, bom, lines, size))
    print("    -> 覆写时必须沿用 EOL=%s" % eol.split("(")[0])
    return 0


def cmd_anchor(p, anchor):
    if not os.path.exists(p):
        return cmd_path(p)
    text = open(p, "r", encoding="utf-8").read()
    n = text.count(anchor)
    print("锚点在文件中出现 %d 次" % n)
    if n == 0:
        print("!!  0 次 = 锚点写错（注意 EOL：文件是 %s，锚点里别混用另一种换行）" % eol_stat(p)[0])
        return 1
    if n > 1:
        print("!!  %d 次 > 1 —— 用 .Replace() 会【全部替换】，造成内容重复插入！" % n)
        print("    对策：让锚点唯一，或只替换第 N 次匹配，改完再数一遍。")
        return 2
    print("OK  唯一锚点，可以安全替换")
    return 0


def cmd_ps1(p):
    if not os.path.exists(p):
        return cmd_path(p)
    b = _read_bytes(p)
    text = b.decode("utf-8", errors="replace")
    has_cn = bool(re.search(r"[\u4e00-\u9fff]", text))
    bom = b[:3] == b"\xef\xbb\xbf"
    rc = 0
    print("%-52s" % os.path.basename(p))
    if has_cn and not bom:
        print("!!  含中文但没有 BOM —— PowerShell 5.1 会按 GBK 读，中文全乱 + 解析崩溃！")
        print("    对策：以 UTF-8 BOM 重写：")
        print("      c=[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)")
        print("      [IO.File]::WriteAllText($p,$c,(New-Object Text.UTF8Encoding($true)))")
        rc = 2
    elif has_cn and bom:
        print("OK  含中文且有 BOM")
    else:
        print("OK  纯 ASCII，无 BOM 无妨")
    bad = []
    if '\\"' in text:
        bad.append('发现 \\" —— PowerShell 的转义符是反引号，\\" 会提前结束字符串')
    for i, line in enumerate(text.splitlines(), 1):
        if line.count('"') >= 4:
            bad.append("第 %d 行双引号 >=4 个，检查是否套娃" % i)
        if line.lstrip().startswith("'") and line.count("'") % 2 == 1:
            bad.append("第 %d 行单引号个数为奇数，可能漏了收尾引号" % i)
    if bad:
        print("!!  引号隐患：")
        for x in bad:
            print("      - " + x)
        rc = max(rc, 1)
    else:
        print("OK  未发现常见引号隐患")
    return rc


def cmd_diff_eol(a, b):
    ea = eol_stat(a)[0]
    eb = eol_stat(b)[0]
    print("%-40s %s" % (os.path.basename(a), ea))
    print("%-40s %s" % (os.path.basename(b), eb))
    if ea == eb:
        print("OK  EOL 一致")
        return 0
    print("!!  EOL 不一致，混用会造成无意的全文件差异")
    return 2


def cmd_mem(warn_gb=6.0, hard_gb=2.0):
    """看【提交内存】—— 不是物理内存。

    为什么必须单独有这一条（2026-09-19 实测事故）：
      我当晚拍了 130+ 张 1382×1339 截图、反复起 Python/PIL 载大图做裁剪放大，
      收尾时 **PowerShell 直接起不来**（`error 800705af`、`未能加载 System.Transactions：页面文件太小`）。
      查下来：**提交上限 57.2 GB / 可用仅 1.5 GB**，而**可见进程合计只约 6 GB**，
      约 50 GB 属系统/驱动层累积（开机 9.75 小时）——**关豆包/微信/Edge 救不回来，只能重启**。
      ★ 关键：**物理内存还有 5.7 GB 的时候，提交已经见底了** —— 只看物理内存会误判。
    """
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        print("取内存状态失败")
        return 1
    G = 1024.0 ** 3
    phys_t, phys_a = st.ullTotalPhys / G, st.ullAvailPhys / G
    com_t, com_a = st.ullTotalPageFile / G, st.ullAvailPageFile / G
    print("物理内存  %.2f GB 可用 / %.2f GB 总   使用率 %d%%" % (phys_a, phys_t, st.dwMemoryLoad))
    print("提交内存  %.2f GB 可用 / %.2f GB 上限   ← ★ 判据看这个" % (com_a, com_t))
    if com_a < hard_gb:
        print("[!!] 提交内存低于 %.1f GB —— **立刻停手**：不要再截图/起 Python/开大程序，"
              "**去重启**（用「重启」不要「关机再开机」，Windows 快速启动会复用内核会话）" % hard_gb)
        return 3
    if com_a < warn_gb:
        print("[warn] 提交内存低于 %.1f GB —— 别做批量图像处理；考虑重启" % warn_gb)
        return 2
    print("[OK] 提交内存充足，可以干活")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    c = argv[1]
    if c == "stamp":
        return cmd_stamp()
    if c == "mem":
        return cmd_mem()
    if c == "path" and len(argv) >= 3:
        return cmd_path(argv[2])
    if c == "file" and len(argv) >= 3:
        return cmd_file(argv[2])
    if c == "anchor" and len(argv) >= 4:
        return cmd_anchor(argv[2], argv[3])
    if c == "ps1" and len(argv) >= 3:
        return cmd_ps1(argv[2])
    if c == "diff-eol" and len(argv) >= 4:
        return cmd_diff_eol(argv[2], argv[3])
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
