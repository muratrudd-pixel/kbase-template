"""Deliver an email: EMAIL_MODE=file (default) writes it to outbox/, EMAIL_MODE=smtp sends it.

SMTP settings come from .env (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO). Use
your provider's SMTP host on port 465 and an app password (Google Account → Security → App passwords). Never commit .env.
"""
import datetime as dt
import html as htmllib
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

from .core import OUTBOX, env, slug

CSS = ("body{font:16px/1.55 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1e1e1c;max-width:680px;margin:24px auto;padding:0 16px}"
       "h1{font-size:24px}h2{font-size:18px;margin-top:28px;border-bottom:1px solid #e6e0d4;padding-bottom:4px}"
       "a{color:#b4532a}li{margin:6px 0}.foot{color:#888;font-size:12px;margin-top:32px}")


def render_html(subject, md, footer=""):
    body = markdown.markdown(md, extensions=["extra", "sane_lists"])
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{htmllib.escape(subject)}</title><style>{CSS}</style>"
            f"</head><body><h1>{htmllib.escape(subject)}</h1>{body}<div class='foot'>{htmllib.escape(footer)}</div></body></html>")


def send(subject, text, html=None, to=None, dry=False):
    """Returns where the email went (a file path or the recipient)."""
    mode = "file" if dry else env("EMAIL_MODE", "file")
    if mode == "file":
        stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        base = OUTBOX / f"{stamp}-{slug(subject, 50)}"
        base.with_suffix(".md").write_text(f"Subject: {subject}\n\n{text}\n")
        if html:
            base.with_suffix(".html").write_text(html)
        return str(base.with_suffix(".html" if html else ".md"))
    host, port = env("SMTP_HOST", "smtp.example.com"), int(env("SMTP_PORT", "465"))
    user, pw = env("SMTP_USER"), env("SMTP_PASSWORD")
    sender, rcpt = env("EMAIL_FROM", user), to or env("EMAIL_TO")
    if not (user and pw and rcpt):
        raise SystemExit("EMAIL_MODE=smtp needs SMTP_USER, SMTP_PASSWORD and EMAIL_TO in .env")
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(re.sub(r"\n{3,}", "\n\n", text), "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))
    msg["Subject"], msg["From"], msg["To"] = subject, sender, rcpt
    with smtplib.SMTP_SSL(host, port, timeout=60) as s:
        s.login(user, pw)
        s.sendmail(sender, [rcpt], msg.as_string())
    return rcpt
