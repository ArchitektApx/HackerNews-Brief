#!/usr/bin/env python3
"""Interest profile store for hn-brief.

Standard library only. All state lives under ${CLAUDE_PLUGIN_DATA}, which survives
plugin updates and reinstalls.

Files owned by this script:
    profile.json          interests, probation, blocked, seen ids, settings
    brief.json            id -> link map, read by tracker.py to resolve redirects
    clicks.jsonl          appended by tracker.py, drained here
    clicks.applied.jsonl  audit trail of drained clicks
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1

DEFAULT_SETTINGS = {
    # Discovery needs a high bar. A story that matches a stated interest does not:
    # a 15 point PowerShell post is worth more to the reader than a 300 point one
    # about something else.
    "min_points": 30,
    "min_points_matched": 8,
    "window_hours": 24,
    "brief_size": 12,
    "discovery_slots": 3,
    "explore_size": 10,
    # A story this popular is shown whatever it is about. Everyone is talking about it,
    # and it is the best chance to find an interest the profile does not know about yet.
    "breakout_points": 250,
    "breakout_slots": 3,
    # Summaries cost a page fetch each, so only the strongest items get one.
    "summarize_max": 4,
    "summarize_min_score": 1.5,
    "summarize_min_points": 400,
    "tracker_port": 47811,
    "tracker_idle_ttl_min": 180,
    "seen_retention_days": 45,
    # Decay counts missed chances, not days. A niche interest that HN rarely covers must
    # never lose weight for the calendar passing, only for being put in front of the
    # reader and ignored.
    "decay_after_impressions": 12,
}

# Weight model. See PLAN.md for the rationale behind each number.
WEIGHT_DEFAULT = 1.0
WEIGHT_PROMOTED = 0.7
WEIGHT_EXPLORE = 0.6
WEIGHT_MAX = 3.0
WEIGHT_PER_HIT = 0.15
CLICK_UNITS = {"article": 2, "comments": 1}
DECAY_FACTOR = 0.9
DECAY_FLOOR = 0.3  # a stated interest never decays into irrelevance on its own
PROBATION_MAX_SHOWS = 3
LEARNED_CAP = 8  # auto-added terms per topic. Past this the topic is drifting, not learning
BRIEF_ITEM_CAP = 500
APPLIED_LOG_CAP = 2000


# ---------------------------------------------------------------- paths / io


def data_dir():
    """Resolve the persistent data directory, creating it on first use.

    CLAUDE_PLUGIN_DATA is the intended source, but it is only exported to hook and MCP
    subprocesses, not to ordinary shell calls. When it is missing, derive the same
    directory from an installed plugin's own path rather than silently writing somewhere
    else and splitting the profile in two.
    """
    d = os.environ.get("CLAUDE_PLUGIN_DATA") or derived_data_dir()
    os.makedirs(d, exist_ok=True)
    return d


def derived_data_dir():
    """Rebuild ~/.claude/plugins/data/<plugin>-<marketplace> from this script's location."""
    parts = os.path.abspath(__file__).split(os.sep)
    try:
        cache = len(parts) - 1 - parts[::-1].index("cache")
        marketplace, plugin = parts[cache + 1], parts[cache + 2]
    except (ValueError, IndexError):
        # Not running from an installed plugin: dev checkout, tests, manual clone.
        return os.path.expanduser("~/.claude/plugins/data/hn-brief-local")
    slug_id = re.sub(r"[^A-Za-z0-9_-]", "-", "%s-%s" % (plugin, marketplace))
    root = os.sep.join(parts[:cache])
    return os.path.join(root, "data", slug_id)


def path_for(name):
    return os.path.join(data_dir(), name)


def read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return fallback
    except json.JSONDecodeError as exc:
        die("%s is corrupt (%s). Move it aside and rerun init." % (path, exc))


