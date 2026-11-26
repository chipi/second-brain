# second-brain

A personal research corpus you choose and own. It fetches the papers you picked, extracts their
text, indexes it, and serves it read-only over MCP, so an agent (or you, through any MCP client)
can search and cite it beside your other sources.

**Status:** MVP (2026-11-26). Seed, fetch (arXiv metadata and HTML, NTRS, Crossref), build (SQLite FTS5 over
section chunks), a local search and the read-only MCP server (`search`, `read`, `paper`) work, on the standard
library alone. Not yet: PDF text, embeddings, the retrieval evaluation, deployment, the weekly search. The design
is [RFC-0001](docs/rfc/RFC-0001-the-library.md).

## What it is

- **Tooling, not content.** This repo is the code: fetchers (arXiv, NASA NTRS, DOIs), text
  extraction, chunking, the index, the read server and a CLI. Your content lives in a repo of your
  own (private, if you like): the verticals you follow, their search topics, and one manifest per
  paper. Full texts are a derived cache, fetched again from each paper's versioned id.
- **Chosen, not crawled.** A paper is in because someone decided it should be.
- **Read-only.** The server answers `search`, `read` and `paper`; nothing it serves can change the
  corpus.
- **Plain.** Python on the standard library where it can: SQLite's FTS5 for full-text search first;
  embeddings only when an evaluation shows full-text search misses.

## Usage

Python 3 with SQLite FTS5, no other dependency. `bin/sb` is `python3 -m sb` from this checkout.

```sh
sb seed   --content ../my-content                      # one papers/<safe-id>.json per entry of seed/*.jsonl
sb fetch  --content ../my-content --data ~/sb-data     # [--limit N] [--only ID ...]: metadata and text into DATA/texts
sb build  --content ../my-content --data ~/sb-data     # DATA/index-<content commit>/library.sqlite, DATA/current -> it
sb search "lambert universal variable" --data ~/sb-data [--vertical space] [-k 8]
sb serve  --data ~/sb-data [--host 127.0.0.1] [--port 8790] [--token-file F]   # MCP at /mcp
```

`fetch` spaces arXiv calls 3 seconds apart, backs off on HTTP 429, and records a failed paper in
`DATA/failures.json` without stopping. The server opens the index read-only, re-resolves `DATA/current` per
request (a new build is served without a restart) and, with `--token-file`, answers 401 without the bearer token.
Tests: `make test`; `make lint`.

## How it fits with other tools

It is one source among several. An agent platform such as
[flotilla](https://github.com/chipi/flotilla) reads it like any other MCP source, records each
read, makes an answer cite what it read, and can run the periodic search that proposes new papers
for a person to approve. second-brain stores and serves; deciding what joins it, and asking across
sources, belong to whoever uses it.

## Licence

[MIT](LICENSE). The papers a corpus holds keep their own licences; this repo holds none of them.
