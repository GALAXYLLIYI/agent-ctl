# -*- coding: utf-8 -*-
r"""server.py —— 本地 HTTP 传输层（纯标准库）

设计依据：详细设计 §6.3
  · **只监听 127.0.0.1**（实测：非管理员用户对防火墙弹框点什么都只会生成 Block 规则，绑回环最稳）
  · **默认不发 CORS 头**；校验 Host 只允许 127.0.0.1:PORT / localhost:PORT（防 DNS rebinding）
  · 令牌在 state\token（安装时随机生成，权限限当前用户）
  · 所有路由最终都调 core.dispatch() —— **三态同源**

路由：
  GET  /v1/health            免令牌（只回"活着"，不泄露任何状态）
  GET  /v1/status
  GET  /v1/events
  POST /v1/<tool 的短名>      例：POST /v1/perceive/shot、/v1/lease、/v1/brake
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentctl import core                                            # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENTCTL_PORT", "8760"))
STATE_DIR = os.environ.get("AGENTCTL_STATE", os.path.join(core.ROOT, "work", "state"))
TOKEN_FILE = os.path.join(STATE_DIR, "token")

# 路由别名 → 工具名
ROUTES = {
    "/v1/status": "ctl_status",
    "/v1/events": "ctl_timeline",
    "/v1/lease": "ctl_lease",
    "/v1/brake": "ctl_brake",
    "/v1/verify": "ctl_verify",
    "/v1/witness": "ctl_witness",
    "/v1/replay": "ctl_replay",
    "/v1/record": "ctl_record",
    "/v1/perceive/windows": "ctl_windows",
    "/v1/perceive/shot": "ctl_shot",
    "/v1/perceive/tree": "ctl_tree",
    "/v1/act/click": "ctl_click",
    "/v1/act/type": "ctl_type",
    "/v1/act/fill": "ctl_fill",
    "/v1/act/focus": "ctl_focus",   # 2026-10-02 补：加了工具却忘了加路由，HTTP 到不了
    "/v1/authorize/request": "ctl_authorize",
    "/v1/browser": "ctl_browser",
}


def ensure_token() -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        tok = secrets.token_hex(32)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass
        return tok
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return f.read().strip()


ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentCtl/0.1"

    # ── 工具 ──
    def _send(self, obj: dict, http: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(http)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # ★ 默认不发 CORS 头（浏览器跨源到不了这里）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").strip().lower()
        return h in {x.lower() for x in ALLOWED_HOSTS}

    def _token_ok(self) -> bool:
        want = ensure_token()
        got = (self.headers.get("X-AgentCtl-Token") or "").strip()
        return bool(got) and secrets.compare_digest(got, want)

    def _actor(self) -> str:
        return (self.headers.get("X-AgentCtl-Actor") or "unknown").strip() or "unknown"

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            j = json.loads(raw.decode("utf-8"))
            return j if isinstance(j, dict) else {"_": j}
        except Exception:
            return {"_raw": raw[:200].decode("utf-8", "replace")}

    def log_message(self, fmt, *a):        # 诊断走 stderr，不污染 stdout
        sys.stderr.write("[http] " + (fmt % a) + "\n")

    # ── 路由 ──
    def do_GET(self):
        if not self._host_ok():
            return self._send({"ok": False, "code": 5, "warning": "Host 头不被接受（防 DNS rebinding）"}, 403)
        path = self.path.split("?")[0]
        if path == "/v1/health":
            return self._send({"ok": True, "code": 0, "data": {"service": "AgentCtl", "alive": True}})
        if not self._token_ok():
            return self._send({"ok": False, "code": 9, "warning": "令牌缺失或错误"}, 401)
        tool = ROUTES.get(path)
        if not tool:
            return self._send({"ok": False, "code": 4, "warning": f"未知路径 {path}"}, 404)
        args = {}
        if "?" in self.path:
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split("?", 1)[1])
            args = {k: (v[0] if len(v) == 1 else v) for k, v in q.items()}
            for k in ("limit", "hwnd", "max_nodes"):
                if k in args:
                    try:
                        args[k] = int(args[k])
                    except Exception:
                        pass
        self._send(core.dispatch(tool, args, actor=self._actor()))

    def do_POST(self):
        if not self._host_ok():
            return self._send({"ok": False, "code": 5, "warning": "Host 头不被接受（防 DNS rebinding）"}, 403)
        path = self.path.split("?")[0]
        if not self._token_ok():
            return self._send({"ok": False, "code": 9, "warning": "令牌缺失或错误"}, 401)
        tool = ROUTES.get(path)
        if not tool:
            return self._send({"ok": False, "code": 4, "warning": f"未知路径 {path}"}, 404)
        self._send(core.dispatch(tool, self._body(), actor=self._actor()))


def serve(port: int = PORT):
    tok = ensure_token()
    srv = ThreadingHTTPServer((HOST, port), Handler)
    print(f"AgentCtl HTTP 已启动： http://{HOST}:{port}/v1/health", flush=True)
    print(f"  令牌文件： {TOKEN_FILE}", flush=True)
    print(f"  令牌预览： {tok[:8]}…（完整值在文件里）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，退出", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    serve()