def write_json(path, payload):
    """Atomic write so a crash mid-save cannot truncate the profile."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def die(msg, code=1):
    print("hn-brief: %s" % msg, file=sys.stderr)
    sys.exit(code)


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today():
    return now().date().isoformat()


# ---------------------------------------------------------------- profile


def blank_profile():
    return {
        "version": SCHEMA_VERSION,
        "created": stamp(),
        "interests": [],
        "probation": [],
        "blocked": [],
        "seen": {},
        "seen_titles": {},
        "settings": dict(DEFAULT_SETTINGS),
    }


def load_profile():
    prof = read_json(path_for("profile.json"), None)
    if prof is None:
        prof = blank_profile()
    # Fill in anything a older version did not write.
    prof.setdefault("version", SCHEMA_VERSION)
    for key in ("interests", "probation", "blocked"):
        prof.setdefault(key, [])
    prof.setdefault("seen", {})
    prof.setdefault("seen_titles", {})
    settings = dict(DEFAULT_SETTINGS)
    settings.update(prof.get("settings") or {})
    prof["settings"] = settings
    return prof


def save_profile(prof):
    write_json(path_for("profile.json"), prof)


def slug(text):
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def derive_terms(topic):
    """Reasonable default terms when the caller supplies none."""
    s = slug(topic)
    terms = {s}
    if "-" in s:
        terms.add(s.replace("-", " "))
    return sorted(t for t in terms if t)


def split_terms(raw):
    if not raw:
        return []
    parts = [p.strip().lower() for p in raw.split(",")]
    return sorted({p for p in parts if p})


def title_key(title):
    """Normalized title. HN gets the same link submitted more than once under new ids,
    so suppressing by id alone lets the same headline come back tomorrow."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def find(entries, topic):
    key = slug(topic)
    for entry in entries:
        if slug(entry.get("topic")) == key:
            return entry
    return None


def drop(entries, topic):
    key = slug(topic)
    kept = [e for e in entries if slug(e.get("topic")) != key]
    removed = len(entries) - len(kept)
    entries[:] = kept
    return removed


# ---------------------------------------------------------------- commands


def cmd_init(args, prof):
    existed = os.path.exists(path_for("profile.json"))
    save_profile(prof)
    print("profile %s at %s" % ("present" if existed else "created", path_for("profile.json")))
    print("interests=%d probation=%d blocked=%d seen=%d"
          % (len(prof["interests"]), len(prof["probation"]), len(prof["blocked"]), len(prof["seen"])))


def cmd_show(args, prof):
    if args.json:
        print(json.dumps(prof, indent=2, ensure_ascii=False))
        return
    print("data dir: %s" % data_dir())
    print("\ninterests (%d):" % len(prof["interests"]))
    for entry in sorted(prof["interests"], key=lambda e: -e.get("weight", 0)):
        learned = set(entry.get("learned", []))
        print("  %-24s w=%.2f hits=%-4d shown=%-4d ignored=%-3d terms=%s"
              % (entry["topic"], entry.get("weight", 0), entry.get("hits", 0),
                 entry.get("shown", 0), entry.get("shown_since_hit", 0),
                 ", ".join(t + "*" if t in learned else t for t in entry.get("terms", []))))
    if any(e.get("learned") for e in prof["interests"]):
        print("  (* learned from a re-rank, not stated by you)")
    print("\nprobation (%d):" % len(prof["probation"]))
    for entry in prof["probation"]:
        print("  %-24s shown=%d since %s" % (entry["topic"], entry.get("shown", 0),
                                             entry.get("first_seen", "?")))
    print("\nblocked (%d):" % len(prof["blocked"]))
    for entry in prof["blocked"]:
        print("  %-24s terms=%s" % (entry["topic"], ", ".join(entry.get("terms", []))))
    print("\nseen ids: %d" % len(prof["seen"]))


def cmd_add(args, prof):
    topic = slug(args.topic)
    if not topic:
        die("empty topic")
    blocked = find(prof["blocked"], topic)
    if blocked and not args.force:
        die("%s is blocked. Run 'unblock %s' first, or pass --force." % (topic, topic))
    if blocked:
        drop(prof["blocked"], topic)
    terms = split_terms(args.terms) or derive_terms(topic)

    existing = find(prof["interests"], topic)
    if existing:
        before = existing.get("terms", [])
        # --replace swaps the term list without touching weight, hits or counters. Without
        # it, pruning one bad term meant forget plus unblock plus re-add, which throws away
        # everything the profile has learned about the topic.
        existing["terms"] = sorted(set(terms)) if args.replace else sorted(set(before) | set(terms))
        if existing.get("learned"):
            kept = set(existing["terms"])
            existing["learned"] = [t for t in existing["learned"] if t in kept]
        if args.weight is not None:
            existing["weight"] = args.weight
        save_profile(prof)
        dropped = sorted(set(before) - set(existing["terms"]))
        print("updated %s (w=%.2f terms=%s%s)"
              % (topic, existing["weight"], ", ".join(existing["terms"]),
                 "; dropped " + ", ".join(dropped) if dropped else ""))
        return

    promoted = drop(prof["probation"], topic)
    weight = args.weight
    if weight is None:
        weight = WEIGHT_PROMOTED if promoted else WEIGHT_DEFAULT
    prof["interests"].append({
        "topic": topic,
        "terms": terms,
        "weight": weight,
        "hits": 0,
        "shown": 0,
        "added": today(),
        "source": args.source,
        "last_hit": None,
    })
    save_profile(prof)
    print("added %s (w=%.2f source=%s terms=%s)" % (topic, weight, args.source, ", ".join(terms)))


