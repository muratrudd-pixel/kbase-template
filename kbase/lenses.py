"""Lenses: cited, fact-checked views of one area of the knowledge base.

Each lens lives in lenses/<slug>/:
  lens.yaml       what it covers: name, scope, topics, source ids, search queries, section headings, windows
  frameworks.md   evergreen "how this area works". Drafted once by the model (`draft-frameworks`); afterwards it
                  changes ONLY when you approve a proposal. Code never overwrites it.
  state.md        rebuilt weekly from this week's evidence only (never seeded from last week's text).
  changelog.md    append-only: each week, the claims that cite a document last week's state didn't.
  proposals/      <date>-frameworks.md — proposed frameworks changes (cited). Never auto-applied.
  runs/           <date>-<mode>.json — counts and drops (read by the Sunday "What changed" email).

Claim rules (enforced in code, not just the prompt): every claim cites [doc:ID] of a document in this run's evidence;
no ID, no claim. A small model checks each claim against the cited excerpts and failures are dropped. Each claim
carries a provenance tag (primary / expert / press) from config/lenses/provenance.yaml.

Usage: python -m kbase.lenses list
       python -m kbase.lenses state <slug|all>
       python -m kbase.lenses draft-frameworks <slug>
       python -m kbase.lenses review-frameworks <slug> [--days N]
"""
import datetime as dt
import json
import re
import subprocess
import sys

import numpy as np
import yaml

from .core import CONFIG, LENSES, ROOT, cfg, db
from .fulltext import body_text
from .llm import claude, parse_json

CONF = CONFIG / "lenses"
CLAIM_RE = re.compile(r"^- (.+?) ((?:\[doc:[^\]]+\]\s*)+)\((primary|expert|press)\)\s*$")
DOC_RE = re.compile(r"\[doc:([^\]]+)\]")
PROV_RANK = {"primary": 0, "expert": 1, "press": 2}


def lens_dirs():
    return sorted(p for p in LENSES.iterdir() if (p / "lens.yaml").exists() and not p.name.startswith("_"))


def load(slug):
    p = LENSES / slug / "lens.yaml"
    if not p.exists():
        raise SystemExit(f"no lens '{slug}' (have: {', '.join(d.name for d in lens_dirs())})")
    L = yaml.safe_load(p.read_text())
    L["slug"], L["dir"] = slug, p.parent
    return L


def models():
    return {"state": "opus", "frameworks": "opus", "verify": "haiku", **((cfg("pipeline").get("models") or {}).get("lenses") or {})}


_prov = None


def provenance(row):
    global _prov
    if _prov is None:
        _prov = yaml.safe_load((CONF / "provenance.yaml").read_text())
    sid, st, src = row["sid"] or "", row["source_type"] or "", (row["source"] or "").lower()
    for level in ("primary", "expert"):
        rules = _prov.get(level) or {}
        if st in (rules.get("source_types") or []) or sid in (rules.get("sids") or []) \
                or any(s.lower() in src for s in rules.get("source_contains") or []):
            return level
    return "press"


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\s+-\s+[^-]{2,60}$", "", (t or "").lower())).strip()[:90]


