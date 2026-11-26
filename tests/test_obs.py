import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request

import support
import test_index_server as base
from sb import cli, obs

SAMPLE = re.compile(r'^([a-zA-Z_:][\w:]*)(?:\{(.*)\})?\s+(\S+)$')
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def parse(text):
    """{(name, frozenset(labels)): value} from Prometheus text; fails on a line that is neither a comment nor a sample."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = SAMPLE.match(line)
        assert m, f"not an exposition line: {line!r}"
        labels = frozenset(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(2) or ""))
        out[(m.group(1), labels)] = float(m.group(3))
    return out


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        e.close()
        return e.code, e.headers, body


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []
        self.setFormatter(obs.JsonFormatter())

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


class Observed(base.Serve):
    def setUp(self):
        super().setUp()
        self.base = self.rpc.url[:-len("/mcp")]
        self.cap = LogCapture()
        logging.getLogger("sb.server").addHandler(self.cap)
        self.addCleanup(logging.getLogger("sb.server").removeHandler, self.cap)

    def metric(self, name, **labels):
        got = parse(get(self.base + "/metrics")[2])
        return got.get((name, frozenset(labels.items())), 0)

    def search(self, q="zebrafish", headers=None):
        self.start()
        return self.rpc.call("tools/call", {"name": "search", "arguments": {"query": q}})


class Metrics(Observed):
    def test_the_exposition_parses_and_counts_move_with_calls(self):
        st, headers, text = get(self.base + "/metrics")
        self.assertEqual(st, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain; version=0.0.4"))
        parse(text)
        before = self.metric("sb_requests_total", method="POST", tool="search", status="ok")
        self.search()
        self.search()
        self.assertEqual(self.metric("sb_requests_total", method="POST", tool="search", status="ok"), before + 2)
        self.assertEqual(self.metric("sb_request_duration_seconds_count", tool="search"), before + 2)

    def test_each_status_is_counted(self):
        self.start()
        self.rpc.call("tools/call", {"name": "read", "arguments": {"paper": "arxiv:nope"}})
        self.rpc.call("tools/call", {"name": "search", "arguments": {}})
        self.rpc.post(None, raw=b"{not json")
        self.rpc.sid = "dead"
        self.rpc.call("tools/list")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/other", timeout=5)
        cm.exception.close()
        for method, tool, status in [("POST", "read", "tool_error"), ("POST", "search", "bad_request"), ("POST", "none", "bad_request"),
                                     ("POST", "none", "not_found"), ("GET", "none", "not_found")]:
            self.assertGreaterEqual(self.metric("sb_requests_total", method=method, tool=tool, status=status), 1, (method, tool, status))

    def test_histogram_buckets_are_cumulative_and_end_at_the_count(self):
        for _ in range(3):
            self.search()
        got = parse(get(self.base + "/metrics")[2])
        buckets = sorted(((float(dict(k[1])["le"]), v) for k, v in got.items() if k[0] == "sb_request_duration_seconds_bucket" and dict(k[1])["tool"] == "search"))
        self.assertGreater(len(buckets), 5)
        vals = [v for _, v in buckets]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(buckets[-1][0], float("inf"))
        self.assertEqual(vals[-1], got[("sb_request_duration_seconds_count", frozenset({("tool", "search")}))])
        self.assertGreater(got[("sb_request_duration_seconds_sum", frozenset({("tool", "search")}))], 0)

    def test_index_gauges_come_from_the_current_index(self):
        got = parse(get(self.base + "/metrics")[2])
        self.assertEqual(got[("sb_index_papers", frozenset())], 4)
        self.assertGreater(got[("sb_index_chunks", frozenset())], 4)
        self.assertEqual(got[("sb_index_info", frozenset({("version", "index-fx")}))], 1)
        built = got[("sb_index_built_timestamp_seconds", frozenset())]
        self.assertAlmostEqual(built, __import__("time").time(), delta=3600)
        self.assertEqual(got[("sb_up", frozenset())], 1)
        self.assertGreater(got[("sb_start_time_seconds", frozenset())], 0)


class MetricsWithToken(Observed):
    token = "s3cret-token"

    def test_metrics_and_healthz_are_open_but_mcp_is_not(self):
        self.assertEqual(get(self.base + "/metrics")[0], 200)
        self.assertEqual(get(self.base + "/healthz")[0], 200)
        self.assertEqual(self.metric("sb_requests_total", method="POST", tool="none", status="unauthorized"), 0)
        self.rpc.token = None
        self.assertEqual(self.rpc.call("tools/list")[0], 401)
        self.assertEqual(self.metric("sb_requests_total", method="POST", tool="none", status="unauthorized"), 1)

    def test_no_log_line_holds_the_token(self):
        self.search()
        self.rpc.token = "wrong-" + self.token
        self.rpc.call("tools/list")
        self.assertTrue(self.cap.lines)
        self.assertNotIn(self.token, json.dumps(self.cap.lines))
        self.assertTrue(any(l["status"] == "unauthorized" and l["level"] == "warning" for l in self.cap.lines))


class Health(Observed):
    def test_ok_when_the_index_opens(self):
        st, _, body = get(self.base + "/healthz")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), {"ok": True, "index_version": "index-fx", "papers": 4})

    def test_503_when_it_does_not(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        from sb.server import make_server
        import threading
        srv = make_server(empty, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        st, _, body = get(url + "/healthz")
        self.assertEqual(st, 503)
        self.assertFalse(json.loads(body)["ok"])
        st, _, text = get(url + "/metrics")
        self.assertEqual(st, 200)
        self.assertNotIn("sb_index_papers", text)
        self.assertEqual(parse(text)[("sb_up", frozenset())], 1)


class Logs(Observed):
    def test_a_request_is_one_json_line_with_the_message_level_and_fields(self):
        self.search("x" * 500)
        line = [l for l in self.cap.lines if l.get("tool") == "search"][-1]
        for k in ("_msg", "level", "ts", "logger"):
            self.assertIn(k, line)
        self.assertEqual((line["level"], line["logger"], line["method"], line["status"], line["index_version"]), ("info", "sb.server", "POST", "ok", "index-fx"))
        self.assertRegex(line["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(line["results"], 0)
        self.assertEqual(len(line["query"]), 200)
        self.assertGreaterEqual(line["duration_ms"], 0)
        self.assertNotIn("trace_id", line)

    def test_traceparent_becomes_trace_id_and_a_bad_one_is_ignored(self):
        self.start()
        for tp in (f"00-{TRACE}-00f067aa0ba902b7-01", "garbage", f"00-{'0' * 32}-00f067aa0ba902b7-01"):
            req = urllib.request.Request(self.rpc.url, data=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "traceparent": tp, "Mcp-Session-Id": self.rpc.sid})
            urllib.request.urlopen(req, timeout=5).read()
        ids = [l.get("trace_id") for l in self.cap.lines if l.get("tool") == "ping"]
        self.assertEqual(ids, [TRACE, None, None])

    def test_an_exception_in_a_request_logs_at_error_with_type_and_traceback(self):
        from sb import server
        orig = server.Index.search
        server.Index.search = lambda *a, **k: 1 / 0
        self.addCleanup(setattr, server.Index, "search", orig)
        st, _, body = self.search()
        self.assertEqual(body["error"]["code"], -32603)
        line = [l for l in self.cap.lines if l["level"] == "error"][-1]
        self.assertEqual((line["error_type"], line["status"], line["tool"]), ("ZeroDivisionError", "error", "search"))
        self.assertIn("ZeroDivisionError", line["traceback"])
        self.assertLess(len(line["traceback"]), 1600)
        self.assertEqual(self.metric("sb_requests_total", method="POST", tool="search", status="error"), 1)


class LogFormats(unittest.TestCase):
    def test_configure_logging_writes_json_or_text_lines(self):
        for fmt in ("json", "text"):
            buf = io.StringIO()
            obs.configure_logging(fmt, buf)
            self.addCleanup(drop_handlers)
            obs.log(logging.getLogger("sb.x"), logging.WARNING, "hello", a=1)
            line = buf.getvalue().strip()
            if fmt == "json":
                self.assertEqual({k: json.loads(line)[k] for k in ("_msg", "level", "logger", "a")}, {"_msg": "hello", "level": "warning", "logger": "sb.x", "a": 1})
            else:
                self.assertRegex(line, r"warning\s+sb\.x: hello\s+a=1$")


def drop_handlers():
    for h in [h for h in obs.LOG.handlers if not isinstance(h, logging.NullHandler)]:
        obs.LOG.removeHandler(h)


class MetricsFile(unittest.TestCase):
    def setUp(self):
        self.addCleanup(drop_handlers)

    def run_cli(self, *args):
        self.assertEqual(cli.main(list(args)), 0)

    def test_build_writes_its_outcome(self):
        tmp, content, data = support.make_library("m")
        self.addCleanup(shutil.rmtree, tmp)
        out = os.path.join(tmp, "build.prom")
        self.run_cli("build", "--content", content, "--data", data, "--version", "m2", "--metrics-file", out, "--log-format", "json")
        with open(out) as f:
            got = parse(f.read())
        self.assertAlmostEqual(got[("sb_build_last_success_timestamp_seconds", frozenset())], __import__("time").time(), delta=60)
        self.assertEqual(got[("sb_build_papers", frozenset())], 4)
        self.assertGreater(got[("sb_build_chunks", frozenset())], 4)
        self.assertFalse([n for n in os.listdir(tmp) if ".tmp" in n])

    def test_fulltext_and_fetch_families(self):
        kinds = cli._text_kinds(os.path.join(support.FIXTURES, "data"))
        self.assertGreater(sum(kinds.values()), 0)
        text = obs.exposition(obs.fulltext_families({"outcomes": {"pdf": 2, "paywalled": 1}}, {"html": 3, "abstract": 5}) + obs.fetch_families({"failed": 2}))
        got = parse(text)
        self.assertEqual(got[("sb_fulltext_papers", frozenset({("kind", "html")}))], 3)
        self.assertEqual(got[("sb_fulltext_outcomes", frozenset({("outcome", "paywalled")}))], 1)
        self.assertEqual(got[("sb_fetch_failures", frozenset())], 2)

    def test_a_failed_build_leaves_no_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        out = os.path.join(tmp, "b.prom")
        with self.assertRaises(Exception):
            cli.main(["build", "--content", os.path.join(tmp, "missing"), "--data", os.path.join(tmp, "d"), "--metrics-file", out])
        self.assertFalse(os.path.exists(out))

    def test_push_script_checks_its_arguments(self):
        script = os.path.join(support.ROOT, "scripts", "push_metrics.sh")
        self.assertEqual(subprocess.run([script], capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run([script, "/nonexistent", "http://x"], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
