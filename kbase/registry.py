"""The source registry: config/sources.yaml (hand-curated) + config/sources_added.yaml (added with `kb add`).

Every source gets a stable id (its `id` field) stored on each document it produces (docs.sid), so you can count,
mute and judge sources individually. Mute a source with `muted: true` and a `why:` — it stops being fetched but its
documents stay searchable.
"""
import datetime as dt

import yaml

from .core import CONFIG, slug

MAIN = CONFIG / "sources.yaml"
ADDED = CONFIG / "sources_added.yaml"
KINDS = ("rss", "google_news", "podcast")


def _read(p):
    return (yaml.safe_load(p.read_text()) if p.exists() else None) or {}


def load(include_muted=False):
    """[{id, kind, name, beat, ...}] from both files."""
    out = []
    for origin, p in (("main", MAIN), ("added", ADDED)):
        for s in _read(p).get("sources") or []:
            s = dict(s, _origin=origin)
            s.setdefault("id", slug(s.get("name") or s.get("url") or s.get("query"), 40))
            if s.get("muted") and not include_muted:
                continue
            out.append(s)
    return out


def beats():
    return _read(MAIN).get("beats") or []


def add(kind, name, beat=None, **fields):
    """Append a source to sources_added.yaml. Returns False if it's already followed."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    for s in load(include_muted=True):
        if any(fields.get(k) and s.get(k) == fields.get(k) for k in ("url", "query")):
            return False
    data = _read(ADDED)
    entry = {"id": slug(name, 40), "kind": kind, "name": name, "beat": beat or "general", **fields,
             "added": dt.date.today().isoformat()}
    data.setdefault("sources", []).append(entry)
    ADDED.write_text("# Sources added with `kb add`. Safe to edit or delete lines.\n"
                     + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return True
