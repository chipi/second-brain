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

## Built in the MVP (2026-11-26)

What exists (package `sb/`, standard library only, 43 unit tests in `tests/`, `make test` and `make lint`):

| Command | What it does |
| --- | --- |
| `sb seed --content DIR` | one flat `papers/<safe-id>.json` per seed entry (572 for 578 entries: 6 ids sit in two lists and merge into `verticals`, `subtopics` and `why`, each per vertical); idempotent; an existing `approver` is kept; `tier` and `first_author` are dropped (see the caveats above); the seed's `html` flag is kept in the manifest because `fetch` needs it |
| `sb fetch --content DIR --data DIR [--limit N] [--only ID]` | `DATA/texts/<safe-id>.json`: arXiv metadata in batches of 50, 3 s apart, 429 backoff (20, 40, 80, 160 s); HTML full text by section where the seed said `html: true` and the page parses (else the abstract, with a `note`); NTRS and Crossref abstracts; `versioned_id` records the arXiv version; failures in `DATA/failures.json` |
| `sb build --content DIR --data DIR` | `DATA/index-<content short sha>[-n]/library.sqlite` (FTS5, porter stemming, BM25), then `DATA/current` swapped by an atomic symlink rename. A manifest without a text is indexed by title only |
| `sb search "q" --data DIR [--vertical v] [-k N]` | the same code the server calls |
| `sb serve --data DIR [--host] [--port 8790] [--token-file F]` | MCP over streamable HTTP at `/mcp`, JSON answers, `Mcp-Session-Id`, protocols 2025-06-18 and 2025-03-26, read-only index |

Contract notes. Tool results carry the payload as JSON text in `content[0].text` and again as `structuredContent`.
A bad argument is a JSON-RPC error (-32602); an unknown paper or section is a result with `isError: true`.
`read` finds a section by exact title, number or unique substring. A query is turned into quoted words joined by
OR, so FTS5 syntax in a question cannot break it. Ids are the seed's unversioned form (`arxiv:2310.06770`); the
versioned id is in `paper`'s `versioned_id` once a text is fetched (AGENTS.md rule 4 is met only after a fetch).
`tests/test_index_server.py` has a flotilla-client interop test, skipped unless `SB_FLOTILLA_RUNTIME` names
flotilla's `runtime/` directory.

Smoke run (6 real papers: 2 arXiv with HTML, 2 without, 1 NTRS, 1 DOI): fetch 0 failures, build 572 papers and
678 chunks, search and `tools/list` over curl worked. Crossref had no abstract for the DOI paper, so it is
indexed by title only.

### Full-text upgrade (2026-11-26)

`sb fulltext --content DIR --data DIR [--limit N] [--only ID] [--retry]` (a separate command, not a `fetch` flag, so `fetch`
stays the metadata pass and `fulltext` can be re-run alone) and `sb status [--json]`. Code: `sb/fulltext.py` (sources and
outcomes), `sb/pdftext.py` (pypdf text, headings, OCR), `sb/report.py`. pypdf 7.1.1 is pinned in `requirements.txt` (chosen by
measurement against pdfminer.six on 7 PDFs: same word yield, 2-4x faster, BSD, no dependencies) and imported only when a PDF is read.

For each text whose `source_kind` is not html/pdf/ocr, in order: arXiv HTML of the versioned id for any year, arXiv PDF (3 s
gap); NTRS `downloads[].links.pdf`; DOI: OpenAlex `best_oa_location.pdf_url`, `open_access.oa_url`, other locations' PDFs (at
most 4 tried), then Crossref `link` entries with `application/pdf`. A PDF must start with `%PDF` and be under 40 MB.
Sections: numbered headings (`1 Intro`, `2.1 ...`, accepted only in sequence), roman and all-caps ones, and a short list of
plain ones (Abstract, Introduction, Methods, Results, Discussion, Conclusion, References, Acknowledgements, Appendix); pages
(`page N`) when fewer than two are found; text before the first heading is `Front matter`; References stays its own section;
an Abstract section is dropped when the source already gave an abstract. Hyphenated line breaks are joined (a hyphen is kept
before `to`, `and`, `of`...), pypdf's `/f_i`-style and Unicode ligatures are fixed.
OCR: when the text layer is under 50 words a page over more than 2 pages (or under 30 words in all), or under 60% of its tokens
look like words (2-24 letters, a vowel, no run of 6 consonants), `pdftoppm -r 300` and `tesseract -l eng` read the first 60 pages
and the text with more real words is kept (`source_kind` `ocr`, a `note`, `fulltext.ocr` with the counts and whether the cap
applied). No tool installed: outcome `scanned_no_ocr_tool`, not final, retried automatically once they are.

Every run writes `fulltext` into the text: `outcome` (html, pdf, ocr, paywalled, no_pdf_in_ntrs, no_open_fulltext, scanned,
scanned_no_ocr_tool, pdf_unreadable, fetch_failed), `final`, `tried` (step and result), `at`. Final outcomes are skipped on re-run
unless `--retry`; `fetch_failed` (network error, 5xx) is not final. `sb status` groups abstract-only papers by these reasons.

Coverage limits. A DOI with no open copy, or one whose OA link is a landing page, stays abstract-only ("paywalled": it means no
open PDF was reachable, not proof of a paywall). arXiv papers with neither HTML nor PDF (withdrawn) end `no_open_fulltext`.
Heading detection is heuristic: running headers, table rows and two-column footnotes can become a stray section or be merged into
the previous one (seen: `2 Dario Izzo` from a running header); appendices lettered `A`, `B` are not detected unless titled
`Appendix`; equations come out as plain text; two-column reading order is pypdf's. `fine-\ntuning` becomes `finetuning`. OCR is
English only and slow (about 1.5 s a page here); a PDF over 60 pages is OCRed for the first 60 only. Measured on a 1969 NTRS report
with a legacy OCR layer: the layer was kept (812 real words vs 809 from tesseract), so the trigger cost 16 s and gained nothing.

Left: running `sb fulltext` over the whole library (not run);
embeddings and the retrieval evaluation (RFC-0001 §5); the installation's deploy and where it runs (RFC-0001 §7
decision 2); flotilla's `library/watch` and an `sb add`; `topics.yaml` per vertical; fetching all 572 papers (not
run: about 470 arXiv HTML pages at 1 s each plus 12 API batches at 3 s); NTRS date handling for records without a
publication date; pruning old `index-*` folders (builds only add).