def cmd_drop_term(args, prof):
    """Remove terms from a topic, keeping its weight and history."""
    topic = slug(args.topic)
    entry = find(prof["interests"], topic) or find(prof["probation"], topic)
    if not entry:
        die("%s is not an interest or a probation topic" % topic)
    wanted = {t.strip().lower() for t in args.terms}
    before = entry.get("terms", [])
    remaining = [t for t in before if t.lower() not in wanted]
    if not remaining:
        die("%s would have no terms left. Use 'add %s --terms ... --replace' instead."
            % (topic, topic))
    entry["terms"] = remaining
    if entry.get("learned"):
        entry["learned"] = [t for t in entry["learned"] if t.lower() not in wanted]
    save_profile(prof)
    print("dropped %s from %s (terms now: %s)"
          % (", ".join(sorted(set(before) - set(remaining))) or "nothing",
             topic, ", ".join(remaining)))


def cmd_learn(args, prof):
    """Add terms the model inferred while re-ranking, so the same miss is not repeated.

    A term list is written once at setup and ages badly. A topic never given "PostgreSQL"
    or `astral.sh` cannot match them, and the miss is invisible: it looks exactly like a
    quiet day. The model already identifies these by hand whenever it re-ranks a story
    into an interest the prescore missed, so this is where that judgement is kept.

    Learned terms are tracked separately from stated ones. They are guesses, and `show`
    marks them so a bad one can be found and dropped.
    """
    topic = slug(args.topic)
    entry = find(prof["interests"], topic) or find(prof["probation"], topic)
    if not entry:
        die("%s is not an interest or a probation topic" % topic)

    blocked = {t for b in prof["blocked"] for t in b.get("terms", [])}
    have = {t.lower() for t in entry.get("terms", [])}
    learned = entry.setdefault("learned", [])
    added, refused, full = [], [], []
    for term in split_terms(args.terms):
        if term in have:
            continue
        if term in blocked:
            refused.append(term)
        elif len(learned) >= LEARNED_CAP:
            full.append(term)
        else:
            learned.append(term)
            have.add(term)
            added.append(term)
    if added:
        entry["terms"] = sorted(set(entry.get("terms", [])) | set(added))
        save_profile(prof)

    parts = ["learned %s for %s" % (", ".join(added), topic) if added
             else "learn %s: nothing new" % topic]
    if refused:
        parts.append("refused (blocked): " + ", ".join(refused))
    if full:
        # Never silently drop them. An overlong list means the topic is drifting, and the
        # user is the one who should decide which terms still name it.
        parts.append("at the %d learned-term cap, skipped %s. Prune with 'drop-term %s <term>'"
                     % (LEARNED_CAP, ", ".join(full), topic))
    print("; ".join(parts))


def cmd_forget(args, prof):
    topic = slug(args.topic)
    entry = find(prof["interests"], topic) or find(prof["probation"], topic)
    terms = entry.get("terms", []) if entry else derive_terms(topic)
    if args.terms:
        terms = sorted(set(terms) | set(split_terms(args.terms)))
    removed = drop(prof["interests"], topic) + drop(prof["probation"], topic)
    if not find(prof["blocked"], topic):
        prof["blocked"].append({"topic": topic, "terms": terms, "removed": today()})
    save_profile(prof)
    print("forgot %s (removed from %d list(s), now blocked on terms: %s)"
          % (topic, removed, ", ".join(terms)))


def cmd_unblock(args, prof):
    topic = slug(args.topic)
    removed = drop(prof["blocked"], topic)
    save_profile(prof)
    print("unblocked %s" % topic if removed else "%s was not blocked" % topic)


