# kbase-template — a personal knowledge base that reads for you

A small, self-hosted pipeline. It follows the sources you choose (RSS, Google News searches, newsletters, podcasts
that publish transcripts), files every item in a searchable archive, has a model tag each one for topic and
relevance, keeps cited and fact-checked "lenses" on the areas you care about, and writes you short brief emails.
It runs on any always-on machine: a spare Mac, a Linux box or a home server.

## What you get

- **Daily Brief** (5am): the handful of things worth knowing, with links.
- **Off the Clock**: the fun read covering film, culture and the interesting internet.
- **What changed** (Sundays): how each lens moved this week, plus framework changes waiting for your OK.
- **Ask Claude**: a read-only MCP tool lets Claude Desktop or Claude Code search your archive while answering you.

## Architecture

```
 sources.yaml ─┐
 newsletters ──┼─► ingest ─► archive/raw/*.md  ─► index (SQLite FTS5 + optional local embeddings)
 podcasts ─────┘      │                         │
                      └─► fulltext (thin items) └─► tagger (topics, relevance 0–3; haiku)
                                                        │
              lenses/<slug>/ ◄── weekly state build ◄───┤  every claim cites [doc:id], checked by a 2nd model
                     │                                  │
                     ▼                                  ▼
          Sunday "What changed"              briefs: daily · offclock ─► email (SMTP) or outbox/ files
                                                        ▲
                         MCP tools (search_archive, get_document, top_items, get_lens) ─► Claude
```

| module | job |
|---|---|
| `kbase/core.py` | paths, `.env`, SQLite schema, `write_doc()` (dedupe + blocklist) |
| `kbase/registry.py` | the source list (`config/sources.yaml` + `sources_added.yaml`) |
| `kbase/ingest.py` | RSS, Google News and podcast-transcript fetchers, with polite per-source intervals |
| `kbase/newsletters.py` | one IMAP folder/label, read-only |
| `kbase/fulltext.py` | article text for headline-only items (trafilatura) |
| `kbase/index.py` | keyword + embedding index, hybrid search |
| `kbase/tagger.py` | topics / relevance / event type from `config/topics.yaml` |
| `kbase/lenses.py` | frameworks, weekly state, changelog and proposals, with per-claim verification |
| `kbase/briefs.py` | brief emails from `config/briefs/<kind>.md` + `config/pipeline.yaml` |
| `kbase/whatchanged.py` | the Sunday email |
| `kbase/notify.py` | email out: `file` (default) or `smtp` |
| `kbase/mcp_server.py` | read-only MCP tools for Claude |
| `kbase/cli.py` | the `bin/kb` command |

Data never goes in git: `archive/`, `index/`, `logs/`, `outbox/` and `.env` are ignored.

## Setup (about 15 minutes)

1. Install Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
2. Install the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) and log in with `claude`. All model calls go through `claude -p`.
3. Clone this repo and `cd` into it.
4. `uv venv && uv pip install -r pyproject.toml` (or `uv sync`).
5. `cp .env.example .env` and set at least `EMAIL_TO`. Leave `EMAIL_MODE=file` for now.
6. Edit `config/topics.yaml`: describe yourself in `reader:`. This is the biggest lever on quality.
7. Edit `config/sources.yaml`: keep, cut or add sources (see below).
8. First ingest: `bin/kb run ingest`. This fetches, indexes and tags; expect a few minutes.
9. Check it: `bin/kb status` and `bin/kb sources`.
10. Dry-run a brief: `bin/kb brief daily --dry`, then open the newest file in `outbox/`.
11. *(Optional)* Turn on semantic search: install [Ollama](https://ollama.com), then `ollama pull nomic-embed-text`.
12. *(Optional)* Real email: set `EMAIL_MODE=smtp` plus the `SMTP_*` values. For Gmail that means an app password.
13. *(Optional)* Newsletters: create a mail label (e.g. "Newsletters") with a filter, then set the `IMAP_*` values.
14. Schedule it:
    - **macOS:** run `claude setup-token`, put the token in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`, then `.venv/bin/python deploy/install_launchd.py`.
    - **Linux:** use `deploy/cron.example`.
15. Hook up Claude: `claude mcp add kbase -- "$PWD/.venv/bin/python" -m kbase.mcp_server`.

After 2–3 weeks of documents, draft each lens's frameworks with `.venv/bin/python -m kbase.lenses draft-frameworks economics-fed`. Read the result and edit `lens.yaml` if the scope is off.

## Adding sources

```
bin/kb add rss https://example.com/feed.xml "Example Blog" economics
bin/kb add news '"interest rates" when:1d' "Google News: rates" economics
bin/kb add podcast https://example.substack.com/feed "Example Podcast" ai
```

Or edit `config/sources.yaml` directly. Each source has a stable `id` that is stored on its documents.

- **Mute a source:** `muted: true` plus a `why:`. It stops being fetched and stays searchable.
- **Publishers that forbid AI processing:** `link_only: true`. Only links are stored, and the item is never sent to a model.
- **Podcasts:** only episodes with a published transcript are stored, either the full text in the feed item (common on Substack) or a `<podcast:transcript>` link. For the rest you'd add local Whisper yourself.

## Adding a lens

Copy `lenses/economics-fed/` to `lenses/<new-slug>/` and edit `lens.yaml`:
- **What it covers:** name, scope, `topics:` from `config/topics.yaml`, source `sids:`, and search `queries:`.
- **How it's laid out:** the section headings.

Empty the `.md` files. The weekly `kb run lenses` builds `state.md` and appends to `changelog.md`. `frameworks.md` changes only through `proposals/` that you approve. See `lenses/README.md`.

## Adding a brief

Add a block under `briefs:` in `config/pipeline.yaml` (topics, window, quotas, model, lenses) and write its rules in `config/briefs/<kind>.md`. Then `bin/kb brief <kind> --dry`.

## Cost

- **Sources:** all free. No paid APIs, and nothing scrapes past a paywall or bot block.
- **Model calls:** run through `claude -p`, so they count against your Claude subscription rather than a separate API bill. Per week, roughly:
  - Tagging: hourly batches on haiku (cheap).
  - Briefs: one call per brief, per day.
  - Lenses: 1 state call (opus) plus about 5 verification calls (haiku) per lens.
- **To spend less:** move briefs or lenses to `sonnet` in `config/pipeline.yaml`, follow fewer sources, or raise `min_rel`.
- **Embeddings:** local (Ollama) and free.
- **Hardware:** any always-on machine. The archive grows about 50–150 MB a month with the example sources.

## Tests

`.venv/bin/python -m unittest discover tests` runs offline smoke tests (no network, no model calls).

## License

MIT. See `LICENSE`.
