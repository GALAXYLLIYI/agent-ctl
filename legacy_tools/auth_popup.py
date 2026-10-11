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

T5 新增（可选，最小改动）：
  python auth_popup.py --request "B级 点击滚动" \
      --targets '{"pids":[1234],"title_contains":["记事本"],"actions":["click"]}'
    → 把这张证**再收紧到具体目标**。三项都可选：
        pid/pids           只认这些 pid
        title_contains     只认窗口标题含这些片段之一的
        action/actions     只认这些动作名
    → **不填 --targets 就完全不写 targets 键** —— 与改前的证逐字节一致。
    → 校验（谁按目标放行）在 gate.py；本文件只管「签发时把目标写进证里」。
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

# ★ D18：跨进程锁**只 import 共享模块，不复制**（T14 整单就是在消灭双份漂移，复制等于造新债）。
#   锁文件与 gate.py 同源：`<AUTHDIR>/gate.lock` —— 两侧共用同一份 auth_pass.json。
from gate_lock import LockTimeout, lock_path  # noqa: E402
from gate_lock import exclusive as _lock_exclusive  # noqa: E402
from gate_lock import save_json  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
AUTHDIR = os.path.join(BASE, "_auth")
KEYFILE = os.path.join(AUTHDIR, "auth_key.json")
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json")
LOCKFILE = lock_path(AUTHDIR)
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
    # ★ D18·R1：原子写（唯一临时名 + 有界重试替换）统一在共享模块 gate_lock.save_json；
    #   本函数只留老名字转发。改前它与 gate._save 各写一份、且已开始漂移（一份有 makedirs、一份没有）。
    save_json(path, obj)


def verifier(key, salt):
    """加盐 + 拉伸 —— 存这个，不存密钥。
    拉伸（PBKDF2 20 万次）是为了短密钥：离线字典攻击从"瞬间"变成"要花时间"。"""
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               bytes.fromhex(salt), PBKDF_ITER).hex()


# ────────────────────── T5：可选「目标三元组」targets ──────────────────────
# 签发侧只做三件事：解析 --targets、在弹窗上如实显示、**填了才写进证里**。
# 判定（谁按目标放行）在 gate.py —— 别在这里重复一套，免得好好的两套口径各自跑偏。
_TARGET_ALIAS = {
    "pids": "pids", "pid": "pids",
    "title_contains": "titles",
    "actions": "actions", "action": "actions",
}