def retrieve(L, days, max_docs, queries):
    """Evidence: topic/source matches in the window + search hits for the lens queries + pinned docs."""
    from .index import available, embed, search
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    topics, sids = L.get("topics") or [], L.get("sids") or []
    conds, args = [], []
    if topics:
        conds.append("(" + " OR ".join("topics LIKE ?" for _ in topics) + ")")
        args += [f'%"{t}"%' for t in topics]
    if sids:
        conds.append(f"sid IN ({','.join('?' * len(sids))})")
        args += sids
    cand = {}
    with db(readonly=True) as c:
        if conds:
            for r in c.execute(f"""SELECT * FROM docs WHERE COALESCE(published, substr(ingested,1,10)) >= ? AND llm_allowed=1
                                   AND (COALESCE(rel,0) >= ? OR source_type IN ('transcript','filing'))
                                   AND ({' OR '.join(conds)})""", [since, L.get("min_rel", 2)] + args):
                cand[r["id"]] = {"row": r, "hits": 0}
        for q in queries:
            for h in search(q, limit=12, since=since):
                r = c.execute("SELECT * FROM docs WHERE id=?", (h["id"],)).fetchone()
                if r and r["llm_allowed"]:
                    cand.setdefault(r["id"], {"row": r, "hits": 0})["hits"] += 1
        for pid in L.get("pinned") or []:
            r = c.execute("SELECT * FROM docs WHERE id=?", (pid,)).fetchone()
            if r:
                cand.setdefault(pid, {"row": r, "hits": 0})["pinned"] = True
    out = []
    for d in cand.values():
        r = d["row"]
        d["prov"] = provenance(r)
        age = (dt.date.today() - dt.date.fromisoformat((r["published"] or r["ingested"])[:10])).days
        d["score"] = (100 if d.get("pinned") else 0) + 2 * (r["rel"] or 1) + 3 * d["hits"] \
            + {"primary": 3, "expert": 2, "press": 0}[d["prov"]] + (2 if age <= L.get("recent_days", 7) else 0)
        out.append(d)
    out.sort(key=lambda d: -d["score"])
    seen, picked = set(), []
    for d in out:
        k = _norm_title(d["row"]["title"])
        if k not in seen:
            seen.add(k)
            picked.append(d)
        if len(picked) >= max_docs:
            break
    focus = embed([L["scope"]] + queries[:6], kind="query").mean(axis=0) if available() else None
    with db(readonly=True) as c:
        for d in picked:
            r = d["row"]
            body = re.sub(r"^\s*# .*\n", "", body_text(r, c), count=1).strip()
            cap = 4500 if d["prov"] == "primary" or r["source_type"] == "transcript" else 2400
            ch = c.execute("SELECT idx, text, emb FROM chunks WHERE doc_id=?", (r["id"],)).fetchall() if focus is not None else []
            if len(body) <= cap or not ch:
                d["excerpt"] = body[:cap]
                continue
            m = np.frombuffer(b"".join(x["emb"] for x in ch), dtype=np.float32).reshape(len(ch), -1)
            take, n = [], 0
            for i in np.argsort(-(m @ focus)):
                t = re.sub(r"^.*?\n\n", "", ch[i]["text"], count=1)
                if n + len(t) > cap:
                    break
                take.append((ch[i]["idx"], t))
                n += len(t)
            d["excerpt"] = "\n[…]\n".join(t for _, t in sorted(take)) or body[:cap]
    return picked


def evidence_block(picked):
    return "\n\n".join(
        f"<doc ref=\"D{i}\" date=\"{d['row']['published']}\" provenance=\"{d['prov']}\" type=\"{d['row']['source_type']}\" "
        f"source=\"{d['row']['source']}\">\nTITLE: {d['row']['title']}\n"
        f"{d['excerpt'] or '(headline only — a claim from this doc may say only what the headline says)'}\n</doc>"
        for i, d in enumerate(picked, 1))


def _prompt(name, **kw):
    t = (CONF / f"{name}.md").read_text()
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def resolve(items, picked):
    """Map D-refs to docs; drop items with no valid citation. Returns (kept, n_dropped)."""
    refs = {f"D{i}": d for i, d in enumerate(picked, 1)}
    kept, dropped = [], 0
    for it in items:
        ds = [refs[x] for x in dict.fromkeys(str(x).strip() for x in it.get("docs") or []) if x in refs]
        if not ds or not (it.get("text") or "").strip():
            dropped += 1
            continue
        it["_docs"] = ds
        it["prov"] = min((d["prov"] for d in ds), key=PROV_RANK.get)
        kept.append(it)
    return kept, dropped


