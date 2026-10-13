# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['winrt.windows.media.ocr', 'winrt.windows.graphics.imaging', 'winrt.windows.storage.streams', 'winrt.windows.globalization', 'winrt.windows.foundation', 'winrt.windows.foundation.collections', 'PIL._tkinter_finder']
hiddenimports += collect_submodules('winrt')
hiddenimports += collect_submodules('uiautomation')
hiddenimports += collect_submodules('comtypes')


a = Analysis(
    ['<工作区>/Documents/agent-ctl/src/agentctl_main.py'],
    pathex=['<工作区>/Documents/agent-ctl/src', '<工作区>/Documents/agent-ctl/vendor', '<工作区>/Documents/agent-ctl/stubs'],
    binaries=[],
    # _selftest_src：把 widget.py 的**源码原文**随包带走。
    # 目的只有一个 —— 装完的 exe 跑 selftest 时要能读到源码做审计
    #（查有没有 require( 调用、有没有 import 屏幕操作模块）。
    # 不带的话 AgentCtl.exe selftest 直接 FileNotFoundError 崩掉，
    # 装出来的包就没法自证没被开后门。平铺目录且**不叫 agentctl**，
    # 免得跟真正的包重名把 import 路径搅乱。
    datas=[('<工作区>/Documents/agent-ctl/contract/tools.json', 'contract'),
           ('<工作区>/Documents/agent-ctl/src/agentctl/widget.py', '_selftest_src'),
           # ★ B 项（2026-10-05）：OCR 的 PowerShell 兜底脚本必须**随包走**。
           #   原来 clock.py 写死了本机路径 F:\..._ctl_recon\WinOcrProbe.ps1，
           #   而这个文件根本没进包 —— 装出来的 exe 那条兜底路是死的。
           #   现在 clock.py 按 _MEIPASS\ocr\ 解析，spec 这里把它放进去，两边对上。
           ('<工作区>/Documents/agent-ctl/ocr/WinOcrProbe.ps1', 'ocr')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

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
