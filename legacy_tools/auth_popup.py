# -*- coding: utf-8 -*-
"""auth_popup.py —— 电脑控制的第 2 步：用户亲自授权的弹窗闸门

设计红线（准则 铁律 11）：
  * 密钥由用户**自己在弹窗里输入**，助手永远看不到明文；
  * 本地只存**加盐 SHA-256 校验值**，不存明文、不写日志、不回显（输入框打点）；
  * 每次申请都要用户**当场点一次**，通过后只发一张**限时 + 限定范围**的通行证；
  * 输错 → 锁定 60 秒（锁定状态持久化，重启弹窗也绕不过）；
  * 全程留痕：谁在什么时候、为哪个范围、授权还是拒绝。

**诚实说明**：这是「人必须在场」的确认闸，不是防御同用户恶意进程的密码学边界
（助手与用户同一 Windows 账户，能读的文件助手也能读）。真正的边界是**用户的手指**和`急停`。

用法：
  python auth_popup.py --set                      # 首次设置/修改密钥（输两遍）
  python auth_popup.py --request "A级 只读感知"    # 申请一次授权（默认 10 分钟）
  python auth_popup.py --status                   # 看当前通行证
  python auth_popup.py --check "A级 只读感知"      # 校验通行证是否可用（供控制脚本调用）
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
AUTHDIR = os.path.join(BASE, "_auth")
KEYFILE = os.path.join(AUTHDIR, "auth_key.json")
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json")
LOGFILE = os.path.join(AUTHDIR, "auth.log")
LOCK_SECONDS = 60
MIN_LEN = 4
PBKDF_ITER = 200_000
TITLE = "DSH电脑控制授权"          # 截图/感知层见到这个标题必须拒绝读取（见 perceive.py 黑名单）


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    os.makedirs(AUTHDIR, exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{_now()}] {msg}\n")
    print(f"[{_now()}] {msg}")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def verifier(key, salt):
    """加盐 + 拉伸 —— 存这个，不存密钥。
    拉伸（PBKDF2 20 万次）是为了短密钥：离线字典攻击从"瞬间"变成"要花时间"。"""
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               bytes.fromhex(salt), PBKDF_ITER).hex()


# ────────────────────────── 弹窗 ──────────────────────────
def _popup(kind, scope="", minutes=10, acts=20):
    """kind: 'set' 设置密钥 / 'request' 申请授权。返回 (结果, 密钥文本或 None)。"""
    import tkinter as tk
    state = {"result": None, "key": None}
    root = tk.Tk()
    root.title(TITLE)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")
    W = 460

    def label(txt, size=11, fg="#dddddd", pady=4):
        tk.Label(root, text=txt, font=("Microsoft YaHei UI", size), fg=fg,
                 bg="#1e1e1e", wraplength=W - 40, justify="left").pack(pady=pady, padx=20, anchor="w")

    if kind == "set":
        label("设置电脑控制授权密钥", 13, "#ffffff", 12)
        label("密钥只在本机做加盐拉伸校验（PBKDF2 20 万次），不保存明文、不进日志。\n"
              "只输一次——请点「显示」核对没打错。\n"
              "唯一硬要求：别用你的名字、别用本机文件或对话里出现过的词。", 10, "#aaaaaa")
    else:
        label("电脑控制授权申请", 13, "#ffffff", 12)
        label(f"本次申请范围：{scope}\n有效期：{minutes} 分钟（过期或撤销即失效）\n"
              f"动作次数上限：{acts} 次（用完即止）", 11, "#ffd479")

    lock = _load(KEYFILE, {})
    locked_until = lock.get("locked_until", 0)
    remain = int(locked_until - time.time())
    if remain > 0:
        label(f"⚠ 已锁定，请等 {remain} 秒后再试（连续输错触发）", 11, "#ff7b72", 10)

    tk.Label(root, text="密钥：", font=("Microsoft YaHei UI", 11), fg="#dddddd",
             bg="#1e1e1e").pack(pady=(10, 0), padx=20, anchor="w")
    e1 = tk.Entry(root, show="●", width=42, font=("Consolas", 11))
    e1.pack(pady=4, padx=20)

    show_var = tk.BooleanVar(value=False)

    def toggle_show():
        e1.config(show="" if show_var.get() else "●")

    if kind == "set":
        tk.Checkbutton(root, text="显示密钥（确认没打错）", variable=show_var, command=toggle_show,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#1e1e1e",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Microsoft YaHei UI", 10)).pack(pady=(0, 4), padx=20, anchor="w")

    tip = tk.Label(root, text="", font=("Microsoft YaHei UI", 10), fg="#ff7b72", bg="#1e1e1e")
    tip.pack(pady=4)

    def submit(_=None):
        key = e1.get()
        if kind == "set":
            if len(key) < MIN_LEN:
                tip.config(text=f"太短了，至少 {MIN_LEN} 位")
                return
            salt = secrets.token_hex(16)
            _save(KEYFILE, {"salt": salt, "verifier": verifier(key, salt),
                            "algo": f"pbkdf2-sha256-{PBKDF_ITER}", "created": _now(),
                            "locked_until": 0, "fail_count": 0,
                            "weak": len(key) < 8})
            state["result"] = "set"
            log("密钥已设置（只存校验值，未存明文）")
            e1.delete(0, "end")
            root.destroy()
            return
        # request
        rec = _load(KEYFILE, {})
        if not rec.get("verifier"):
            tip.config(text="还没有密钥，请先运行 --set")
            return
        if time.time() < rec.get("locked_until", 0):
            tip.config(text="锁定中，稍后再试")
            return
        if hmac.compare_digest(verifier(key, rec["salt"]), rec["verifier"]):
            expires = time.time() + minutes * 60
            scopes = [s.strip() for s in scope.split(",") if s.strip()]
            _save(PASSFILE, {"scope": "、".join(scopes), "scopes": scopes, "issued": _now(),
                             "expires_at": expires,
                             "expires": datetime.datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S"),
                             "acts_limit": acts, "nonce": secrets.token_hex(8), "consumed": False})
            rec["fail_count"] = 0
            _save(KEYFILE, rec)
            state["result"] = "granted"
            log(f"✅ 授权通过：范围「{scope}」 {minutes} 分钟，动作上限 {acts} 次")
            e1.delete(0, "end")
            root.destroy()
        else:
            rec["fail_count"] = rec.get("fail_count", 0) + 1
            rec["locked_until"] = time.time() + LOCK_SECONDS
            _save(KEYFILE, rec)
            log(f"❌ 密钥错误（第 {rec['fail_count']} 次），锁定 {LOCK_SECONDS} 秒")
            e1.delete(0, "end")
            tip.config(text=f"密钥不对，已锁定 {LOCK_SECONDS} 秒")
            root.destroy()

    def deny():
        state["result"] = "denied"
        log(f"⛔ 用户拒绝授权：范围「{scope}」")
        root.destroy()

    bar = tk.Frame(root, bg="#1e1e1e")
    bar.pack(pady=12)
    tk.Button(bar, text="授 权", width=12, command=submit,
              bg="#2d7d46", fg="white", font=("Microsoft YaHei UI", 11)).pack(side="left", padx=8)
    tk.Button(bar, text="拒 绝", width=12, command=deny,
              bg="#6b2b2b", fg="white", font=("Microsoft YaHei UI", 11)).pack(side="left", padx=8)
    root.bind("<Return>", submit)
    root.bind("<Escape>", lambda e: deny())
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{root.winfo_reqheight()}+{(sw - W) // 2}+{(sh - root.winfo_reqheight()) // 3}")
    e1.focus_force()
    root.mainloop()
    return state["result"]


# ────────────────────────── 命令 ──────────────────────────
def check_pass(scope=None, consume=False):
    p = _load(PASSFILE, {})
    if not p:
        return False, "没有通行证"
    if p.get("consumed"):
        return False, "通行证已被使用"
    if time.time() > p.get("expires_at", 0):
        return False, f"通行证已过期（{p.get('expires')}）"
    if scope and p.get("scope") != scope:
        return False, f"通行证范围不符：持「{p.get('scope')}」，要用「{scope}」"
    if consume:
        p["consumed"] = True
        p["consumed_at"] = _now()
        _save(PASSFILE, p)
    return True, f"范围「{p.get('scope')}」有效至 {p.get('expires')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="store_true")
    ap.add_argument("--request")
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--acts", type=int, default=20, help="本张通行证的动作用量上限")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check")
    ap.add_argument("--consume", action="store_true")
    a = ap.parse_args()

    if a.set:
        return 0 if _popup("set") == "set" else 5
    if a.status:
        ok, why = check_pass()
        print(("✅ " + why) if ok else ("— " + why))
        return 0 if ok else 3
    if a.check is not None:
        ok, why = check_pass(a.check or None, a.consume)
        print(("✅ " + why) if ok else ("⛔ " + why))
        return 0 if ok else 3
    if a.request:
        r = _popup("request", a.request, a.minutes, a.acts)
        if r == "granted":
            ok, why = check_pass()
            print("通行证已签发：" + why)
            return 0
        print("未授权：" + (r or "窗口被关闭"))
        return 3
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
