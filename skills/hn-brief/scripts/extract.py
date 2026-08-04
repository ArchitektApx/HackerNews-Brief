#!/usr/bin/env python3
"""Fetch article pages and reduce them to plain text, for summarizing a few top items.

Standard library only. Comments are never fetched, only the linked article.

Deliberately conservative, because this reaches out to third party sites rather than one
known API:
  * honors robots.txt for the plugin's own user agent, one lookup per host per run
  * only text/html, only http and https, response body capped
  * per URL timeout, and one failure never blocks the others
  * strips script, style, nav, header, footer, aside, and form content before extracting

Usage:
    extract.py URL [URL ...] [--max-chars 2000] [--timeout 12] [--workers 4]
    extract.py --ids 49067854 49071365          # resolved from the candidate cache

Emits a JSON array on stdout, one object per URL in the order given.
"""

import argparse
import http.client
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

def _load_sibling(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load_sibling("hn_brief_profile", "profile.py")

USER_AGENT = "hn-brief (Claude Code plugin; +https://github.com/ArchitektApx/HackerNews-Brief)"
MAX_BYTES = 1_500_000
# How much article text a summary is written from. Lives here rather than in fetch.py
# because it is the default of extract_many below and duplicating the number in both
# modules is worse than either home. Gate 2 settled it at 13 of 14 truncations usable.
SUMMARY_TEXT_CHARS = 1200
SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}
BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

_robots_cache = {}

PROSE_BLOCK = 200  # a block this long is article text, not navigation
# An interactive page (a demo, a visualization) serves a disclaimer and renders the rest in
# JS, clearing any low floor while saying nothing. Failing here is cheap: the caller has
# spare ids and the item falls back to a one-line note, which beats a summary of a banner.
MIN_TEXT = 600
# Length alone cannot tell prose from furniture: a code-hosting page ships several hundred
# characters of menu labels ("Search syntax tips", "Provide feedback") and clears any floor
# worth setting. Menu labels are not sentences, so count sentence endings instead.
MIN_SENTENCES = 3
SENTENCE_END = re.compile(r"[.!?][\s\"')\]]|[.!?]$")
KEEP_BEFORE = 80   # shorter leading blocks than this are chrome and get dropped

# Long enough to pass for prose, but never worth summarizing from.
BOILERPLATE = re.compile(
    r"you signed (in|out) with another tab|reload to refresh your session"
    r"|skip to (main )?content|enable javascript|accept (all )?cookies"
    r"|we use cookies|subscribe to (our|the) newsletter|sign in to|create (a free )?account",
    re.IGNORECASE)


def strip_chrome(text):
    """Drop the navigation rubble that sits above the first real paragraph.

    Site headers ("Skip to content", "Sign in", menu items) arrive as many tiny blocks.
    They are worthless to summarize from and every one of them costs context, so leading
    blocks are dropped until the first substantial one.
    """
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    blocks = [b for b in blocks if not BOILERPLATE.search(b)]
    first = next((i for i, b in enumerate(blocks) if len(b) >= PROSE_BLOCK), None)
    if first is None:
        return "\n\n".join(blocks)
    kept_head = [b for b in blocks[:first] if len(b) >= KEEP_BEFORE]
    return "\n\n".join(kept_head + blocks[first:])


