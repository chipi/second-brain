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
| The first research pass | four candidate lists, one per vertical, verified against arXiv's or NASA NTRS's API or a resolving DOI; to be moved into the content repo's `seed/` by the flotilla session, then pruned by the operator |
| The tmux session | `second-brain`: windows `claude`, `terminal` (this repo), `content` |
| flotilla's side | flotilla RFC-0015; case 1's plan `docs/wip/rfc-0014-case1-plan.md` there |
| How flotilla reads MCP servers | `/work/flotilla/main/runtime/mcp_client.py` (streamable HTTP) and RFC-0014 §4.8 there |

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
