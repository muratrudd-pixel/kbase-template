"""Shared paths, settings, the archive database, and write_doc() — the one function every ingester uses.

Layout (all relative to the repo unless overridden in .env):
  archive/raw/YYYY/MM/<id>.md   every document as Markdown + YAML front matter (gitignored)
  index/kbase.db                SQLite: docs table, FTS5 keyword index, embedding chunks (gitignored)
  logs/                         job logs (gitignored)
  outbox/                       rendered emails when EMAIL_MODE=file (gitignored)
"""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path=ROOT / ".env"):
    """Minimal .env reader (KEY=value lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in "\"'" and v[:1] and v.count(v[0]) >= 2:
            v = v[1:v.index(v[0], 1)]          # quoted value: keep everything inside the quotes
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()   # unquoted: drop an inline "# comment"
        os.environ.setdefault(k.strip(), v)


_load_env()


def env(key, default=None):
    return os.environ.get(key, default)


def _path(key, default):
    p = Path(os.path.expanduser(env(key, str(default))))
    return p if p.is_absolute() else ROOT / p


CONFIG = ROOT / "config"
LENSES = ROOT / "lenses"
ARCHIVE = _path("KB_ARCHIVE_DIR", "archive")
RAW = ARCHIVE / "raw"
INDEX = _path("KB_INDEX_DIR", "index")
DB = INDEX / "kbase.db"
LOGS = _path("KB_LOG_DIR", "logs")
STATE = INDEX / "state"          # small run-state files (briefs sent, source run times)
OUTBOX = _path("KB_OUTBOX_DIR", "outbox")

for p in (RAW, INDEX, LOGS, STATE, OUTBOX):
    p.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY, path TEXT, source TEXT, source_type TEXT, title TEXT, url TEXT,
  published TEXT, ingested TEXT, dedupe TEXT UNIQUE, llm_allowed INTEGER DEFAULT 1,
  license TEXT, tags TEXT, fts_done INTEGER DEFAULT 0, emb_done INTEGER DEFAULT 0,
  sid TEXT, topics TEXT, rel INTEGER, event TEXT, tagged INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS docs_pub ON docs(published);
CREATE INDEX IF NOT EXISTS docs_sid ON docs(sid);
CREATE INDEX IF NOT EXISTS docs_rel ON docs(rel);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  id UNINDEXED, title, body, source, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS chunks(doc_id TEXT, idx INTEGER, text TEXT, emb BLOB, PRIMARY KEY(doc_id, idx));
CREATE TABLE IF NOT EXISTS blocked(ts TEXT, source TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS source_runs(sid TEXT PRIMARY KEY, last TEXT, ok INTEGER, new INTEGER, error TEXT);
"""


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def cfg(name):
    p = CONFIG / f"{name}.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def db(readonly=False):
    if readonly and DB.exists():
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    else:
        c = sqlite3.connect(DB, timeout=30)
        c.executescript(SCHEMA)
    c.row_factory = sqlite3.Row
    return c


def slug(s, n=60):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:n].strip("-") or "untitled"


# ---------------------------------------------------------------- blocklist
_guard = None


def blocked_reason(*texts):
    """A reason string if any text hits config/blocklist.yaml (domains or regexes you never want stored), else None.
    Use it to keep e.g. your employer's email domain or confidential project names out of the archive."""
    global _guard
    if _guard is None:
        g = cfg("blocklist")
        _guard = ([d.lower() for d in g.get("blocked_domains") or []],
                  [re.compile(t, re.I) for t in g.get("blocked_patterns") or []])
    domains, pats = _guard
    blob = "\n".join(t or "" for t in texts)
    low = blob.lower()
    for d in domains:
        if d in low:
            return f"domain:{d}"
    for p in pats:
        if p.search(blob):
            return f"pattern:{p.pattern}"
    return None


# ---------------------------------------------------------------- archive write
def exists(dedupe):
    with db() as c:
        return c.execute("SELECT 1 FROM docs WHERE dedupe=?", (dedupe,)).fetchone() is not None


def write_doc(*, source, source_type, title, body, url=None, published=None, dedupe=None,
              llm_allowed=True, license="personal_use", tags=None, sid=None, extra=None):
    """Store one document as Markdown + YAML in archive/raw and register it. Returns its id, or None if it was
    blocked or is a duplicate."""
    reason = blocked_reason(source, title, url, body)
    if reason:
        with db() as c:
            c.execute("INSERT INTO blocked VALUES(?,?,?)", (now(), source, reason))
        return None
    body = (body or "").strip()
    dedupe = dedupe or ("sha256:" + hashlib.sha256(re.sub(r"\s+", " ", body).encode()).hexdigest())
    if exists(dedupe):
        return None
    published = (published or now())[:10]
    h = hashlib.sha1(dedupe.encode()).hexdigest()[:6]
    doc_id = f"{published}-{slug(source, 24)}-{slug(title, 50)}-{h}"
    path = RAW / published[:4] / published[5:7] / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": doc_id, "title": title, "source": source, "source_type": source_type, "url": url,
            "published": published, "ingested": now(), "llm_allowed": bool(llm_allowed), "license": license,
            "tags": tags or [], "sid": sid}
    meta.update(extra or {})
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text(f"---\n{fm}---\n\n# {title}\n\n{body}\n", encoding="utf-8")
    with db() as c:
        c.execute("INSERT INTO docs(id,path,source,source_type,title,url,published,ingested,dedupe,llm_allowed,"
                  "license,tags,sid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (doc_id, str(path), source, source_type, title, url, published, meta["ingested"], dedupe,
                   int(bool(llm_allowed)), license, json.dumps(tags or []), sid))
    return doc_id


def read_body(path):
    t = Path(path).read_text(encoding="utf-8")
    if t.startswith("---\n"):
        end = t.find("\n---\n", 4)
        if end != -1:
            return t[end + 5:]
    return t
