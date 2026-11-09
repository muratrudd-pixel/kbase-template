"""Fetch the article text for thin items (headline-only feeds, Google News) with trafilatura.

Stored in table `fulltext` (not in the archive file), and used by briefs and lenses when the stored body is thin.
Respects llm_allowed (link-only sources are never fetched) and skips domains listed in config/pipeline.yaml
fulltext.skip_domains (sites that refuse scripts — don't try to get round a block).
"""
import re
import urllib.parse

import requests
import trafilatura

from .core import cfg, db, read_body
from .ingest import UA

THIN = 400


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS fulltext(doc_id TEXT PRIMARY KEY, url TEXT, text TEXT, ok INTEGER)")


def _resolve(url):
    """Google News links redirect through news.google.com; follow to the publisher when possible."""
    if "news.google.com" not in url:
        return url
    try:
        r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
        return r.url if "news.google.com" not in r.url else None
    except requests.RequestException:
        return None


def body_text(row, c=None):
    """The best text for a doc: stored body, or fetched full text when the body is thin."""
    body = read_body(row["path"])
    if len(body) >= THIN:
        return body
    c = c or db(readonly=True)
    try:
        r = c.execute("SELECT text FROM fulltext WHERE doc_id=? AND ok=1", (row["id"],)).fetchone()
    except Exception:
        r = None
    return r["text"] if r else body


def main(limit=60):
    conf = cfg("pipeline").get("fulltext") or {}
    skip = tuple(conf.get("skip_domains") or [])
    c = db()
    _ensure(c)
    rows = c.execute("SELECT * FROM docs WHERE llm_allowed=1 AND url IS NOT NULL AND id NOT IN (SELECT doc_id FROM fulltext) "
                     "ORDER BY ingested DESC LIMIT ?", (limit * 4,)).fetchall()
    done = ok = 0
    for r in rows:
        if done >= limit:
            break
        if len(read_body(r["path"])) >= THIN:
            continue
        done += 1
        url = _resolve(r["url"])
        text = None
        if url and not urllib.parse.urlsplit(url).netloc.endswith(skip or ("\0",)):
            try:
                html = requests.get(url, headers=UA, timeout=20).text
                text = trafilatura.extract(html, include_comments=False) or None
            except requests.RequestException:
                text = None
        if text:
            text = re.sub(r"\n{3,}", "\n\n", text)[:40000]
        c.execute("INSERT OR REPLACE INTO fulltext VALUES(?,?,?,?)", (r["id"], url, text, int(bool(text))))
        c.commit()
        ok += bool(text)
    print(f"fulltext: tried {done}, got {ok}")


if __name__ == "__main__":
    main()
