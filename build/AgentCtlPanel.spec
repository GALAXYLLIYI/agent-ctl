# -*- mode: python ; coding: utf-8 -*-
"""面板专用打包配置：console=False，出来的 exe 双击**不弹黑窗口**。

对照 AgentCtl.spec（console=True）——那个必须留着，
因为 mcp 入口靠 stdout/stdin 通信，命令行输出也靠控制台。
详见 src/panel_main.py 的模块说明。
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['winrt.windows.media.ocr', 'winrt.windows.graphics.imaging',
                 'winrt.windows.storage.streams', 'winrt.windows.globalization',
                 'winrt.windows.foundation', 'winrt.windows.foundation.collections',
                 'PIL._tkinter_finder']
hiddenimports += collect_submodules('winrt')
hiddenimports += collect_submodules('uiautomation')
hiddenimports += collect_submodules('comtypes')


a = Analysis(
    ['<工作区>/Documents/agent-ctl/src/panel_main.py'],
    pathex=['<工作区>/Documents/agent-ctl/src',
            '<工作区>/Documents/agent-ctl/vendor',
            '<工作区>/Documents/agent-ctl/stubs'],
    binaries=[],
    datas=[('<工作区>/Documents/agent-ctl/contract/tools.json', 'contract')],
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
    name='AgentCtlPanel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # ★ 与 AgentCtl.spec 唯一的实质差别
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
    name='AgentCtlPanel',
)
