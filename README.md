# second-brain

A personal research corpus you choose and own. It fetches the papers you picked, extracts their
text, indexes it, and serves it read-only over MCP, so an agent (or you, through any MCP client)
can search and cite it beside your other sources.

**Status:** MVP (2026-11-26). Seed, fetch (arXiv metadata and HTML, NTRS, Crossref, web pages), build (SQLite FTS5 over
section chunks), a local search and the read-only MCP server (`search`, `read`, `paper`) work, on the standard
library alone. Also: PDF full text (`fulltext`), a Dockerfile, health, metrics and JSON logs. Not yet: embeddings, the retrieval evaluation, the weekly search. The design
is [RFC-0001](docs/rfc/RFC-0001-the-library.md).

## What it is

- **Tooling, not content.** This repo is the code: fetchers (arXiv, NASA NTRS, DOIs, web pages), text
  extraction, chunking, the index, the read server and a CLI. Your content lives in a repo of your
  own (private, if you like): the verticals you follow, their search topics, and one manifest per
  paper. Full texts are a derived cache, fetched again from each paper's versioned id.
- **Chosen, not crawled.** A paper is in because someone decided it should be.
- **Read-only.** The server answers `search`, `read` and `paper`; nothing it serves can change the
  corpus.
- **Plain.** Python on the standard library where it can: SQLite's FTS5 for full-text search first;
  embeddings only when an evaluation shows full-text search misses.

## Usage

Python 3 with SQLite FTS5. The one dependency is `pypdf` (pinned in `requirements.txt`), imported only when a PDF is
read: `make venv` makes `.venv`, and `bin/sb` and `make test` use it when it exists, else `python3`. Scanned PDFs are
read by OCR with the system tools `pdftoppm` (poppler) and `tesseract`, if installed (nothing to pip-install).

```sh
sb seed   --content ../my-content                      # one papers/<safe-id>.json per entry of seed/*.jsonl
sb fetch  --content ../my-content --data ~/sb-data     # [--limit N] [--only ID ...]: metadata and text into DATA/texts
sb fulltext --content ../my-content --data ~/sb-data    # [--limit N] [--only ID ...] [--retry]: upgrade abstract-only texts to full text
sb status --content ../my-content --data ~/sb-data     # [--json]: what is held, why papers are abstract only, is the index stale
sb build  --content ../my-content --data ~/sb-data     # [--metrics-file F] [--log-format json]: DATA/index-<content commit>/library.sqlite, DATA/current -> it
sb search "lambert universal variable" --data ~/sb-data [--vertical space] [-k 8]
sb serve  --data ~/sb-data [--host 127.0.0.1] [--port 8790] [--token-file F] [--log-format text]   # MCP at /mcp, /healthz, /metrics
```

`fetch` spaces arXiv calls 3 seconds apart, backs off on HTTP 429, and records a failed paper in
`DATA/failures.json` without stopping. The server opens the index read-only, re-resolves `DATA/current` per
request (a new build is served without a restart) and, with `--token-file`, answers 401 without the bearer token.
`fulltext` is the second pass, run after `fetch`. For each cached text that is abstract only it looks for a legal open
full text, in order: arXiv HTML (any year), the arXiv PDF; the NTRS record's PDF; for a DOI the open-access PDF OpenAlex
lists, then Crossref's PDF links; for a web page (id `url:<absolute http(s) URL>`, scheme and host
lower-cased, fragment dropped) the page itself: HTML as sections, or a PDF by its `%PDF` bytes through the same PDF path, OCR
included; a page with under 500 characters of text ends `thin_page` (its metadata comes from the seed; hosts are called a
second apart). No identity or e-mail is sent. A PDF is capped at 40 MB, must start with `%PDF`, is
split into sections on its headings (pages when fewer than two are found) and gets `source_kind` `pdf`. A PDF with a
poor text layer (under 50 words a page, or mostly non-words) is read by OCR as well and the better text kept
(`source_kind` `ocr`, at most 60 pages). Each text records in `fulltext` what was tried and why it ended where it did;
a final outcome (paywalled, no PDF in NTRS, scanned, thin page, no open full text) is not tried again unless `--retry`, a fetch
error is. `status` reports the result per vertical.
Tests: `make test`; `make lint`.

## Running it

Two images from the one `Dockerfile` (base `python:3.13-slim`, pinned by digest; runs as uid 10001; `ENTRYPOINT python -m sb`;
nothing is written outside the `/data` volume and `/tmp`, so `--read-only` works):

