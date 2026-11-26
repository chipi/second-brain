import argparse
import json
import sys

from . import build as build_mod
from . import seed as seed_mod
from .fetch import Fetcher, fetch_all
from .store import BadInput, Index, NotFound


def _search(a):
    try:
        idx = Index(a.data)
        rows = idx.search(a.query, a.vertical, a.k)
    except (BadInput, NotFound) as e:
        print(f"sb: {e}", file=sys.stderr)
        return 2
    print(f"index {idx.version}: {len(rows)} results")
    for r in rows:
        print(f"{r['score']:8.3f}  {r['paper']}  [{r['section']}]  {r['title']} ({r['year']})")
        print("          " + r["snippet"].replace("\n", " "))
    return 0


def _serve(a):
    from .server import make_server
    srv = make_server(a.data, a.host, a.port, a.token_file)
    print(f"serving http://{a.host}:{srv.server_address[1]}/mcp (read-only{', bearer token' if a.token_file else ''})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="sb")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="write one manifest per seed entry into CONTENT/papers")
    s.add_argument("--content", required=True)

    s = sub.add_parser("fetch", help="fetch metadata and text for manifests without a cached text")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--only", action="append", help="an id to fetch (repeatable)")

    s = sub.add_parser("build", help="build the FTS5 index and point DATA/current at it")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--version", help="override the content commit used as the index's name")

    s = sub.add_parser("search", help="query the current index")
    s.add_argument("query")
    s.add_argument("--data", required=True)
    s.add_argument("--vertical")
    s.add_argument("-k", type=int, default=8)

    s = sub.add_parser("serve", help="the read-only MCP server at /mcp")
    s.add_argument("--data", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8790)
    s.add_argument("--token-file")

    a = p.parse_args(argv)
    if a.cmd == "seed":
        print(json.dumps(seed_mod.seed(a.content)))
        return 0
    if a.cmd == "fetch":
        stats = fetch_all(a.content, a.data, Fetcher(log=lambda m: print(m, flush=True)), a.limit, a.only)
        print(json.dumps(stats))
        return 1 if stats["failed"] else 0
    if a.cmd == "build":
        print(json.dumps(build_mod.build(a.content, a.data, a.version)))
        return 0
    if a.cmd == "search":
        return _search(a)
    return _serve(a)
