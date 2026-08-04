#!/usr/bin/env python3
"""Pull Hacker News stories and split them against the interest profile.

Standard library only. Uses the HN Algolia API, which needs no key and no account:
    https://hn.algolia.com/api/v1/search?tags=front_page
    https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=...

Output is compact JSON on stdout: a profile summary, a `matched` pool scored against
the user's interests, and an `unmatched` pool ranked by heat for discovery.
The lexical score here is a prefilter. Semantic ranking happens in SKILL.md.
"""

import argparse
import http.client
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone


def _load_sibling(name, filename):
    """Load a sibling script by path. Avoids shadowing the stdlib `profile` module."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load_sibling("hn_brief_profile", "profile.py")
extract = _load_sibling("hn_brief_extract", "extract.py")

API = "https://hn.algolia.com/api/v1/"
USER_AGENT = "hn-brief (Claude Code plugin; +https://github.com/topics/claude-code-plugins)"
TIMEOUT = 20
RETRIES = 2
PAGE_SIZE = 100
MAX_PAGES = 3
DOMAIN_CAP = 2  # per domain in the discovery pool, so one busy blog cannot flood it
FRONT_PAGE_MIN_POINTS = 10
CANDIDATE_CACHE_CAP = 800  # ids stay resolvable across later fetches
URL_ONLY_PENALTY = 0.4     # a term found only in a URL slug, never in title or domain
PREFIX_MIN = 6             # term length from which a match may run on: postgres/PostgreSQL
BROAD_DF = 0.04     # a term matching more than this share of the pool stops discriminating
BROAD_FLOOR = 0.15  # even a very broad term keeps some pull, so its topic is not silenced
# Pages fetched per brief so failures cost a spare rather than a summary slot. Measured
# failure rate is 18% over 38 pages, so 8 for 4 slots is sized, not generous. Not a
# setting: the model never needs it, and every setting is paid for in the payload.
SUMMARY_FETCH_BUDGET = 8
SUMMARY_FETCH_TIMEOUT = 8  # per page, below extract.py's CLI default of 12 on purpose


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        # http.client.HTTPException covers a truncated response (IncompleteRead) and descends
        # from Exception, not from OSError or URLError, so none of the others imply it. It is
        # precisely the transient failure this retry loop exists for, and without it a
        # half-delivered Algolia response bypassed the retry and killed the brief outright.
        except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
                TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(1 + 2 * attempt)
    raise SystemExit("hn-brief: HN API unreachable (%s). Check the network and retry." % last)


def fetch_front_page():
    url = API + "search?" + urllib.parse.urlencode({"tags": "front_page", "hitsPerPage": PAGE_SIZE})
    return http_json(url).get("hits", [])


def fetch_recent(start, end, min_points, max_pages=MAX_PAGES):
    hits = []
    for page in range(max_pages):
        query = urllib.parse.urlencode({
            "tags": "story",
            "numericFilters": "created_at_i>%d,created_at_i<%d,points>%d" % (start, end, min_points),
            "hitsPerPage": PAGE_SIZE,
            "page": page,
        })
        payload = http_json(API + "search_by_date?" + query)
        batch = payload.get("hits", [])
        hits.extend(batch)
        if len(batch) < PAGE_SIZE or page + 1 >= payload.get("nbPages", 0):
            break
    return hits


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def day_start(day):
    """Local midnight for a date, as a unix timestamp."""
    return time.mktime(datetime(day.year, day.month, day.day).timetuple())


def parse_range(spec, window_hours):
    """Turn a phrase like "yesterday" or "since monday" into a time band.

    Calendar aware on purpose: "yesterday" means that whole calendar day in local time,
    not a rolling 24 to 48 hours ago, because that is what a reader means by it.
    Returns (start, end, label). No spec means the rolling default window.
    """
    now_ts = time.time()
    today = datetime.now().date()
    spec = (spec or "").strip().lower()

    if not spec or spec in ("today", "day", "24h"):
        return now_ts - window_hours * 3600, now_ts, "last %dh" % window_hours

    if spec == "yesterday":
        start = day_start(today - timedelta(days=1))
        return start, start + 86400, "yesterday"

    if spec in ("this week", "week"):
        return day_start(today - timedelta(days=today.weekday())), now_ts, "this week"

    if spec == "last week":
        monday = today - timedelta(days=today.weekday() + 7)
        return day_start(monday), day_start(monday + timedelta(days=7)), "last week"

    if spec in ("weekend", "this weekend"):
        saturday = today - timedelta(days=(today.weekday() - 5) % 7)
        return day_start(saturday), now_ts, "this weekend"

    match = re.fullmatch(r"(?:since\s+|last\s+)?(\d+)\s*(h|hours?|d|days?)", spec)
    if match:
        count = int(match.group(1))
        hours = count if match.group(2).startswith("h") else count * 24
        return now_ts - hours * 3600, now_ts, "last %d%s" % (count, "h" if hours == count else "d")

    for name in WEEKDAYS:
        if spec in (name, "since " + name, "last " + name):
            back = (today.weekday() - WEEKDAYS.index(name)) % 7 or 7
            start = day_start(today - timedelta(days=back))
            # A bare weekday means that day alone, "since <day>" means up to now.
            end = now_ts if spec.startswith("since") else start + 86400
            return start, end, spec
        if spec == "since yesterday":
            start = day_start(today - timedelta(days=1))
            return start, now_ts, "since yesterday"

    match = re.fullmatch(r"(?:since\s+)?(\d{4})-(\d{2})-(\d{2})", spec)
    if match:
        day = date(*(int(g) for g in match.groups()))
        start = day_start(day)
        return start, now_ts if spec.startswith("since") else start + 86400, spec

    raise SystemExit(
        "hn-brief: cannot read the range %r. Try today, yesterday, this week, last week, "
        "since monday, 3 days, 48h, or a date like 2026-07-26." % spec)


def title_key(title):
    """Normalized title, used to collapse the same story submitted twice."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def domain_of(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def normalize(hit):
    story_id = str(hit.get("objectID") or "")
    if not story_id.isdigit():
        return None
    title = (hit.get("title") or "").strip()
    if not title:
        return None
    hn_url = "https://news.ycombinator.com/item?id=%s" % story_id
    url = (hit.get("url") or "").strip() or hn_url
    created = hit.get("created_at_i")
    age_h = round(max(0.0, (time.time() - created) / 3600.0), 1) if created else None
    return {
        "id": story_id,
        "title": title,
        "url": url,
        # hn_url is always news.ycombinator.com/item?id=<id>, so it is derived rather than
        # carried. Every field here is paid for in context on each run.
        "domain": domain_of(url) or "news.ycombinator.com",
        "points": int(hit.get("points") or 0),
        "comments": int(hit.get("num_comments") or 0),
        "age_h": age_h,
    }