def verify(claims, model, batch=10):
    """Check each claim against the excerpts it cites. Returns (kept, dropped[(claim, why)])."""
    kept, dropped = [], []
    for s in range(0, len(claims), batch):
        part = claims[s:s + batch]
        blocks = []
        for i, cl in enumerate(part):
            ex = "\n".join(f"  <excerpt doc=\"{d['row']['id']}\" source=\"{d['row']['source']}\" date=\"{d['row']['published']}\">"
                           f"\nTITLE: {d['row']['title']}\n{d['excerpt'] or '(headline only)'}\n  </excerpt>" for d in cl["_docs"])
            blocks.append(f"<claim i=\"{i}\">\n{cl['text']}\n{ex}\n</claim>")
        verdicts, err = None, ""
        for _ in range(2):
            try:
                verdicts = {int(v["i"]): v for v in parse_json(claude(_prompt("verify", claims="\n\n".join(blocks)), model, timeout=600))}
                break
            except Exception as e:
                err = str(e)
        reasons = reason_verdicts([(cl["text"], (verdicts or {}).get(i)) for i, cl in enumerate(part)], model)
        for i, cl in enumerate(part):
            v = (verdicts or {}).get(i)
            if reasons.get(i, bool(v) and v.get("supported") is True):
                kept.append(cl)
            else:
                dropped.append((cl, (v or {}).get("why") or (f"verifier unavailable: {err[:120]}" if verdicts is None else "no verdict")))
    return kept, dropped


def reason_verdicts(items, model):
    """A verdict must follow its own reason. Returns {i: bool} from the reasons alone."""
    blocks = [f"<item i=\"{i}\">\nCLAIM: {text}\nVERDICT: {'supported' if v.get('supported') is True else 'not supported'}\n"
              f"REASON: {v['why']}\n</item>" for i, (text, v) in enumerate(items) if v and v.get("why")]
    if not blocks:
        return {}
    try:
        return {int(r["i"]): r["supported"] is True
                for r in parse_json(claude(_prompt("reconcile", items="\n\n".join(blocks)), model, timeout=600))}
    except Exception:
        return {}


def claim_line(cl):
    return f"- {cl['text'].strip()} " + " ".join(f"[doc:{d['row']['id']}]" for d in cl["_docs"]) + f" ({cl['prov']})"


def sources_block(claims):
    seen = {}
    for cl in claims:
        for d in cl["_docs"]:
            seen[d["row"]["id"]] = d
    return "\n".join(f"- [doc:{did}] {d['row']['title']} — {d['row']['source']}, {d['row']['published']} ({d['prov']})"
                     + (f" <{d['row']['url']}>" if d["row"]["url"] else "")
                     for did, d in sorted(seen.items(), key=lambda x: x[1]["row"]["published"] or "", reverse=True))


def parse_claims(md):
    out = []
    for line in md.splitlines():
        m = CLAIM_RE.match(line.strip())
        if m:
            out.append((m.group(1).strip(), frozenset(DOC_RE.findall(m.group(2))), m.group(3)))
    return out


def _write_run(L, mode, stats):
    (L["dir"] / "runs").mkdir(exist_ok=True)
    p = L["dir"] / "runs" / f"{dt.date.today().isoformat()}-{mode}.json"
    p.write_text(json.dumps(stats, indent=2, default=str))


def _maybe_commit(L, msg):
    if not (cfg("pipeline").get("lenses") or {}).get("git_commit"):
        return
    rel = str(L["dir"].relative_to(ROOT))
    subprocess.run(["git", "add", "--", rel], cwd=ROOT)
    subprocess.run(["git", "commit", "-q", "-m", msg, "--", rel], cwd=ROOT)


def _sections(out, picked):
    allc, uncited = [], 0
    for sec in out.get("sections") or []:
        kept, d = resolve(sec.get("claims") or [], picked)
        uncited += d
        for cl in kept:
            cl["_section"] = sec.get("heading") or "Other"
        allc += kept
    return allc, uncited


