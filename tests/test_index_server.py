import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import support
from sb import build, store
from sb.server import make_server


class Library(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.content, cls.data = support.make_library("fx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.idx = store.Index(self.data)
        self.addCleanup(self.idx.close)


class Build(Library):
    def test_current_points_at_the_versioned_index(self):
        link = os.path.join(self.data, "current")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), "index-fx")
        self.assertEqual(self.idx.version, "index-fx")

    def test_a_second_build_of_the_same_version_gets_a_counter_and_current_moves(self):
        tmp, content, data = support.make_library("same")
        self.addCleanup(shutil.rmtree, tmp)
        st = build.build(content, data, "same")
        self.assertEqual(st["version"], "index-same-2")
        self.assertEqual(os.readlink(os.path.join(data, "current")), "index-same-2")
        other = store.Index(data)
        self.addCleanup(other.close)
        self.assertEqual(other.version, "index-same-2")

    def test_the_same_inputs_give_the_same_chunks(self):
        def dump(data):
            db = sqlite3.connect(os.path.join(data, "current", "library.sqlite"))
            try:
                return db.execute("SELECT paper, section, position, verticals, title, text FROM chunks ORDER BY rowid").fetchall()
            finally:
                db.close()
        tmp, content, data = support.make_library("a")
        self.addCleanup(shutil.rmtree, tmp)
        first = dump(data)
        build.build(content, data, "b")
        self.assertEqual(dump(data), first)

    def test_a_manifest_without_a_text_is_findable_by_title_only(self):
        r = self.idx.search("porkchop")
        self.assertEqual(r[0]["paper"], "doi:10.1000/fixture.2")
        self.assertEqual(self.idx.paper("doi:10.1000/fixture.2")["source_kind"], None)


class Search(Library):
    def test_a_known_chunk_ranks_first(self):
        r = self.idx.search("zebrafish module")
        self.assertEqual((r[0]["paper"], r[0]["section"]), ("arxiv:2001.00001", "2 Method"))
        self.assertIn("[zebrafish]", r[0]["snippet"])
        self.assertEqual(set(r[0]), {"paper", "title", "year", "section", "snippet", "score", "index_version"})
        self.assertEqual((r[0]["index_version"], r[0]["year"]), ("index-fx", 2020))
        self.assertGreater(r[0]["score"], 0)

    def test_scores_descend_and_k_limits(self):
        r = self.idx.search("robot exploration agent tests", k=2)
        self.assertLessEqual(len(r), 2)
        self.assertEqual([x["score"] for x in r], sorted((x["score"] for x in r), reverse=True))

    def test_vertical_filter_including_a_paper_in_two_verticals(self):
        self.assertEqual({r["paper"] for r in self.idx.search("agent", vertical="ai")}, {"arxiv:2002.00002"})
        self.assertEqual({r["paper"] for r in self.idx.search("agent", vertical="software")}, {"arxiv:2002.00002"})
        self.assertEqual(self.idx.search("agent", vertical="robotics"), [])
        self.assertEqual(self.idx.search("universal variable", vertical="space")[0]["paper"], "ntrs:20000000001")

    def test_query_punctuation_is_not_fts_syntax(self):
        self.assertTrue(self.idx.search('frontier" OR (* NEAR/2 C++ -x'))

    def test_bad_input_raises_not_crashes(self):
        for q, k, v in [("", 8, None), (None, 8, None), ("!!", 8, None), ("x", 0, None), ("x", 21, None), ("x", "a", None), ("x", True, None), ("x", 8, "a b%")]:
            with self.assertRaises(store.BadInput, msg=(q, k, v)):
                self.idx.search(q, v, k)


