# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

# ★ 2026-10-17（用户裁：**不公开用户名**）：构建路径不再写死本机目录。
#   PyInstaller 执行 spec 时会注入 SPEC（本文件完整路径）/ SPECPATH（所在目录）；
#   由它推导仓根 ⇒ 与原来那串绝对路径**完全等价**，换机器/换用户名都不用改。
_SPEC = globals().get("SPEC") or ""
_SPEC_DIR = (os.path.dirname(os.path.abspath(_SPEC)) if _SPEC
             else os.path.abspath(globals().get("SPECPATH") or "."))
_ROOT = os.path.dirname(_SPEC_DIR)


def _R(*parts):
    """仓内路径（统一正斜杠，PyInstaller 两种分隔符都吃）。"""
    return os.path.join(_ROOT, *parts).replace("\\", "/")


hiddenimports = ['winrt.windows.media.ocr', 'winrt.windows.graphics.imaging', 'winrt.windows.storage.streams', 'winrt.windows.globalization', 'winrt.windows.foundation', 'winrt.windows.foundation.collections', 'PIL._tkinter_finder']
hiddenimports += collect_submodules('winrt')
hiddenimports += collect_submodules('uiautomation')
hiddenimports += collect_submodules('comtypes')


a = Analysis(
    [_R('src/agentctl_main.py')],
    pathex=[_R('src'), _R('vendor'), _R('stubs')],
    binaries=[],
    # _selftest_src：把 widget.py 的**源码原文**随包带走。
    # 目的只有一个 —— 装完的 exe 跑 selftest 时要能读到源码做审计
    #（查有没有 require( 调用、有没有 import 屏幕操作模块）。
    # 不带的话 AgentCtl.exe selftest 直接 FileNotFoundError 崩掉，
    # 装出来的包就没法自证没被开后门。平铺目录且**不叫 agentctl**，
    # 免得跟真正的包重名把 import 路径搅乱。
    datas=[(_R('contract/tools.json'), 'contract'),
           (_R('src/agentctl/widget.py'), '_selftest_src'),
           # ★ B 项（2026-10-05）：OCR 的 PowerShell 兜底脚本必须**随包走**。
           #   原来 clock.py 写死了本机路径 <工作区>\_ctl_recon\WinOcrProbe.ps1，
           #   而这个文件根本没进包 —— 装出来的 exe 那条兜底路是死的。
           #   现在 clock.py 按 _MEIPASS\ocr\ 解析，spec 这里把它放进去，两边对上。
           (_R('ocr/WinOcrProbe.ps1'), 'ocr'),
           # ★ P2（2026-10-16）：动作面异常刹车的**阈值文件**必须随包走。
           #   action_guard.py 按「模块旁边的 json」找它 ⇒ 放 agentctl/ 目录，
           #   与冻结态 __file__ 落在 _MEIPASS\agentctl\ 对上。
           #   不带的话装出来的 exe 只能吃代码里的兜底值 ⇒ 用户改阈值改不动。
           (_R('src/agentctl/action_guard_policy.json'), 'agentctl')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
# ★ C-0（2026-10-24 · M19P3-W3）：**不分发** msvcp140.dll。
#   实测：vendor 里 7 个 winrt 的 `.pyd`（`_winrt*.cp312-win_amd64.pyd`）的 **PE 导入表里写着
#   `MSVCP140.dll`** ⇒ PyInstaller 照导入表收依赖时，会从搜索路径（**本机 System32**）收一份进来
#   ⇒ 那等于**又分发**了微软专有件（条款：再分发 "limited to licensed Visual Studio users"），
#   而 vendor 侧那份已被 C-0 移除 ⇒ 冻结构建**必须显式剔掉**，否则 C-0 在这条产物线上破功。
#   ⇒ 剔掉后运行时走**目标机 System32 自带那份**（缺 VC++ 运行库时由 install.ps1 指路 vcredist）。
#   ⚠ 本条**未实跑验证**：git worktree 检出里没有 vendor 的 `.pyd`（未跟踪）⇒ 冻结构建在本机跑不起来。
a.binaries = [(k, v) for (k, v) in a.binaries
              if os.path.basename(k).lower() != "msvcp140.dll"]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AgentCtl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AgentCtl',
)
