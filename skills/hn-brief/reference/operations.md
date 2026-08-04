# Tracker and settings

## Tracker

```bash
hn-brief tracker status
hn-brief tracker stop
hn-brief tracker stop --all   # every tracker on the machine, not just this profile's
```

A redirect server on 127.0.0.1 that logs which link was clicked, then forwards to the real URL.
That is the only way a click in a terminal can become a signal.

If asked how safe it is: loopback only and never the LAN, story ids must be digits, the redirect
target is read from the recorded brief rather than from the request, and its only write is an append
to `clicks.jsonl`. It exits after `tracker_idle_ttl_min` idle minutes.

With `HN_BRIEF_REMOTE` set no tracker runs, so `stopped` is the correct answer rather than a fault.

A tracker serving a different data directory is never adopted, so a second install gets the next
free port instead of cross-wiring, and one machine can end up with a tracker per directory. That is
what `stop --all` is for.

## Settings

```bash
hn-brief settings
hn-brief settings min_points=50 brief_size=15
```

Bare `settings` prints every key with its current value, so quote that rather than a default. Most
names say what they do. The ones that do not:

| Key | Meaning |
| :-- | :-- |
| `min_points` | Bar for an unmatched story to earn a discovery slot |
| `min_points_matched` | Much lower bar for a story matching a stated interest |
| `breakout_points` | Points at which topic stops mattering |
| `summarize_min_score` | Match strength earning a summary |
| `summarize_min_points` | Popularity earning a summary |
| `decay_after_impressions` | Ignored *impressions*, never elapsed days |

## Article text, for debugging

```bash
hn-brief extract --ids <id> <id> --max-chars 2000
```

A brief already fetches and ships the text it needs, so this is never part of a run. It exists for
one question: when a summary reads badly, was it the page or the prompt? Ids resolve against the
candidates of the last brief or explore run, and each result carries the `id` it came from plus a
`reason` when the fetch was refused.

## Moving a profile

```bash
hn-brief export > profile-backup.json
hn-brief import profile-backup.json
```

Needed if the plugin or marketplace is ever renamed, since the data directory derives from
`<plugin>@<marketplace>` and a rename orphans the old one.
