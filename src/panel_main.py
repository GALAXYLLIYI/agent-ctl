# -*- coding: utf-8 -*-
"""面板专用入口 —— 打成**无控制台窗口**的 exe。

## 为什么要拆成两个 exe（而不是把 console 改成 False）
AgentCtl.exe 必须留控制台：
  · mcp 入口靠 stdout/stdin 通信 —— 一旦没有 stdout 就直接死
  · serve / status / selftest 的输出全靠控制台
所以改成两个：
  AgentCtl.exe       console=True   命令行、mcp、服务
  AgentCtlPanel.exe  console=False  只有悬浮面板（快捷方式指向它）

## 无窗口模式下的坑：崩了都没人知道
面板启动失败就变成「双击了没反应」，用户只能干瞪眼。
所以这里把异常抓到本地日志，不依赖控制台。

⚠ 一个已被实测推翻的说法（写在这里是为了下次不再猜）：
原记录写的是「console=False 时 sys.stdout 是 None，**print 会抛 AttributeError**」——**这是错的**。
实测 CPython 3.12.7：
    print(...) / traceback.print_exc()   → 静默 no-op，不抛
    sys.stdout.write(...) / .flush()     → AttributeError（才会抛）
真正需要 try 的是后两种写法，不是 print。
真正的陷阱在别处：面板崩了用户不知道，而不是字符串打不出来。
"""
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

LOG_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'AgentCtl')
LOG = os.path.join(LOG_DIR, 'panel_crash.log')


def _log(text):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        import datetime
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('[%s]\n%s\n\n'
                    % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), text))
    except Exception:
        pass


def main():
    try:
        from agentctl import widget
        return widget.run()
    except Exception:
        _log(traceback.format_exc())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
