# Explore

Discovery only, for when the user wants topics outside the profile rather than today's news.

Same preamble as the daily brief, in the same one call:

```bash
hn-brief explore --range "<range or empty>"
```

Defaults to the last 72 hours, since discovery needs a wider net than a daily brief. Accepts the
same range grammar.

Pick `settings.explore_size` items from `unmatched`, one per topic, ranked by how plausibly this
user would adopt the topic rather than by points. Spread beats popularity here.

Render one flat numbered list, closing with ``Click any of them, or `/hn-brief keep 1 4 7`. ``

```markdown
## Explore · 10 topics outside your profile

1. **[Story title](http://127.0.0.1:PORT/c/<id>)**
   example.com · 340 pts · [120 comments](http://127.0.0.1:PORT/t/<id>)
   → would add `<new-topic>`
```

An item whose real topic is already an interest keeps `topics` and a null `new_topic`, same rule
as a breakout item, so a click strengthens what exists instead of duplicating it. Say so in its
line rather than offering a new topic.

That case is also a term-list hole, since the item reached `unmatched` at all. Put the term in the
batch's `learn` under the same restraint as the brief: a name you can point at the story for,
never a guess.

Write `mode: "explore"`, ids plus `new_topic` and `new_terms` on every genuinely new item, and
`learn` for the rescues:

```bash
hn-brief apply --file <batch path>
```

Two differences from the daily brief, both deliberate and both handled by `mode`:

- **Nothing is marked seen.** A topic the user did not adopt should stay eligible for the daily
  discovery block.
- **Nothing enters probation.** Explore is opt-in only, so leave `probation` out of the batch:
  appearing in the list is not acceptance.
