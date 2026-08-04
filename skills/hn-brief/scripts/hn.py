#!/usr/bin/env python3
"""One command per phase of a run, so a single permission rule covers the plugin.

Claude Code matches a permission rule against every subcommand of a compound Bash call,
and the rule it saves carries that call's arguments. A preamble written as five statements
therefore asks five times, and the rule saved for `shown <interest>` never matches the
`shown <other-interest>` of the next run, so the asking never settles. Worse, only five
rules are kept per approval, so a longer write-back silently keeps prompting forever.

Each phase here is one command with a fixed shape. `Bash(python3 *hn.py *)` covers all of
them, permanently, and survives a version bump because the path is not in the rule.

This is a dispatcher and nothing else. Every decision still lives in the module it belongs
to, and each step is the same call the module's own CLI would make.

Standard library only.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys


def _load_sibling(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load_sibling("hn_brief_profile", "profile.py")
fetch = _load_sibling("hn_brief_fetch", "fetch.py")
tracker = _load_sibling("hn_brief_tracker", "tracker.py")
extract = _load_sibling("hn_brief_extract", "extract.py")


def run(module, argv):
    """Call a module's CLI and return what it printed, so steps can be composed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main(argv)
    return buffer.getvalue().strip()


def preamble():
    """Everything a run needs before it can rank: apply clicks, age the profile, get links.

    Failures here are reported, never fatal. A tracker that will not start costs clickable
    links, which is worth saying out loud, but it is not worth losing the brief over.

    `HN_BRIEF_REMOTE` means the browser that opens the brief is not on this machine, so a
    loopback redirect is unreachable by definition. Skipping `ensure` rather than discarding
    its result is the point: no process, no startup poll, no `tracker.json` nobody can use.
    """
    notices = {}
    run(store, ["init"])
    for step in ("clicks", "maintain"):
        line = run(store, [step])
        if line:
            notices[step] = line
    if os.environ.get("HN_BRIEF_REMOTE"):
        notices["tracker"] = {"status": "remote", "port": None}
        return notices
    try:
        notices["tracker"] = json.loads(run(tracker, ["ensure"]))
    except (SystemExit, ValueError, json.JSONDecodeError):
        notices["tracker"] = {"status": "failed", "port": None}
    return notices


def cmd_run(args, mode):
    notices = preamble()
    payload = json.loads(run(fetch, [mode, "--range", args.range or ""]))
    payload["notices"] = notices
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def cmd_apply(args):
    """Apply a rendered list from one JSON argument, instead of a shell line per decision.

    The batch already names every topic decision, so restating them as arguments only
    invites a quoting mistake. `mode` decides whether these stories are suppressed later:
    a brief has been read, while an explore list is an offer the user may still take up.

    Expanding here before anything runs means a malformed batch fails loud and leaves the
    store untouched, rather than half of it applied.
    """
    try:
        batch = store.expand_batch(json.loads(args.batch))
    except json.JSONDecodeError as exc:
        raise SystemExit("hn-brief: --batch is not valid JSON: %s" % exc)
    explore = batch["mode"] == "explore"

    out = [run(store, ["record", "--batch", args.batch]
               + ([] if explore else ["--mark-seen"]))]

    if batch["shown"]:
        out.append(run(store, ["shown"] + list(batch["shown"])))
    # Explore is opt-in: appearing in the list is an offer, not acceptance, so nothing it
    # shows may enter probation or be suppressed later. The topics are still recorded, since
    # `keep N` needs them to know what a click would adopt.
    for topic, terms in (() if explore else batch["probation"].items()):
        out.append(run(store, ["probation-add", topic, "--terms", ",".join(terms)]))
    # After probation-add, because `learn` dies for a topic that does not exist yet and a
    # topic offered this run may receive learned terms in the same apply.
    for topic, terms in batch["learn"].items():
        out.append(run(store, ["learn", topic, "--terms", ",".join(terms)]))

    print("\n".join(line for line in out if line))


USAGE = """hn.py, the single entry point for hn-brief.

  brief|explore [--range R]   preamble and candidates, as one payload
  apply --batch JSON          record a rendered list and every decision in it
  extract ...                 article text, arguments as extract.py
  terms ...                   term verdicts, arguments as fetch.py terms
  tracker ...                 arguments as tracker.py
  <anything else>             passed to profile.py unchanged: show, add, forget,
                              keep, drop-term, learn, settings, export, import

Everything routes through here so one permission rule covers the plugin.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return

    command, rest = argv[0], argv[1:]
    if command in ("brief", "explore"):
        parser = argparse.ArgumentParser(prog="hn.py " + command)
        parser.add_argument("--range", help="today, yesterday, this week, since monday, 48h, a date")
        cmd_run(parser.parse_args(rest), command)
    elif command == "apply":
        parser = argparse.ArgumentParser(prog="hn.py apply")
        parser.add_argument("--batch", required=True)
        cmd_apply(parser.parse_args(rest))
    elif command == "extract":
        extract.main(rest)
    elif command == "terms":
        fetch.main(argv)
    elif command == "tracker":
        tracker.main(rest)
    else:
        # Anything unclaimed is a profile.py subcommand. Its parser stays the one source
        # of truth for those flags, and an unknown name gets its error message, not ours.
        store.main(argv)


if __name__ == "__main__":
    main()
