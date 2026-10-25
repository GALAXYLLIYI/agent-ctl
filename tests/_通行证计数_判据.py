# -*- coding: utf-8 -*-
r"""tests\_通行证计数_判据.py —— 第 30 条判据：**新签的证从 0 起算**。

治的病（2026-10-19 窗口D 实跑时踩出，用户当场看到的现象）：
    用户点「授权」、批准 200 次动作，回执却是 ——
        本张通行证动作次数已用尽（104/20）
    而且要批到 **比历史累计还大**（20 不行 → 60 不行 → 200 才过）才放行。
    => **用户每批一次，弹窗上写的那个次数说了不算。**

根因：`auth_popup.merge_pass` 在签发新证时原样保留旧的 `acts` 计数器，
    注释写的是「只保留，不重置、不新建」，意图是防模型自己给自己续额度。
    但**这一行只有人在弹窗里亲手输了密钥才走得到**（铁律 11④：明文密钥永不接触模型），
    模型够不着 => 防的是一个够不着的威胁，代价是**人的决定被静默丢弃**。

修法：新签发时 `acts` 归零，旧计数留档到 `acts_carried`（审计不丢，只是不再卡新证）。
    追加模式（window_ack）仍在更上面就 return，**一分额度都不给**，口径没被碰到。

★ 本条**不需要通行证**：被测的是纯逻辑函数 `merge_pass`，不碰闸门、不碰屏幕。
  => 用户不在场也能复跑（这在两小时自主作业里是关键区别，见框架文件 §⑧）。

退出码：0=绿；1=红；2=验不了（找不到真源 auth_popup.py）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _find_legacy_tools():
    r"""定位真源 auth_popup.py 所在目录。**不许把盘符写死**（tests\_M_可移植性判据.py 的红线）。"""
    cands = []
    env = os.environ.get("AGENTCTL_LEGACY_HOME")
    if env:
        cands.append(env)
    cands.append(os.path.join(ROOT, "legacy_tools"))
    cands.append(os.path.join(os.path.dirname(ROOT), "dsh-viewer-plugin", "tools"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "auth_popup.py")):
            return c
    return ""


GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, str(detail)[:200]), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def main():
    home = _find_legacy_tools()
    if not home:
        rec("前置·找到真源 auth_popup.py", False, "三处候选都没找到", skip=True)
        return 2
    sys.path.insert(0, home)
    import auth_popup as A

    now = time.time()
    new = {"scopes": ["A", "B", "C"], "acts_limit": 20, "issued": now,
           "expires_at": now + 1800, "expires": "新", "nonce": "new"}
    old = {"scopes": ["A"], "scope": "A级 只读感知", "acts_limit": 20, "acts": 104,
           "issued": now - 9999, "expires_at": now - 3600, "expires": "旧",
           "nonce": "old", "consumed": False}

    out = A.merge_pass(dict(old), new, window_ack=False)
    rec("① 新签发 => acts 归零（不是接着 104 数）", out.get("acts") == 0,
        "acts=%s limit=%s" % (out.get("acts"), out.get("acts_limit")))
    rec("② 旧计数留档 acts_carried（审计不丢）", out.get("acts_carried") == 104,
        "acts_carried=%s" % out.get("acts_carried"))
    rec("③ 弹窗批的次数照旧生效（limit 不被历史覆盖）", out.get("acts_limit") == 20,
        "limit=%s" % out.get("acts_limit"))

    ack = A.merge_pass(dict(out), new, window_ack=True)
    rec("④ 窗口追加模式仍一分额度都不给（口径没被碰到）",
        ack.get("acts") == out.get("acts") and ack.get("acts_limit") == 20,
        "acts=%s limit=%s" % (ack.get("acts"), ack.get("acts_limit")))

    fresh = A.merge_pass({}, new, window_ack=False)
    rec("⑤ 全新签发（无旧证）=> acts=0 且不留空档位",
        fresh.get("acts") == 0 and "acts_carried" not in fresh,
        "acts=%s carried=%s" % (fresh.get("acts"), fresh.get("acts_carried")))

    if RED:
        print("\n红 %d：%s" % (len(RED), "；".join(RED)), flush=True)
        return 1
    print("\n全绿 %d" % len(GREEN), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
