# Managing interests

One `hn-brief` invocation per Bash call, never several joined together.

## Show

```bash
hn-brief show
```

Three short sections: interests with weight and hits, probation with times shown, blocked. Add
one line: weight rises with clicks and falls only after repeated ignored impressions, never from
time passing.

A term marked `*` was learned from a re-rank rather than stated. Those are the first candidates
for `drop-term` if a topic starts pulling in the wrong stories.

## Add

```bash
hn-brief terms --topic TOPIC --terms "term one,term two"
hn-brief add TOPIC --terms "term one,term two"
```

Propose the terms yourself, three to five, lowercase, including abbreviations and product names.
The topic slug alone matches poorly.

Terms are what the whole profile rests on, so check them against today's stories before adding.
`terms` labels each one:

| Verdict | Meaning | What to do |
| :-- | :-- | :-- |
| `ok` | Selective enough to carry a match | keep |
| `broad` | Matches a large share of all stories | replace, or keep knowing it is weak |
| `dead` | Matched nothing today | keep if it is a real name that is simply quiet, drop if it was a guess |

Good terms name the thing: products, libraries, file extensions, domains, protocols. A term
describing *where* a story was posted, or one that could head a story on any subject, drags noise
into that topic on every future run.

A topic listed in `broad_only` has no term that is not broad, so no rewording rescues it: nothing
in a story names the topic. Say so and propose the narrower topics the user probably meant, since
adding it anyway floods every future brief with whatever shares that term.

Matching folds punctuation and handles plurals, so `github.com` matches a github.com URL and
`open weights` matches "open-weight". A term of six letters or more also matches a longer word it
begins, so `postgres` covers "PostgreSQL" and `sqlite` covers "sqlite3". No need to list variants.

A term written with a leading dot, like `.net`, is treated as a technology name and matched in
titles only. Otherwise it would hit every `.net` domain on the site.

Report which terms you used and flag anything that came back `broad`.

`terms` reports only what you asked about. Pass `--all` to audit the whole profile.

## Edit terms

```bash
hn-brief drop-term TOPIC term-to-remove
hn-brief add TOPIC --terms "new,list" --replace
```

Both keep the topic's weight, hits and counters. Never reach for `forget` plus `unblock` plus
`add` to prune a term: that path discards everything the profile has learned about the topic and
passes through a state where its terms are blocked.

## Forget

```bash
hn-brief forget TOPIC
hn-brief unblock TOPIC
```

Removes the topic from interests and probation, then blocks its terms so it cannot return through
discovery either. Confirm which terms are now blocked, since a broad one suppresses a lot.

## Keep

```bash
hn-brief keep 4 11
```

Resolves numbers against the last rendered list, whether that was a brief or an explore run.
Report its output as-is.

## Setup

Run when the profile is empty, or on request. Ask for interests in one message rather than a
questionnaire, suggesting starting points from the user's work visible in this session, and say
that clicks refine everything from here. Then one `add` per topic with checked terms, and offer
to run the first brief.
