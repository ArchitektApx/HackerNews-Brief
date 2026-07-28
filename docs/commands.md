# 🎛️ Commands

Every command works as a slash command, and as plain language. "What's on HN", "show me something
new", "stop showing me crypto" and "I like number 4" all route to the right place.

## Reading

| Command | What it does |
| :-- | :-- |
| `/hn-brief` | Today's brief |
| `/hn-brief <range>` | The same brief over a different window, see below |
| `/hn-brief explore` | Ten stories outside your profile, each tagged with the topic it would add |

## Interests

| Command | What it does |
| :-- | :-- |
| `/hn-brief setup` | First-run interview |
| `/hn-brief interests` | Interests with weights, probation topics, blocked terms |
| `/hn-brief add TOPIC` | Add an interest, with match terms checked against today's stories |
| `/hn-brief forget TOPIC` | Remove it and block its terms so discovery cannot bring it back |
| `/hn-brief keep N [N...]` | Adopt numbered items from the last list you were shown |

Numbers in `keep` resolve against whatever was rendered last, a brief or an explore run, so
`/hn-brief keep 4 11` right after a brief adopts those two topics.

## Maintenance

| Command | What it does |
| :-- | :-- |
| `/hn-brief tracker status` | Click tracker state, `stop` to shut it down |
| `/hn-brief settings` | Show or change thresholds, see [Settings](settings.md) |

## ⏱️ Time ranges

Ranges are calendar aware. `yesterday` means that whole calendar day in your local time, not a
rolling 24 to 48 hours ago.

```
/hn-brief yesterday
/hn-brief this week
/hn-brief last week
/hn-brief since monday
/hn-brief last sunday
/hn-brief 3 days
/hn-brief 48h
/hn-brief 2026-07-26
```

A wider range scales both how much is shown and how many candidates are considered, so `this week`
is a week's brief rather than a day's.

Phrasing outside that grammar is translated once into a date or an hour count, and the reading is
stated above the brief. Every brief titles itself with the resolved start and end, so a
misinterpretation is visible rather than silent.

## 🏷️ Editing match terms

Terms are what the whole profile rests on. A topic matches a story when one of its terms appears
in the title or the domain.

```
hn-brief terms --topic TOPIC --terms "term one,term two"   # are these any good?
hn-brief add TOPIC --terms "new,list" --replace            # swap the list, keep the history
hn-brief drop-term TOPIC bad-term                          # remove one term
```

`terms` scores each candidate term against today's stories and labels it:

| Verdict | Meaning |
| :-- | :-- |
| `ok` | Selective enough to carry a match |
| `broad` | Matches a large share of all stories, so it will drag in noise |
| `dead` | Matched nothing today, which is fine for a real name that is simply quiet |

Both `--replace` and `drop-term` keep the topic's weight, hits and counters. Reaching for
`forget` and re-adding instead throws away everything the profile has learned about it.

## 💾 Moving a profile

```
hn-brief export > profile-backup.json
hn-brief import profile-backup.json
```

Needed if the plugin or marketplace is ever renamed, since the data directory derives from
`<plugin>@<marketplace>` and a rename orphans the old one.
