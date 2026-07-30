# 🔬 How it works

## 🧩 Ranking

A run pulls the window's stories, scores each against your interest terms, and splits them into
three pools. The score is a lexical prefilter only, matching terms against titles and domains. It
cannot judge meaning, so the model re-ranks on top of it: a release announcement can belong to a
topic it shares no word with, and a term found only in a URL slug usually means nothing, since a
news URL names every entity a story mentions.

Terms that match a large share of the day's stories are down-weighted automatically, measured per
run rather than from a maintained list. A term that is broad today and specific next month is
treated correctly both times.

## 🏷️ Term matching

Matching folds punctuation and handles plurals, so `github.com` matches a github.com URL and
`open weights` matches "open-weight". A term of six letters or more also matches a longer word it
starts, so `postgres` catches "PostgreSQL" and `sqlite` catches "sqlite3". Short terms do not, or
`rust` would match "rusty".

A term written with a leading dot, like `.net`, is treated as a technology name and matched in
titles only. Otherwise it would hit every `.net` domain on the site.

### Terms learn themselves

A term list written once ages badly, because an ecosystem keeps shipping names it was never
given, and the failure is silent: a missed story looks exactly like a quiet day.

So when a story is clearly about one of your interests but the term list missed it, the name that
should have caught it gets added. Learned terms are marked `*` in `/hn-brief interests` and can be
removed with `hn-brief drop-term TOPIC term`. Only names it can point at a story for are kept,
never guesses, and there is a cap per topic so a drifting topic gets your attention instead of
quietly widening.

## 🖱️ The click tracker

A terminal cannot report a click, so brief links point at a redirect server on `127.0.0.1` which
logs the click and forwards you to the real page.

Its limits are deliberate:

- Binds localhost only, never the LAN.
- Story ids must be digits, and the redirect target is read from the recorded brief rather than
  from the request, so it cannot be pointed anywhere the plugin did not already record.
- Its only write is an append to `clicks.jsonl`, never the profile.
- It exits after `tracker_idle_ttl_min` idle minutes, or on `/hn-brief tracker stop`.

If it is not running, the brief renders plain Hacker News links and says clicks are not being
recorded. Set `HN_BRIEF_REMOTE` to choose that on purpose, for a brief you read on another device
where a `127.0.0.1` link cannot reach the tracker. See [Settings](settings.md).

## ⚖️ Weights and decay

An article click counts double, a comments link counts once, and clicking the same story twice
counts once, since the signal is that a story was worth opening rather than how many times the
link fired.

Clicking a discovery pick promotes that topic into a real interest. Clicking stories in an
existing interest raises its weight, which breaks ties in future rankings.

An interest loses weight only after `decay_after_impressions` briefs where it was rendered and
you ignored it. Elapsed time costs nothing. A niche topic HN covers twice a month is judged on
those two chances, not on the silence between them, and no interest ever decays into
irrelevance: there is a floor.

Clicks are applied at the start of the next brief, not the moment you click, which is why a run
opens with what changed since last time.

## 🗃️ Data

Everything lives in `~/.claude/plugins/data/hn-brief-hn-brief/`:

| File | What it is |
| :-- | :-- |
| `profile.json` | Interests, terms, weights, probation, blocked, seen ids |
| `brief.json` | The last rendered list, so link ids resolve |
| `candidates.json` | Recent story metadata, so summaries can resolve an id to a URL |
| `clicks.jsonl` | Clicks waiting to be applied |
| `clicks.applied.jsonl` | Audit trail of clicks already counted |

One writer, atomic writes. A brief, the tracker and a click can all land at once.

## 🧪 Running the scripts directly

The skill is orchestration. Every capability sits behind the `hn-brief` command, which the plugin
puts on the PATH of Claude Code's Bash tool. So these are what runs under the hood when you ask in
plain language, and what you can ask for directly:

```bash
hn-brief show
hn-brief add kubernetes --terms "kubernetes,k8s,kubectl"
hn-brief terms --topic k8s --terms "kubernetes,k8s"
hn-brief brief | jq .counts
hn-brief tracker status
```

## ⚠️ Known limits

- Clicks are recorded only while the tracker is alive, so a remote session learns nothing until you
  say `/hn-brief keep N`.
- The Algolia index lags Hacker News slightly, so a story posted minutes ago may miss one run and
  appear in the next.
- Summaries need readable HTML. Paywalls, PDFs and client rendered pages fall back to one line.
- A term list can only match names it was given. Learning closes that gap over time, but the
  first miss on a brand new tool is invisible.