def cmd_probation_add(args, prof):
    topic = slug(args.topic)
    if not topic:
        die("empty topic")
    if find(prof["blocked"], topic) or find(prof["interests"], topic):
        print("skip %s (already blocked or already an interest)" % topic)
        return
    entry = find(prof["probation"], topic)
    if entry:
        entry["shown"] = entry.get("shown", 0) + 1
        entry["last_shown"] = today()
        if args.terms:
            entry["terms"] = sorted(set(entry.get("terms", [])) | set(split_terms(args.terms)))
    else:
        prof["probation"].append({
            "topic": topic,
            "terms": split_terms(args.terms) or derive_terms(topic),
            "shown": 1,
            "first_seen": today(),
            "last_shown": today(),
        })
    save_profile(prof)
    print("probation %s (shown=%d)" % (topic, find(prof["probation"], topic)["shown"]))


def cmd_promote(args, prof):
    topic = slug(args.topic)
    entry = find(prof["probation"], topic)
    if not entry:
        die("%s is not on probation. Use 'add %s' instead." % (topic, topic))
    drop(prof["probation"], topic)
    prof["interests"].append({
        "topic": topic,
        "terms": entry.get("terms") or derive_terms(topic),
        "weight": args.weight if args.weight is not None else WEIGHT_PROMOTED,
        "hits": 0,
        "shown": entry.get("shown", 0),
        "added": today(),
        "source": "promoted",
        "last_hit": None,
    })
    save_profile(prof)
    print("promoted %s to interests" % topic)


def apply_hit(prof, topic, units):
    """Bump an interest's weight. Promotes from probation when needed."""
    entry = find(prof["interests"], topic)
    if not entry:
        if find(prof["blocked"], topic):
            return None
        if find(prof["probation"], topic):
            probation = find(prof["probation"], topic)
            drop(prof["probation"], topic)
            entry = {
                "topic": slug(topic),
                "terms": probation.get("terms") or derive_terms(topic),
                "weight": WEIGHT_PROMOTED,
                "hits": 0,
                "shown": probation.get("shown", 0),
                "added": today(),
                "source": "click-promoted",
                "last_hit": None,
            }
            prof["interests"].append(entry)
        else:
            return None
    entry["hits"] = entry.get("hits", 0) + units
    entry["weight"] = min(WEIGHT_MAX, entry.get("weight", WEIGHT_DEFAULT) + WEIGHT_PER_HIT * units)
    entry["last_hit"] = today()
    entry["shown_since_hit"] = 0  # a click clears the ignored streak
    return entry


def cmd_hit(args, prof):
    entry = apply_hit(prof, args.topic, args.n)
    if not entry:
        die("%s is not a known interest or probation topic" % args.topic)
    save_profile(prof)
    print("hit %s (w=%.2f hits=%d)" % (entry["topic"], entry["weight"], entry["hits"]))


def cmd_shown(args, prof):
    """Record that a topic got a slot in a brief. This is the only thing that can
    eventually cost it weight, so scarcity on HN never counts against it."""
    touched = 0
    for topic in args.topics:
        entry = find(prof["interests"], topic)
        if entry:
            entry["shown"] = entry.get("shown", 0) + 1
            entry["shown_since_hit"] = int(entry.get("shown_since_hit", 0)) + 1
            touched += 1
    save_profile(prof)
    print("shown bumped for %d topic(s)" % touched)


def cmd_seen(args, prof):
    day = today()
    for story_id in args.ids:
        if re.fullmatch(r"\d+", story_id):
            prof["seen"][story_id] = day
    save_profile(prof)
    print("seen: %d ids tracked" % len(prof["seen"]))


