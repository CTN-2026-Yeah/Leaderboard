#!/usr/bin/env python3
"""Fetch the CTN leaderboard and append a snapshot to data/history.json if scores changed.

Signs in with CTN_EMAIL / CTN_PASSWORD when they are set (env vars, or a gitignored .env file
next to this script). Otherwise falls back to a session cookie from CTN_COOKIE or cookie.txt.
Uses only the standard library so it runs anywhere Python 3 does.
"""
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://community.capturethenarrative.com"
URL = f"{BASE}/user/competitions/2"
LOGIN_URL = f"{BASE}/login"
ROOT = Path(__file__).resolve().parent
DATA_JSON = ROOT / "data" / "history.json"
DATA_JS = ROOT / "data" / "history.js"  # same data, loadable by index.html over file://
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")


class TableParser(HTMLParser):
    """Collects the rows of the first <tbody> on the page as lists of cell text."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
        self.in_tbody = self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "tbody":
            self.in_tbody = True
        elif self.in_tbody and tag == "tr":
            self.row = []
        elif self.row is not None and tag == "td":
            self.cell = []

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag == "tbody":
            self.in_tbody, self.done = False, True
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def load_dotenv():
    """Read KEY=VALUE lines from .env into os.environ without overriding real env vars."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get_cookie():
    cookie = os.environ.get("CTN_COOKIE", "").strip()
    if not cookie and (ROOT / "cookie.txt").exists():
        cookie = (ROOT / "cookie.txt").read_text().strip()
    if not cookie:
        sys.exit("No credentials: set CTN_EMAIL and CTN_PASSWORD, or CTN_COOKIE / cookie.txt")
    return cookie if cookie.startswith("accounts_session=") else f"accounts_session={cookie}"


def open_url(opener, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "text/html", **(headers or {})})
    with opener.open(req, timeout=30) as resp:
        return resp.geturl(), resp.read().decode("utf-8", "replace")


def login(opener, email, password):
    _, page = open_url(opener, LOGIN_URL)
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
    if not match:
        sys.exit("Could not find the csrf_token on the login page - the login form may have changed")
    form = urllib.parse.urlencode({
        "csrf_token": match.group(1), "email": email, "email_password": password}).encode()
    final_url, _ = open_url(opener, LOGIN_URL, data=form, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE, "Referer": LOGIN_URL})
    if urllib.parse.urlparse(final_url).path.rstrip("/") == "/login":
        sys.exit("Login failed - check CTN_EMAIL and CTN_PASSWORD")


def fetch():
    load_dotenv()
    email, password = os.environ.get("CTN_EMAIL", "").strip(), os.environ.get("CTN_PASSWORD", "")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if email and password:
        login(opener, email, password)
        return open_url(opener, URL)[1]
    return open_url(opener, URL, headers={"Cookie": get_cookie()})[1]


def parse(html):
    parser = TableParser()
    parser.feed(html)
    teams = {}
    for cells in parser.rows:
        if len(cells) < 5:
            continue
        _, name, uni, members, score = cells[:5]
        teams[name] = {"uni": uni, "members": int(members or 0), "score": int(score or 0)}
    return teams


def main():
    teams = parse(fetch())
    if not teams:
        sys.exit("Leaderboard table not found - the session cookie has probably expired")

    history = json.loads(DATA_JSON.read_text()) if DATA_JSON.exists() else {"teams": {}, "snapshots": []}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    scores = {name: t["score"] for name, t in teams.items()}

    for name, t in teams.items():
        history["teams"][name] = {"uni": t["uni"], "members": t["members"]}
    history["checked_at"] = now

    snaps = history["snapshots"]
    if snaps and snaps[-1]["scores"] == scores:
        print(f"{now}: no change ({len(scores)} teams)")
    else:
        snaps.append({"t": now, "scores": scores})
        print(f"{now}: new snapshot #{len(snaps)} ({len(scores)} teams)")

    DATA_JSON.parent.mkdir(exist_ok=True)
    text = json.dumps(history, separators=(",", ":"), ensure_ascii=False)
    DATA_JSON.write_text(text)
    DATA_JS.write_text(f"window.HISTORY={text};\n")


if __name__ == "__main__":
    main()
