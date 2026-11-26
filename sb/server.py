"""The read-only MCP server: streamable HTTP at /mcp, JSON answers, three tools over the index."""
import hmac
import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, obs
from .store import MAX_K, BadInput, Index, NotFound

VERSIONS = ("2025-06-18", "2025-03-26")
MAX_REQUEST = 1 << 20
LOG = logging.getLogger("sb.server")
UNAUTHENTICATED = ("/healthz", "/metrics")

TOOLS = [
    {"name": "search", "description": "Full-text (BM25) search over the library's paper chunks. Returns the best chunks with paper id, title, year, section, snippet, score and index version.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to search for"},
         "vertical": {"type": "string", "description": "limit to one vertical"},
         "k": {"type": "integer", "minimum": 1, "maximum": MAX_K, "default": 8}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "read", "description": "Without section: a paper's title, abstract and section titles. With section: that section's text (capped at 12000 chars).",
     "inputSchema": {"type": "object", "properties": {
         "paper": {"type": "string", "description": "paper id such as arxiv:2001.00001"},
         "section": {"type": "string", "description": "a section title from the section list"}}, "required": ["paper"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "paper", "description": "A paper's manifest: title, authors, year, verticals, why it is in, who approved it, and links.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "paper id such as arxiv:2001.00001"}}, "required": ["id"]},
     "annotations": {"readOnlyHint": True}},
]


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _result(payload, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload, "isError": is_error}


TOOL_NAMES = {t["name"] for t in TOOLS}
RPC_STATUS = {-32602: "bad_request", -32600: "bad_request", -32700: "bad_request", -32601: "not_found", -32603: "error"}


def _tool_label(method, params):
    """A bounded label for the metrics: a tool's name, a known method, or `unknown` / `other`."""
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        return name if name in TOOL_NAMES else "unknown"
    return method if method in ("initialize", "ping", "tools/list") else "other"


def call_tool(data, params, info=None):
    info = {} if info is None else info
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise RpcError(-32602, "tools/call needs a tool name")
    name, args = params["name"], params.get("arguments") or {}
    if name not in TOOL_NAMES:
        raise RpcError(-32602, f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise RpcError(-32602, "arguments must be an object")
    try:
        idx = Index(data)
    except NotFound as e:
        info["error_type"] = "NotFound"
        raise RpcError(-32603, str(e)) from None
    info["index_version"] = idx.version
    try:
        if name == "search":
            if "query" not in args:
                raise BadInput("query is required")
            info["query"] = obs.clip_query(args["query"])
            k = args.get("k", 8)
            rows = idx.search(args["query"], args.get("vertical"), k)
            info["results"] = len(rows)
            return _result({"results": rows})
        if name == "read":
            if "paper" not in args:
                raise BadInput("paper is required")
            return _result(idx.read(args["paper"], args.get("section")))
        if "id" not in args:
            raise BadInput("id is required")
        return _result(idx.paper(args["id"]))
    except BadInput as e:
        raise RpcError(-32602, str(e)) from None
    except NotFound as e:
        info["status"] = "tool_error"
        return _result({"error": str(e)}, is_error=True)
    finally:
        idx.close()


def handle(data, sessions, msg, info=None):
    """(response dict or None, new session id or None) for one JSON-RPC message.

    `info`, when given, is filled with what the request log and the metrics need: tool, status, results, index_version, error.
    """
    info = {} if info is None else info
    info.update(tool="other", status="ok")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        info["status"] = "bad_request"
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32600, "message": "not a JSON-RPC 2.0 request"}}, None
    method, rid = msg["method"], msg.get("id")
    info["tool"] = _tool_label(method, msg.get("params"))
    if "id" not in msg:
        info["tool"] = "notification"
        return None, None
    sid = None
    try:
        if method == "initialize":
            want = (msg.get("params") or {}).get("protocolVersion") if isinstance(msg.get("params"), dict) else None
            sid = uuid.uuid4().hex
            sessions.add(sid)
            res = {"protocolVersion": want if want in VERSIONS else VERSIONS[0], "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "second-brain", "version": __version__}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            res = call_tool(data, msg.get("params"), info)
        else:
            raise RpcError(-32601, f"no such method {method!r}")
    except RpcError as e:
        info["status"] = RPC_STATUS.get(e.code, "error")
        if info["status"] == "error":
            info.setdefault("error_type", "RpcError")
            info["error"] = e.message
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}}, None
    except Exception as e:
        info.update(status="error", error_type=type(e).__name__, error=str(e)[:300], traceback=obs.short_traceback(e))
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "internal error"}}, None
    return {"jsonrpc": "2.0", "id": rid, "result": res}, sid


