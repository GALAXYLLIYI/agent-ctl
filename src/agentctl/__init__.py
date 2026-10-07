# -*- coding: utf-8 -*-
r"""agentctl —— 本机电脑控制的统一控制面

模块一览：
    core    唯一调度核心（HTTP/MCP/CLI 三态同源的"同源"就在这里）
    gate    安全内核：闸门（原子）＋急停＋单写者租约＋观测-动作绑定
    store   时间线真相源：jsonl ＋ 哈希链 ＋ 可重建 SQLite 索引 ＋ 带外锚点
    clock   三层时间源：单调钟 ＋ 墙钟锚点 ＋ 任务栏时钟见证（OCR 双路）
    clip    自研 MJPEG-AVI 出片器（纯标准库，不依赖 ffmpeg）
    server  HTTP 传输层
    mcp     MCP stdio 传输层（⚠️ 绝不能用 -w 打包）
    cli     命令行传输层

★ 为什么要显式写 __init__.py：
   2026-10-02 实测 —— 没有它时，本地靠隐式命名空间包能跑，
   但 **PyInstaller 冻结后模块收不全**（冻结自检报
   "cannot import name 'clip' from 'agentctl' (unknown location)"）。
   补上它就正常了。这是一条值钱的教训：**冻结前先确保每个包都有 __init__.py**。
"""

__version__ = "0.1.0"
__all__ = ["core", "gate", "store", "clock", "clip", "server", "mcp", "cli"]