| Target | For | Holds | Default command |
| --- | --- | --- | --- |
| `serve` (180 MB) | the read server, always on | python, pypdf | `serve --data /data --host 0.0.0.0` (port 8790; a `HEALTHCHECK` on `/healthz`) |
| `build` (354 MB) | the weekly `fetch`, `fulltext`, `build` | the same plus `poppler-utils` and `tesseract-ocr` (English) | `status --content /content --data /data` |

```sh
docker build --target serve -t sb:serve .   &&   docker build --target build -t sb:build .
docker run -d --read-only --tmpfs /tmp -v sb-data:/data:ro -p 8790:8790 sb:serve                  # [--token-file /run/secrets/sb-token]
docker run --rm --read-only --tmpfs /tmp -v sb-data:/data -v ../content:/content:ro -v /var/lib/sb-metrics:/out \
    sb:build build --content /content --data /data --metrics-file /out/build.prom                  # likewise fetch, fulltext
scripts/push_metrics.sh /var/lib/sb-metrics/build.prom http://victoriametrics:8428/api/v1/import/prometheus
```

There is no sb-specific environment variable. The server reads its flags (`--data`, `--host`, `--port`, `--token-file`,
`--log-format`); the image sets `PYTHONUNBUFFERED=1` so log lines are not held back. `fetch` and `fulltext` need network
access; the server needs none. The server mounts `/data` read-only (rule 2); the build container writes it.

**Endpoints** (besides `POST /mcp`): `GET /healthz` answers 200 `{"ok": true, "index_version": ..., "papers": n}` when the
current index opens, else 503 `{"ok": false, "error": ...}`. `GET /metrics` is Prometheus text. **Both are unauthenticated even
with `--token-file`**: they hold no library content (counts, a version, a build time) and are meant for the internal network;
do not publish the port. `/mcp` still needs the bearer token.

**Logs.** `--log-format json|text` on `serve`, `build`, `fetch` and `fulltext`; default `json` under `serve`, `text` for the
others (their output is then exactly what it always was). JSON is one line per event on stdout with `_msg`, `level`
(`info`, `warning`, `error`), `ts` (RFC 3339 UTC, milliseconds), `logger` and fields. A request logs `method`, `tool`, `status`
(below), `http_status`, `duration_ms`, and for `search` the `query` (cut at 200 characters) and `results`, plus `index_version`
and, when the request carried a W3C `traceparent` header, its `trace_id`. Headers are never logged, so never the bearer token.
An unexpected exception logs at `error` with `error_type`, `error` and a short `traceback`; 401s, bad requests and 404s log at
`warning`. `/healthz` and `/metrics` are not logged.

**Metrics** on `/metrics`:

| Metric | Type | Labels |
| --- | --- | --- |
| `sb_requests_total` | counter | `method`, `tool`, `status`: `ok`, `tool_error` (a tool answered `isError`), `bad_request`, `unauthorized`, `not_found`, `error` |
| `sb_request_duration_seconds` | histogram (5 ms to 10 s) | `tool` |
| `sb_index_papers`, `sb_index_chunks` | gauge | the index `current` points at, read per scrape |
| `sb_index_built_timestamp_seconds` | gauge | when that index was built |
| `sb_index_info` | gauge, always 1 | `version` |
| `sb_up`, `sb_start_time_seconds` | gauge | the process |

`tool` is a tool's name, `initialize`, `ping`, `tools/list`, `notification`, `unknown`, `other`, or `none` for a request that
never reached JSON-RPC (wrong path, 401, unparsable body, unknown session). The index gauges are left out when no index opens.

**Build metrics.** `build`, `fetch` and `fulltext` take `--metrics-file PATH` and, after a run that completed, write a Prometheus
text file (atomically): `build` gives `sb_build_last_success_timestamp_seconds`, `sb_build_papers`, `sb_build_chunks`; `fetch`
gives `sb_fetch_failures`; `fulltext` gives `sb_fulltext_papers{kind}` (every cached text by `source_kind`) and
`sb_fulltext_outcomes{outcome}` (what this run ended on). A run that raises writes nothing, so `last_success` goes stale and an
alert on its age fires. `scripts/push_metrics.sh FILE URL` sends the file with `curl --data-binary` (VictoriaMetrics:
`.../api/v1/import/prometheus`). A series has no timestamp in the file, so the push time stamps it.


## How it fits with other tools

It is one source among several. An agent platform such as
[flotilla](https://github.com/chipi/flotilla) reads it like any other MCP source, records each
read, makes an answer cite what it read, and can run the periodic search that proposes new papers
for a person to approve. second-brain stores and serves; deciding what joins it, and asking across
sources, belong to whoever uses it.

## Licence

[MIT](LICENSE). The papers a corpus holds keep their own licences; this repo holds none of them.
