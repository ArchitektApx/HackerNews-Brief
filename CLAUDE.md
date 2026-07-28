# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A Claude Code plugin that produces a personalized daily Hacker News brief. The repo is both the
marketplace and the single plugin it serves, which is why `marketplace.json` points at `"./"`.

Python standard library only, no dependencies, by design. The interest profile is local JSON.
Nothing leaves the machine except requests to HN's public Algolia API and to article pages the
user asked to have summarized.

## Gotchas

**One Bash call is one bare `hn-brief` invocation.** A permission rule is matched against each
statement of a compound command and saved carrying that statement's arguments, and at most five
rules are kept per approval. A preamble split into five statements therefore asks five times, and
a rule saved for `shown <interest>` never matches the next run's `shown <other-interest>`, so the
prompting never stops. `bin/hn-brief` and `hn.py` exist only so every phase is one fixed-shape
command that `Bash(hn-brief *)` covers for good. Two things quietly undo that: adding a second
statement to a documented call, and writing the interpreter or a path instead of the bare command,
which puts the installed version number into the saved rule so the next release starts asking
again.

No `PreToolUse` hook grants the plugin permission, deliberately. It could, and since a plugin
already runs unsandboxed hooks it would grant no new capability, but such a hook is only as good
as its command validator: one matching on a substring would auto-approve
`hn-brief brief && <anything>`, making the plugin a general bypass in every repository.

**`CLAUDE_PLUGIN_DATA` is not in the Bash tool's environment.** Claude Code exports it to hook and
MCP subprocesses only, so `profile.py:derived_data_dir()` rebuilds the path from the script's own
location instead. That works because the scripts sit in the plugin cache tree, and it is why no
Bash call has to export anything. Outside that tree, in a checkout, it resolves to
`hn-brief-local`, which keeps development off the real profile.

**Weight decay is impression-based, never time-based.** A topic loses weight only after
`decay_after_impressions` briefs where it was rendered and ignored. HN rarely covering a niche
topic must cost that topic nothing. Same reason `profile.py shown` takes only the interests that
actually got a heading: passing the whole profile would tax every quiet topic.

**`score` from `fetch.py` is a lexical prefilter, not a verdict.** The model re-ranks on meaning.
Known blind spots, all confirmed in live runs: a term list written at setup ages badly, `postgres`
does not match "PostgreSQL", and a domain the topic was never given (`astral.sh`, `claude.com`) is
no evidence at all. A miss looks exactly like a quiet day.

**Algolia's `tags=front_page` returns historical front-page entries**, not the current front page.
Results are band-filtered and score-floored to drop them.

**`profile.py` is the only writer of the data directory** and it writes atomically. A brief, a
tracker and a click can all land at once.

## Token budget is a feature

This runs daily, so cost is a design constraint rather than a nicety:

- A brief is three Bash calls: `hn-brief brief`, `extract`, `apply`. Nothing else.
- `SKILL.md` is the hot path and stays small. `reference/*.md` is read only for the mode invoked.
- `fetch.py` slims its payload on purpose: no URLs, no `tracker_*` settings. Anything added there
  is paid for on every future run.

Before adding a field, an instruction or a Bash call, work out what it costs per brief.

## House rules

Everything written here, whether prose or a payload the model will read, follows
https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models.
Read it before a substantial edit to `SKILL.md`, `reference/*.md` or this file.

- Spend tokens on gotchas. Delete anything the file tree or the code already states.
- Progressive disclosure. Hot path in `SKILL.md`, everything else behind a reference file that is
  loaded only when its mode runs.
- State the constraint and the reason, then trust judgment. Do not script the model step by step.
- Rules use generic placeholders. A hardcoded example story or interest teaches the wrong specific.
- No em dashes.

## Verifying a change

No test suite. Verification is a live run against the real API, so exercise the path you touched
and read the output. Reports live in `TEST-RUN*.md`, local and gitignored.

## Releasing

`version` appears in both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`. Bump
both, or `/plugin marketplace update` detects nothing.
