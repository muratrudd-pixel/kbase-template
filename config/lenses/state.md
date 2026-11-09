You are building the weekly STATE file for one lens of the reader's personal knowledge base.
Lens: {{lens}}
Scope: {{scope}}
Today: {{today}}. Evidence window: the last {{window}} days (plus any pinned documents).
Reader: {{reader}}

The lens's evergreen FRAMEWORKS file is below for orientation only (what matters, how things work). Do NOT restate it,
and do NOT cite it — state.md is about what is true NOW according to this week's evidence.

<frameworks>
{{frameworks}}
</frameworks>

EVIDENCE — the only material you may use. Each document has a ref (D1, D2, ...), a date, a provenance tag and an excerpt.
{{evidence}}

RULES (hard; code enforces them and deletes anything that breaks them):
1. Every claim is ONE atomic, checkable statement of fact taken from the excerpt(s) it cites. A checker reads only the
   cited excerpts; if the claim isn't plainly supported there, it is deleted. No outside knowledge, no inference beyond
   the text, no forecasts unless the excerpt states the forecast and who made it. A document marked "(headline only)"
   supports only what its headline says — attribute it ("Reuters reported that …").
2. Cite with the doc refs ("docs": ["D3"]), 1–3 per claim. No ref = claim deleted.
3. Prefer primary documents; when press and primary say the same thing, cite the primary one.
4. Specific beats general: names, numbers, dates, who said it. Attribute opinions.
5. 10–40 claims total. Skip trivia and anything outside the scope. If sources conflict, write both claims, each cited.
6. Plain English. No hype words.
7. Use these section headings (skip a heading with nothing solid; never pad):
{{sections}}

Also return:
- "open_questions": 2–5 questions this week's evidence raises but does not answer (cite the docs that raise them).
- "framework_proposals": 0–3 changes to the FRAMEWORKS file that this week's evidence justifies (a new mechanism, a
  number that is now outdated). Each: section, change (add|modify|remove), text, why, docs. Usually 0–1.

Return ONLY JSON, no prose:
{"sections":[{"heading":"...","claims":[{"text":"...","docs":["D1"]}]}],
 "open_questions":[{"q":"...","docs":["D2"]}],
 "framework_proposals":[{"section":"...","change":"add","text":"...","why":"...","docs":["D4"]}]}
