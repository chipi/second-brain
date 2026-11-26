"""The read-only MCP server: streamable HTTP at /mcp, JSON answers, three tools over the index."""
import hmac
import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .store import MAX_K, BadInput, Index, NotFound

VERSIONS = ("2025-06-18", "2025-03-26")
MAX_REQUEST = 1 << 20

TOOLS = [
    {"name": "search", "description": "Full-text (BM25) search over the library's paper chunks. Returns the best chunks with paper id, title, year, section, snippet, score and index version.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to search for"},
         "vertical": {"type": "string", "description": "limit to one vertical"},
         "k": {"type": "integer", "minimum": 1, "maximum": MAX_K, "default": 8}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "read", "description": "Without section: a paper's title, abstract and section titles. With section: that section's text (capped at 12000 chars).",
     "inputSchema": {"type": "object", "properties": {
         "paper": {"type": "string", "description": "paper id such as arxiv:2310.06770"},
         "section": {"type": "string", "description": "a section title from the section list"}}, "required": ["paper"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "paper", "description": "A paper's manifest: title, authors, year, verticals, why it is in, who approved it, and links.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "paper id such as arxiv:2310.06770"}}, "required": ["id"]},
     "annotations": {"readOnlyHint": True}},
]


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _result(payload, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload, "isError": is_error}


def call_tool(data, params):
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise RpcError(-32602, "tools/call needs a tool name")
    name, args = params["name"], params.get("arguments") or {}
    if name not in {t["name"] for t in TOOLS}:
        raise RpcError(-32602, f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise RpcError(-32602, "arguments must be an object")
    try:
        idx = Index(data)
    except NotFound as e:
        raise RpcError(-32603, str(e)) from None
    try:
        if name == "search":
            if "query" not in args:
                raise BadInput("query is required")
            k = args.get("k", 8)
            return _result({"results": idx.search(args["query"], args.get("vertical"), k)})
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
        return _result({"error": str(e)}, is_error=True)
    finally:
        idx.close()


def handle(data, sessions, msg):
    """(response dict or None, new session id or None) for one JSON-RPC message."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32600, "message": "not a JSON-RPC 2.0 request"}}, None
    method, rid = msg["method"], msg.get("id")
    if "id" not in msg:
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
            res = call_tool(data, msg.get("params"))
        else:
            raise RpcError(-32601, f"no such method {method!r}")
    except RpcError as e:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}}, None
    except Exception as e:
        print(f"internal error in {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "internal error"}}, None
    return {"jsonrpc": "2.0", "id": rid, "result": res}, sid


def make_server(data, host="127.0.0.1", port=8790, token_file=None):
    sessions = set()
    lock = threading.Lock()

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

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype=None, headers=()):
            self.send_response(code)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

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

        def _gate(self):
            if self.path.split("?")[0] != "/mcp":
                self._send(404)
                return False
            if not self._authorised():
                self._send(401, b"", None, [("WWW-Authenticate", "Bearer")])
                return False
            return True

        def do_POST(self):
            if not self._gate():
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400)
            if n > MAX_REQUEST:
                return self._send(413)
            raw = self.rfile.read(n)
            try:
                msg = json.loads(raw)
            except ValueError:
                return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            sid = self.headers.get("Mcp-Session-Id")
            is_init = isinstance(msg, dict) and msg.get("method") == "initialize"
            with lock:
                known = sid in sessions
            if sid and not known and not is_init:
                return self._send(404)
            if isinstance(msg, list):
                outs = [r for r, _ in (handle(data, sessions, m) for m in msg) if r is not None]
                return self._json(200, outs) if outs else self._send(202)
            resp, new_sid = handle(data, sessions, msg)
            if resp is None:
                return self._send(202)
            self._json(200, resp, [("Mcp-Session-Id", new_sid)] if new_sid else [])

        def do_GET(self):
            if self._gate():
                self._send(405, b"", None, [("Allow", "POST, DELETE")])

        def do_DELETE(self):
            if not self._gate():
                return
            with lock:
                sessions.discard(self.headers.get("Mcp-Session-Id"))
            self._send(204)

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    return srv
