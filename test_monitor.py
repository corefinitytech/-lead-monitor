import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import monitor as m


class MonitorTests(unittest.TestCase):
    def test_rss_description(self):
        with patch.object(m, "get", return_value="<rss><channel><item><link>https://x/1</link><description>Need a developer</description></item></channel></rss>"):
            self.assertEqual(m.fetch_rss("x", "https://x")[0]["text"], "Need a developer")

    def test_atom_alternate_and_missing_id(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><link rel="self" href="https://x/feed"/><link rel="alternate" href="https://x/post"/><title>Ask</title><content>Body</content></entry></feed>'
        with patch.object(m, "get", return_value=xml):
            post = m.fetch_reddit("test")[0]
        self.assertEqual(post["url"], "https://x/post")
        self.assertEqual(post["id"], "reddit:https://x/post")

    def test_decimal_budget(self):
        p = dict(m.FIXTURE_POSTS[0], title="Need a developer", text="Budget $1.5k")
        self.assertIn("budget $1,500", m.score(p)[1])

    def test_hiring_word_boundary(self):
        self.assertEqual(m.score(dict(m.FIXTURE_POSTS[0], title="Hiringly news", text=""))[0], 0)

    def test_corrupt_state_fails(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            m.SEEN_FILE.write_text("broken")
            with self.assertRaises(ValueError):
                m.load_seen()

    def test_seen_does_not_forget_live_ids(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            seen = {str(i) for i in range(5001)}
            m.save_seen(seen)
            self.assertEqual(m.load_seen(), seen)

    def test_missing_credentials_fails(self):
        with patch.dict(m.os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            m.telegram("test")

    def test_hn_title_query_and_pagination(self):
        calls = []
        def get(url):
            calls.append(url)
            if "story%2Cauthor_whoishiring" in url:
                return json.dumps({"hits": [{"title": "Freelancer? Seeking freelancer?", "objectID": "1"}]})
            page = "page=1" in url
            return json.dumps({"nbPages": 2, "hits": [{"objectID": "3" if page else "2", "comment_text": "<p>SEEKING FREELANCER: Need a developer</p>"}]})
        with patch.object(m, "get", side_effect=get):
            posts = m.fetch_hn_freelancer_thread()
        self.assertEqual(len(posts), 2)
        self.assertIn("restrictSearchableAttributes=title", calls[0])

    def run_main(self, posts, send=None, limit=10):
        config = dict(m.CONFIG, subreddits=["test"], hn_freelancer_thread=False, hn_queries=[], extra_rss={}, max_alerts_per_run=limit)
        with patch.object(m, "CONFIG", config), patch.object(m, "FIXTURE", False), patch.object(m, "DRY", False), patch.object(m, "fetch_reddit", return_value=posts), patch.object(m.time, "sleep"), patch.object(m, "telegram", side_effect=send) as telegram, contextlib.redirect_stdout(io.StringIO()):
            m.main()
        return telegram

    def test_first_run_sends_one_live_message(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            sent = self.run_main(m.FIXTURE_POSTS)
            self.assertEqual(sent.call_count, 1)
            self.assertIn("6", sent.call_args.args[0])
            self.assertIn("live", sent.call_args.args[0])
            self.assertEqual(len(m.load_seen()), 6)

    def test_alert_limit_leaves_unsent_pending(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            m.save_seen(set())
            posts = [dict(m.FIXTURE_POSTS[0], id=str(i)) for i in range(3)]
            self.run_main(posts, limit=1)
            self.assertEqual(len(m.load_seen()), 1)

    def test_partial_delivery_is_saved(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            m.save_seen(set())
            posts = [dict(m.FIXTURE_POSTS[0], id=str(i)) for i in range(2)]
            with self.assertRaises(RuntimeError):
                self.run_main(posts, send=[None, RuntimeError("send failed")])
            self.assertEqual(m.load_seen(), {"0"})

    def test_retry_after_date(self):
        error = urllib.error.HTTPError("https://x", 429, "limit", {"Retry-After": "Thu, 22 Oct 2026 00:00:10 GMT"}, None)
        with patch.object(m.urllib.request, "urlopen", side_effect=[error, io.BytesIO(b"ok")]), patch.object(m.time, "time", return_value=1792627200), patch.object(m.time, "sleep") as sleep:
            self.assertEqual(m.get("https://x"), "ok")
            self.assertEqual(sleep.call_args.args[0], 10)

    def test_retry_exhaustion_and_long_retry_after(self):
        error = urllib.error.HTTPError("https://x", 429, "limit", {}, None)
        with patch.object(m.urllib.request, "urlopen", side_effect=error) as request, patch.object(m.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                m.get("https://x")
            self.assertEqual(request.call_count, 3)
        error.headers["Retry-After"] = "120"
        with patch.object(m.urllib.request, "urlopen", side_effect=error), patch.object(m.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                m.get("https://x")
            sleep.assert_not_called()

    def test_duplicate_sources_send_once(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            m.save_seen(set())
            sent = self.run_main([m.FIXTURE_POSTS[0]] * 2)
            self.assertEqual(sent.call_count, 1)

    def test_failed_activation_does_not_initialize(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"):
            with self.assertRaises(RuntimeError):
                self.run_main(m.FIXTURE_POSTS, send=RuntimeError("send failed"))
            self.assertFalse(m.SEEN_FILE.exists())

    def test_dry_run_sends_and_saves_nothing(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"), patch.object(m, "DRY", True), patch.object(m, "FIXTURE", False), patch.object(m, "CONFIG", dict(m.CONFIG, subreddits=["test"], hn_freelancer_thread=False, hn_queries=[])), patch.object(m, "fetch_reddit", return_value=m.FIXTURE_POSTS), patch.object(m.time, "sleep"), patch.object(m, "telegram") as send, contextlib.redirect_stdout(io.StringIO()):
            m.main()
            send.assert_not_called()
            self.assertFalse(m.SEEN_FILE.exists())

    def test_all_failed_sources_do_not_initialize(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "SEEN_FILE", Path(d)/"seen.json"), patch.object(m, "FIXTURE", False), patch.object(m, "DRY", False), patch.object(m, "CONFIG", dict(m.CONFIG, subreddits=["test"], hn_freelancer_thread=False, hn_queries=[])), patch.object(m, "fetch_reddit", side_effect=urllib.error.HTTPError("https://x", 403, "blocked", {}, None)), patch.object(m.time, "sleep"), patch.object(m, "telegram") as send, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                m.main()
            self.assertFalse(m.SEEN_FILE.exists())
            self.assertIn("every source failed", send.call_args.args[0])

    def test_telegram_errors_do_not_expose_token_url(self):
        with patch.dict(m.os.environ, {"TELEGRAM_BOT_TOKEN": "test-secret", "TELEGRAM_CHAT_ID": "123"}), patch.object(m.urllib.request, "urlopen", side_effect=urllib.error.URLError("https://api.telegram.org/bottest-secret/sendMessage")):
            with self.assertRaises(RuntimeError) as caught:
                m.telegram("hello")
            self.assertNotIn("test-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