def cmd_record(args, prof):
    """Merge a rendered brief into brief.json so the tracker can resolve links."""
    payload = read_json(args.file, None)
    if payload is None:
        die("no such file: %s" % args.file)
    incoming = payload.get("items") or {}
    if not isinstance(incoming, dict):
        die("items must be an object keyed by story id")

    book = read_json(path_for("brief.json"), {"updated": None, "items": {}})
    items = book.get("items") or {}
    mode = payload.get("mode", "brief")
    # Title and URL are already known from the fetch that produced these candidates, so a
    # batch may carry ids alone. Retyping them would cost output tokens for nothing.
    cache = (read_json(path_for("candidates.json"), {}) or {}).get("items") or {}
    for story_id, item in incoming.items():
        if not re.fullmatch(r"\d+", str(story_id)):
            continue
        known = cache.get(str(story_id), {})
        item = {**known, **{k: v for k, v in item.items() if v not in (None, "", [])}}
        url = item.get("url") or ""
        hn_url = item.get("hn_url") or "https://news.ycombinator.com/item?id=%s" % story_id
        if not url.startswith(("http://", "https://")):
            url = hn_url
        items[str(story_id)] = {
            "title": item.get("title", ""),
            "url": url,
            "hn_url": hn_url,
            "topics": item.get("topics") or [],
            "new_topic": item.get("new_topic"),
            "new_terms": item.get("new_terms") or [],
            "mode": item.get("mode", mode),
            "first_seen": items.get(str(story_id), {}).get("first_seen") or stamp(),
        }

    if len(items) > BRIEF_ITEM_CAP:
        ordered = sorted(items.items(), key=lambda kv: kv[1].get("first_seen") or "")
        items = dict(ordered[-BRIEF_ITEM_CAP:])

    # Ordered id list of this batch, so `keep N` can resolve the numbers the user sees.
    batch = [str(i) for i in (payload.get("order") or list(incoming.keys()))]
    write_json(path_for("brief.json"), {
        "updated": stamp(),
        "items": items,
        "last_batch": {"mode": mode, "rendered": stamp(), "ids": batch},
    })

    marked = 0
    if args.mark_seen:
        day = today()
        for story_id, item in incoming.items():
            prof["seen"][str(story_id)] = day
            key = title_key(item.get("title"))
            if key:
                prof["seen_titles"][key] = day
            marked += 1
        save_profile(prof)

    print("recorded %d item(s), %d link(s) live%s"
          % (len(incoming), len(items), ", %d marked seen" % marked if marked else ""))


def cmd_keep(args, prof):
    """Adopt numbered items from the last rendered brief or explore list."""
    book = read_json(path_for("brief.json"), {})
    batch = (book.get("last_batch") or {}).get("ids") or []
    items = book.get("items") or {}
    if not batch:
        die("nothing to keep: no brief has been rendered yet")

    added, bumped, missing = [], {}, []
    for number in args.numbers:
        if number < 1 or number > len(batch):
            missing.append(number)
            continue
        item = items.get(batch[number - 1])
        if not item:
            missing.append(number)
            continue
        new_topic = item.get("new_topic")
        if new_topic and not find(prof["interests"], new_topic):
            if find(prof["blocked"], new_topic):
                missing.append(number)
                continue
            drop(prof["probation"], new_topic)
            prof["interests"].append({
                "topic": slug(new_topic),
                "terms": item.get("new_terms") or derive_terms(new_topic),
                "weight": WEIGHT_EXPLORE,
                "hits": 1,
                "shown": 1,
                "added": today(),
                "source": "keep",
                "last_hit": today(),
            })
            added.append(slug(new_topic))
        else:
            for topic in item.get("topics") or []:
                entry = apply_hit(prof, topic, CLICK_UNITS["article"])
                if entry:
                    bumped[entry["topic"]] = bumped.get(entry["topic"], 0) + CLICK_UNITS["article"]

    save_profile(prof)
    parts = []
    if added:
        parts.append("added " + ", ".join(added))
    if bumped:
        parts.append("bumped " + ", ".join("%s+%d" % kv for kv in sorted(bumped.items())))
    if missing:
        parts.append("no such item: " + ", ".join(str(n) for n in missing))
    print("; ".join(parts) or "nothing to do")


