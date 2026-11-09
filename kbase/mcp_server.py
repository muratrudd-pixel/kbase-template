"""Read-only MCP tools so Claude (Desktop, Code) can search the knowledge base while answering you.

Register it with Claude Code (from the repo folder):
  claude mcp add kbase -- "$PWD/.venv/bin/python" -m kbase.mcp_server
"""
import json

from mcp.server.fastmcp import FastMCP

from .core import db, read_body
from .index import search

mcp = FastMCP("kbase")


@mcp.tool()
def search_archive(query: str, limit: int = 8, source_type: str | None = None, since: str | None = None) -> str:
    """Search the knowledge base (articles, newsletters, podcast transcripts) by keyword and meaning.
    source_type: news|newsletter|transcript. since: YYYY-MM-DD. Call get_document(id) for full text.
    Cite titles and dates when answering."""
    res = search(query, limit=min(limit, 25), source_type=source_type, since=since)
    if not res:
        return "No matches."
    return "\n\n".join(f"[{r['id']}] {r['title']} — {r['source']} ({r['type']}, {r['published']})"
                       + (f"\n{r['url']}" if r["url"] else "") + f"\n{r['snippet']}" for r in res)


@mcp.tool()
def get_document(doc_id: str, max_chars: int = 30000) -> str:
    """Full text of one document by id."""
    with db(readonly=True) as c:
        d = c.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        return "Not found."
    body = read_body(d["path"])
    head = f"{d['title']}\nSource: {d['source']} | {d['source_type']} | {d['published']}\nURL: {d['url'] or '-'}\n"
    return head + "\n" + body[:max_chars] + ("\n…[truncated]" if len(body) > max_chars else "")


@mcp.tool()
def recent_documents(days: int = 7, source_type: str | None = None, limit: int = 40) -> str:
    """Documents added in the last N days, newest first."""
    q, args = "SELECT id,title,source,source_type,published FROM docs WHERE ingested >= date('now', ?)", [f"-{int(days)} days"]
    if source_type:
        q += " AND source_type=?"
        args.append(source_type)
    q += " ORDER BY ingested DESC LIMIT ?"
    args.append(min(limit, 200))
    with db(readonly=True) as c:
        rows = c.execute(q, args).fetchall()
    return "\n".join(f"[{r['id']}] {r['title']} — {r['source']} ({r['source_type']}, {r['published']})" for r in rows) or "Nothing."


@mcp.tool()
def top_items(days: int = 7, topic: str | None = None, min_relevance: int = 2, limit: int = 30) -> str:
    """Highest-relevance tagged items in the last N days, optionally for one topic (see config/topics.yaml)."""
    q = "SELECT id,title,source,published,topics,rel FROM docs WHERE published >= date('now', ?) AND COALESCE(rel,0) >= ?"
    args = [f"-{int(days)} days", min_relevance]
    if topic:
        q += " AND topics LIKE ?"
        args.append(f'%"{topic}"%')
    q += " ORDER BY rel DESC, published DESC LIMIT ?"
    args.append(min(limit, 100))
    with db(readonly=True) as c:
        rows = c.execute(q, args).fetchall()
    return "\n".join(f"[{r['id']}] r{r['rel']} {r['title']} — {r['source']} ({r['published']}) {json.loads(r['topics'] or '[]')}"
                     for r in rows) or "Nothing."


@mcp.tool()
def list_lenses() -> str:
    """The lenses (cited, fact-checked views of one area) and when each was last built."""
    from .lenses import lens_dirs, load
    out = []
    for d in lens_dirs():
        L = load(d.name)
        st = d / "state.md"
        out.append(f"{d.name}: {L['name']} — {L['scope'][:120]}" + (" (state built)" if st.exists() and st.read_text().strip() else " (no state yet)"))
    return "\n".join(out) or "No lenses."


@mcp.tool()
def get_lens(slug: str, part: str = "state") -> str:
    """One lens file: part = state | frameworks | changelog."""
    from .lenses import load
    p = load(slug)["dir"] / f"{part}.md"
    return p.read_text() if p.exists() else f"No {part}.md yet."


def main():
    mcp.run()


if __name__ == "__main__":
    main()
