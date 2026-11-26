# second-brain

A personal research corpus you choose and own. It fetches the papers you picked, extracts their
text, indexes it, and serves it read-only over MCP, so an agent (or you, through any MCP client)
can search and cite it beside your other sources.

**Status:** design. The first RFC is [RFC-0001](docs/rfc/RFC-0001-the-library.md); no code yet.

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

## How it fits with other tools

It is one source among several. An agent platform such as
[flotilla](https://github.com/chipi/flotilla) reads it like any other MCP source, records each
read, makes an answer cite what it read, and can run the periodic search that proposes new papers
for a person to approve. second-brain stores and serves; deciding what joins it, and asking across
sources, belong to whoever uses it.

## Licence

[MIT](LICENSE). The papers a corpus holds keep their own licences; this repo holds none of them.
