# -*- coding: utf-8 -*-
r"""mcp_http.py —— MCP 的**第二种传输**：Streamable HTTP（POST /mcp）

为什么要有它（2026-10-20 实测出来的死因）：
  stdio 传输的命门是 **stdin 由父进程（宿主）持有**。父进程一关管道，server 收到
  EOF 就必须退出（MCP 规范也这么要求），而宿主**不重连** ⇒ 工具条目还留在列表里，
  一调就是 ``Transport closed``。本机日志当场给出这个死因：
      [17:38:34] tools/call ctl_quiz -> ok=True code=0
      [17:38:34] stdin 结束（EOF / 父进程断开）⇒ server 退出
  ⇒ 心跳/重试是错层（stdin EOF 之后没法 reopen，往里写只会 OSError），
    **重连永远是宿主的事，而我们改不了宿主** ⇒ 只能换一个不靠宿主连接的传输。

★ **传输层不是授权层**：HTTP 入口照样走 core.dispatch()，
  通行证（A/B/C）、单写者租约、三条急停、动作额度，**一道不少**。
  「谁都能连上这个端口」≠「谁都能动手」——动手还是那套闸门。

★ 协议逻辑**不复制**：直接复用 mcp.py 的 handle()（输出目标与 actor 由本文件注入）。

用法：
    python -X utf8 src/agentctl/mcp_http.py            # 起服务（默认 127.0.0.1:8761）
    curl -s http://127.0.0.1:8761/mcp -H 'Authorization: Bearer <token>' \
         -H 'Content-Type: application/json' \
         -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)                    # src\agentctl 的上一级 = src
for _p in (_HERE, _SRC):                         # ★ 两个都插：直接 `python src\agentctl\mcp_http.py`
    if _p not in sys.path:                       #   时 sys.path[0] 是包目录本身，
        sys.path.insert(0, _p)                   #   没有 src 就 import 不到 agentctl
from agentctl import core, mcp                                       # noqa: E402
from agentctl import server as _server                              # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENTCTL_MCP_PORT", "8761"))

# ★★ 2026-10-21 实测踩的坑：pythonw.exe 下 **sys.stderr 是 None**。
#   启动器 run_mcp_http.cmd 用的是 pythonw（不要弹控制台窗口）⇒
#   BaseHTTPRequestHandler.send_response() → log_request() → log_message()
#   → sys.stderr.write(...) → **AttributeError** ⇒ 响应头一个字节都没写出去 ⇒
#   客户端只看到「Empty reply from server」（curl 52）/「error sending request」。
#   实测证据：mcp_server.log 里只有「HTTP 响应形态=JSON」这一行（写在写响应之前），
#   后面本该有的「HTTP 响应 status=200」永远不出现 ⇒ 死在写响应这一行。
#   修法：先把 stderr 兜住（devnull），再让 log_message 本身也做 None 检查。
if sys.stderr is None:                       # pythonw.exe / PyInstaller(console=False)
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
STATE_DIR = os.environ.get("AGENTCTL_STATE", os.path.join(core.ROOT, "work", "state"))
TOKEN_FILE = os.path.join(STATE_DIR, "token")
TOKEN_ENV = "AGENTCTL_MCP_TOKEN"
SESSION_ID = "agentctl-mcp-1"        # 常量 session id（DSH 的判据：客户端要就给它）
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}", f"[{HOST}]:{PORT}"}
DEBUG = bool(os.environ.get("AGENTCTL_MCP_DEBUG"))   # 请求头转储开关（调试用）


def ensure_token() -> str:
    """令牌：**环境变量优先**，没有才用 state\token（与 HTTP 服务同一个文件）。

    ★ 为什么环境变量优先：`codex mcp add --bearer-token-env-var AGENTCTL_MCP_TOKEN`
      要求 Codex 进程里带着这个变量；两边得是同一个值，所以启动脚本会先把
      state\token 的内容 set 进环境变量，两边自然一致。
    """
    env = (os.environ.get(TOKEN_ENV) or "").strip()
    if env:
        return env
    return _server.ensure_token()


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "AgentCtlMCP/0.1"
    protocol_version = "HTTP/1.1"

    # ── 基础 ──
    def _json(self, obj, http: int = 200, headers: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(http)
        # ★ Content-Type 必须**精确**是 "application/json"（不带 charset 参数）。
        #   rmcp（Codex 的 MCP 客户端）按 content-type 做**字符串相等**判断，
        #   "application/json; charset=utf-8" 对不上 ⇒ 它报
        #   "error sending request ... when send initialize request"（看着像网络错，其实是响应头对不上）。
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", SESSION_ID)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        mcp._log("HTTP 响应 status=%d ct=application/json len=%d" % (http, len(body)))

    def _sse(self, obj):
        """SSE 形态的响应（Streamable HTTP 规范允许 application/json 或 text/event-stream）。

        ★ 什么时候用 SSE：客户端的 Accept 里带 text/event-stream（MCP 规范要求客户端都带上）
          ⇒ 回 SSE 更符合规范，也省得客户端再判断 content-type。
        ★ 用 chunked 且**不写结束块** ⇒ 连接保持打开，客户端可以继续等后续消息
          （关掉连接的话，客户端会以为流结束了）。
        """
        data = ("event: message\ndata: "
                + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Mcp-Session-Id", SESSION_ID)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
            # ★ 必须写结束块：客户端（reqwest/rmcp）会把响应体**整个读完**，
            #   chunked 一直不结束 ⇒ 它就一直等 ⇒ 最后报
            #   "error sending request ... when send initialize request"（看着像网络错，其实在等流结束）。
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        mcp._log("HTTP 响应 status=200 ct=text/event-stream len=%d" % len(data))

    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").strip().lower()
        return h in {x.lower() for x in ALLOWED_HOSTS}

    def _token_ok(self) -> bool:
        want = ensure_token()
        got = ""
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
        if not got:
            got = (self.headers.get("X-AgentCtl-Token") or "").strip()
        ok = bool(got) and secrets.compare_digest(got, want)
        # ★ 请求级日志（2026-10-20 加）：光记 tools/call 看不出「连了没连上」。
        #   之前只看到端口上一堆 TimeWait，日志里却一条 HTTP 记录都没有 ⇒
        #   分不清「没连」还是「连了被 401 拒了」。现在每次 POST 都留一行：
        #   有没有带令牌、过了没有、是谁（actor）。
        mcp._log("HTTP POST 令牌=%s 鉴权=%s actor=%s ua=%s"
                 % ("有" if got else "无", "过" if ok else "拒",
                    self._actor(), (self.headers.get("User-Agent") or "")[:40]))
        return ok

    def _actor(self) -> str:
        return (self.headers.get("X-AgentCtl-Actor") or "").strip() or "mcp-http"

    def log_message(self, fmt, *a):        # 诊断走 stderr
        # ★ pythonw.exe 下 sys.stderr 可能是 None ⇒ 这里再兜一次，
        #   否则 log_request() 一调就把整个响应打飞（见文件头注释）。
        try:
            if sys.stderr is not None:
                sys.stderr.write("[mcp-http] " + (fmt % a) + "\n")
        except Exception:
            pass

    def _read_body(self) -> bytes:
        """读请求体：Content-Length **或** chunked 两种都要支持。

        ★ 为什么加 chunked：reqwest（rmcp 底下用的）有时会用
          `Transfer-Encoding: chunked` 发请求体；只认 Content-Length 就会读到 0 字节
          ⇒ 以为请求是空的 ⇒ 回 400。
        """
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except Exception:
                    break
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _dump_headers(self):
        if not DEBUG:
            return
        h = {k: v for k, v in self.headers.items()}
        # ★ Authorization 必须脱敏：原样转储会把 **Bearer token 明文写进日志**
        #   （2026-10-21 踩过：调试模式转储请求头 ⇒ token 明文进了 mcp_server.log）。
        #   日志是明文文件、还会被贴给别人看 ⇒ 这里只留前 8 位。
        for k in ("authorization", "x-agentctl-token"):
            if h.get(k):
                h[k] = h[k][:12] + "…（已脱敏）"
        mcp._log("请求头：%s" % json.dumps(h, ensure_ascii=False)[:600])

    # ── 路由 ──
    def do_GET(self):
        if not self._host_ok():
            return self._json({"ok": False, "code": 5,
                                "warning": "Host 头不被接受（防 DNS rebinding）"}, 403)
        path = self.path.split("?")[0]
        if path == "/v1/health":
            return self._json({"ok": True, "code": 0,
                                "data": {"service": "AgentCtlMCP", "transport": "streamable-http",
                                         "port": PORT, "alive": True}})
        if path == "/mcp":
            # GET 用于开 SSE 流；本服务不做服务端主动消息 ⇒ 明说 405，别装糊涂
            return self._json({"ok": False, "code": 4,
                                "warning": "本服务不提供服务端主动消息；请用 POST /mcp"},
                               405, {"Allow": "POST"})
        return self._json({"ok": False, "code": 4, "warning": f"未知路径 {path}"}, 404)

    def do_POST(self):
        self._dump_headers()
        if not self._host_ok():
            return self._json({"ok": False, "code": 5,
                                "warning": "Host 头不被接受（防 DNS rebinding）"}, 403)
        path = self.path.split("?")[0]
        if path != "/mcp":
            return self._json({"ok": False, "code": 4, "warning": f"未知路径 {path}"}, 404)
        if not self._token_ok():
            return self._json({"ok": False, "code": 9, "warning": "令牌缺失或错误"}, 401)
        raw = self._read_body()
        if not raw:
            return self._json({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": "empty body"}}, 400)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self._json({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": f"Parse error: {e}"}}, 400)
        msgs = payload if isinstance(payload, list) else [payload]
        out: list = []
        actor = self._actor()
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            try:
                mcp.handle(msg, out=out.append, actor=actor)
            except Exception as e:
                mcp._log("HTTP 处理异常 %s: %s" % (type(e).__name__, e))
                if msg.get("id") is not None:
                    out.append({"jsonrpc": "2.0", "id": msg.get("id"),
                                "error": {"code": -32603, "message": f"Internal error: {e}"}})
        if not out:
            # 通知（无 id）没有响应体 ⇒ 202
            self.send_response(202)
            self.send_header("Mcp-Session-Id", SESSION_ID)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = out if len(out) > 1 else out[0]
        accept = (self.headers.get("Accept") or "").lower()
        # ★ 两种都接受时优先 JSON：客户端 Accept 一般是 "application/json, text/event-stream"，
        #   这时回 JSON（Content-Length 明确、body 一次读完）最省事；
        #   只有客户端**只**要 text/event-stream 时才回 SSE。
        if "text/event-stream" in accept and "application/json" not in accept:
            mcp._log("HTTP 响应形态=SSE（客户端只接受 text/event-stream）")
            return self._sse(body)
        mcp._log("HTTP 响应形态=JSON（客户端 Accept=%s）" % accept[:60])
        return self._json(body)

    def do_DELETE(self):
        """删会话（rmcp 退出时会 DELETE /mcp）。

        ★ 不实现的话 rmcp 会打一条错：`fail to delete session: unexpected server
          response: DELETE returned HTTP 501`（无害，但日志噪音，也让人以为出了事）。
        """
        self._dump_headers()
        if not self._host_ok():
            return self._json({"ok": False, "code": 5,
                                "warning": "Host 头不被接受（防 DNS rebinding）"}, 403)
        mcp._log("HTTP DELETE 会话已释放（session=%s）"
                 % (self.headers.get("Mcp-Session-Id") or "?"))
        return self._json({"ok": True, "code": 0, "data": {"released": True}})


def _daemon_watchdog():
    """每 30s 确认急停监听还在；不在就拉起（组件活着 = 热键/弹窗必须活着）。

    ★ 为什么放在 mcp_http 而不是登录自启：用户要求「agent 使用组件时自动运行」，
      不占开机启动项；组件退出后它也跟着没人管（下次启动会再补）。
    """
    from agentctl import service as _svc
    while True:
        time.sleep(30)
        try:
            r = _svc.ensure_daemon()
            if r.get("started"):
                mcp._log("急停监听不在 ⇒ 已自动拉起 pid=%s" % r.get("pid"))
        except Exception as e:                                # noqa: BLE001
            try:
                mcp._log("急停监听看护异常：%r" % (e,))
            except Exception:
                pass


def serve(port: int = PORT):
    # ★ 幂等：端口已被占用 ⇒ 说明已有一个实例在跑（登录自启 + 手动启动会撞车）。
    #   直接退出，别抛栈、也别把已跑着的服务顶掉。
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        busy = s.connect_ex((HOST, port)) == 0
    if busy:
        print("端口 %d 已被占用 ⇒ 已有实例在跑，本次不重复启动" % port, flush=True)
        return
    tok = ensure_token()
    core.CHANNEL = "mcp"
    srv = ThreadingHTTPServer((HOST, port), MCPHandler)
    print("AgentCtl MCP(HTTP) 已启动： http://%s:%d/mcp" % (HOST, port), flush=True)
    print("  令牌文件： %s" % TOKEN_FILE, flush=True)
    print("  令牌预览： %s…（完整值在文件里）" % tok[:8], flush=True)
    mcp._log("HTTP server 启动 pid=%d port=%d transport=streamable-http" % (os.getpid(), port))
    try:
        from agentctl import ctrl_border
        ctrl_border.start_follow("mcp")
    except Exception as e:
        sys.stderr.write("[border] 接线异常（服务照常）：%r\n" % e)
    # ★ 2026-10-24：急停监听随组件启动 + 看护（用户要求：不占开机自启）
    try:
        from agentctl import service as _svc
        _svc.ensure_daemon()
        threading.Thread(target=_daemon_watchdog, name="daemon-watchdog", daemon=True).start()
        print("  急停监听：%s" % _svc.daemon_status_line(), flush=True)
    except Exception as e:
        sys.stderr.write("[daemon] 接线异常（服务照常）：%r\n" % e)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，退出", flush=True)
    finally:
        mcp._log("HTTP server 退出 pid=%d" % os.getpid())
        try:
            ctrl_border.stop_follow()
        except Exception as e:
            sys.stderr.write("[border] 收边框异常：%r\n" % e)
        srv.server_close()


if __name__ == "__main__":
    serve()
