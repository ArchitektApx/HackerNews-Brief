# Tracker and settings

## Tracker

```bash
hn-brief tracker status
hn-brief tracker stop
hn-brief tracker stop --all   # every tracker on the machine, not just this profile's
```

A redirect server on 127.0.0.1 that logs which link was clicked, then forwards to the real URL.
That is the only way a click in a terminal can become a signal.

If asked how safe it is: localhost only, never the LAN. Story ids must be digits, and the
redirect target is read from the recorded brief rather than from the request, so it cannot be
pointed anywhere the plugin did not already record. Its only write is an append to
`clicks.jsonl`, never the profile. It exits after `tracker_idle_ttl_min` idle minutes.

`ensure` refuses to adopt a server on the port that serves a different data directory, so two
installs cannot cross-wire. It will pick the next free port instead. That also means switching
between a development checkout and an installed copy leaves one tracker per directory, which is
what `stop --all` is for.

## Settings

```bash
hn-brief settings
hn-brief settings min_points=50 brief_size=15
```

Bare `settings` prints every key with its current value, so quote that rather than a default.
What the values mean:

| Key | Meaning |
| :-- | :-- |
| `min_points` | Bar for an unmatched story to earn a discovery slot |
| `min_points_matched` | Much lower bar for a story matching a stated interest |
| `window_hours` | How far back a brief looks |
| `brief_size` | Max matched items |
| `discovery_slots` | Adjacent discovery picks |
| `explore_size` | Items in explore |
| `breakout_points` | Points at which topic stops mattering |
| `breakout_slots` | Max breakout stories |
| `summarize_max` | Max pages fetched per brief |
| `summarize_min_score` | Match strength earning a summary |
| `summarize_min_points` | Popularity earning a summary |
| `decay_after_impressions` | Ignored impressions before weight drops |
| `tracker_port` | Preferred port, scans upward if taken |
| `tracker_idle_ttl_min` | Tracker idle shutdown |
| `seen_retention_days` | How long a story stays suppressed |

The two point floors are deliberately far apart: relevance beats popularity for a topic the user
already stated, while an unmatched story has to earn its slot on size alone.

## Moving a profile

```bash
hn-brief export > profile-backup.json
hn-brief import profile-backup.json
```

Needed if the plugin or marketplace is ever renamed, since the data directory derives from
`<plugin>@<marketplace>` and a rename orphans the old one.
