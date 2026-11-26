"""Logs and metrics, standard library only: JSON-line or text logging, a Prometheus text exposition, a metrics file."""
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

LOG = logging.getLogger("sb")
LOG.addHandler(logging.NullHandler())

LEVELS = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "error"}
QUERY_CAP = 200
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def _ts(created):
    return datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(created * 1000) % 1000:03d}Z"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = dict(getattr(record, "fields", None) or {})
        out.update({"_msg": record.getMessage(), "level": LEVELS.get(record.levelno, "info"), "ts": _ts(record.created), "logger": record.name})
        return json.dumps(out, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record):
        fields = " ".join(f"{k}={v}" for k, v in (getattr(record, "fields", None) or {}).items() if k != "traceback")
        return f"{_ts(record.created)} {LEVELS.get(record.levelno, 'info'):7} {record.name}: {record.getMessage()}" + (f"  {fields}" if fields else "")


def configure_logging(fmt, stream=None):
    """Send the `sb` loggers to stdout, one line per event; `fmt` is "json" or "text"."""
    for h in [h for h in LOG.handlers if not isinstance(h, logging.NullHandler)]:
        LOG.removeHandler(h)
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    LOG.addHandler(h)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def log(logger, level, msg, **fields):
    logger.log(level, msg, extra={"fields": fields})


def short_traceback(exc, frames=4, cap=1500):
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__, limit=-frames)
    return "".join(tb)[-cap:]


def trace_id(traceparent):
    """The trace id of a W3C traceparent header, or None when absent or malformed."""
    m = TRACEPARENT.match((traceparent or "").strip().lower())
    return m.group(1) if m and set(m.group(1)) != {"0"} else None


def clip_query(q):
    return q[:QUERY_CAP] if isinstance(q, str) else None


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v):
    if isinstance(v, float):
        if math.isinf(v):
            return "+Inf" if v > 0 else "-Inf"
        return repr(v)
    return str(int(v))


def exposition(families):
    """Prometheus text format from [(name, type, help, [(sample name, labels dict, value)])]."""
    lines = []
    for name, typ, help_, samples in families:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {typ}")
        for sname, labels, value in samples:
            lab = "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "}" if labels else ""
            lines.append(f"{sname}{lab} {_num(value)}")
    return "\n".join(lines) + "\n"


class Metrics:
    """The server's request counters and latency histograms; every method is safe to call from any thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.requests = {}
        self.hist = {}

    def observe(self, method, tool, status, seconds):
        with self.lock:
            key = (method, tool, status)
            self.requests[key] = self.requests.get(key, 0) + 1
            h = self.hist.setdefault(tool, [[0] * len(BUCKETS), 0.0, 0])
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[0][i] += 1
                    break
            h[1] += seconds
            h[2] += 1

    def render(self, index=None):
        """`index` is Index.stats() or None when no index opens (the index gauges are then left out)."""
        with self.lock:
            reqs = sorted(self.requests.items())
            hist = {t: ([*h[0]], h[1], h[2]) for t, h in sorted(self.hist.items())}
        fams = [
            ("sb_up", "gauge", "1 while the server process is running", [("sb_up", {}, 1)]),
            ("sb_start_time_seconds", "gauge", "Unix time the server process started", [("sb_start_time_seconds", {}, self.started)]),
            ("sb_requests_total", "counter", "HTTP requests by method, tool and status", [
                ("sb_requests_total", {"method": m, "tool": t, "status": s}, n) for (m, t, s), n in reqs]),
        ]
        hs = []
        for tool, (counts, total, n) in hist.items():
            run = 0
            for b, c in zip(BUCKETS, counts):
                run += c
                hs.append(("sb_request_duration_seconds_bucket", {"tool": tool, "le": format(b, "g")}, run))
            hs += [("sb_request_duration_seconds_bucket", {"tool": tool, "le": "+Inf"}, n),
                   ("sb_request_duration_seconds_sum", {"tool": tool}, total), ("sb_request_duration_seconds_count", {"tool": tool}, n)]
        fams.append(("sb_request_duration_seconds", "histogram", "Request latency by tool", hs))
        if index:
            fams += [
                ("sb_index_papers", "gauge", "Papers in the current index", [("sb_index_papers", {}, index["papers"])]),
                ("sb_index_chunks", "gauge", "Chunks in the current index", [("sb_index_chunks", {}, index["chunks"])]),
                ("sb_index_info", "gauge", "The current index's version", [("sb_index_info", {"version": index["version"]}, 1)]),
            ]
            if index.get("built_at") is not None:
                fams.append(("sb_index_built_timestamp_seconds", "gauge", "Unix time the current index was built",
                             [("sb_index_built_timestamp_seconds", {}, index["built_at"])]))
        return exposition(fams)


def gauge(name, help_, value, **labels):
    return (name, "gauge", help_, [(name, labels, value)])


def labelled(name, help_, values, label):
    return (name, "gauge", help_, [(name, {label: k}, v) for k, v in sorted(values.items())])


def write_metrics_file(path, families):
    """Atomically write a Prometheus text file (for a host script to push); the directory must exist."""
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(exposition(families))
    os.replace(tmp, path)


def build_families(stats, now=None):
    return [
        gauge("sb_build_last_success_timestamp_seconds", "Unix time of the last successful build", now or time.time()),
        gauge("sb_build_papers", "Papers in the last build", stats["papers"]),
        gauge("sb_build_chunks", "Chunks in the last build", stats["chunks"]),
    ]


def fetch_families(stats):
    return [gauge("sb_fetch_failures", "Papers the last fetch run failed on", stats["failed"])]


def fulltext_families(stats, kinds):
    """`kinds` is {source_kind: papers} over the whole cache; the outcomes are this run's."""
    return [labelled("sb_fulltext_papers", "Cached papers by kind of text (html, pdf, ocr, abstract)", kinds, "kind"),
            labelled("sb_fulltext_outcomes", "Papers the last fulltext run ended on, by outcome", stats.get("outcomes") or {}, "outcome")]
