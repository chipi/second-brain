import argparse
import glob
import json
import logging
import os
import sys

from . import build as build_mod
from . import obs, report
from . import seed as seed_mod
from .fetch import Fetcher, fetch_all
from .fulltext import fulltext_all
from .pdftext import PdfUnavailable
from .store import BadInput, Index, NotFound

LOG = logging.getLogger("sb.cli")


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
    obs.log(LOG, logging.INFO, f"serving http://{a.host}:{srv.server_address[1]}/mcp (read-only{', bearer token' if a.token_file else ''})",
            host=a.host, port=srv.server_address[1], auth=bool(a.token_file), log_format=a.log_format)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _progress(a):
    """Where a long command's per-paper lines go: stdout as they are, or one JSON log line each."""
    if a.log_format == "json":
        return lambda m: obs.log(LOG, logging.INFO, m)
    return lambda m: print(m, flush=True)


def _result(a, stats):
    """The command's final stats: the JSON object on stdout, or a JSON log line carrying them."""
    if a.log_format == "json":
        obs.log(LOG, logging.INFO, f"{a.cmd} done", **stats)
    else:
        print(json.dumps(stats))


def _text_kinds(data):
    kinds = {}
    for p in glob.glob(os.path.join(data, "texts", "*.json")):
        with open(p, encoding="utf-8") as f:
            k = json.load(f).get("source_kind") or "unknown"
        kinds[k] = kinds.get(k, 0) + 1
    return kinds


def _metrics_file(a, families):
    if a.metrics_file:
        obs.write_metrics_file(a.metrics_file, families)


def _failed_exit(stats, max_share, step):
    """A failed fetch is retried next run, so a few fail the step only above a share: a first build can then still serve."""
    failed, wanted = stats.get("failed", 0), stats.get("wanted", 0)
    if not failed:
        return 0
    if wanted and failed / wanted <= max_share:
        print(f"sb: {step}: {failed} of {wanted} failed, within {max_share:.0%}: retried next run", file=sys.stderr)
        return 0
    print(f"sb: {step}: {failed} of {wanted} failed, over {max_share:.0%}", file=sys.stderr)
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="sb")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-format", choices=("text", "json"), help="text (default; json under serve): the log lines on stdout")
    mfile = argparse.ArgumentParser(add_help=False)
    mfile.add_argument("--metrics-file", help="write the run's outcome here as a Prometheus text file")
    share = argparse.ArgumentParser(add_help=False)
    share.add_argument("--max-failed-share", type=float, default=0.05,
                       help="fail the step (exit 1) only when more than this share of the papers wanted failed (default 0.05; 0: any)")

    s = sub.add_parser("seed", help="write one manifest per seed entry into CONTENT/papers")
    s.add_argument("--content", required=True)

    s = sub.add_parser("fetch", parents=[common, mfile, share], help="fetch metadata and text for manifests without a cached text")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--only", action="append", help="an id to fetch (repeatable)")

    s = sub.add_parser("fulltext", parents=[common, mfile, share], help="upgrade abstract-only texts with an open HTML or PDF full text, recording what was tried")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--only", action="append", help="an id to upgrade (repeatable)")
    s.add_argument("--retry", action="store_true", help="also retry papers whose last outcome was final (paywalled, scanned, ...)")

    s = sub.add_parser("status", help="what the library holds, why papers are abstract only, and whether the index is stale")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("build", parents=[common, mfile], help="build the FTS5 index and point DATA/current at it")
    s.add_argument("--content", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--version", help="override the content commit used as the index's name")

    s = sub.add_parser("search", help="query the current index")
    s.add_argument("query")
    s.add_argument("--data", required=True)
    s.add_argument("--vertical")
    s.add_argument("-k", type=int, default=8)

    s = sub.add_parser("serve", parents=[common], help="the read-only MCP server at /mcp, with /healthz and /metrics")
    s.add_argument("--data", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8790)
    s.add_argument("--token-file")

    a = p.parse_args(argv)
    if hasattr(a, "log_format"):
        a.log_format = a.log_format or ("json" if a.cmd == "serve" else "text")
        obs.configure_logging(a.log_format)
    if a.cmd == "seed":
        print(json.dumps(seed_mod.seed(a.content)))
        return 0
    if a.cmd == "fetch":
        stats = fetch_all(a.content, a.data, Fetcher(log=_progress(a)), a.limit, a.only)
        _result(a, stats)
        _metrics_file(a, obs.fetch_families(stats))
        return _failed_exit(stats, a.max_failed_share, a.cmd)
    if a.cmd == "fulltext":
        try:
            stats = fulltext_all(a.content, a.data, Fetcher(log=_progress(a)), a.limit, a.only, a.retry)
        except PdfUnavailable as e:
            print(f"sb: {e}", file=sys.stderr)
            return 2
        _result(a, stats)
        _metrics_file(a, obs.fulltext_families(stats, _text_kinds(a.data)))
        return _failed_exit(stats, a.max_failed_share, a.cmd)
    if a.cmd == "status":
        st = report.status(a.content, a.data)
        print(json.dumps(st, indent=1) if a.json else report.render(st))
        return 0
    if a.cmd == "build":
        stats = build_mod.build(a.content, a.data, a.version)
        _result(a, stats)
        _metrics_file(a, obs.build_families(stats))
        return 0
    if a.cmd == "search":
        return _search(a)
    return _serve(a)
