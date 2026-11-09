"""Keyword (SQLite FTS5) + meaning (local embeddings via Ollama, optional) index over the archive, and hybrid search.

Embeddings are optional: without Ollama running, keyword search still works and lenses fall back to plain excerpts.
To turn them on: install Ollama, `ollama pull nomic-embed-text`, and leave OLLAMA_URL at its default.
"""
import re

import numpy as np
import requests

from .core import cfg, db, env, read_body

OLLAMA = env("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/embed"
_available = None


def _model():
    return (cfg("pipeline").get("index") or {}).get("embed_model", "nomic-embed-text")


def available():
    global _available
    if _available is None:
        try:
            requests.get(OLLAMA.rsplit("/api/", 1)[0], timeout=2)
            _available = True
        except requests.RequestException:
            _available = False
    return _available


def embed(texts, kind="document"):
    prefix = "search_document: " if kind == "document" else "search_query: "
    r = requests.post(OLLAMA, json={"model": _model(), "input": [prefix + t for t in texts]}, timeout=300)
    r.raise_for_status()
    v = np.array(r.json()["embeddings"], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)


def chunks(text, size=1400):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = ""
        while len(p) > size:
            out.append(p[:size])
            p = p[size:]
        cur = (cur + "\n\n" + p).strip()
    if cur:
        out.append(cur)
    return out[:400]


def update():
    c = db()
    todo = c.execute("SELECT id,path,title,source,llm_allowed FROM docs WHERE fts_done=0").fetchall()
    for d in todo:
        c.execute("INSERT INTO docs_fts(id,title,body,source) VALUES(?,?,?,?)",
                  (d["id"], d["title"], read_body(d["path"]), d["source"]))
        c.execute("UPDATE docs SET fts_done=1, emb_done=CASE WHEN llm_allowed=0 THEN -1 ELSE emb_done END WHERE id=?",
                  (d["id"],))
    c.commit()
    n_fts, embedded = len(todo), 0
    if not available():
        print(f"index: keyword-indexed {n_fts}; embeddings off (Ollama not running)")
        return
    todo = c.execute("SELECT id,path,title FROM docs WHERE emb_done=0 AND llm_allowed=1").fetchall()
    for d in todo:
        parts = [f"{d['title']}\n\n{t}" for t in chunks(read_body(d["path"]))] or [d["title"]]
        try:
            vecs = np.concatenate([embed(parts[i:i + 16]) for i in range(0, len(parts), 16)])
        except requests.RequestException as e:
            print(f"index: embeddings failed ({e}); keyword search still works")
            break
        c.execute("DELETE FROM chunks WHERE doc_id=?", (d["id"],))
        c.executemany("INSERT INTO chunks VALUES(?,?,?,?)", [(d["id"], i, parts[i], vecs[i].tobytes()) for i in range(len(parts))])
        c.execute("UPDATE docs SET emb_done=1 WHERE id=?", (d["id"],))
        c.commit()
        embedded += 1
    print(f"index: keyword-indexed {n_fts}, embedded {embedded}")


def _fts_query(q):
    toks = re.findall(r"[\w$.'-]+", q)
    return " OR ".join(f'"{t}"' for t in toks) if toks else '""'


def search(query, limit=8, source_type=None, since=None):
    """Hybrid search: FTS5 bm25 ranks + embedding ranks, fused by reciprocal rank."""
    c = db(readonly=True)
    where, args = ["1=1"], []
    if source_type:
        where.append("d.source_type=?")
        args.append(source_type)
    if since:
        where.append("d.published>=?")
        args.append(since)
    filt = " AND ".join(where)
    ranks, snippets = {}, {}
    rows = c.execute(f"SELECT f.id, snippet(docs_fts, 2, '**', '**', ' … ', 24) snip FROM docs_fts f JOIN docs d ON d.id=f.id "
                     f"WHERE docs_fts MATCH ? AND {filt} ORDER BY bm25(docs_fts) LIMIT 40", [_fts_query(query)] + args).fetchall()
    for i, r in enumerate(rows):
        ranks[r["id"]] = ranks.get(r["id"], 0) + 1 / (60 + i)
        snippets[r["id"]] = r["snip"]
    if available():
        try:
            qv = embed([query], kind="query")[0]
            emb = c.execute(f"SELECT ch.doc_id, ch.text, ch.emb FROM chunks ch JOIN docs d ON d.id=ch.doc_id WHERE {filt}", args).fetchall()
            if emb:
                m = np.frombuffer(b"".join(r["emb"] for r in emb), dtype=np.float32).reshape(len(emb), -1)
                best = {}
                for idx in np.argsort(-(m @ qv))[:80]:
                    did = emb[idx]["doc_id"]
                    if did not in best:
                        best[did] = (len(best), emb[idx]["text"])
                for did, (rank, text) in best.items():
                    ranks[did] = ranks.get(did, 0) + 1 / (60 + rank)
                    snippets.setdefault(did, text[:320].replace("\n", " ") + " …")
        except requests.RequestException:
            pass
    out = []
    for did in sorted(ranks, key=ranks.get, reverse=True)[:limit]:
        d = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        out.append({"id": did, "title": d["title"], "source": d["source"], "type": d["source_type"],
                    "published": d["published"], "url": d["url"], "snippet": snippets.get(did, "")})
    return out


if __name__ == "__main__":
    update()