def make_server(data, host="127.0.0.1", port=8790, token_file=None):
    sessions = set()
    lock = threading.Lock()
    metrics = obs.Metrics()

    def token():
        if not token_file:
            return None
        with open(token_file) as f:
            t = f.read().strip()
        if not t:
            raise RuntimeError("the token file is empty")
        return t

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        _code = 0

        def log_message(self, *a):
            pass

        def _observe(self, tool, status, started, info=None):
            """Count one request and write its log line. Never logs a header, so never the bearer token."""
            secs = time.perf_counter() - started
            metrics.observe(self.command, tool, status, secs)
            info = info or {}
            fields = {"method": self.command, "tool": tool, "status": status, "http_status": self._code, "duration_ms": round(secs * 1000, 2)}
            for k in ("query", "results", "index_version", "error_type", "error", "traceback"):
                if info.get(k) is not None:
                    fields[k] = info[k]
            tid = obs.trace_id(self.headers.get("traceparent"))
            if tid:
                fields["trace_id"] = tid
            level = logging.ERROR if status == "error" else logging.INFO if status in ("ok", "tool_error") else logging.WARNING
            obs.log(LOG, level, f"{self.command} {tool} {status}" + (f" ({info['error_type']})" if status == "error" and info.get("error_type") else ""), **fields)

        def _send(self, code, body=b"", ctype=None, headers=()):
            self._code = code
            self.send_response(code)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _refuse(self, code, status, started, headers=()):
            """Log and count first, then answer: once the client has its answer, the line and the counter are there."""
            self._code = code
            self._observe("none", status, started)
            self._send(code, b"", None, headers)

        def _json(self, code, obj, headers=()):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json", headers)

        def _authorised(self):
            try:
                want = token()
            except (OSError, RuntimeError):
                return False
            if want is None:
                return True
            got = self.headers.get("Authorization") or ""
            return hmac.compare_digest(got.encode(), ("Bearer " + want).encode())

        def _gate(self, started):
            if self.path.split("?")[0] != "/mcp":
                self._refuse(404, "not_found", started)
                return False
            if not self._authorised():
                self._refuse(401, "unauthorized", started, [("WWW-Authenticate", "Bearer")])
                return False
            return True

        def _guarded(self, started, fn):
            """Run a handler; an exception outside the JSON-RPC layer is a logged 500, not a dropped connection."""
            try:
                fn()
            except Exception as e:
                self._code = 500
                self._observe("none", "error", started, {"error_type": type(e).__name__, "error": str(e)[:300], "traceback": obs.short_traceback(e)})
                try:
                    self._send(500)
                except OSError:
                    pass

        def do_POST(self):
            started = time.perf_counter()
            self._guarded(started, lambda: self._post(started))

        def _post(self, started):
            if not self._gate(started):
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._refuse(400, "bad_request", started)
            if n > MAX_REQUEST:
                return self._refuse(413, "bad_request", started)
            raw = self.rfile.read(n)
            try:
                msg = json.loads(raw)
            except ValueError:
                self._code = 400
                self._observe("none", "bad_request", started)
                return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            sid = self.headers.get("Mcp-Session-Id")
            is_init = isinstance(msg, dict) and msg.get("method") == "initialize"
            with lock:
                known = sid in sessions
            if sid and not known and not is_init:
                return self._refuse(404, "not_found", started)
            outs, new_sid = [], None
            for m in msg if isinstance(msg, list) else [msg]:
                t0, info = time.perf_counter(), {}
                resp, s = handle(data, sessions, m, info)
                new_sid = new_sid or s
                if resp is not None:
                    outs.append(resp)
                self._code = 200 if resp is not None else 202
                self._observe(info["tool"], info["status"], t0, info)
            if isinstance(msg, list):
                return self._json(200, outs) if outs else self._send(202)
            if not outs:
                return self._send(202)
            self._json(200, outs[0], [("Mcp-Session-Id", new_sid)] if new_sid else [])

        def _healthz(self):
            try:
                idx = Index(data)
                try:
                    st = idx.stats()
                finally:
                    idx.close()
            except Exception as e:
                return self._json(503, {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
            self._json(200, {"ok": True, "index_version": st["version"], "papers": st["papers"]})

        def _metrics(self):
            try:
                idx = Index(data)
                try:
                    st = idx.stats()
                finally:
                    idx.close()
            except Exception:
                st = None
            self._send(200, metrics.render(st).encode(), "text/plain; version=0.0.4; charset=utf-8")

        def do_GET(self):
            started = time.perf_counter()
            path = self.path.split("?")[0]
            if path in UNAUTHENTICATED:
                return self._guarded(started, self._healthz if path == "/healthz" else self._metrics)
            if self._gate(started):
                self._refuse(405, "bad_request", started, [("Allow", "POST, DELETE")])

        def do_DELETE(self):
            started = time.perf_counter()
            if not self._gate(started):
                return
            with lock:
                sessions.discard(self.headers.get("Mcp-Session-Id"))
            self._code = 204
            self._observe("none", "ok", started)
            self._send(204)

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    srv.metrics = metrics
    return srv
