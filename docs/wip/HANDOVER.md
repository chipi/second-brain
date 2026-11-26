# Handover: second-brain, from the flotilla session (2026-11-26)

For the Claude session that takes over this repo. Read this, then
[RFC-0001](../rfc/RFC-0001-the-library.md), then `AGENTS.md`.

## How it started

The operator's second brain answers questions from what they read, hear, write and decide. In
flotilla, RFC-0014's case 1 ("ask the second brain") reads live sources: Google Docs and Tasks,
Close Listening (the operator's podcast corpus, with its own vector index and MCP server), later
Granola (meetings). The missing source was research papers. The operator decided on 2026-11-26:

- **Two repos:** this one, public, MIT, the tooling; `chipi/second-brain-content`, private, the
  content (checked out at `/work/second-brain-content/main`).
- **Four verticals to start:** `ai`, `software`, `robotics` (the operator's robotics lab:
  `/work/robotics-lab`), `space` (Orrery: `/work/orrery/main`). More may come; a vertical is data.
- **One PDF-to-text dependency is allowed,** chosen by measurement and pinned.
- **flotilla runs the periodic search** and the operator approves each paper (flotilla's
  RFC-0015, `/work/flotilla/main/docs/rfc/RFC-0015-the-library.md`). Nothing here depends on
  flotilla.
- **Asking across sources stays in flotilla's answer routine,** where each source's reads are
  recorded and citations checked; this repo is one source, not a hub.
- The operator wants this repo to grow into further second-brain capabilities over time; each new
  one gets its own RFC here.

## Where things are

| What | Where |
| --- | --- |
| The design | `docs/rfc/RFC-0001-the-library.md` |
| The content repo | `/work/second-brain-content/main` (private): skeleton only so far |
| The first research pass | done 2026-11-26: the content repo's `seed/<vertical>.jsonl` (ai 162, software 198, robotics 117, space 101; `robotics-reserve.json` holds 56 more, verified, cut for size); every entry verified against arXiv's or NASA NTRS's API or a resolving DOI by the agent that found it, and a random 20 re-checked by the flotilla session (all matched) |
| The tmux session | `second-brain`: windows `claude`, `terminal` (this repo), `content` |
| flotilla's side | flotilla RFC-0015; case 1's plan `docs/wip/rfc-0014-case1-plan.md` there |
| How flotilla reads MCP servers | `/work/flotilla/main/runtime/mcp_client.py` (streamable HTTP) and RFC-0014 §4.8 there |

## The goal now: an MVP flotilla's case 1 can read

The operator's aim (2026-11-26) is an MVP here, so flotilla's case 1 (the second brain's question
flow) can read the library as one more MCP source. The flotilla session builds case 1 in parallel
against a stub MCP server; the two meet at this repo's three tools. The MVP:

- **Manifests from the seed:** one `papers/<id>.json` per seed entry (a paper may belong to more
  than one vertical: 6 ids are in two lists, all agent papers in both `ai` and `software`, so a
  manifest has `verticals: [...]`), approver "seeded 2026-11-26" (decision 1 below).
- **`sb build`:** arXiv metadata and abstract for every arXiv paper; arXiv's HTML full text where it
  exists (`html: true` in the seed), parsed with the standard library and chunked by section; NTRS
  and DOI papers as their abstract (NTRS's API, Crossref's); SQLite FTS5 over all chunks.
- **`sb serve`:** the read-only MCP server, streamable HTTP, `search`, `read`, `paper` as RFC-0001
  §3.3 has them. It must answer flotilla's client (`runtime/mcp_client.py` in flotilla):
  `initialize` with a session id, `tools/list`, `tools/call`.
- **Fixture tests** (RFC-0001 §5).
- **Left for after the MVP:** PDF text (the allowed dependency), embeddings, the evaluation set,
  the installation's deploy, and flotilla's `library/watch`.

## The seed's caveats

- `tier` was set by year, differently per list (ai: 2023 and before is foundational; software:
  2022; space: 2015; robotics: by judgment): normalise it, or drop it, in the manifests.
- `first_author` is a last name taken mechanically; wrong for names with particles or non-Western
  order. Take authors from the source's API at build time instead.
- `why` lines are the research agents' own words, written without opening the papers.
- NTRS records without a publication date got their year from the id's first four digits.
- arXiv's `html` flag was checked only for recent papers; older ones say `false` unchecked.

## Next, in RFC-0001 §6's order

1. The operator prunes the four seed lists; the pruned entries become manifests in
   `papers/<v>/<id>.json`, and each vertical gets its `topics.yaml`.
2. Here: the manifest schema and `sb add`; the fetchers (arXiv HTML, PDF, NTRS, DOI); extraction
   and chunking by section; the build and the FTS5 index; the read server (`search`, `read`,
   `paper`); the fixture tests (RFC-0001 §5).
3. The retrieval evaluation: 40 questions, 10 per vertical, written with the operator.
4. The installation: build and read server beside flotilla's reads worker on the mini (the
   operator's decision 2 in RFC-0001 §7 is still open).

## Open decisions (RFC-0001 §7)

1. The first pass's size: keep everything verified and prune by hand, or cap each vertical first.
2. Where the build and the read server run.

## Known from the research pass

- arXiv's API rate-limits hard (HTTP 429, then a block of minutes): wait 3 seconds between calls
  and batch ids (up to 50 per `id_list`). Use `https://export.arxiv.org`, not `http://`, which
  returned empty bodies.
- Crossref sometimes returns a short title ("Dynamo", "MapReduce"); keep the full title in the
  manifest and the DOI as the id.