def fold(text):
    return re.sub(r"[/_\-.]+", " ", (text or "").lower())


def title_text(item):
    return fold(item["title"])


def domain_text(item):
    """Who published it. Real evidence: a post on a project's own site is about it."""
    return fold(item["domain"])


def path_text(item):
    """The URL slug. Weak evidence: news slugs name every entity a story mentions."""
    return fold(urllib.parse.urlparse(item["url"]).path)


def haystack(item):
    """Everything a term may match, used for measuring term frequency."""
    return " ".join([title_text(item), domain_text(item), path_text(item)])


def normalize_term(term):
    """Fold a term the same way haystack folds the text it is matched against.

    Without this, every dotted or hyphenated term is silently dead: `haystack` turns
    "github.com" in a URL into "github com", so the literal term "github.com" could never
    match anything.
    """
    return re.sub(r"[/_\-.]+", " ", (term or "").strip().lower()).strip()


def inflect(word):
    """Singular and plural forms of one word, so `open weights` matches `open-weight`."""
    forms = {word}
    if len(word) < 3 or any(ch.isdigit() for ch in word):
        return forms
    if word.endswith("ies") and len(word) > 4:
        forms.add(word[:-3] + "y")
    elif word.endswith("es") and len(word) > 3:
        forms.update({word[:-1], word[:-2]})
    elif word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
    else:
        forms.add(word + "s")
        if word.endswith(("s", "x", "z", "ch", "sh")):
            forms.add(word + "es")
        if word.endswith("y") and word[-2] not in "aeiou":
            forms.add(word[:-1] + "ies")
    return forms


