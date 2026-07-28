# ⚙️ Settings

```
/hn-brief settings                              show every key and its current value
/hn-brief settings min_points=50 brief_size=15  change one or more
```

Changes report what moved, so `min_points 30 -> 50` rather than a silent write.

## 🎯 What gets in

| Key | Default | Meaning |
| :-- | --: | :-- |
| `min_points` | 30 | Floor for an unmatched story to earn a discovery slot |
| `min_points_matched` | 8 | Much lower floor for a story matching a stated interest |
| `breakout_points` | 250 | Points at which topic stops mattering at all |
| `window_hours` | 24 | How far back a brief looks by default |

The two point floors sit deliberately far apart. Relevance beats popularity for a topic you
already told it about, while a story matching nothing has to earn its slot on size alone. Raise
`min_points` if discovery feels noisy; lower `min_points_matched` if your niche topics go quiet.

## 📏 How much you get

| Key | Default | Meaning |
| :-- | --: | :-- |
| `brief_size` | 12 | Max items in the matched section |
| `discovery_slots` | 3 | Adjacent discovery picks |
| `breakout_slots` | 3 | Max big-on-HN stories |
| `explore_size` | 10 | Items in `/hn-brief explore` |

## 📝 Summaries

| Key | Default | Meaning |
| :-- | --: | :-- |
| `summarize_max` | 4 | Max article pages fetched per brief |
| `summarize_min_score` | 1.5 | Match strength that earns a summary |
| `summarize_min_points` | 400 | Popularity that earns a summary |

Each fetched page is capped before it is read, so raising `summarize_max` costs roughly one page
of text per extra summary. This is the most expensive part of a run.

## 🧠 Learning

| Key | Default | Meaning |
| :-- | --: | :-- |
| `decay_after_impressions` | 12 | Ignored impressions before an interest loses weight |
| `seen_retention_days` | 45 | How long a story stays suppressed once shown |

Decay counts impressions, never elapsed time. Raise `decay_after_impressions` to give topics
longer to prove themselves, or lower it if stale interests linger.

## 🖱️ Click tracker

| Key | Default | Meaning |
| :-- | --: | :-- |
| `tracker_port` | 47811 | Preferred port, scans upward if taken |
| `tracker_idle_ttl_min` | 180 | Idle minutes before it shuts itself down |
