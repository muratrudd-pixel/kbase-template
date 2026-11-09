"""Pull every source in the registry into the archive.

Kinds:
  rss          any RSS/Atom feed. Full text if the feed carries it; thin items get their page fetched later
               (kbase/fulltext.py).
  google_news  a Google News search (free RSS). `query` uses Google News syntax, e.g. '"Federal Reserve" when:1d'.
  podcast      a podcast feed whose episodes carry a published transcript — either in the item text (many Substack
               podcasts post the full transcript) or as a <podcast:transcript> link. Episodes without one are
               skipped (add local Whisper transcription yourself if you want them).

Each source has a minimum interval (`every_hours`, default 1; Google News 3) so hourly runs stay polite.
"""
import datetime as dt
import re
import time
import traceback
import urllib.parse

import feedparser
import html2text
import requests

from . import registry
from .core import db, env, now, write_doc

UA = {"User-Agent": env("KB_USER_AGENT", "kbase-template/0.1 (personal research; +https://github.com/)")}
DEFAULT_EVERY = {"rss": 1, "google_news": 3, "podcast": 6}
_h2t = html2text.HTML2Text()
_h2t.body_width = 0
_h2t.ignore_images = True
MIN_TRANSCRIPT = 3000   # characters of episode text that count as "a published transcript"


def fetch(url, timeout=30):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def due(sid, every_hours, force=False):
    if force:
        return True
    with db() as c:
        r = c.execute("SELECT last FROM source_runs WHERE sid=?", (sid,)).fetchone()
    if not r:
        return True
    last = dt.datetime.fromisoformat(r["last"])
    return (dt.datetime.now(last.tzinfo) - last).total_seconds() >= every_hours * 3600 - 300


def record(sid, ok, new=0, error=None):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO source_runs VALUES(?,?,?,?,?)", (sid, now(), int(ok), new, error))


def canon(url):
    p = urllib.parse.urlsplit(url or "")
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def _date(e):
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return dt.date(*e[k][:3]).isoformat()
    return None


def _text(e):
    html = ""
    if e.get("content"):
        html = max((c.get("value", "") for c in e["content"]), key=len)
    html = html if len(html) > len(e.get("summary", "")) else e.get("summary", "")
    return re.sub(r"\n{3,}", "\n\n", _h2t.handle(html)).strip() if html else ""


def _recent(e, days):
    d = _date(e)
    return not d or d >= (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _transcript(e):
    """A published transcript for a podcast episode: the episode text if long enough, else a podcast:transcript link."""
    text = _text(e)
    if len(text) >= MIN_TRANSCRIPT:
        return text
    for link in e.get("podcast_transcript", []) if isinstance(e.get("podcast_transcript"), list) else []:
        url = link.get("url")
        if url:
            body = fetch(url).text
            body = re.sub(r"(?m)^\d+\n|^[\d:.,]+ --> [\d:.,]+.*\n|^WEBVTT.*\n", "", body)
            return _h2t.handle(body) if "<" in body[:200] else body
    return None


def run_source(s, days):
    kind = s.get("kind", "rss")
    if kind == "google_news":
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": s["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})
    else:
        url = s["url"]
    feed = feedparser.parse(fetch(url).content)
    new = 0
    for e in feed.entries[: s.get("max_items", 50)]:
        if not _recent(e, days):
            continue
        link = canon(e.get("link"))
        title = re.sub(r"\s+", " ", e.get("title") or "(untitled)").strip()
        if kind == "podcast":
            if not e.get("enclosures"):      # a written post in a podcast's feed, not an episode
                continue
            body = _transcript(e)
            if not body:
                continue
            stype = "transcript"
        else:
            body = _text(e)
            if kind == "google_news":     # Google News items are headline + publisher; text comes from fulltext.py
                pub = (e.get("source") or {}).get("title") or ""
                body = f"{title}\n\nPublisher: {pub}" if pub else title
            stype = s.get("source_type", "news")
        doc = write_doc(source=s.get("name") or s["id"], source_type=stype, title=title, body=body, url=link,
                        published=_date(e), dedupe=f"url:{link}" if link else None, sid=s["id"],
                        llm_allowed=not s.get("link_only"), tags=[s.get("beat", "general")])
        new += bool(doc)
    return new


def main(force=False, days=None):
    days = days or int(env("KB_LOOKBACK_DAYS", "3"))
    total, failed = 0, []
    for s in registry.load():
        every = s.get("every_hours", DEFAULT_EVERY.get(s.get("kind", "rss"), 1))
        if not due(s["id"], every, force):
            continue
        try:
            n = run_source(s, days)
            record(s["id"], True, n)
            total += n
            print(f"  {s['id']:28} +{n}")
        except Exception as ex:
            record(s["id"], False, 0, str(ex)[:300])
            failed.append(s["id"])
            print(f"  {s['id']:28} FAILED: {str(ex)[:120]}")
            traceback.print_exc(limit=1)
        time.sleep(1)
    print(f"ingest: {total} new documents" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return total


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