class Read(Library):
    def test_without_section_title_abstract_and_section_titles(self):
        r = self.idx.read("arxiv:2001.00001")
        self.assertEqual(r["title"], "Frontier Exploration with Learned Occupancy Maps")
        self.assertIn("learned map prior", r["abstract"])
        self.assertEqual(r["sections"], ["1 Introduction", "2 Method", "3 Results", "References"])
        self.assertEqual(r["index_version"], "index-fx")

    def test_abstract_only_paper_has_no_sections(self):
        r = self.idx.read("ntrs:20000000001")
        self.assertEqual((r["sections"], r["source_kind"]), ([], "abstract"))

    def test_with_section_exact_number_and_unique_substring(self):
        want = "The planner scores each frontier"
        for sec in ("2 Method", "2 method", "2", "Method"):
            self.assertTrue(self.idx.read("arxiv:2001.00001", sec)["text"].startswith(want), sec)
        r = self.idx.read("arxiv:2001.00001v2", "Results")
        self.assertEqual((r["section"], r["paper"], r["index_version"]), ("3 Results", "arxiv:2001.00001", "index-fx"))
        self.assertNotIn("cut at", r["text"])

    def test_unknown_or_ambiguous_section_and_paper(self):
        with self.assertRaises(store.NotFound):
            self.idx.read("arxiv:2001.00001", "Appendix Z")
        with self.assertRaisesRegex(store.NotFound, "ambiguous"):
            self.idx.read("arxiv:2001.00001", "r")
        with self.assertRaises(store.NotFound):
            self.idx.read("arxiv:0000.00000")
        with self.assertRaises(store.BadInput):
            self.idx.read("")

    def test_a_long_section_is_cut_with_a_visible_marker(self):
        tmp, content, data = support.make_library("long")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(data, "texts", "arxiv_2002.00002.json")
        with open(path) as f:
            doc = json.load(f)
        body = "\n\n".join(("paragraph %d about tools " % i) * 20 for i in range(40))
        doc["sections"].append({"title": "3 Appendix", "text": body})
        with open(path, "w") as f:
            json.dump(doc, f)
        build.build(content, data, "long")
        idx = store.Index(data)
        self.addCleanup(idx.close)
        r = idx.read("arxiv:2002.00002", "Appendix")
        marker = f"…[cut at 12000 of {len(body)} chars]"
        self.assertTrue(r["text"].endswith(marker))
        self.assertEqual(r["text"][:12000], body[:12000])
        self.assertGreater(len(body), 12000)
        self.assertGreater(idx.db.execute("SELECT count(*) FROM chunks WHERE paper='arxiv:2002.00002' AND position=3").fetchone()[0], 5)


class PaperTool(Library):
    def test_manifest_fields_and_links(self):
        p = self.idx.paper("arxiv:2002.00002")
        self.assertEqual((p["verticals"], p["approver"], p["authors"], p["year"]), (["ai", "software"], "seeded", ["Bo Sample"], 2021))
        self.assertEqual(p["links"]["arxiv"], "https://arxiv.org/abs/2002.00002v1")
        self.assertEqual(p["subtopics"], {"ai": "agents", "software": "repair"})
        self.assertEqual(self.idx.paper("ntrs:20000000001")["links"], {"ntrs": "https://ntrs.nasa.gov/citations/20000000001"})
        self.assertEqual(self.idx.paper("doi:10.1000/Fixture.2")["links"], {"doi": "https://doi.org/10.1000/fixture.2"})
        with self.assertRaises(store.NotFound):
            self.idx.paper("arxiv:nope")


class Rpc:
    def __init__(self, url, token=None):
        self.url, self.token, self.sid, self.n = url, token, None, 0

    def post(self, body, sid=True, raw=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if sid and self.sid:
            headers["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.url, data=raw if raw is not None else json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
                return r.status, r.headers, json.loads(data) if data else None
        except urllib.error.HTTPError as e:
            data = e.read()
            e.close()
            return e.code, e.headers, json.loads(data) if data.startswith(b"{") else None

    def call(self, method, params=None):
        self.n += 1
        return self.post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})


class Serve(Library):
    token = None

    def setUp(self):
        super().setUp()
        for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            os.environ.pop(k, None)
        os.environ["NO_PROXY"] = "127.0.0.1"
        tf = None
        if self.token:
            tf = os.path.join(tempfile.mkdtemp(), "tok")
            with open(tf, "w") as f:
                f.write(self.token + "\n")
        self.srv = make_server(self.data, "127.0.0.1", 0, tf)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.rpc = Rpc(f"http://127.0.0.1:{self.srv.server_address[1]}/mcp", self.token)

    def start(self):
        status, headers, body = self.rpc.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.rpc.sid = headers.get("Mcp-Session-Id")
        return status, headers, body


