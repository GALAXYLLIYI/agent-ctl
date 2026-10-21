# -*- coding: utf-8 -*-
"""单例弹窗仲裁器 —— **同一时刻屏幕上只允许一个授权弹窗**。

病根（2026-10-21 用户现场抱怨「为什么会有四个闸口弹窗访问我」）：
    并发的 ctl_authorize 请求**各自拉一个独立弹窗进程** ⇒ 屏幕上同时出现 N 个弹窗。
    实证（闸门日志 _auth/gate_agentctl.log）：2026-10-21 00:25:27 / 00:26:25 / 00:28:10
    三笔授权请求**重叠**，而第一笔直到 00:29:10 才被点掉 ⇒ 那一刻屏幕上最多同时有 3 个弹窗。

治法（**语义不变，只降噪**）：
    ① 跨进程**单例槽**：同一时刻只允许一个进程持有弹窗槽。
       槽文件在 AUTHDIR 下（popup_slot.json），**取槽/放槽用「原子创建」实现**
       （O_CREAT|O_EXCL，Windows 上也是原子的）。
    ② 后到的请求**等**：拿到槽之后先查证「现在这张证是不是已经覆盖我要的范围」
       ⇒ 覆盖就直接放行（**不再弹窗**）；不覆盖才弹 —— 此时前一个弹窗已经关了，
       屏幕上仍然只有一个。
    ③ 等不到 ⇒ **fail-closed**（返回 busy 并拒绝），绝不 fail-open（绝不「先弹了再说」）。

★ 为什么槽不用 gate.lock 一直持有：弹窗要等人输密钥（可达 180 秒），
  而 gate.lock 是**每一次动作检查**都要拿的锁 —— 拿着它等 3 分钟会把整个闸门堵死。
  ⇒ 槽是**另一个文件**、**另一种机制**（存在性声明 + 过期接管），不是复制一份 gate_lock。

★ 过期接管：持有者进程崩了 / 被人杀了 ⇒ 槽文件会留下。超过 stale_after 秒
  （默认 300 秒 > 默认弹窗超时 180 秒）就判定为陈旧并接管 —— 不接管的话，
  一次崩溃就能让授权功能永久卡死（那比多一个弹窗严重得多）。
"""

import errno
import json
import os
import time

SLOT_NAME = "popup_slot.json"
DEFAULT_WAIT = 240.0        # 等前一个弹窗多久还没完就放弃（秒）
DEFAULT_STALE = 300.0       # 槽声明超过多久算陈旧、可以接管（秒）
POLL = 0.2


class SlotBusy(Exception):
    """等不到槽。调用方**必须**据此拒绝（fail-closed），不许绕过去硬弹。"""


def _slot_path(authdir):
    return os.path.join(authdir, SLOT_NAME)


