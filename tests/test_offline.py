"""Offline smoke tests (no network, no model): python -m unittest discover tests"""
import os
import tempfile
import unittest

tmp = tempfile.mkdtemp()
os.environ.update(KB_ARCHIVE_DIR=f"{tmp}/archive", KB_INDEX_DIR=f"{tmp}/index", KB_LOG_DIR=f"{tmp}/logs",
                  KB_OUTBOX_DIR=f"{tmp}/outbox", EMAIL_MODE="file")

from kbase import core, index, registry  # noqa: E402
from kbase.briefs import scrub_links  # noqa: E402
from kbase.lenses import CLAIM_RE  # noqa: E402
from kbase.notify import render_html, send  # noqa: E402


class T(unittest.TestCase):
    def test_write_dedupe_search(self):
        a = core.write_doc(source="Test", source_type="news", title="Fed holds rates", body="The FOMC held rates steady.",
                           url="https://example.com/a", dedupe="url:a", sid="test")
        self.assertTrue(a)
        self.assertIsNone(core.write_doc(source="Test", source_type="news", title="x", body="y", dedupe="url:a"))
        index.update()
        self.assertEqual(index.search("FOMC")[0]["id"], a)

    def test_registry(self):
        self.assertEqual(len(registry.load()), 10)

    def test_scrub_links(self):
        md = "[ok](https://a.com/1) and [bad](https://evil.com/x)"
        self.assertEqual(scrub_links(md, {"https://a.com/1"}), "[ok](https://a.com/1) and bad")

    def test_claim_re(self):
        self.assertTrue(CLAIM_RE.match("- The Fed held rates. [doc:1999-01-01-x-y-abc123] (primary)"))

    def test_env_inline_comment(self):
        envf = core.Path(tmp) / "t.env"
        envf.write_text('KB_T1=2   # a comment\nKB_T2="a # b"\n')
        core._load_env(envf)
        self.assertEqual((os.environ["KB_T1"], os.environ["KB_T2"]), ("2", "a # b"))

    def test_email_to_file(self):
        p = send("Hello", "# hi", render_html("Hello", "# hi"))
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