def build_state(slug):
    L, M = load(slug), models()
    today = dt.date.today().isoformat()
    window = L.get("window_days", 30)
    queries = L.get("queries") or []
    picked = retrieve(L, window, L.get("max_docs", 60), queries)
    if len(picked) < L.get("min_docs", 5):
        print(f"{slug}: only {len(picked)} documents in the window — not enough to build a state yet")
        return None
    fw = L["dir"] / "frameworks.md"
    prompt = _prompt("state", lens=L["name"], scope=L["scope"], sections="\n".join(f"- {s}" for s in L["sections"]),
                     frameworks=fw.read_text()[:20000] if fw.exists() and fw.read_text().strip() else "(not drafted yet)",
                     today=today, window=window, evidence=evidence_block(picked), reader=cfg("topics").get("reader", ""))
    out = parse_json(claude(prompt, M["state"]))
    allc, uncited = _sections(out, picked)
    verified, dropped = verify(allc, M["verify"])
    oq, _ = resolve([dict(text=q.get("q") or q.get("text"), docs=q.get("docs")) for q in out.get("open_questions") or []], picked)
    props, _ = resolve(out.get("framework_proposals") or [], picked)

    prev = (L["dir"] / "state.md").read_text() if (L["dir"] / "state.md").exists() else ""
    prev_docs = set().union(*[c[1] for c in parse_claims(prev)]) if parse_claims(prev) else set()
    new_claims = [cl for cl in verified if {d["row"]["id"] for d in cl["_docs"]} - prev_docs]
    provc = {p: sum(cl["prov"] == p for cl in verified) for p in PROV_RANK}
    since = (dt.date.today() - dt.timedelta(days=window)).isoformat()
    md = [f"# {L['name']} — state",
          f"_Built {today} from {len(picked)} documents ({since} → {today}). Every claim checked against its cited excerpt. "
          f"Rebuilt weekly from evidence only; never edited by hand._",
          f"_Claims: {len(verified)} kept · {len(dropped)} dropped by verification · {uncited} dropped for no citation. "
          f"Provenance: primary {provc['primary']} · expert {provc['expert']} · press {provc['press']}._", ""]
    for heading in dict.fromkeys(cl["_section"] for cl in verified):
        md += [f"## {heading}", ""] + [claim_line(cl) for cl in verified if cl["_section"] == heading] + [""]
    if oq:
        md += ["## Open questions", ""] + [f"- {q['text'].strip()} " + " ".join(f"[doc:{d['row']['id']}]" for d in q["_docs"]) for q in oq] + [""]
    md += ["## Sources", "", sources_block(verified), ""]
    (L["dir"] / "state.md").write_text("\n".join(md))

    cl_path = L["dir"] / "changelog.md"
    if not cl_path.exists() or not cl_path.read_text().strip():
        cl_path.write_text(f"# {L['name']} — changelog\n\n_Append-only. Each weekly run adds the claims that cite a document "
                           f"the previous state didn't._\n")
    entry = [f"\n## {today} — weekly state", f"_{len(new_claims)} new · {len(verified) - len(new_claims)} carried · "
             f"{len(dropped)} dropped by verification._", ""] + [claim_line(cl) for cl in new_claims]
    if dropped:
        entry += ["", f"### Dropped by the fact-checker ({len(dropped)}) — not claims", ""] + [
            f"- {cl['text'].strip()} — {why}" for cl, why in dropped]
    with cl_path.open("a") as f:
        f.write("\n".join(entry) + "\n")

    if props:
        (L["dir"] / "proposals").mkdir(exist_ok=True)
        (L["dir"] / "proposals" / f"{today}-frameworks.md").write_text(
            f"# Proposed frameworks changes — {L['name']} — {today}\n\n_Not applied. Approve or reject; approved changes are "
            f"copied into frameworks.md by hand (or by an agent on your say-so)._\n\n" + "\n".join(
                f"- **{p.get('change', 'modify')}** · {p.get('section', '')}: {p['text'].strip()} — why: {p.get('why', '').strip()} "
                + " ".join(f"[doc:{d['row']['id']}]" for d in p["_docs"]) for p in props) + "\n")
    stats = {"lens": slug, "date": today, "mode": "state", "docs_retrieved": len(picked), "claims_kept": len(verified),
             "dropped_verification": len(dropped), "dropped_uncited": uncited, "new_claims": len(new_claims),
             "provenance": provc, "open_questions": [q["text"] for q in oq], "proposals": len(props)}
    _write_run(L, "state", stats)
    _maybe_commit(L, f"lens {slug}: weekly state {today} — {len(verified)} claims (+{len(new_claims)} new)")
    print(f"{slug}: {len(picked)} docs → {len(verified)} claims, {len(dropped)} dropped, {uncited} uncited, "
          f"{len(new_claims)} new, {len(props)} proposals")
    return stats