def compile_terms(terms):
    """Pair each term with a pattern and a scope, so frequency and reach are per term."""
    compiled = []
    for raw in terms or []:
        tokens = normalize_term(raw).split()
        if not tokens:
            continue
        # ".net", ".ts", ".py" name a technology. Matching them against a URL turns every
        # smoothbrains.net and harpguitars.net into a false positive, so they stay in the
        # title. Terms that are themselves domains ("github.com") keep full reach.
        scope = "title" if raw.strip().startswith(".") else "any"
        # Only the final word is inflected. "certificate authority" should also match
        # "certificate authorities", but not "certificates authority".
        alternates = sorted(inflect(tokens[-1]), key=len, reverse=True)
        tail = "(?:%s)" % "|".join(re.escape(a) for a in alternates)
        body = "".join(re.escape(t) + r"\s+" for t in tokens[:-1]) + tail
        # A term is often only the prefix of the name actually printed: "postgres" inside
        # "PostgreSQL", "sqlite" inside "sqlite3". Refusing those is a silent miss, and a
        # miss is indistinguishable from a quiet day. Long alphabetic tokens may run on;
        # short ones may not, since "rust" would then swallow "rusty" and "go" everything.
        open_ended = len(tokens[-1]) >= PREFIX_MIN and tokens[-1].isalpha()
        # Boundary without \b, which breaks on terms like "tla+" or "c#".
        compiled.append((raw.strip().lower(),
                         re.compile(r"(?<![a-z0-9])%s%s"
                                    % (body, "" if open_ended else r"(?![a-z0-9])")), scope))
    return compiled


def term_strength(items, matchers):
    """Down-weight terms that match a large share of the day's stories.

    A term is only useful to the degree it separates one story from the rest. Site-wide
    markers ("show hn"), platform names, and single common words match a big slice of any
    day's pool, so a story matching one of them tells you almost nothing about whether the
    reader wants it. Measuring this per run keeps it self-correcting: no term list to
    maintain, and a term that is broad today but specific next month is treated correctly
    both times.
    """
    texts = [haystack(i) for i in items]
    total = len(texts) or 1
    strength = {}
    for entry, compiled in matchers:
        for term, pattern, _scope in compiled:
            df = sum(1 for text in texts if pattern.search(text)) / total
            strength[(entry["topic"], term)] = (
                1.0 if df <= BROAD_DF else max(BROAD_FLOOR, BROAD_DF / df))
    return strength


def score_item(item, matchers, strength):
    """Interest weight times how well its best matching terms discriminate.

    A term found in the title is evidence. The same term found only in a URL slug usually
    is not: news URLs name every entity a story mentions, so a story about one company
    carries a competitor's name in its path. Those matches are kept but demoted and
    flagged, because the payload no longer ships URLs for the model to judge them by.
    """
    title, domain, path = title_text(item), domain_text(item), path_text(item)
    topics, total = [], 0.0
    for entry, compiled in matchers:
        strong, weak = [], []
        for term, pattern, scope in compiled:
            value = strength.get((entry["topic"], term), 1.0)
            if pattern.search(title) or (scope != "title" and pattern.search(domain)):
                strong.append(value)
            elif scope != "title" and pattern.search(path):
                weak.append(value)
        hits = sorted(strong or weak, reverse=True)
        if not hits:
            continue
        factor = hits[0] + 0.15 * sum(hits[1:3])
        if not strong:
            factor *= URL_ONLY_PENALTY
        topic = {"topic": entry["topic"], "score": round(float(entry.get("weight", 1.0)) * factor, 3)}
        if not strong:
            topic["via"] = "url"
        topics.append(topic)
        total += topic["score"]
    topics.sort(key=lambda t: -t["score"])
    return topics, round(total, 3)


def heat(item):
    age = item["age_h"] if item["age_h"] is not None else 12.0
    return round((item["points"] + 2 * item["comments"]) / ((age + 2) ** 0.6), 2)


def slim(item):
    """Emit only what the brief actually renders.

    Everything in this payload is read into the model's context on every run, so ranking
    scratch values (heat) stay out, and so do URLs: `extract.py` and `profile.py record`
    both resolve those from the candidate cache by id, which keeps ~55 bytes per candidate
    out of the context and out of what the model has to type back.
    """
    out = {
        "id": item["id"],
        "title": item["title"],
        "domain": item["domain"],
        "points": item["points"],
        "comments": item["comments"],
        "age_h": round(item["age_h"]) if item["age_h"] is not None else None,
    }
    if item.get("topics"):
        out["topics"] = [t["topic"] for t in item["topics"]]
        out["score"] = round(item["score"], 2)
        via = [t["topic"] for t in item["topics"] if t.get("via") == "url"]
        if via:
            out["matched_via_url"] = via
    if item.get("probation_hint"):
        out["probation_hint"] = item["probation_hint"]
    if item.get("breakout"):
        out["breakout"] = True
    return out


