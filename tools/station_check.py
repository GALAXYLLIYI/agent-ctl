# -*- coding: utf-8 -*-
r"""station_check.py —— 工作站体检（只读，30 秒回答"现在能不能干活"）。

工单：work\工单_001_工作站体检_20261019.md
输出：人读一页；`--json` 一份机器读。

字段（与工单 §1① 逐项对齐）：
  channels.uia{nodes,usable} / channels.ocr{usable} / channels.cdp{port,usable}
  runtime.act_py_md5 / runtime.repo_act_md5 / runtime.consistent
  desk.border_windows / desk.notepad_leftover
  env.disk_free_gb / env.model_keys{minimax,glm,mimo}   ← 只布尔，不读任何密钥内容
  auth.pass_present / auth.pass_expires / auth.lease_holder / auth.brake
  verdict ∈ ready | runtime_drift | no_pass | brake_on | blind | dirty_desktop
  advice ≤60 字

★ 只读：不 spawn 键鼠、不改任何文件。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

RUNTIME_ACT = r"<工作区>\Documents\dsh-viewer-plugin\tools\act.py"
REPO_ACT = os.path.join(ROOT, "legacy_tools", "act.py")


def md5_8(path):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:8]
    except Exception:
        return ""


def _enum(pred):
    u = ctypes.windll.user32
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        try:
            if u.IsWindowVisible(h) and pred(h):
                hits.append(int(h))
        except Exception:
            pass
        return True
    u.EnumWindows(cb, 0)
    return hits


def _cls(h):
    b = ctypes.create_unicode_buffer(128)
    ctypes.windll.user32.GetClassNameW(h, b, 128)
    return b.value


def channels():
    out = {}
    # UIA：拿前台窗探一下（拿不到就当不可用，不假装）
    try:
        from agentctl import uia_tree
        u = ctypes.windll.user32
        fg = int(u.GetForegroundWindow() or 0)
        nodes = 0
        if fg:
            # ★ 必须用 walk()：tree(detail=False) 的「节点」恒为 []（uia_tree.py:715），
            #   拿它数节点永远是 0；上一版数到的其实是 dict 键数，是个假数字。
            got = uia_tree.walk(fg, max_nodes=200, detail=False)
            if isinstance(got, tuple):
                got = got[0]
            nodes = len(got or [])
        out["uia"] = {"nodes": nodes, "usable": nodes > 0, "probe_hwnd": fg}
    except Exception as e:
        out["uia"] = {"nodes": None, "usable": False, "error": str(e)[:80]}
    # OCR
    try:
        import importlib
        importlib.import_module("agentctl.ocr")
        out["ocr"] = {"usable": True}
    except Exception as e:
        out["ocr"] = {"usable": False, "error": str(e)[:80]}
    # CDP
    port = int(os.environ.get("AGENTCTL_CDP_PORT") or 9222)
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as f:
            info = json.loads(f.read().decode("utf-8", "replace"))
        out["cdp"] = {"port": port, "usable": True, "browser": str(info.get("Browser") or "")[:40]}
    except Exception as e:
        out["cdp"] = {"port": port, "usable": False, "error": str(e)[:60]}
    return out


def desk():
    border = _enum(lambda h: "AgentCtlCtrlBorder" in _cls(h))
    notes = _enum(lambda h: _cls(h) == "Notepad")
    return {"border_windows": len(border), "notepad_leftover": len(notes)}


def envinfo():
    free_gb = 0.0
    try:
        du = shutil.disk_usage(os.path.splitdrive(ROOT)[0] + "\\")
        free_gb = round(du.free / (1024 ** 3), 1)
    except Exception:
        pass
    # 只判"配没配"，绝不读值、绝不打印
    keys = {
        "minimax": bool(os.environ.get("MINIMAX_API_KEY") or os.environ.get("MMX_API_KEY")),
        "glm": bool(os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY")),
        "mimo": bool(os.environ.get("MIMO_API_KEY")
                     or os.path.exists(os.path.expanduser("~/.mimocode"))),
    }
    return {"disk_free_gb": free_gb, "model_keys": keys}


def auth():
    # ★ 走 gate.status()（纯读）：core.dispatch 的动作出口会调 action_guard.observe() 落盘，
    #   「只读体检」不该顺手写文件。
    from agentctl import gate
    try:
        d = gate.status() or {}
    except Exception as e:
        return {"error": str(e)[:80], "pass_present": None, "brake": None,
                "pass_expires": None, "lease_holder": None}
    p = d.get("pass") or {}
    l = d.get("lease") or {}
    b = d.get("brake") or {}
    # ★ 通行证"存在"≠"有效"：必须把过期算进去（2026-10-19 实测踩到：
    #   present=True 但 expires=16:13，已过期却被判成 ready —— 这种"假绿"最误事）
    expires = p.get("expires")
    valid = None
    try:
        if expires:
            valid = time.mktime(time.strptime(expires, "%Y-%m-%d %H:%M:%S")) > time.time()
    except Exception:
        valid = None
    return {"pass_present": bool(p.get("present")),
            "pass_valid": valid,
            "pass_expires": expires,
            "pass_acts": [p.get("acts"), p.get("acts_limit")],
            # ★ 实测踩到：租约释放后 holder 字段仍在（只有 active=false）⇒ 必须看 active
            "lease_holder": (l.get("holder") or None) if l.get("active") else None,
            "brake": bool(b.get("engaged"))}


def verdict_of(rep):
    if rep["auth"].get("brake"):
        return "brake_on", "急停已拉下：先松开 _急停.flag 再谈干活"
    if rep["runtime"].get("blocked"):
        return ("runtime_drift",
                "运行时/真源 act.py 读不到（%s）：**验不了，不是漂移**"
                % rep["runtime"]["blocked"][0][:40])
    if not rep["runtime"]["consistent"]:
        return ("runtime_drift",
                "运行时 act.py 与真源不一致（%s vs %s）：先刷新并留 md5"
                % (rep["runtime"]["act_py_md5"], rep["runtime"]["repo_act_md5"]))
    th = rep["auth"]
    if not th.get("pass_present") or th.get("pass_valid") is False:
        return ("no_pass",
                "通行证%s：让用户弹窗签发一张（A/B/C）"
                % ("已过期（至 %s）" % th.get("pass_expires") if th.get("pass_present")
                   else "不存在"))
    if rep["desk"]["notepad_leftover"]:
        return "dirty_desktop", "屏幕上有 %d 个遗留记事本：先关掉再开工" % rep["desk"]["notepad_leftover"]
    ch = rep["channels"]
    if not (ch["uia"].get("usable") or ch["cdp"].get("usable")):
        return "blind", "UIA 读不到且 CDP 不通：只能像素+OCR，风险高，先开调试端口"
    lease = th.get("lease_holder")
    if lease:
        return "ready", "可以开工，但租约仍被「%s」持有：写动作前先 ctl_lease release" % lease
    return "ready", "可以开工（通道正常、无漂移、无遗留窗）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rep = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "channels": channels(), "desk": desk(), "env": envinfo(), "auth": auth()}
    rep["runtime"] = {
        "runtime_act": RUNTIME_ACT,
        "repo_act": REPO_ACT,
        "act_py_md5": md5_8(RUNTIME_ACT),
        "repo_act_md5": md5_8(REPO_ACT),
    }
    blocked = []
    if not rep["runtime"]["act_py_md5"]:
        blocked.append("runtime_act.py 读不到（权限/路径），无法核对")
    if not rep["runtime"]["repo_act_md5"]:
        blocked.append("repo act.py 读不到，无法核对")
    rep["runtime"]["blocked"] = blocked
    rep["runtime"]["verdict_status"] = "ok" if not blocked else "cannot_verify"
    rep["runtime"]["consistent"] = (not blocked) and \
        rep["runtime"]["act_py_md5"] == rep["runtime"]["repo_act_md5"]

    v, advice = verdict_of(rep)
    rep["verdict"], rep["advice"] = v, advice

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if v in ("ready", "dirty_desktop") else 1

    ch, th = rep["channels"], rep["auth"]
    print("=" * 64)
    print("工作站体检 · %s" % rep["ts"])
    print("=" * 64)
    print("通道   UIA 节点 %-5s 可用=%-5s ｜ OCR %-5s ｜ CDP %s 可用=%s"
          % (ch["uia"].get("nodes"), ch["uia"].get("usable"),
             ch["ocr"].get("usable"), ch["cdp"].get("port"), ch["cdp"].get("usable")))
    print("运行时 act.py md5=%s ｜ 真源 md5=%s ｜ 一致=%s"
          % (rep["runtime"]["act_py_md5"], rep["runtime"]["repo_act_md5"],
             rep["runtime"]["consistent"]))
    print("桌面   遗留记事本 %d ｜ 指示层边框窗 %d"
          % (rep["desk"]["notepad_leftover"], rep["desk"]["border_windows"]))
    print("环境   磁盘剩余 %.1f GB ｜ 模型凭据 minimax=%s glm=%s mimo=%s"
          % (rep["env"]["disk_free_gb"], rep["env"]["model_keys"]["minimax"],
             rep["env"]["model_keys"]["glm"], rep["env"]["model_keys"]["mimo"]))
    print("授权   通行证=%s（至 %s，%s 次）｜ 租约=%s ｜ 急停=%s"
          % (th.get("pass_present"), th.get("pass_expires"), th.get("pass_acts"),
             th.get("lease_holder") or "无", th.get("brake")))
    print("-" * 64)
    print("结论   %s —— %s" % (rep["verdict"], rep["advice"]))
    return 0 if v in ("ready", "dirty_desktop") else 1


if __name__ == "__main__":
    sys.exit(main())
