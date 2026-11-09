"""Sunday "What changed" email: how each lens moved this week.

Per lens: the claims new this week (from changelog.md), open questions, and any frameworks proposals waiting for
your approval — then a short model-written opener ("the three shifts that matter") from config/briefs/whatchanged.md.
No model call when nothing changed (and no email).

Usage: python -m kbase.whatchanged [--dry]
"""
import datetime as dt
import json
import re
import sys

from .core import CONFIG, cfg
from .lenses import lens_dirs, load
from .llm import claude
from .notify import render_html, send


def week_entries(text, since):
    """Changelog sections dated on/after `since`, without the dropped lists."""
    out = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        date = block[:10]
        if date >= since:
            out.append(re.split(r"(?m)^### Dropped", block)[0].strip())
    return out


def digest(slug, since):
    L = load(slug)
    d = L["dir"]
    cl = d / "changelog.md"
    entries = week_entries(cl.read_text(), since) if cl.exists() else []
    claims = [ln for e in entries for ln in e.splitlines() if ln.startswith("- ")]
    props = [p for p in sorted((d / "proposals").glob("*.md")) if p.stem[:10] >= since] if (d / "proposals").exists() else []
    runs = [json.loads(p.read_text()) for p in sorted((d / "runs").glob("*-state.json")) if p.stem[:10] >= since] if (d / "runs").exists() else []
    oq = runs[-1].get("open_questions", []) if runs else []
    return {"slug": slug, "name": L["name"], "claims": claims, "proposals": props, "open_questions": oq}


def main(argv=None):
    dry = "--dry" in (argv or sys.argv[1:])
    today = dt.date.today()
    since = (today - dt.timedelta(days=7)).isoformat()
    ds = [digest(p.name, since) for p in lens_dirs()]
    if not any(d["claims"] or d["proposals"] for d in ds):
        print("whatchanged: nothing changed this week — no email")
        return None
    body = []
    for d in ds:
        body.append(f"## {d['name']}")
        body.append(f"_{len(d['claims'])} new claims this week._\n")
        body += [re.sub(r"\s*\[doc:[^\]]+\]", "", c) for c in d["claims"][:12]]
        if d["open_questions"]:
            body += ["", "**Open questions**", ""] + [f"- {re.sub(r'\s*\[doc:[^\]]+\]', '', q)}" for q in d["open_questions"]]
        for p in d["proposals"]:
            body += ["", f"**Proposed frameworks changes waiting for your OK** (`{p.relative_to(CONFIG.parent)}`)", ""]
            body += [re.sub(r"\s*\[doc:[^\]]+\]", "", ln) for ln in p.read_text().splitlines() if ln.startswith("- ")]
        body.append("")
    md_body = "\n".join(body)
    model = (cfg("pipeline").get("models") or {}).get("whatchanged", "sonnet")
    opener = claude((CONFIG / "briefs" / "whatchanged.md").read_text() + f"\n\nREADER PROFILE:\n{cfg('topics').get('reader', '')}"
                    f"\n\nTHIS WEEK:\n{md_body}", model).strip()
    md = f"{opener}\n\n{md_body}"
    subject = f"What changed — week of {today:%b %-d}"
    where = send(subject, md, render_html(subject, md), dry=dry)
    print(f"whatchanged → {where}")
    return where


if __name__ == "__main__":
    main()