class Extractor(HTMLParser):
    """Minimal readability: drop chrome tags, keep the text, remember title and description."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.chunks = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and not self.description:
            data = dict(attrs)
            key = (data.get("property") or data.get("name") or "").lower()
            if key in ("og:description", "description", "twitter:description"):
                self.description = (data.get("content") or "").strip()
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        if data.strip():
            self.chunks.append(data)

    def text(self):
        raw = "".join(self.chunks)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return strip_chrome(raw.strip())


def robots_allows(url, timeout):
    """One robots.txt lookup per host, cached. Failure to read robots means allowed."""
    parts = urllib.parse.urlparse(url)
    host = "%s://%s" % (parts.scheme, parts.netloc)
    if host not in _robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(host + "/robots.txt")
        try:
            request = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                parser.parse(resp.read(200_000).decode("utf-8", "replace").splitlines())
        except Exception:
            parser = None
        _robots_cache[host] = parser
    parser = _robots_cache[host]
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, url)


def extract_one(url, max_chars, timeout):
    result = {"url": url, "ok": False, "title": "", "description": "", "text": "", "reason": ""}

    if not url.startswith(("http://", "https://")):
        result["reason"] = "unsupported scheme"
        return result
    if urllib.parse.urlparse(url).netloc.endswith("news.ycombinator.com"):
        result["reason"] = "hn thread, not an article"
        return result

    try:
        if not robots_allows(url, timeout):
            result["reason"] = "disallowed by robots.txt"
            return result
    except Exception as exc:
        result["reason"] = "robots check failed: %s" % exc
        return result

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en,de;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype:
                result["reason"] = "not html (%s)" % (ctype.split(";")[0] or "unknown")
                return result
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read(MAX_BYTES).decode(charset, "replace")
    # http.client.HTTPException, which covers IncompleteRead and a truncated or malformed
    # response line, descends from Exception rather than from OSError or URLError, so it is
    # not implied by any of the others. Leaving it out let one truncated page escape this
    # handler entirely and take every summary in the brief with it.
    except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
            TimeoutError, OSError, ValueError) as exc:
        result["reason"] = "fetch failed: %s" % exc
        return result

    parser = Extractor()
    try:
        parser.feed(body)
    except Exception as exc:
        result["reason"] = "parse failed: %s" % exc
        return result

    text = parser.text()
    sentences = len(SENTENCE_END.findall(text))
    if len(text) < MIN_TEXT or sentences < MIN_SENTENCES:
        result["reason"] = ("only %d chars in %d sentence(s), page furniture rather than an "
                            "article" % (len(text), sentences))
        result["title"] = parser.title.strip()
        result["description"] = parser.description
        return result

    result.update({
        "ok": True,
        "title": parser.title.strip(),
        "description": parser.description,
        "text": text[:max_chars],
        "chars": len(text),
    })
    return result


def resolve_ids(ids):
    """Pair story ids with article URLs from the cache fetch.py writes.

    The pairing is returned, not just the URLs, because the caller has to hand the id back
    with each result. The brief payload ships no URLs, so an id is the only handle the
    reader has on a story, and a bare list of results could not be matched to anything.
    """
    book = store.read_json(store.path_for("candidates.json"), {"items": {}})
    items = book.get("items") or {}
    pairs, missing = [], []
    for story_id in ids:
        entry = items.get(str(story_id))
        if entry and entry.get("url"):
            pairs.append((str(story_id), entry["url"]))
        else:
            missing.append(str(story_id))
    if missing:
        raise SystemExit("hn-brief: unknown story id(s): %s. They must come from the "
                         "candidates of a brief or explore run." % ", ".join(missing))
    return pairs


def extract_all(pairs, max_chars=SUMMARY_TEXT_CHARS, timeout=12, workers=6):
    """Fetch (id, url) pairs in parallel and return every result, in the order given.

    The id travels with its result rather than being matched back by URL afterwards. The
    brief payload ships no URLs, so an id is the only handle a later step has on a story,
    and pairing by position is correct where pairing by URL quietly breaks the moment the
    same link is submitted twice under two ids.

    A story id may be None, which is what the CLI passes for a bare URL argument.
    """
    pairs = list(pairs)
    if not pairs:
        return []

    def one(pair):
        # "One failure never blocks the others" is this module's contract, and it has to hold
        # against anything a third party site can provoke, not only the errors named in
        # extract_one. Anything unhandled becomes that page's failure, never the batch's.
        try:
            return extract_one(pair[1], max_chars, timeout)
        except Exception as exc:
            return {"url": pair[1], "ok": False, "title": "", "description": "", "text": "",
                    "reason": "extract failed: %s: %s" % (type(exc).__name__, exc)}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(pairs)))) as pool:
        results = list(pool.map(one, pairs))
    for (story_id, _url), result in zip(pairs, results):
        if story_id is not None:
            result["id"] = story_id
    return results


def extract_many(pairs, want, max_chars=SUMMARY_TEXT_CHARS, timeout=12, workers=6):
    """Fetch pairs of (id, url) in parallel and return the first `want` successes in order.

    Failures are dropped rather than reported, which is what the brief run wants: it passes
    a shortlist longer than `want` so a page behind robots.txt or a paywall costs a spare
    instead of a summary slot, and a refusal the model cannot act on is not worth shipping.
    The CLI wants the opposite and uses extract_all directly.
    """
    return [r for r in extract_all(pairs, max_chars, timeout, workers) if r["ok"]][:want]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="extract.py", description="article text for summaries")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--ids", nargs="+", help="story ids, resolved from the candidate cache")
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--want", type=int,
                        help="stop after this many succeed; pass spare ids to backfill "
                             "the ones robots.txt or a paywall will refuse")
    args = parser.parse_args(argv)

    pairs = [(None, url) for url in args.urls] + (resolve_ids(args.ids) if args.ids else [])
    if not pairs:
        raise SystemExit("hn-brief: nothing to extract. Pass URLs or --ids.")

    results = extract_all(pairs, args.max_chars, args.timeout, args.workers)

    if args.want:
        # Keep the first N that worked, in the order asked for, then name the ones that did
        # not. Dropping them entirely left the caller unable to tell which ids it got back,
        # since a result is identified by URL and the brief payload carries none.
        good = [r for r in results if r["ok"]][:args.want]
        if len(good) < args.want:
            results = good + [r for r in results if not r["ok"]]
        else:
            failed = [{"id": r.get("id"), "url": r["url"], "ok": False, "reason": r["reason"]}
                      for r in results if not r["ok"]]
            results = good + failed

    json.dump(results, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
