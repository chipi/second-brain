# AGENTS.md — second-brain

This file layers on top of the operator's global AGENTS.md (`~/.config/AGENTS.md`). It holds only
what is specific to this project, and never contradicts the global rules.

## Project overview

second-brain is the public tooling for a personal research corpus: fetch chosen papers, extract
their text, index it, serve it read-only over MCP. Design:
[RFC-0001](docs/rfc/RFC-0001-the-library.md).

## Stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Language | Python, standard library first | small, auditable, no framework |
| Index | SQLite FTS5 (BM25) | in the standard library; embeddings only after an evaluation asks for them |
| PDF text | one PDF-to-text library, pinned | the operator allowed it (2026-11-26); chosen by measurement (RFC-0001 §3.2) |
| OCR | `pdftoppm` (poppler) and `tesseract`, system binaries called by subprocess; optional | the operator allowed it (2026-11-26); no Python package |
| Read server | MCP, streamable HTTP | what flotilla's live reads and other clients speak |
| Tests | `unittest` | standard library |

## Project-specific rules

1. **Public tooling, private content.** Never commit a corpus: no manifests of a real library, no
   paper texts, no topic lists of a real installation, no host names, user paths or credentials.
   Tests use a small fixture library under `tests/fixtures/`, made for the purpose. Examples in code,
   docs and tests use made-up ids and titles (`arxiv:2001.00001`), never one from the real list.
2. **The read server is read-only,** holds no credential, and mounts the corpus read-only.
3. **The MCP tools are a contract.** `search`, `read` and `paper` keep their names and fields; a
   change is versioned and noted in the changelog, since other repos call them.
4. **Every paper is identified by a versioned source id** (`arxiv:<id>v<n>`, `ntrs:<id>`,
   `doi:<doi>`, `url:<url>`), so a text can be fetched again and a citation stays exact.
5. **Nothing is added without a manifest,** and a manifest says why the paper is in and who
   decided.
