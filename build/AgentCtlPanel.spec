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