def draft_frameworks(slug):
    L, M = load(slug), models()
    fw = L["dir"] / "frameworks.md"
    if fw.exists() and parse_claims(fw.read_text()):
        raise SystemExit(f"{slug}: frameworks.md already has claims — it changes only via proposals")
    today = dt.date.today().isoformat()
    picked = retrieve(L, L.get("framework_days", 365), L.get("framework_docs", 80), L.get("framework_queries") or L.get("queries") or [])
    out = parse_json(claude(_prompt("frameworks", lens=L["name"], scope=L["scope"], today=today,
                                    sections="\n".join(f"- {s}" for s in L["framework_sections"]),
                                    evidence=evidence_block(picked), reader=cfg("topics").get("reader", "")), M["frameworks"]))
    allc, uncited = _sections(out, picked)
    verified, dropped = verify(allc, M["verify"])
    gaps = [g for g in out.get("gaps") or [] if isinstance(g, str)]
    md = [f"# {L['name']} — frameworks", "",
          "> Model-drafted, pending your review. Every line is cited and was checked against its source. From now on this "
          "file changes only through proposals in `proposals/` that you approve.", "",
          f"_Drafted {today} · {len(picked)} documents · {len(verified)} claims · {len(dropped)} dropped by verification._", ""]
    for heading in dict.fromkeys(cl["_section"] for cl in verified):
        md += [f"## {heading}", ""] + [claim_line(cl) for cl in verified if cl["_section"] == heading] + [""]
    if gaps:
        md += ["## Gaps (no source yet — not claims)", ""] + [f"- {g}" for g in gaps] + [""]
    md += ["## Sources", "", sources_block(verified), ""]
    fw.write_text("\n".join(md))
    _write_run(L, "frameworks", {"lens": slug, "date": today, "claims_kept": len(verified), "dropped": len(dropped), "gaps": gaps})
    _maybe_commit(L, f"lens {slug}: frameworks drafted {today}")
    print(f"{slug} frameworks: {len(picked)} docs → {len(verified)} claims, {len(dropped)} dropped, {len(gaps)} gaps")


def review_frameworks(slug, days=35):
    L, M = load(slug), models()
    fw = L["dir"] / "frameworks.md"
    today = dt.date.today().isoformat()
    picked = retrieve(L, days, L.get("max_docs", 60), (L.get("framework_queries") or []) + (L.get("queries") or []))
    out = parse_json(claude(_prompt("review", lens=L["name"], frameworks=fw.read_text()[:30000], today=today,
                                    evidence=evidence_block(picked)), M["frameworks"]))
    props, uncited = resolve(out.get("proposals") or [], picked)
    verified, dropped = verify(props, M["verify"])
    (L["dir"] / "proposals").mkdir(exist_ok=True)
    (L["dir"] / "proposals" / f"{today}-monthly-review.md").write_text(
        f"# Monthly frameworks review — {L['name']} — {today}\n\n_Proposals only; frameworks.md is unchanged. "
        f"{len(verified)} kept, {len(dropped)} dropped by verification._\n\n" + "\n".join(
            f"- **{p.get('change', 'modify')}** · {p.get('section', '')}: {p['text'].strip()} — why: {p.get('why', '').strip()} "
            + " ".join(f"[doc:{d['row']['id']}]" for d in p["_docs"]) for p in verified) + "\n")
    print(f"{slug} review: {len(verified)} proposals ({len(dropped)} dropped)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        for d in lens_dirs():
            L = load(d.name)
            print(f"{d.name:18} {L['name']}")
    elif a[0] == "state":
        for s in ([d.name for d in lens_dirs()] if a[1] == "all" else [a[1]]):
            build_state(s)
    elif a[0] == "draft-frameworks":
        draft_frameworks(a[1])
    elif a[0] == "review-frameworks":
        review_frameworks(a[1], days=int(a[a.index("--days") + 1]) if "--days" in a else 35)
    else:
        print(__doc__)
