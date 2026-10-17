# -*- mode: python ; coding: utf-8 -*-
"""面板专用打包配置：console=False，出来的 exe 双击**不弹黑窗口**。

对照 AgentCtl.spec（console=True）——那个必须留着，
因为 mcp 入口靠 stdout/stdin 通信，命令行输出也靠控制台。
详见 src/panel_main.py 的模块说明。
"""
from PyInstaller.utils.hooks import collect_submodules
import os

# ★ 2026-10-17（用户裁：**不公开用户名**）：构建路径不再写死本机目录（同 AgentCtl.spec）。
_SPEC = globals().get("SPEC") or ""
_SPEC_DIR = (os.path.dirname(os.path.abspath(_SPEC)) if _SPEC
             else os.path.abspath(globals().get("SPECPATH") or "."))
_ROOT = os.path.dirname(_SPEC_DIR)


def _R(*parts):
    return os.path.join(_ROOT, *parts).replace("\\", "/")


hiddenimports = ['winrt.windows.media.ocr', 'winrt.windows.graphics.imaging',
                 'winrt.windows.storage.streams', 'winrt.windows.globalization',
                 'winrt.windows.foundation', 'winrt.windows.foundation.collections',
                 'PIL._tkinter_finder']
hiddenimports += collect_submodules('winrt')
hiddenimports += collect_submodules('uiautomation')
hiddenimports += collect_submodules('comtypes')


a = Analysis(
    [_R('src/panel_main.py')],
    pathex=[_R('src'), _R('vendor'), _R('stubs')],
    binaries=[],
    datas=[(_R('contract/tools.json'), 'contract'),
           # ★ P2（2026-10-16）：面板要读「动作面异常提示」那份文件（hint_view），
           #   阈值文件同样随包走 —— 否则面板显示的阈值和引擎用的兜底值可能不是一套。
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
