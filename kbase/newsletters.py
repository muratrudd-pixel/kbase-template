"""Archive newsletters from one mail folder/label over IMAP (read-only; nothing is marked read or moved).

Set up: in your mail client, make a label (e.g. "Newsletters") and a filter that files newsletters there. Then set
IMAP_HOST / IMAP_USER / IMAP_PASSWORD / IMAP_FOLDER in .env (Gmail: use an app password; the label name is the folder).
Skipped quietly when IMAP isn't configured.

Senders listed in config/pipeline.yaml newsletters.link_only_senders are stored as links only and never sent to a
model (for publishers whose terms forbid AI processing).
"""
import datetime as dt
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

import html2text

from .core import cfg, env, write_doc

_h2t = html2text.HTML2Text()
_h2t.body_width = 0
_h2t.ignore_images = True


def _dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _body(msg):
    html, text = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        s = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/html" and html is None:
            html = s
        elif part.get_content_type() == "text/plain" and text is None:
            text = s
    md = _h2t.handle(html) if html else (text or "")
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def _links(md, limit=40):
    out = []
    for label, url in re.findall(r"\[([^\]]{6,200})\]\((https?://[^)\s]+)\)", md):
        if not any(x in label.lower() for x in ("unsubscribe", "view in browser", "manage", "privacy")):
            out.append(f"- [{re.sub(r'\s+', ' ', label).strip()}]({url})")
        if len(out) >= limit:
            break
    return "\n".join(out)


def main():
    host, user, pw = env("IMAP_HOST"), env("IMAP_USER"), env("IMAP_PASSWORD")
    if not (host and user and pw):
        print("newsletters: IMAP not configured in .env — skipped")
        return 0
    conf = cfg("pipeline").get("newsletters") or {}
    folder = env("IMAP_FOLDER", "Newsletters")
    link_only = [s.lower() for s in conf.get("link_only_senders") or []]
    since = (dt.date.today() - dt.timedelta(days=conf.get("lookback_days", 3))).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL(host)
    M.login(user, pw)
    typ, _ = M.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        raise SystemExit(f"IMAP folder not found: {folder}")
    _, data = M.search(None, "SINCE", since)
    added = 0
    for num in data[0].split():
        _, got = M.fetch(num, "(BODY.PEEK[])")
        msg = email.message_from_bytes(got[0][1])
        mid = msg.get("Message-ID", "").strip() or f"{num.decode()}-{msg.get('Date')}"
        name, addr = parseaddr(_dec(msg.get("From")))
        try:
            pub = parsedate_to_datetime(msg.get("Date")).date().isoformat()
        except Exception:
            pub = None
        md = _body(msg)
        lo = any(s in addr.lower() for s in link_only)
        body = ("Link-only source (publisher terms prohibit AI processing).\n\n" + _links(md)) if lo else md
        doc = write_doc(source=name or addr, source_type="newsletter", title=_dec(msg.get("Subject")) or "(no subject)",
                        body=body, published=pub, dedupe=f"msgid:{mid}", llm_allowed=not lo,
                        license="link_only" if lo else "personal_use", sid=f"newsletter:{addr.lower()}")
        added += bool(doc)
    M.logout()
    print(f"newsletters: scanned {len(data[0].split())}, added {added}")
    return added


if __name__ == "__main__":
    main()
