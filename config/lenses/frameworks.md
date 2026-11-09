You are drafting the evergreen FRAMEWORKS file for one lens of the reader's personal knowledge base.
Lens: {{lens}}
Scope: {{scope}}
Today: {{today}}.
Reader: {{reader}}

Frameworks = how this area works and what to watch: the structure of the field, the key mechanisms and drivers, the
main players and how they differ, the rules and numbers that anchor judgement. It is NOT news. It should still be
true in six months. The reader will review it; until then nothing changes without their OK.

EVIDENCE — the only material you may use. Each document has a ref (D1, D2, ...), a date, a provenance tag and an excerpt.
{{evidence}}

RULES (hard; code enforces them and deletes anything that breaks them):
1. Every claim is ONE atomic statement supported by the cited excerpt(s). A checker reads only those excerpts; if the
   claim isn't plainly there, it is deleted. No outside knowledge — even things you are sure of. A document marked
   "(headline only)" supports only what its headline says. If an important piece of the framework has no support in
   the evidence, list it under "gaps" (a short phrase, no claim, no citation).
2. Cite 1–3 refs per claim ("docs": ["D7"]). No ref = deleted. Prefer primary sources, then expert, then press.
3. Durable over dated: prefer structural facts and stable numbers with their date. Attribute views to who holds them.
4. 20–50 claims. Plain English. No hype.
5. Use these section headings (skip one only if the evidence has nothing):
{{sections}}

Return ONLY JSON, no prose:
{"sections":[{"heading":"...","claims":[{"text":"...","docs":["D1","D4"]}]}], "gaps":["..."]}