TITLE_STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "your", "you",
                   "how", "why", "what", "when", "are", "was", "its", "has", "new",
                   "show", "ask", "tell", "hn", "using", "into", "about", "after"}


def distinctive_tokens(title, pool_frequency):
    """Words specific enough to identify a subject rather than a genre."""
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+.#-]{3,}", title.lower())
            if w not in TITLE_STOPWORDS and pool_frequency.get(w, 0) <= 2}


def collapse_same_subject(items, pool_frequency):
    """Drop a second story about the same thing, keeping the busier one.

    Title dedupe already catches identical resubmissions. This catches the other shape:
    a release and its technical report, posted separately, both large enough to reach the
    breakout tier and between them consuming every slot meant for distinct subjects.
    """
    kept, claimed = [], set()
    for item in sorted(items, key=lambda i: -i["points"]):
        tokens = distinctive_tokens(item["title"], pool_frequency)
        if tokens and tokens & claimed:
            continue
        claimed |= tokens
        kept.append(item)
    return kept


def cap_by_domain(items, cap=DOMAIN_CAP):
    seen, kept = {}, []
    for item in items:
        count = seen.get(item["domain"], 0)
        if count >= cap:
            continue
        seen[item["domain"]] = count + 1
        kept.append(item)
    return kept


def summary_shortlist(matched, breakout, unmatched, settings):
    """Ordered summary candidates: qualifiers, breadth-first over topics.

    Only qualifiers are eligible, the same rule SKILL.md used to state: a `matched` item at
    `score >= summarize_min_score`, or anything anywhere at `points >= summarize_min_points`.
    The pool is larger than the cap on essentially every run, so this ordering, not the cap,
    is what decides which stories get read.

    Breadth-first because `score` is a lexical prefilter and cannot separate contenders once
    the pool exceeds the cap. Ordering by it instead spends every slot on whichever interest
    carries the most weight and the longest term list, which on the profile this was measured
    against takes all four slots on most days and leaves large stories in other interests
    unread.

    Takes full item records, not slimmed ones, so `topics` is still a list of dicts here.
    """
    min_score = float(settings.get("summarize_min_score", 1.5))
    min_points = int(settings.get("summarize_min_points", 400))

    buckets = {}
    for items, scored in ((matched, True), (breakout, False), (unmatched, False)):
        for item in items:
            score = float(item.get("score") or 0.0)
            if not ((scored and score >= min_score) or item["points"] >= min_points):
                continue
            topics = item.get("topics") or []
            # None is one shared bucket for everything with no stated interest behind it,
            # which is every breakout and every discovery candidate.
            buckets.setdefault(topics[0]["topic"] if topics else None, []).append(item)

    for topic, group in buckets.items():
        group.sort(key=(lambda i: -i["points"]) if topic is None
                   else (lambda i: (-float(i.get("score") or 0.0), -i["points"])))

    ordered, depth = [], 0
    while any(len(group) > depth for group in buckets.values()):
        wave = [group[depth] for group in buckets.values() if len(group) > depth]
        wave.sort(key=lambda i: (-float(i.get("score") or 0.0), -i["points"]))
        ordered.extend(wave)
        depth += 1
    return ordered


