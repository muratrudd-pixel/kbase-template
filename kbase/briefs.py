"""Brief emails written from the knowledge base.

A brief = the tagged items in its window (topics + minimum relevance, with per-topic quotas) + optional newsletters
+ the current state of its lenses + its last few issues (for continuity), turned into one email by one model call
following config/briefs/<kind>.md. Every link in the output must come from the inputs — anything else is unlinked.

Kinds are defined in config/pipeline.yaml under `briefs:` (daily and offclock ship with the template; add your own).

Usage: python -m kbase.briefs <kind> [--dry] [--date YYYY-MM-DD]
       --dry renders to outbox/ without sending or recording it as sent.
"""
import argparse
import datetime as dt
import json
import re

from .core import CONFIG, LENSES, STATE, cfg, db
from .fulltext import body_text
from .llm import claude
from .notify import render_html, send

OUT = STATE / "briefs"


def kinds():
    return cfg("pipeline").get("briefs") or {}


def _clean(t, cap):
    t = re.sub(r"^\s*# .*\n", "", t or "", count=1)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()[:cap]


def collect(K, today):
    """Tagged items in the window, best first, within per-topic quotas."""
    since = (dt.datetime.combine(today, dt.time()) - dt.timedelta(hours=K.get("hours", 26))).date().isoformat()
    topics = K["topics"]
    cond = " OR ".join("topics LIKE ?" for _ in topics)
    with db(readonly=True) as c:
        rows = c.execute(f"""SELECT * FROM docs WHERE COALESCE(published, substr(ingested,1,10)) >= ? AND COALESCE(rel,0) >= ?
                             AND source_type != 'newsletter' AND ({cond})
                             ORDER BY rel DESC, published DESC""", [since, K.get("min_rel", 2)] + [f'%"{t}"%' for t in topics]).fetchall()
        quotas, used, items = K.get("quotas") or {}, {}, []
        for r in rows:
            ts = json.loads(r["topics"] or "[]")
            q = next((k for k in quotas if set(k.split("|")) & set(ts)), None)
            if q and used.get(q, 0) >= quotas[q]:
                continue
            used[q] = used.get(q, 0) + 1
            items.append({"row": r, "text": _clean(body_text(r, c), K.get("item_chars", 1200))})
            if len(items) >= K.get("max_items", 80):
                break
        letters = []
        if K.get("newsletters"):
            for r in c.execute("SELECT * FROM docs WHERE source_type='newsletter' AND llm_allowed=1 AND published >= ? "
                               "ORDER BY published DESC LIMIT 8", [since]).fetchall():
                letters.append({"row": r, "text": _clean(body_text(r, c), 6000)})
    return items, letters


def _lens_states(K, cap=6000):
    out = []
    for slug in K.get("lenses") or []:
        p = LENSES / slug / "state.md"
        if p.exists() and p.read_text().strip():
            out.append(f"<lens name=\"{slug}\">\n{re.sub(r'\[doc:[^\]]+\]', '', p.read_text())[:cap]}\n</lens>")
    return "\n\n".join(out) or "(none yet)"


def _previous(kind, today, n=3):
    d = OUT / kind
    files = sorted(p for p in d.glob("*.md") if p.stem < today.isoformat() and "-dry" not in p.stem)[-n:] if d.exists() else []
    return "\n\n".join(f"<previous date=\"{p.stem}\">\n{p.read_text()[:4000]}\n</previous>" for p in files) or "(none)"


def build_prompt(kind, K, today, items, letters):
    rules = (CONFIG / "briefs" / f"{kind}.md").read_text()
    it = "\n\n".join(f"<item n=\"{i}\" source=\"{x['row']['source']}\" date=\"{x['row']['published']}\" "
                     f"topics=\"{','.join(json.loads(x['row']['topics'] or '[]'))}\" rel=\"{x['row']['rel']}\" "
                     f"url=\"{x['row']['url'] or ''}\">\nTITLE: {x['row']['title']}\n{x['text']}\n</item>"
                     for i, x in enumerate(items, 1))
    nl = "\n\n".join(f"<newsletter source=\"{x['row']['source']}\" date=\"{x['row']['published']}\">\n"
                     f"SUBJECT: {x['row']['title']}\n{x['text']}\n</newsletter>" for x in letters) or "(none)"
    return (f"{rules}\n\nToday is {today:%A, %B %-d, %Y}.\n\nREADER PROFILE:\n{cfg('topics').get('reader', '')}\n\n"
            f"ITEMS:\n{it or '(none)'}\n\nNEWSLETTERS:\n{nl}\n\nLENSES (background, for continuity — not news):\n"
            f"{_lens_states(K)}\n\nPREVIOUS ISSUES:\n{_previous(kind, today)}\n")


def allowed_urls(items, letters):
    urls = {x["row"]["url"] for x in items if x["row"]["url"]}
    for x in letters:
        urls |= set(re.findall(r"\((https?://[^)\s]+)\)", x["text"]))
    return urls


def scrub_links(md, urls):
    """Unlink any URL the model didn't get from the inputs."""
    def fix(m):
        return m.group(0) if m.group(2) in urls else m.group(1)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", fix, md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("kind")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    K = kinds().get(a.kind)
    if not K:
        raise SystemExit(f"unknown brief '{a.kind}' (have: {', '.join(kinds())})")
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    items, letters = collect(K, today)
    if len(items) < K.get("min_items", 3):
        print(f"{a.kind}: only {len(items)} items — nothing sent")
        return None
    md = claude(build_prompt(a.kind, K, today, items, letters), K.get("model", "sonnet")).strip()
    md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md)
    md = scrub_links(md, allowed_urls(items, letters))
    subject = f"{K['title']} — {today:%a %b %-d}"
    (OUT / a.kind).mkdir(parents=True, exist_ok=True)
    stem = today.isoformat() + ("-dry" if a.dry else "")
    (OUT / a.kind / f"{stem}.md").write_text(md)
    html = render_html(subject, md, footer=f"{len(items)} items · {len(letters)} newsletters · written by {K.get('model', 'sonnet')}")
    where = send(subject, md, html, dry=a.dry)
    print(f"{a.kind}: {len(items)} items, {len(letters)} newsletters → {where}")
    return where


if __name__ == "__main__":
    main()