def _norm_popup_targets(raw):
    """把 --targets 的 JSON 串规整成 gate 侧认的三元组。
    没给 → None。给得不对 → 抛 ValueError（**宁可不发证，也不发一张含糊的证**）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError(f"--targets 不是合法 JSON：{e}")
    if not isinstance(obj, dict):
        raise ValueError(f"--targets 必须是 JSON 对象，本次是 {type(obj).__name__}")
    norm = {"pids": [], "titles": [], "actions": []}
    for k, v in obj.items():
        ks = str(k)
        if ks.startswith("_"):
            continue                       # 下划线开头 = 注释，不参与判定（与 gate.py 同规矩）
        g = _TARGET_ALIAS.get(ks)
        if not g:
            raise ValueError(f"--targets 里有闸门不认识的键「{ks}」"
                             "（只认 pid/pids、title_contains、action/actions）")
        vals = v if isinstance(v, (list, tuple)) else [v]
        norm[g].extend(str(x).strip() for x in vals if str(x).strip())
    if not (norm["pids"] or norm["titles"] or norm["actions"]):
        raise ValueError("--targets 里没有可判定的项（三项都空）"
                         "—— 空声明不等于不限制，拒绝签发")
    out = {}
    if norm["pids"]:
        pids = []
        for x in norm["pids"]:
            try:
                pids.append(int(float(x)))
            except Exception:
                raise ValueError(f"--targets 里的 pid「{x}」不是整数")
        out["pids"] = pids
    if norm["titles"]:
        out["title_contains"] = norm["titles"]
    if norm["actions"]:
        out["actions"] = norm["actions"]
    return out


def _sum_targets(tg):
    """一行话把三元组说清（弹窗上给用户当场核对）。"""
    parts = []
    if tg.get("pids"):
        parts.append("pid " + "/".join(str(x) for x in tg["pids"]))
    if tg.get("title_contains"):
        parts.append("窗口标题含 " + "/".join(f"「{x}」" for x in tg["title_contains"]) + " 之一")
    if tg.get("actions"):
        parts.append("动作名 " + "/".join(tg["actions"]) + " 之一")
    return " · ".join(parts)


def targets_note():
    """通行证带 targets 时补一句提醒：按目标放行是 gate.py 判的。
    不带 targets 时返回空串 ⇒ --status/--check 的输出与改前逐字节一致。"""
    p = _load(PASSFILE, {})
    if isinstance(p, dict) and "targets" in p:
        return ("\n   ⚠ 本证带目标三元组 targets：本命令只查范围/时限/次数，"
                "真正「按目标放行」由 gate.py 判"
                "（guard 侧只做 pid+动作名，窗口标题项不实现）")
    return ""


# ────────────────────────── 弹窗 ──────────────────────────
def _popup(kind, scope="", minutes=10, acts=20, note="", targets=None):
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
        if note:
            # 2026-10-07 加：窗口审批要用。note 只**显示**，绝不混进 scope ——
            # scope 会被切分后存成通行证范围，塞别的东西进去会让范围对不上。
            label(note, 11, "#7ee787", 8)
        label(f"本次申请范围：{scope}\n有效期：{minutes} 分钟（过期或撤销即失效）\n"
              f"动作次数上限：{acts} 次（用完即止）", 11, "#ffd479")
        if targets:
            label("本次限定目标（targets）：\n" + _sum_targets(targets)
                  + "\n目标对不上、或拿不到目标信息 → 一律拒绝", 10, "#8fd3f4")

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
            pass_rec = {"scope": "、".join(scopes), "scopes": scopes, "issued": _now(),
                        "expires_at": expires,
                        "expires": datetime.datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S"),
                        "acts_limit": acts, "nonce": secrets.token_hex(8), "consumed": False}
            if targets is not None:
                # ★ 只有用户真填了目标才写这个键。绝不能写成 targets=None ——
                #   gate.py 用 `"targets" in p` 判存在，写了键就等于「声明了目标」，
                #   会把一张不设限的老证变成受限证。
                pass_rec["targets"] = targets
            _save(PASSFILE, pass_rec)
            rec["fail_count"] = 0
            _save(KEYFILE, rec)
            state["result"] = "granted"
            log(f"✅ 授权通过：范围「{scope}」 {minutes} 分钟，动作上限 {acts} 次"
                + (f"，限定目标〔{_sum_targets(targets)}〕" if targets is not None else ""))
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
    """★ D18·R3：整段「读 → 判定 → 写回」进**跨进程锁**（与 gate.require 同一把锁）。

    改前这里不取锁，而 gate.require 已取锁 ⇒ 后者能把前者的 `consumed` 覆盖回去：
    实测（把窗口拉到确定顺序）stale 写回后 `consumed=False`、`consumed_at` 消失
    ⇒ **已消费的证复活**。等锁超时按本模块契约返回 `(False, 原因)`，不抛异常。
    """
    try:
        with _lock_exclusive(AUTHDIR):
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
    except LockTimeout as e:
        return False, f"{e}，本次拒绝（不越权）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="store_true")
    ap.add_argument("--request")
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--acts", type=int, default=20, help="本张通行证的动作用量上限")
    ap.add_argument("--note", default="", help="只显示在弹窗上的一句话（不进通行证范围）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check")
    ap.add_argument("--consume", action="store_true")
    ap.add_argument("--targets", help="T5 可选：把这张证再收紧到具体目标，JSON 形如 "
                                      '{"pids":[1234],"title_contains":["记事本"],"actions":["click"]}'
                                      "（只在 --request 时生效；不填则不写 targets 键）")
    a = ap.parse_args()

    if a.targets and not a.request:
        print("⚠ --targets 只在 --request（签发新证）时才有意义，本次忽略")

    if a.set:
        return 0 if _popup("set") == "set" else 5
    if a.status:
        ok, why = check_pass()
        print((("✅ " + why) if ok else ("— " + why)) + targets_note())
        return 0 if ok else 3
    if a.check is not None:
        ok, why = check_pass(a.check or None, a.consume)
        print((("✅ " + why) if ok else ("⛔ " + why)) + targets_note())
        return 0 if ok else 3
    if a.request:
        try:
            tg = _norm_popup_targets(a.targets)
        except ValueError as e:
            print(f"⛔ 不签发：{e}")
            return 4
        r = _popup("request", a.request, a.minutes, a.acts, a.note, targets=tg)
        if r == "granted":
            ok, why = check_pass()
            print("通行证已签发：" + why)
            if tg:
                print("   └ 已写入限定目标：" + _sum_targets(tg))
            return 0
        print("未授权：" + (r or "窗口被关闭"))
        return 3
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