class Protocol(Serve):
    def test_initialize_gives_a_session_id_and_the_notification_is_202(self):
        status, headers, body = self.start()
        self.assertEqual(status, 200)
        self.assertTrue(self.rpc.sid)
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", body["result"]["capabilities"])
        self.assertEqual(body["id"], 1)
        st, _, none = self.rpc.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((st, none), (202, None))

    def test_an_older_protocol_version_is_echoed_and_an_unknown_one_gets_ours(self):
        _, _, b = self.rpc.call("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(b["result"]["protocolVersion"], "2025-03-26")
        _, _, b = self.rpc.call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(b["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list_names_and_schemas(self):
        self.start()
        _, _, b = self.rpc.call("tools/list")
        tools = {t["name"]: t for t in b["result"]["tools"]}
        self.assertEqual(set(tools), {"search", "read", "paper"})
        self.assertEqual(tools["search"]["inputSchema"]["required"], ["query"])
        self.assertEqual(set(tools["search"]["inputSchema"]["properties"]), {"query", "vertical", "k"})
        self.assertEqual(set(tools["read"]["inputSchema"]["properties"]), {"paper", "section"})
        self.assertEqual(set(tools["paper"]["inputSchema"]["properties"]), {"id"})

    def tool(self, name, args):
        _, _, b = self.rpc.call("tools/call", {"name": name, "arguments": args})
        return b

    def test_each_tool_through_tools_call(self):
        self.start()
        b = self.tool("search", {"query": "zebrafish", "k": 3})
        res = b["result"]
        self.assertFalse(res["isError"])
        payload = json.loads(res["content"][0]["text"])
        self.assertEqual(payload, res["structuredContent"])
        self.assertEqual(payload["results"][0]["paper"], "arxiv:2001.00001")
        b = self.tool("read", {"paper": "arxiv:2001.00001"})
        self.assertEqual(json.loads(b["result"]["content"][0]["text"])["sections"][1], "2 Method")
        b = self.tool("read", {"paper": "arxiv:2001.00001", "section": "Method"})
        self.assertIn("zebrafish", json.loads(b["result"]["content"][0]["text"])["text"])
        b = self.tool("paper", {"id": "ntrs:20000000001"})
        self.assertEqual(json.loads(b["result"]["content"][0]["text"])["links"]["ntrs"], "https://ntrs.nasa.gov/citations/20000000001")

    def test_not_found_is_an_error_result_and_bad_input_a_jsonrpc_error(self):
        self.start()
        b = self.tool("paper", {"id": "arxiv:nope"})
        self.assertTrue(b["result"]["isError"])
        for name, args in [("search", {}), ("search", {"query": "x", "k": 99}), ("read", {}), ("paper", {}), ("nosuch", {}), ("search", [1])]:
            b = self.tool(name, args)
            self.assertEqual(b["error"]["code"], -32602, (name, args))
        b = self.rpc.call("tools/call", {})[2]
        self.assertEqual(b["error"]["code"], -32602)

    def test_garbage_is_an_error_not_a_crash(self):
        self.start()
        st, _, b = self.rpc.post(None, raw=b"{not json")
        self.assertEqual((st, b["error"]["code"]), (400, -32700))
        st, _, b = self.rpc.post([1, 2, 3])
        self.assertEqual(st, 200)
        self.assertEqual({e["error"]["code"] for e in b}, {-32600})
        _, _, b = self.rpc.post({"jsonrpc": "2.0", "id": 5, "method": "nope"})
        self.assertEqual(b["error"]["code"], -32601)
        _, _, b = self.rpc.post({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        self.assertEqual(b["result"], {})
        self.assertEqual(self.rpc.post({"x": 1})[2]["error"]["code"], -32600)
        self.assertEqual(self.tool("search", {"query": "still alive"})["result"]["isError"], False)

    def test_an_unknown_session_gets_404_so_the_client_starts_again(self):
        self.start()
        self.rpc.sid = "dead"
        self.assertEqual(self.rpc.call("tools/list")[0], 404)

    def test_wrong_path_and_get(self):
        base = self.rpc.url
        for url, code in [(base[:-4] + "/other", 404), (base, 405)]:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(cm.exception.code, code)
            cm.exception.close()

    def test_a_missing_index_is_an_internal_error(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        srv = make_server(empty, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        rpc = Rpc(f"http://127.0.0.1:{srv.server_address[1]}/mcp")
        b = rpc.call("tools/call", {"name": "search", "arguments": {"query": "x"}})[2]
        self.assertEqual(b["error"]["code"], -32603)


class Auth(Serve):
    token = "s3cret-token"

    def test_401_without_or_with_a_wrong_token_and_200_with_it(self):
        good = self.rpc.token
        for bad in (None, "wrong"):
            self.rpc.token = bad
            st, headers, _ = self.rpc.call("initialize", {"protocolVersion": "2025-06-18"})
            self.assertEqual(st, 401)
            self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.rpc.token = good
        self.assertEqual(self.start()[0], 200)
        self.assertEqual(self.rpc.call("tools/list")[0], 200)


class ReadOnly(Library):
    def test_the_index_refuses_a_write(self):
        with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
            self.idx.db.execute("DELETE FROM papers")
        with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
            self.idx.db.execute("INSERT INTO chunks (paper, section, position, verticals, title, text) VALUES ('x','x',0,'','','x')")
        self.assertEqual(self.idx.db.execute("SELECT count(*) FROM papers").fetchone()[0], 4)


@unittest.skipUnless(os.environ.get("SB_FLOTILLA_RUNTIME"), "set SB_FLOTILLA_RUNTIME to flotilla's runtime/ directory to run the client interop test")
class FlotillaClient(Serve):
    def test_flotillas_mcp_client_reads_the_library(self):
        import sys
        sys.path.insert(0, os.environ["SB_FLOTILLA_RUNTIME"])
        import mcp_client
        c = mcp_client.McpClient(self.rpc.url)
        self.assertEqual({t["name"] for t in c.list_tools()}, {"search", "read", "paper"})
        out = json.loads(mcp_client.text(c.call_tool("search", {"query": "zebrafish"})))
        self.assertEqual(out["results"][0]["paper"], "arxiv:2001.00001")


if __name__ == "__main__":
    unittest.main()
