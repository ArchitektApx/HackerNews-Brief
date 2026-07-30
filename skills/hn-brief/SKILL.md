---
name: hn-brief
description: Personalized Hacker News brief, ranked against a stored interest profile that learns from what gets opened. Use for "hn brief", "hacker news digest", "tech news for me", "what's on HN today", and for managing interests with add, forget, explore, or keep.
---

# hn-brief

```
REF=${CLAUDE_PLUGIN_ROOT}/skills/hn-brief/reference
```

`hn-brief` is on the Bash tool's PATH and is the only command to run. Write it bare, one invocation
per Bash call: no path, no `python3`, no second statement. Any other shape makes the user re-approve
almost every run. Never edit the profile JSON by hand.

## Routing

The argument picks the mode, and natural phrasing maps to the same routes ("what's on HN", "stop
showing me crypto", "I like number 4"). Read a reference file only for the mode invoked.

| Argument | Action |
| :-- | :-- |
| none | Daily brief, below |
| `explore` | `$REF/explore.md`, which also takes a range |
| `interests\|add\|forget\|keep\|setup` | `$REF/interests.md` |
| `tracker\|settings` | `$REF/operations.md` |
| anything else | A time range such as `yesterday` or `since monday`, passed to `--range` verbatim |

Never convert a range to hours yourself. If the script rejects one it prints the grammar it accepts:
translate into that once, rerun, and state the reading in a line above the brief. Twice means ask
instead.

## Daily brief

One Bash call:

```bash
hn-brief brief --range "<range or empty>"
```

It applies pending clicks, ages the profile and prints one payload. Rank from that, it carries the
profile, settings and candidates, so nothing else needs querying. If `profile.interests` is empty,
run setup instead.

`notices.clicks` reports what the user's clicks changed. When something moved, open with one line
like `Learned since last brief: <topic> +2, new interest <topic>.`

`notices.tracker` gives the `port` for every link. On `"status": "failed"` or `"remote"` there is no
tracker: link each title to `https://news.ycombinator.com/item?id=<id>`, drop the comments link and
every invitation to click, and say once that clicks are not tracked this run, so `keep N` is the
only signal left.

`range` carries the resolved band and a `days` multiplier. Title the brief with `range.label`, plus
the resolved dates whenever the range was anything but the default, so a misread phrase is obvious.
Scale how many items you show by `range.days`: a week deserves more than a day's worth, and the
candidate caps already scaled.

### Ranking

`score` is a lexical prefilter, so re-rank on meaning. A release announcement can belong to a topic
with no term overlap, and heavier topic weight breaks ties. `matched_via_url` lists topics whose
only evidence was the URL slug: usually noise, since a news URL names every entity a story mentions,
so drop those unless the title supports the topic. Two hard constraints: never use a label from
`profile.blocked`, and treat low scorers in `profile.broad_terms` topics as unproven unless the
title backs them up. Mention a broad term once at the end so the user can tighten it.

Rescuing a story into an interest the prescore missed means that term list has a hole. Name the term
that should have caught it in the batch's `learn`; nothing else catches that, because a missed story
looks exactly like a quiet day.

Three pools come back, each becoming a section:

| Pool | What it is | How many |
| :-- | :-- | :-- |
| `matched` | Hit a stated interest | up to `settings.brief_size` |
| `breakout` | Matched nothing, but big enough that everyone is discussing it | up to `settings.breakout_slots` |
| `unmatched` | Matched nothing, chosen for adjacency | `settings.discovery_slots` |

`matched` regularly arrives larger than `brief_size`, since the prescore cannot judge meaning and
hands you the choice. Trim by breadth: every interest that earned a place gets its best item before
any gets a second, and the topic with most candidates loses the surplus first. One busy topic
filling the brief is worse than one showing every corner of the profile.

An item carrying two topics appears once, under the interest the story is actually about rather than
the one that scored higher. List both in `topics` so a click credits the real subject too.

Show every `breakout` item whatever it is about. Each needs a topic decision the prescore could not
make: if its topic is already an interest, put it in `topics` and leave `new_topic` null so a click
strengthens that interest rather than duplicating it; otherwise give it `new_topic` and `new_terms`
like a discovery pick.

Discovery picks come from distinct topics, are adjacent to an existing interest rather than random,
and prefer items carrying a `probation_hint`. Each gets a kebab-case `new_topic` and two or three
`new_terms` that would match similar stories later.

### Summaries

The standouts get a real summary: `matched` items at `score >= settings.summarize_min_score`, and
anything at `points >= settings.summarize_min_points`. Cap at `settings.summarize_max`, fetched in
one call, ordered by fit to this profile with points only breaking ties.

```bash
hn-brief extract --ids <id> <id> <id> --want <summarize_max>
```

Pass two or three spare ids beyond the cap. `--want` returns the first that succeed, so a page
behind robots.txt or a paywall costs a spare rather than a summary slot. Every result carries the
`id` it came from, including the refusals listed after the successes, so match on that.

Write two or three sentences of substance from each `ok` result: what it says, what is new, what it
concludes. When `ok` is false, fall back to the one-line note without mentioning the failure or
substituting comments. Nothing else in the brief justifies a page fetch.

### Output

```markdown
## HN Brief · 28 Jul 2026 · 9 for you, 2 big, 3 new

### <interest>
1. **[Story title](http://127.0.0.1:PORT/c/<id>)**
   example.com · 412 pts · [188 comments](http://127.0.0.1:PORT/t/<id>)
   Two or three sentences for a summarized item, one line of why it is here for the rest.

### Big on HN today · outside your interests
9. **[Story title](http://127.0.0.1:PORT/c/<id>)**
   example.net · 1294 pts · [505 comments](http://127.0.0.1:PORT/t/<id>)
   One line. Would add `<new-topic>`.

### New to you · click to add, or `/hn-brief keep 11`
11. **[Story title](http://127.0.0.1:PORT/c/<id>)**
    example.dev · 210 pts · [64 comments](http://127.0.0.1:PORT/t/<id>)
    Adjacent to `<interest>`. Would add `<new-topic>`.
```

Numbering runs continuously from 1 across all sections, because that is what `keep` resolves
against. Section order is fixed; skip an empty section rather than padding it.

Two things the format depends on:

- **Never restate a matched topic.** The heading already names it, so no ``Matches `<interest>` ``
  trailers. If the heading does not explain why an item is there, fix the grouping.
- Topic notes appear only where a click changes something: ``Would add `<topic>` `` on a discovery
  or breakout pick, ``Counts toward `<topic>` `` when a breakout item maps to an existing interest.

### Write back

`order` must match the numbering shown. Titles and URLs are already cached from the fetch, so write
ids and topic decisions only, to a session scratchpad path rather than a fixed name an earlier
session may already own.

```json
{
  "mode": "brief",
  "order": ["<id>", "<id>", "<id>"],
  "items": {
    "<id>": {"topics": ["<interest>"]},
    "<id>": {"topics": [], "new_topic": "<new-topic>", "new_terms": ["term one", "term two"]}
  },
  "shown": ["<interest>"],
  "probation": {"<new-topic>": ["term one", "term two"]},
  "learn": {"<interest>": ["term one"]}
}
```

```bash
hn-brief apply --file <batch path>
```

`mode` decides suppression, so a brief's stories are marked seen and will not return.

`probation` takes every topic you offered, discovery picks and breakout items alike, since both are
offers a click can accept.

`shown` takes **only** interests you actually rendered a group for. It is the sole input to weight
decay, so a topic that got no slot today must not be listed: scarcity on HN may never cost an
interest anything.

`learn` holds only terms you can point at a story for: product, project and domain names. A guess
outlives the miss it fixed and matches every day after. Say which terms were learned in one line at
the end.

## Rules

- Report a non-zero exit by quoting its stderr line. They are written to be read.
- Zero matches is a real answer: say so, show the other sections, suggest `explore`.
- Never list interests that had no stories. That is the normal case, not news.
- This runs daily, so the brief costs three Bash calls: `brief`, `extract`, `apply`. Do not re-read
  the payload or narrate the pipeline.
