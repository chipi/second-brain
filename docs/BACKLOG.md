# Backlog

Work for a dedicated agent, found while building the MVP and its installation (homelab's
`infra/second-brain`, 2026-11-26). Each item says what is wrong or missing and what done looks like.

1. **Errors to GlitchTip.** sb reports nothing to the installation's GlitchTip. Either the Sentry SDK (a
   new dependency: the operator's approval first) or a minimal standard-library envelope POST to the
   DSN. Homelab creates the project (its `infra/glitchtip/projects.json`) and passes the DSN; sb sends
   the environment, the release `sha-<7>`, and fingerprinted errors.
2. **The index's name without git.** `sb build` names the index from `git rev-parse` in the content
   directory, but the build image has no git, so the name is `index-nogit`; `infra/second-brain`
   passes `--version <content sha7>` instead. Read `.git/HEAD` (and the ref it names) directly, or put
   git in the build image.
3. **Old indexes are never removed.** `sb build` leaves every `index-*` folder; `infra/second-brain`'s
   `update.sh` keeps the current one plus one. Own it in sb: `sb build --keep N`.
4. **Traces.** sb reads `traceparent` into its log lines' `trace_id` but exports no spans. OTLP to
   VictoriaTraces (a dependency question, like 1) would put sb's requests under flotilla's runs.
5. **The full fetch on the installation.** On the laptop the whole library was fetched and upgraded
   (2026-11-26: 572 papers; full text for 486 of them, the rest abstract only with a recorded reason,
   see `sb status`), but the installation has only run `--limit 2`. Measure there: time, each source's
   rate limits and politeness, failure counts.
6. **Volume ownership.** The image runs as uid 10001 and needs `/data` (and `/out`) writable by it;
   `infra/second-brain` hands the volumes over as root. Say so in the README's "Running it".
