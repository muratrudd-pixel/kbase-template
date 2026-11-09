# Lenses

A lens is a cited, fact-checked view of one area of your knowledge base. Each folder holds:

| file | what it is | who changes it |
|---|---|---|
| `lens.yaml` | name, scope, topics, source ids, search queries, section headings | you |
| `frameworks.md` | evergreen "how this area works" (empty until you run `draft-frameworks`) | the model once, then only proposals you approve |
| `state.md` | what's true now, rebuilt weekly from this week's evidence | `kb run lenses` |
| `changelog.md` | append-only: claims new each week | `kb run lenses` |
| `proposals/` | proposed frameworks changes, never auto-applied | the weekly run |
| `runs/` | per-run stats (read by the Sunday email) | the weekly run |

Every claim line looks like `- <claim> [doc:<id>] (primary|expert|press)` and was checked against the cited excerpt.

New lens: copy a folder, edit `lens.yaml`, empty the .md files, then once you have a few weeks of documents:
`.venv/bin/python -m kbase.lenses draft-frameworks <slug>`.