def _read_json(path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except Exception:
        return {}


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    try:
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _claim(authdir, owner):
    """原子地占槽。返回 (拿到?, 槽内容)。"""
    path = _slot_path(authdir)
    rec = {"pid": os.getpid(), "owner": owner, "since": time.time()}
    try:
        os.makedirs(authdir, exist_ok=True)
    except Exception:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as e:
        if e.errno not in (errno.EEXIST,):
            # 建不了（权限/只读）⇒ 当成拿不到，交给上层 fail-closed
            return False, {"error": str(e)}
        old = _read_json(path)
        return False, old
    try:
        os.write(fd, json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    # 回读确认是自己那份（别人可能刚好清掉重建了）
    back = _read_json(path)
    if back.get("owner") != owner:
        return False, back
    return True, rec


def _release(authdir, owner):
    path = _slot_path(authdir)
    cur = _read_json(path)
    if cur.get("owner") == owner:
        try:
            os.unlink(path)
        except OSError:
            pass


def covers(passfile, scopes):
    """当前这张证**是否覆盖**给定的范围列表（读，只读）。"""
    p = _read_json(passfile or "")
    if not p:
        return False, "没有通行证"
    if p.get("consumed"):
        return False, "通行证已被使用"
    exp = p.get("expires_at")
    try:
        if not exp or float(exp) <= time.time():
            return False, "通行证已过期"
    except Exception:
        return False, "通行证有效期读不出来"
    have = p.get("scopes")
    if not isinstance(have, list):
        raw = p.get("scope") or ""
        have = [s for s in str(raw).replace("、", ",").split(",") if s.strip()]
    have = [str(s).strip() for s in have]
    want = [str(s).strip() for s in (scopes or [])]
    miss = [s for s in want if s not in have]
    if miss:
        return False, "通行证范围不含：%s（现有：%s）" % ("、".join(miss), "、".join(have))
    return True, "通行证已覆盖 %s" % "、".join(want)


def authorize(authdir, passfile, scopes, popup_fn, *, wait=DEFAULT_WAIT,
              stale_after=DEFAULT_STALE, poll=POLL, owner=None, log=None):
    """在**单例槽**里申请授权。

    参数：
      authdir / passfile —— 闸门状态目录与通行证文件（只读通行证）
      scopes            —— 本次要的范围（**完整名**列表，如 ["B级 点击滚动"]）
      popup_fn          —— 无参可调用；返回 (rc, out, err)；**只有真正要弹窗时才调**
      wait / stale_after —— 等多久放弃 / 槽声明多久算陈旧
      log               —— 可选日志回调（str → None）

    返回 dict：
      {"outcome": "granted_pass" | "granted_popup" | "denied" | "busy",
       "rc": int, "out": str, "err": str, "detail": str, "waited": float}
    """
    owner = owner or ("pid-%d-%d" % (os.getpid(), int(time.time() * 1000)))
    t0 = time.time()
    deadline = t0 + float(wait)
    n_wait = 0
    while True:
        # ① 先查证：证已经覆盖了？覆盖 ⇒ 直接放行（**一个弹窗都不弹**）
        ok, why = covers(passfile, scopes)
        if ok:
            return {"outcome": "granted_pass", "rc": 0, "out": "", "err": "",
                    "detail": why, "waited": time.time() - t0}
        # ② 抢槽
        got, rec = _claim(authdir, owner)
        if got:
            try:
                # ③ 拿到槽后再查一次（等锁期间别人可能刚签过）
                ok2, why2 = covers(passfile, scopes)
                if ok2:
                    return {"outcome": "granted_pass", "rc": 0, "out": "", "err": "",
                            "detail": why2, "waited": time.time() - t0}
                # ④ 确实要弹 ⇒ 弹（**此刻屏幕上只有这一个**）
                rc, out, err = popup_fn()
                if rc == 0:
                    return {"outcome": "granted_popup", "rc": rc, "out": out, "err": err,
                            "detail": "弹窗已通过", "waited": time.time() - t0}
                return {"outcome": "denied", "rc": rc, "out": out, "err": err,
                        "detail": "弹窗未通过（用户拒绝、超时未操作、或密钥错误）",
                        "waited": time.time() - t0}
            finally:
                _release(authdir, owner)
        # ⑤ 没抢到 ⇒ 看是不是陈旧槽（持有者崩了）⇒ 接管
        since = float(rec.get("since") or 0) if isinstance(rec, dict) else 0
        if rec.get("error"):
            return {"outcome": "busy", "rc": 9, "out": "", "err": str(rec.get("error")),
                    "detail": "弹窗槽建不了（%s）—— fail-closed，本次拒绝" % rec.get("error"),
                    "waited": time.time() - t0}
        if since and (time.time() - since) > float(stale_after):
            try:
                os.unlink(_slot_path(authdir))
                if log:
                    log("授权弹窗槽陈旧（持有者可能已退出，超过 %ds）⇒ 接管" % stale_after)
            except OSError:
                pass
            continue
        # ⑥ 正常等待
        if time.time() > deadline:
            return {"outcome": "busy", "rc": 9, "out": "", "err": "",
                    "detail": "等了 %.0fs 还没轮到弹窗（前面那个还没结束）—— fail-closed，本次拒绝"
                              % (time.time() - t0),
                    "waited": time.time() - t0}
        n_wait += 1
        if log and n_wait == 1:
            log("已有一个授权弹窗在屏幕上 ⇒ 本次请求排队等待（同一时刻只弹一个）")
        time.sleep(poll)
