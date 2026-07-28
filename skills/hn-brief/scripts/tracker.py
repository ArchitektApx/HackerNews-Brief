#!/usr/bin/env python3
"""Localhost click tracker for hn-brief.

A terminal cannot report a click back, so brief links point at this server instead of
straight at Hacker News. It logs the click and redirects to the real destination.

Security properties, on purpose:
  * binds 127.0.0.1 only, never 0.0.0.0, so nothing on the LAN can reach it
  * the redirect target is never taken from the request. It is looked up in brief.json,
    which only `profile.py record` writes, and re-validated as http/https before use
  * story ids must match ^[0-9]+$, unknown ids get a 404
  * the only write this process performs is an append to clicks.jsonl
  * it shuts itself down after an idle period rather than lingering forever

Standard library only.
"""

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _load_sibling(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load_sibling("hn_brief_profile", "profile.py")

ID_RE = re.compile(r"^/(c|t)/([0-9]+)$")
PORT_SCAN = 20
STARTUP_WAIT = 6.0

_state = {
    "last_activity": time.time(),
    "brief_mtime": 0.0,
    "brief_items": {},
}
_lock = threading.Lock()


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def brief_items():
    """Reload brief.json when it changes so new briefs work without a restart."""
    path = store.path_for("brief.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if mtime != _state["brief_mtime"]:
            data = store.read_json(path, {"items": {}})
            _state["brief_items"] = data.get("items") or {}
            _state["brief_mtime"] = mtime
        return _state["brief_items"]


def log_click(story_id, kind, item):
    record = {
        "ts": stamp(),
        "id": story_id,
        "kind": kind,
        "mode": item.get("mode", "brief"),
        "title": item.get("title", ""),
        "topics": item.get("topics") or [],
        "new_topic": item.get("new_topic"),
    }
    if item.get("new_terms"):
        record["new_terms"] = item["new_terms"]
    with _lock:
        with open(store.path_for("clicks.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "hn-brief"
    sys_version = ""

    def log_message(self, fmt, *args):
        """Silence stdout. Access noise is not worth a notification or a log file."""

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802  (http.server API)
        _state["last_activity"] = time.time()
        path = self.path.split("?", 1)[0]

        if path == "/health":
            # `data` lets a caller confirm this server serves its own profile and not
            # another install that happened to grab the port first.
            payload = json.dumps({"ok": True, "service": "hn-brief",
                                  "data": store.data_dir(), "links": len(brief_items())})
            self._send(200, payload.encode("utf-8"), {"Content-Type": "application/json"})
            return

        match = ID_RE.match(path)
        if not match:
            self._send(404, b"not found\n", {"Content-Type": "text/plain"})
            return

        kind_code, story_id = match.groups()
        item = brief_items().get(story_id)
        if not item:
            self._send(404, b"unknown story id\n", {"Content-Type": "text/plain"})
            return

        kind = "article" if kind_code == "c" else "comments"
        target = item.get("url") if kind == "article" else item.get("hn_url")
        if not isinstance(target, str) or not target.startswith(("http://", "https://")):
            target = "https://news.ycombinator.com/item?id=%s" % story_id

        try:
            log_click(story_id, kind, item)
        except OSError:
            pass  # never block the redirect on a logging failure

        self._send(302, b"", {"Location": target, "Content-Type": "text/plain"})


def idle_watchdog(server, ttl_seconds):
    def loop():
        while True:
            time.sleep(30)
            if time.time() - _state["last_activity"] > ttl_seconds:
                server.shutdown()
                return
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def write_state(port, pid):
    store.write_json(store.path_for("tracker.json"), {
        "port": port, "pid": pid, "started": stamp(),
    })


def read_state():
    return store.read_json(store.path_for("tracker.json"), {})


def probe(port, timeout=1.0):
    """Return the health payload of whatever answers on this port, or None."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if payload.get("ok") is True and payload.get("service") == "hn-brief" else None


def health(port, timeout=1.0):
    """True only when the responder is an hn-brief tracker serving *this* data dir.

    Another install, or a dev run against a different profile, can hold the port. Treating
    that as ours would resolve links against the wrong brief.json.
    """
    payload = probe(port, timeout)
    return bool(payload) and payload.get("data") == store.data_dir()


def bind_server(preferred):
    """Bind the preferred port, or the next free one above it."""
    last = None
    for port in range(preferred, preferred + PORT_SCAN + 1):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), Handler), port
        except OSError as exc:
            last = exc
    raise SystemExit("hn-brief: no free port in %d..%d (%s)"
                     % (preferred, preferred + PORT_SCAN, last))


def cmd_serve(args):
    prof = store.load_profile()
    preferred = args.port or int(prof["settings"].get("tracker_port", 47811))
    ttl_min = args.ttl_min or int(prof["settings"].get("tracker_idle_ttl_min", 180))

    server, port = bind_server(preferred)
    write_state(port, os.getpid())
    idle_watchdog(server, ttl_min * 60)

    def terminate(signum, frame):
        # shutdown() blocks until serve_forever returns, and serve_forever runs in this
        # same thread, so calling it directly from the handler deadlocks: the process
        # survives SIGTERM and holds its port forever. Hand it to another thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    print("hn-brief tracker on http://127.0.0.1:%d (idle ttl %dm)" % (port, ttl_min), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        state = read_state()
        if state.get("pid") == os.getpid():
            try:
                os.unlink(store.path_for("tracker.json"))
            except OSError:
                pass


def cmd_ensure(args):
    prof = store.load_profile()
    preferred = args.port or int(prof["settings"].get("tracker_port", 47811))

    state = read_state()
    if state.get("port") and health(int(state["port"])):
        print(json.dumps({"status": "running", "port": int(state["port"])}))
        return
    if health(preferred):
        print(json.dumps({"status": "running", "port": preferred}))
        return

    logfile = store.path_for("tracker.log")
    cmd = [sys.executable, os.path.abspath(__file__), "serve", "--port", str(preferred)]
    with open(logfile, "a", encoding="utf-8") as log:
        subprocess.Popen(  # detached so it outlives this shell and the session
            cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=os.path.dirname(os.path.abspath(__file__)),
        )

    deadline = time.time() + STARTUP_WAIT
    while time.time() < deadline:
        time.sleep(0.2)
        state = read_state()
        port = int(state.get("port") or 0)
        if port and health(port):
            print(json.dumps({"status": "started", "port": port}))
            return

    print(json.dumps({"status": "failed", "port": None, "log": logfile}))
    sys.exit(3)


def cmd_status(args):
    state = read_state()
    port = int(state.get("port") or 0)
    alive = bool(port) and health(port)
    print(json.dumps({
        "status": "running" if alive else "stopped",
        "port": port or None,
        "data": store.data_dir(),
        "pid": state.get("pid"),
        "started": state.get("started"),
        "links": len(brief_items()),
        "pending_clicks": count_pending(),
    }))


def count_pending():
    path = store.path_for("clicks.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def cmd_stop(args):
    if args.all:
        stop_all()
        return
    state = read_state()
    pid = state.get("pid")
    if not pid:
        print("tracker not running")
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError) as exc:
        print("could not stop pid %s (%s)" % (pid, exc))
        return
    for _ in range(20):
        time.sleep(0.1)
        if not os.path.exists(store.path_for("tracker.json")):
            break
    print("tracker stopped (pid %s)" % pid)


def stop_all():
    """Stop every hn-brief tracker on this machine, whatever profile it serves.

    Useful after switching between a development checkout and an installed copy, which
    leaves one server per data directory.
    """
    try:
        listing = subprocess.run(["pgrep", "-f", "tracker.py serve"],
                                 capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        print("could not list trackers (%s)" % exc)
        return
    stopped = 0
    for pid in listing:
        try:
            os.kill(int(pid), signal.SIGTERM)
            stopped += 1
        except (OSError, ValueError):
            pass
    print("stopped %d tracker(s)" % stopped)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tracker.py", description="hn-brief click tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ensure", help="start the tracker if it is not already up")
    p.add_argument("--port", type=int)

    p = sub.add_parser("serve", help="run the server in the foreground")
    p.add_argument("--port", type=int)
    p.add_argument("--ttl-min", type=int)

    sub.add_parser("status", help="report tracker state")
    p = sub.add_parser("stop", help="stop the tracker")
    p.add_argument("--all", action="store_true",
                   help="stop every hn-brief tracker, not just this profile's")

    args = parser.parse_args(argv)
    {"ensure": cmd_ensure, "serve": cmd_serve, "status": cmd_status, "stop": cmd_stop}[args.command](args)


if __name__ == "__main__":
    main()