def fetch_summaries(matched, breakout, unmatched, settings):
    """Shortlist, fetch and return `summaries` for the payload. Never raises.

    Extraction reaches third party sites, so it is the least reliable thing in a brief. It
    used to sit in its own command, where a network problem cost summaries and nothing else;
    folding it into the brief has to preserve that or it trades two model turns for a brief
    that a single unreachable host can destroy.

    Contained but not silent. Swallowing everything would also swallow a bug in the
    shortlist, and summaries would then disappear from every brief with nothing to show for
    it. The payload is stdout, so the warning goes to stderr where it cannot corrupt it.
    """
    try:
        # A story with no link of its own carries the HN item page as its url, and that is
        # the one thing extract.py refuses by construction. Such an item can still qualify
        # on points, and the budget exists so failures cost a spare rather than a summary
        # slot, so spending a slot on a certain failure defeats what it is for.
        shortlist = [i for i in summary_shortlist(matched, breakout, unmatched, settings)
                     if not domain_of(i["url"]).endswith("news.ycombinator.com")]
        results = extract.extract_many([(i["id"], i["url"]) for i in shortlist[:SUMMARY_FETCH_BUDGET]],
                                       want=int(settings.get("summarize_max", 4)),
                                       timeout=SUMMARY_FETCH_TIMEOUT)
        return [{"id": r["id"], "text": r["text"]} for r in results if r.get("id")]
    except Exception as exc:
        print("hn-brief: summaries skipped, extraction failed (%s: %s)"
              % (type(exc).__name__, exc), file=sys.stderr)
        return []


