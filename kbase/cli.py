"""`kb` — the command line for the knowledge base.

  kb run ingest            newsletters + sources + full text + index + tag (the hourly job)
  kb run lenses            rebuild every lens's state (weekly)
  kb brief <kind> [--dry]  write and send a brief (daily, offclock, ...)
  kb whatchanged [--dry]   the Sunday "What changed" email
  kb search "<query>"      hybrid search
  kb add rss <url> "<name>" [beat]        follow a feed
  kb add news "<query>" "<name>" [beat]   follow a Google News search
  kb add podcast <feed-url> "<name>" [beat]
  kb sources               list sources with doc counts and last run
  kb status                archive counts and recent runs
"""
import sys
import traceback

from . import registry


def _steps(steps):
    failed = []
    for name, fn in steps:
        try:
            fn()
        except Exception:
            failed.append(name)
            print(f"[{name}] FAILED\n{traceback.format_exc()}")
    if failed:
        print(f"failed steps: {', '.join(failed)}")
        sys.exit(1)


def run(job):
    if job == "ingest":
        from . import fulltext, index, ingest, newsletters, tagger
        _steps([("newsletters", newsletters.main), ("sources", ingest.main), ("fulltext", fulltext.main),
                ("index", index.update), ("tag", tagger.main)])
    elif job == "lenses":
        from .lenses import build_state, lens_dirs
        _steps([(d.name, lambda s=d.name: build_state(s)) for d in lens_dirs()])
    else:
        raise SystemExit(f"unknown job {job}")


def status():
    from .core import db
    with db(readonly=True) as c:
        for r in c.execute("SELECT source_type, count(*) n, min(published) a, max(published) b FROM docs GROUP BY 1 ORDER BY 2 DESC"):
            print(f"{r['source_type']:12} {r['n']:6}  {r['a']} → {r['b']}")
        print(f"tagged: {c.execute('SELECT count(*) FROM docs WHERE tagged=1').fetchone()[0]}  "
              f"blocked: {c.execute('SELECT count(*) FROM blocked').fetchone()[0]}")


def sources():
    from .core import db
    with db(readonly=True) as c:
        counts = dict(c.execute("SELECT sid, count(*) FROM docs GROUP BY sid").fetchall())
        runs = {r["sid"]: r for r in c.execute("SELECT * FROM source_runs")}
    for s in registry.load(include_muted=True):
        r = runs.get(s["id"])
        last = f"{r['last'][:16]} {'ok' if r['ok'] else 'FAIL'}" if r else "never run"
        print(f"{s['id']:28} {s.get('kind', 'rss'):11} {s.get('beat', ''):10} docs={counts.get(s['id'], 0):<5} {last}"
              + ("  (muted)" if s.get("muted") else ""))


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
    elif a[0] == "run":
        run(a[1])
    elif a[0] == "brief":
        from .briefs import main as brief
        brief(a[1:])
    elif a[0] == "whatchanged":
        from .whatchanged import main as wc
        wc(a[1:])
    elif a[0] == "search":
        from .index import search
        for r in search(" ".join(a[1:])):
            print(f"[{r['id']}] {r['title']} — {r['source']} ({r['published']})\n   {r['snippet'][:200]}\n")
    elif a[0] == "add":
        kind = {"rss": "rss", "news": "google_news", "podcast": "podcast"}[a[1]]
        field = "query" if kind == "google_news" else "url"
        ok = registry.add(kind, a[3], a[4] if len(a) > 4 else None, **{field: a[2]})
        print("added — it is fetched on the next `kb run ingest`" if ok else "already followed")
    elif a[0] == "sources":
        sources()
    elif a[0] == "status":
        status()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
