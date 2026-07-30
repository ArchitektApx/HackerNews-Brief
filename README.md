# 📰 hn-brief

A Claude Code plugin that gives you a daily Hacker News brief filtered to your interests, and
learns from the links you click.

🐍 Python standard library only. No API key, no account, no external service, nothing to sign up
for.

```
## HN Brief · 28 Jul 2026 · 9 for you, 2 big, 3 new
Learned since last brief: powershell +2, new interest nix.

### Rust
1. **How is the Bun rewrite in Rust going?**
   lockwood.dev · 440 pts · 331 comments
   Benchmarks both builds and concludes the rewrite is real but the timeline was not.

### PowerShell
2. **PSResourceGet 2.0 ships**
   devblogs.microsoft.com · 90 pts · 31 comments
   Replaces PowerShellGet.

### Big on HN today · outside your interests
9. **Kimi-K3 on HuggingFace**
   huggingface.co · 1299 pts · 505 comments
   Open weight multimodal model, 2.8T parameters. Would add `open-weight-models`.

### New to you · click to add, or `/hn-brief keep 11`
11. **Formal verification of Ada in industry**
    adacore.com · 210 pts · 64 comments
    Adjacent to `rust`. Would add `formal-methods`.
```

## 🖥️ Two ways to run it

|  | Claude Code runs on | You read the brief on | It learns from |
| :-- | :-- | :-- | :-- |
| **Local** | your machine | the same machine | clicking links |
| **Remote** | one machine you leave running | your phone, or any device you connect from | `/hn-brief keep N` |

**Local.** The default, nothing to configure. Links point at a small redirect on `127.0.0.1`, so
clicking one records the click and forwards you to the page.

**Remote.** Keep the session on one machine, connect to it from your phone or another device, and
set `HN_BRIEF_REMOTE` there:

```json
// ~/.claude/settings.json
{ "env": { "HN_BRIEF_REMOTE": "1" } }
```

Links become Hacker News links that open from anywhere, and `/hn-brief keep 4 11` tells it what you
liked, counting exactly the same as clicking those two items. One profile either way. See
[Settings](docs/settings.md).

## 🚀 Install

```
/plugin marketplace add ArchitektApx/HackerNews-Brief
/plugin install hn-brief@hn-brief
```

## 🔑 Approve it once

Every phase of a run is a single `hn-brief` command, so one rule covers the whole plugin. Add it
to `~/.claude/settings.json` and the approval prompts stop for good:

```json
{ "permissions": { "allow": ["Bash(hn-brief *)"] } }
```

User settings apply in every directory, unlike approving in a session, which is saved per
repository and would have to be repeated wherever you next run a brief. The rule names no path
and no version, so a plugin update never invalidates it.

No hook ships with this plugin to grant itself permission. Nothing runs without a rule you wrote
or an approval you gave.

## ✨ First run

```
/hn-brief setup
```

Tell it what you are interested in, in one message, in plain words. It proposes match terms,
checks each one against today's front page so you can see which are too broad to be useful, and
saves the ones that survive. Takes a minute, once.

## 📅 Daily use

```
/hn-brief                 today's brief
/hn-brief explore         ten stories outside your profile
```

Locally, open a link the way your terminal opens links: ⌘-click on macOS, Ctrl+click on Linux and
Windows, Ctrl+Shift+click in the VS Code terminal. That click is what teaches the plugin, so open
what you actually find interesting and the brief follows you.

Three sections, every run:

- 🎯 **Your topics.** Matched against your interests with a deliberately low point floor. A 16
  point post about what you work on beats a 300 point post about what you do not.
- 🔥 **Big on HN today.** Genuinely large stories that match nothing you follow, shown anyway,
  each tagged with the topic a click would add.
- 🌱 **New to you.** Adjacent picks chosen for fit rather than size.

The strongest few get a real summary written from the article itself. Comments are never fetched.

Time ranges work in plain language, and are calendar aware:

```
/hn-brief yesterday
/hn-brief this week
/hn-brief since monday
```

## 🧠 How it learns

Open a "New to you" pick about PowerShell and `powershell` becomes an interest without you typing
anything. Open stories in a topic you already follow and its weight rises, which wins ties later.
In a remote session `/hn-brief keep 4 11` says the same thing in words.

An interest only loses weight after it was shown to you repeatedly and you ignored it every time.
Elapsed time costs nothing, so a niche topic HN covers twice a month is judged on those two
chances, never on the silence in between.

Stories you have already been shown do not come back.

## 🔒 Privacy

Everything lives in `~/.claude/plugins/data/hn-brief-hn-brief/` and survives plugin updates. The
only network traffic is the public HN search API, plus direct page fetches for the handful of
articles that get summarized, which honor `robots.txt`. Your profile is never transmitted.

## 📚 Docs

| Guide | What is in it |
| :-- | :-- |
| [Commands](docs/commands.md) | Every command, time range grammar, managing interests |
| [Settings](docs/settings.md) | Tuning thresholds and sizes, the click tracker, reading on another device |
| [How it works](docs/how-it-works.md) | Ranking, term matching, decay, the tracker, known limits |

## License

MIT