def run(args):
    prof = store.load_profile()
    settings = prof["settings"]
    if args.hours:
        now_ts = time.time()
        start, end, label = now_ts - args.hours * 3600, now_ts, "last %dh" % args.hours
    elif args.command == "explore" and not args.range:
        now_ts = time.time()
        start, end, label = now_ts - 72 * 3600, now_ts, "last 72h"
    else:
        start, end, label = parse_range(args.range, settings["window_hours"])
    hours = (end - start) / 3600.0
    # A wider range holds proportionally more of everything, so the candidate caps and the
    # page budget scale with it rather than showing one day's worth of a week.
    scale = max(1, min(3, round(hours / 24.0)))
    # Discovery floor: what an unmatched story must clear to be worth a look.
    min_points = args.min_points if args.min_points is not None else (
        60 if args.command == "explore" else settings["min_points"])
    # Query floor: deliberately lower, so niche stories that match a stated interest
    # still reach the matched pool. Explore never uses the low floor.
    query_points = min_points if args.command == "explore" else min(
        min_points, int(settings.get("min_points_matched", 8)))

    interests = prof["interests"]
    matchers = [(e, compile_terms(e.get("terms"))) for e in interests]
    blocked = [(e, compile_terms(e.get("terms"))) for e in prof["blocked"]]
    probation = [(e, compile_terms(e.get("terms"))) for e in prof["probation"]]
    seen_ids = set(prof["seen"].keys())
    seen_titles = set(prof.get("seen_titles", {}).keys())

    raw = fetch_front_page() + fetch_recent(start, end, query_points, MAX_PAGES * scale)

    # The front_page tag is historical, so that query returns stories that were on the
    # front page at some point, not only current ones. Everything is held to the requested
    # band and a light score floor, otherwise a 3-month-old 6-point story lands in the brief.
    front_floor = max(FRONT_PAGE_MIN_POINTS, query_points)
    now_ts = time.time()

    items, dupes, blocked_out, seen_out, stale = {}, 0, 0, 0, 0
    by_title, counted = {}, set()
    for hit in raw:
        item = normalize(hit)
        if item is None:
            continue
        if item["id"] in items or item["id"] in counted:
            dupes += 1
            continue
        counted.add(item["id"])
        # Suppress by title as well as id: the same headline gets resubmitted under a
        # new id, and having already read it, the reader does not want it back.
        if item["id"] in seen_ids or title_key(item["title"]) in seen_titles:
            seen_out += 1
            continue
        created = now_ts - (item["age_h"] or 0) * 3600
        if not (start <= created <= end) or item["points"] < front_floor:
            stale += 1
            continue
        text = haystack(item)
        hit_block = next((e["topic"] for e, pats in blocked
                          if any(p.search(text) for _, p, _s in pats)), None)
        if hit_block:
            blocked_out += 1
            continue

        # HN gets the same link submitted more than once. Keep the busiest copy.
        key = title_key(item["title"])
        twin = by_title.get(key)
        if twin is not None:
            if item["points"] <= items[twin]["points"]:
                dupes += 1
                continue
            del items[twin]
            dupes += 1
        by_title[key] = item["id"]
        items[item["id"]] = item

    strength = term_strength(list(items.values()), matchers)
    matched, unmatched = [], []
    for item in items.values():
        topics, total = score_item(item, matchers, strength)
        item["heat"] = heat(item)
        if topics:
            item["topics"] = topics
            item["score"] = total
            matched.append(item)
        elif item["points"] >= min_points:
            # Unmatched stories are held to the higher discovery bar. Below it they are
            # neither relevant nor popular enough to spend a slot on.
            text = haystack(item)
            hints = [e["topic"] for e, pats in probation
                     if any(p.search(text) for _, p, _s in pats)]
            if hints:
                item["probation_hint"] = hints
            unmatched.append(item)

    matched.sort(key=lambda i: (-i["score"], -i["heat"]))
    unmatched.sort(key=lambda i: (-len(i.get("probation_hint", [])), -i["heat"]))
    unmatched = cap_by_domain(unmatched)

    # Breakout tier: unmatched stories big enough that not showing them would be a
    # failure of the brief, whatever they are about. Held apart from the discovery
    # slots, which are chosen for adjacency rather than size.
    breakout = []
    if args.command != "explore":
        floor = int(settings.get("breakout_points", 250))
        slots = int(settings.get("breakout_slots", 3))
        for item in sorted(unmatched, key=lambda i: -i["points"]):
            if item["points"] < floor or len(breakout) >= (slots + 1) * scale:
                break
            item["breakout"] = True
            breakout.append(item)
        frequency = {}
        for candidate in items.values():
            for word in set(re.findall(r"[a-z0-9][a-z0-9+.#-]{3,}", candidate["title"].lower())):
                frequency[word] = frequency.get(word, 0) + 1
        breakout = collapse_same_subject(breakout, frequency)[:slots]
        taken = {i["id"] for i in breakout}
        unmatched = [i for i in unmatched if i["id"] not in taken]
        # More stories clear the floor than there are slots. Those that lose one stay as
        # ordinary discovery candidates, so the flag has to come off with the slot: a
        # `breakout` item sitting in `unmatched` is a contradiction the reader cannot act
        # on, since the two pools are rendered as different sections.
        for item in unmatched:
            item.pop("breakout", None)

    if args.command == "explore":
        matched = []
        unmatched = unmatched[:args.limit or settings["explore_size"] * 3]
    else:
        # Enough candidates for the model to choose well, not the whole day's HN.
        matched = matched[:args.limit or settings["brief_size"] * 2 * scale]
        unmatched = unmatched[:settings["discovery_slots"] * 3 * scale]

    # Before slim(), which strips `url` deliberately, and explore only ever renders one line
    # per item so fetching for it would spend 8 requests on text nothing reads.
    summaries = [] if args.command == "explore" else fetch_summaries(
        matched, breakout, unmatched, settings)

    # Terms so common today that they barely narrow anything down. Worth telling the user.
    broad_terms = {}
    for (topic, term), value in sorted(strength.items()):
        if value < 1.0:
            broad_terms.setdefault(topic, []).append(term)

    payload = {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "mode": args.command,
        "range": {"label": label, "hours": round(hours), "days": scale,
                  "start": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                  "end": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")},
        "min_points": min_points,
        # Tracker settings are omitted: the live port comes from `tracker.py ensure`, and a
        # stale preference here would contradict it. summarize_* went with them once the
        # script started selecting: the model no longer reads either threshold or the cap.
        "settings": {k: v for k, v in settings.items()
                     if not k.startswith(("tracker_", "summarize_"))},
        "profile": {
            # Topic names and weights only. The match terms already did their work in the
            # prescore, and repeating them here would cost context for nothing.
            "interests": {e["topic"]: round(float(e.get("weight", 1.0)), 2) for e in interests},
            "probation": [e["topic"] for e in prof["probation"]],
            "blocked": [e["topic"] for e in prof["blocked"]],
            "broad_terms": broad_terms,
        },
        "counts": {
            "fetched": len(raw),
            "unique": len(items),
            "duplicates": dupes,
            "dropped_stale": stale,
            "dropped_seen": seen_out,
            "dropped_blocked": blocked_out,
            "matched": len(matched),
            "breakout": len(breakout),
            "unmatched": len(unmatched),
        },
        "matched": [slim(i) for i in matched],
        "breakout": [slim(i) for i in breakout],
        "unmatched": [slim(i) for i in unmatched],
    }
    if summaries:
        payload["summaries"] = summaries
    # Full records for everything emitted, so later steps can look up by id alone. Merged
    # rather than replaced: an explore run between a brief and its extract call must not
    # invalidate the ids that brief just rendered.
    cache = (store.read_json(store.path_for("candidates.json"), {}) or {}).get("items") or {}
    cache.update({i["id"]: {"title": i["title"], "url": i["url"],
                            "hn_url": "https://news.ycombinator.com/item?id=%s" % i["id"],
                            "domain": i["domain"]}
                  for i in matched + breakout + unmatched})
    if len(cache) > CANDIDATE_CACHE_CAP:
        cache = dict(list(cache.items())[-CANDIDATE_CACHE_CAP:])
    store.write_json(store.path_for("candidates.json"),
                     {"updated": payload["generated"], "items": cache})

    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def run_terms(args):
    """Report how well each interest term discriminates against today's stories.

    Use this when writing terms for a new interest. A term matching a large share of the
    pool will pull in noise no matter how good the rest of the profile is.
    """
    prof = store.load_profile()
    settings = prof["settings"]
    hours = args.hours or settings["window_hours"]
    floor = min(int(settings["min_points"]), int(settings.get("min_points_matched", 8)))

    extra = [{"topic": args.topic or "candidate",
              "terms": [t.strip() for t in (args.terms or "").split(",") if t.strip()],
              "weight": 1.0}] if args.terms else []
    # Scope to what was asked about. Re-reporting every existing interest on each check
    # buries the answer and costs context in the place the docs promise cheapness.
    if extra and not args.all:
        interests = extra
    elif args.topic and not args.all:
        interests = [e for e in prof["interests"] if e["topic"] == args.topic] or extra
    else:
        interests = prof["interests"] + extra
    if not interests:
        raise SystemExit("hn-brief: nothing to check. Pass --terms, or --all for the whole profile.")

    now_ts = time.time()
    raw = fetch_front_page() + fetch_recent(now_ts - hours * 3600, now_ts, floor)
    items, seen = [], set()
    for hit in raw:
        item = normalize(hit)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)

    matchers = [(e, compile_terms(e.get("terms"))) for e in interests]
    strength = term_strength(items, matchers)
    texts = [haystack(i) for i in items]
    total = len(texts) or 1

    report = {}
    for entry, compiled in matchers:
        rows = []
        for term, pattern, _scope in compiled:
            hits = sum(1 for text in texts if pattern.search(text))
            rows.append({
                "term": term,
                "matches": hits,
                "share": round(hits / total, 3),
                "strength": round(strength[(entry["topic"], term)], 2),
                "verdict": "broad" if strength[(entry["topic"], term)] < 1.0 else
                           ("dead" if hits == 0 else "ok"),
            })
        report[entry["topic"]] = rows

    # A topic reachable only through broad terms cannot be saved by better wording: nothing
    # names it. All-dead is different, and is just a quiet day, so it is not flagged.
    verdicts = {t: {r["verdict"] for r in rows} for t, rows in report.items()}
    broad_only = sorted(t for t, v in verdicts.items() if "broad" in v and "ok" not in v)

    payload = {"pool": total, "window_hours": hours, "terms": report}
    if broad_only:
        payload["broad_only"] = broad_only
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fetch.py", description="pull and prescore HN stories")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("brief", help="matched pool, breakout tier, discovery candidates")
    p.add_argument("--range", help="today, yesterday, this week, since monday, 3 days, 48h, or a date")
    p.add_argument("--hours", type=int)
    p.add_argument("--min-points", type=int)
    p.add_argument("--limit", type=int, help="matched cap, default brief_size * 2")

    p = sub.add_parser("explore", help="discovery candidates only")
    p.add_argument("--range", help="same range grammar as brief")
    p.add_argument("--hours", type=int)
    p.add_argument("--min-points", type=int)
    p.add_argument("--limit", type=int, help="default explore_size * 3")

    p = sub.add_parser("terms", help="how well each interest term discriminates today")
    p.add_argument("--hours", type=int)
    p.add_argument("--terms", help="comma separated candidate terms to test before adding")
    p.add_argument("--topic", help="label for the candidate terms, or an existing topic to check")
    p.add_argument("--all", action="store_true", help="check every interest in the profile")

    args = parser.parse_args(argv)
    (run_terms if args.command == "terms" else run)(args)


if __name__ == "__main__":
    main()
