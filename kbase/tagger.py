"""Tag every document so briefs and lenses can pull a small, clean candidate set.

Topics (1–3), relevance (0–3) and event type come from a small model (haiku by default) in batches of 40, judged
against the reader profile and topic list in config/topics.yaml. Link-only sources (llm_allowed=0) are never sent to
a model: they get their source's beat as topic and relevance 1. Items older than stale_days get relevance 0 without
a model call.
"""
import datetime as dt
import json
import re
import time

from .core import cfg, db, read_body
from .llm import claude, parse_json

BATCH = 40


def _conf():
    t = cfg("topics")
    m = (cfg("pipeline").get("models") or {}).get("tagger", "haiku")
    return t["reader"], t["topics"], t.get("events") or ["other"], t.get("stale_days", 30), t.get("beat_to_topic") or {}, m


def _snippet(d):
    try:
        body = read_body(d["path"])
    except Exception:
        body = ""
    body = re.sub(r"^\s*# .*\n", "", body, count=1)
    return re.sub(r"\s+", " ", body).strip()[:280]


def classify(batch, reader, topics, events, model):
    lines = [json.dumps({"i": i, "src": d["source"], "title": d["title"], "text": _snippet(d)}, ensure_ascii=False)
             for i, d in enumerate(batch)]
    prompt = f"""{reader}

Classify each item. Topics (1-3 per item, most specific first): {json.dumps(topics)}
Event types: {events}

Items (one JSON per line):
{chr(10).join(lines)}

Return ONLY a JSON array, one object per item, no prose: [{{"i":0,"t":["topic",...],"r":0-3,"e":"event"}}, ...]"""
    return parse_json(claude(prompt, model, timeout=300))


def main(limit=2000, max_minutes=25):
    reader, topics, events, stale_days, b2t, model = _conf()
    start = time.time()
    with db() as c:
        docs = c.execute("SELECT * FROM docs WHERE COALESCE(tagged,0)=0 ORDER BY ingested DESC LIMIT ?", (limit,)).fetchall()
    if not docs:
        print("tagger: nothing new")
        return
    cutoff = (dt.date.today() - dt.timedelta(days=stale_days)).isoformat()
    todo, quick = [], 0
    with db() as c:
        for d in docs:
            beat = (json.loads(d["tags"] or "[]") or ["general"])[0]
            beat_topic = b2t.get(beat, "other")
            if (d["published"] or "9") < cutoff and d["source_type"] in ("news", "newsletter"):
                c.execute("UPDATE docs SET topics=?,rel=0,event='other',tagged=1 WHERE id=?", (json.dumps([beat_topic]), d["id"]))
                quick += 1
            elif not d["llm_allowed"]:
                c.execute("UPDATE docs SET topics=?,rel=1,event='other',tagged=1 WHERE id=?", (json.dumps([beat_topic]), d["id"]))
                quick += 1
            else:
                todo.append(d)
    done = fails = 0
    for k in range(0, len(todo), BATCH):
        if time.time() - start > max_minutes * 60:
            break
        batch = todo[k:k + BATCH]
        try:
            res = {r["i"]: r for r in classify(batch, reader, topics, events, model) if isinstance(r, dict) and "i" in r}
        except Exception as ex:
            fails += 1
            print(f"tagger: batch failed: {str(ex)[:200]}")
            if fails >= 3:
                break
            continue
        with db() as c:
            for i, d in enumerate(batch):
                r = res.get(i)
                if not r:
                    continue
                ts = [t for t in (r.get("t") or []) if t in topics][:3] or ["other"]
                rel = max(0, min(3, int(r.get("r", 1))))
                ev = r.get("e") if r.get("e") in events else "other"
                c.execute("UPDATE docs SET topics=?,rel=?,event=?,tagged=1 WHERE id=?", (json.dumps(ts), rel, ev, d["id"]))
                done += 1
    print(f"tagger: {len(docs)} docs · rule-tagged {quick} · model-tagged {done} · left {len(todo) - done}")
    if fails >= 3 and done == 0:
        raise SystemExit("tagger: classifier unavailable")


if __name__ == "__main__":
    main()