def cmd_clicks(args, prof):
    """Drain clicks.jsonl and turn clicks into weight."""
    log = path_for("clicks.jsonl")
    if not os.path.exists(log):
        print("no clicks pending")
        return
    with open(log, encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    if not lines:
        print("no clicks pending")
        return

    applied, promoted, ignored, repeats = {}, [], 0, 0
    # One story counts once per drain. The signal is that a story was worth opening, not
    # how many times the redirect fired, and a link reopened after a browser or terminal
    # misfire would otherwise weigh as much as genuine interest in a second story. Article
    # and comments stay separate, since those are two different signals about one story.
    counted = set()
    for line in lines:
        try:
            click = json.loads(line)
        except json.JSONDecodeError:
            ignored += 1
            continue
        key = (str(click.get("id")), click.get("kind"))
        if key in counted:
            repeats += 1
            continue
        counted.add(key)
        units = CLICK_UNITS.get(click.get("kind"), 1)

        new_topic = click.get("new_topic")
        if new_topic and not find(prof["interests"], new_topic) and not find(prof["blocked"], new_topic):
            drop(prof["probation"], new_topic)
            prof["interests"].append({
                "topic": slug(new_topic),
                "terms": click.get("new_terms") or derive_terms(new_topic),
                "weight": WEIGHT_EXPLORE,
                "hits": units,
                "shown": 1,
                "added": today(),
                "source": "explore-click",
                "last_hit": today(),
            })
            promoted.append(slug(new_topic))
            continue

        for topic in click.get("topics") or []:
            entry = apply_hit(prof, topic, units)
            if entry:
                applied[entry["topic"]] = applied.get(entry["topic"], 0) + units
            else:
                ignored += 1

    if args.dry_run:
        print("dry run: %d click(s), would bump %s, would add %s"
              % (len(lines) - repeats, applied or "nothing", promoted or "nothing"))
        return

    save_profile(prof)
    # Move drained lines to the audit log, then truncate the inbox.
    audit = path_for("clicks.applied.jsonl")
    existing = []
    if os.path.exists(audit):
        with open(audit, encoding="utf-8") as fh:
            existing = [ln.strip() for ln in fh if ln.strip()]
    with open(audit, "w", encoding="utf-8") as fh:
        for line in (existing + lines)[-APPLIED_LOG_CAP:]:
            fh.write(line + "\n")
    open(log, "w", encoding="utf-8").close()

    parts = ["%d click(s) applied" % (len(lines) - repeats)]
    if applied:
        parts.append("bumped " + ", ".join("%s+%d" % (k, v) for k, v in sorted(applied.items())))
    if promoted:
        parts.append("new interests: " + ", ".join(promoted))
    if repeats:
        parts.append("%d repeat click(s) counted once" % repeats)
    if ignored:
        parts.append("%d ignored" % ignored)
    print("; ".join(parts))


def cmd_maintain(args, prof):
    """Decay ignored interests, retire dead probation topics, prune seen ids.

    Decay is driven by impressions, never by elapsed time. A topic HN covers twice a year
    is not a weaker interest than one it covers hourly, it just has fewer chances to be
    shown. Weight only drops after the topic was actually put in front of the reader
    `decay_after_impressions` times without a single click.
    """
    threshold = int(prof["settings"].get("decay_after_impressions", 12))
    decayed = []
    for entry in prof["interests"]:
        ignored = int(entry.get("shown_since_hit", 0))
        if ignored < threshold:
            continue
        before = entry.get("weight", WEIGHT_DEFAULT)
        entry["weight"] = round(max(DECAY_FLOOR, before * DECAY_FACTOR), 4)
        entry["shown_since_hit"] = 0
        if entry["weight"] != before:
            decayed.append(entry["topic"])

    dropped = [e["topic"] for e in prof["probation"] if e.get("shown", 0) >= PROBATION_MAX_SHOWS]
    prof["probation"] = [e for e in prof["probation"] if e.get("shown", 0) < PROBATION_MAX_SHOWS]

    retention = int(prof["settings"].get("seen_retention_days", 45))
    cutoff_seen = (now().date() - timedelta(days=retention)).isoformat()
    before_seen = len(prof["seen"]) + len(prof["seen_titles"])
    prof["seen"] = {k: v for k, v in prof["seen"].items() if (v or "") >= cutoff_seen}
    prof["seen_titles"] = {k: v for k, v in prof["seen_titles"].items() if (v or "") >= cutoff_seen}

    save_profile(prof)
    print("maintain: decayed=%d dropped_probation=%d pruned_seen=%d"
          % (len(decayed), len(dropped),
             before_seen - len(prof["seen"]) - len(prof["seen_titles"])))
    if dropped:
        print("  retired: %s" % ", ".join(dropped))


def cmd_settings(args, prof):
    if not args.pairs:
        for key, value in sorted(prof["settings"].items()):
            print("%s=%s" % (key, value))
        return
    changed = []
    for pair in args.pairs:
        if "=" not in pair:
            die("expected KEY=VALUE, got %r" % pair)
        key, value = pair.split("=", 1)
        key = key.strip()
        if key not in DEFAULT_SETTINGS:
            die("unknown setting %r (known: %s)" % (key, ", ".join(sorted(DEFAULT_SETTINGS))))
        before = prof["settings"].get(key)
        for cast in (int, float, str):
            try:
                prof["settings"][key] = cast(value)
                break
            except ValueError:
                continue
        changed.append("%s %s -> %s" % (key, before, prof["settings"][key]))
    save_profile(prof)
    # Name the change. "settings updated" leaves the user to go and look for what moved.
    print("; ".join(changed))


def cmd_export(args, prof):
    print(json.dumps(prof, indent=2, ensure_ascii=False))


def cmd_import(args, prof):
    incoming = read_json(args.file, None)
    if incoming is None:
        die("no such file: %s" % args.file)
    if not isinstance(incoming, dict) or "interests" not in incoming:
        die("not an hn-brief profile export")
    incoming.setdefault("version", SCHEMA_VERSION)
    settings = dict(DEFAULT_SETTINGS)
    settings.update(incoming.get("settings") or {})
    incoming["settings"] = settings
    save_profile(incoming)
    print("imported %d interest(s) into %s" % (len(incoming["interests"]), path_for("profile.json")))


# ---------------------------------------------------------------- cli


def build_parser():
    parser = argparse.ArgumentParser(prog="profile.py", description="hn-brief interest store")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the store if absent")

    p = sub.add_parser("show", help="print the profile")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("add", help="add an interest")
    p.add_argument("topic")
    p.add_argument("--terms", help="comma separated match terms")
    p.add_argument("--weight", type=float)
    p.add_argument("--source", default="manual")
    p.add_argument("--force", action="store_true", help="add even if blocked")
    p.add_argument("--replace", action="store_true",
                   help="replace the term list instead of merging, keeping weight and history")

    p = sub.add_parser("drop-term", help="remove terms from a topic without losing its history")
    p.add_argument("topic")
    p.add_argument("terms", nargs="+")

    p = sub.add_parser("learn", help="teach a topic a term that should have matched")
    p.add_argument("topic")
    p.add_argument("--terms", required=True, help="comma separated")

    p = sub.add_parser("forget", help="remove an interest and block it")
    p.add_argument("topic")
    p.add_argument("--terms", help="extra terms to block")

    p = sub.add_parser("unblock", help="lift a block")
    p.add_argument("topic")

    p = sub.add_parser("probation-add", help="record a discovery topic")
    p.add_argument("topic")
    p.add_argument("--terms")

    p = sub.add_parser("promote", help="probation to interest")
    p.add_argument("topic")
    p.add_argument("--weight", type=float)

    p = sub.add_parser("hit", help="register a positive signal")
    p.add_argument("topic")
    p.add_argument("--n", type=int, default=1)

    p = sub.add_parser("shown", help="bump shown counters")
    p.add_argument("topics", nargs="+")

    p = sub.add_parser("seen", help="mark story ids as already shown")
    p.add_argument("ids", nargs="+")

    p = sub.add_parser("record", help="merge a rendered brief into brief.json")
    p.add_argument("--file", required=True)
    p.add_argument("--mark-seen", action="store_true",
                   help="also suppress these stories, by id and by title, in later runs")

    p = sub.add_parser("keep", help="adopt numbered items from the last rendered list")
    p.add_argument("numbers", nargs="+", type=int)

    p = sub.add_parser("clicks", help="drain clicks.jsonl and apply signals")
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("maintain", help="decay, retire, prune")

    p = sub.add_parser("settings", help="show or set settings")
    p.add_argument("pairs", nargs="*")

    sub.add_parser("export", help="dump the profile to stdout")

    p = sub.add_parser("import", help="load a profile export")
    p.add_argument("file")

    return parser


HANDLERS = {
    "init": cmd_init,
    "show": cmd_show,
    "add": cmd_add,
    "drop-term": cmd_drop_term,
    "learn": cmd_learn,
    "forget": cmd_forget,
    "unblock": cmd_unblock,
    "probation-add": cmd_probation_add,
    "promote": cmd_promote,
    "hit": cmd_hit,
    "shown": cmd_shown,
    "seen": cmd_seen,
    "record": cmd_record,
    "keep": cmd_keep,
    "clicks": cmd_clicks,
    "maintain": cmd_maintain,
    "settings": cmd_settings,
    "export": cmd_export,
    "import": cmd_import,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    prof = load_profile()
    HANDLERS[args.command](args, prof)


if __name__ == "__main__":
    main()
