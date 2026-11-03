import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import rss_to_telegram as rt

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "feed.xml")
with open(FIXTURE, "rb") as _handle:
    FEED = _handle.read()

ID_A = "https://www.ansa.it/basilicata/notizie/a.html"
ID_B = "https://www.ansa.it/basilicata/notizie/b.html"
ID_C = "https://www.ansa.it/basilicata/notizie/c.html"
ID_D = "https://www.ansa.it/basilicata/notizie/d.html?a=1&b=2"
TOKEN = "123456:SECRET-token_value"


def fake_fetch(url, sleep):
    return FEED


class FakeSender:
    def __init__(self, fail_on=None):
        self.messages = []
        self.fail_on = fail_on  # indice (0-based) della chiamata che fallisce
        self.calls = 0

    def __call__(self, text):
        call = self.calls
        self.calls += 1
        if self.fail_on is not None and call == self.fail_on:
            raise rt.TelegramError("HTTP 400: Bad Request")
        self.messages.append(text)


def http_error(code, body):
    raw = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError("https://api.telegram.org/bot%s/sendMessage" % TOKEN,
                                  code, "error", {}, io.BytesIO(raw))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ok_response():
    return FakeResponse(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "sent.json")
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **overrides):
        config = {"token": TOKEN, "chat_id": "@test", "feed_url": "http://feed", "state_file": self.state,
                  "api_url": "http://api", "bootstrap": 0, "dry_run": False}
        config.update(overrides)
        return config

    def run_once(self, sender, **overrides):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rt.run(self.config(**overrides), sender, self.sleeps.append, fetch=fake_fetch)
        return code, out.getvalue()

    def saved_ids(self):
        with open(self.state, encoding="utf-8") as handle:
            return json.load(handle)["sent_ids"]

    def write_state(self, ids):
        rt.save_state(self.state, ids)


class ParseTests(unittest.TestCase):
    def test_sorted_oldest_first(self):
        items = rt.parse_feed(FEED)
        self.assertEqual([i["id"] for i in items], [ID_A, ID_B, ID_C, ID_D])

    def test_guid_falls_back_to_link(self):
        item = [i for i in rt.parse_feed(FEED) if i["title"] == "Notizia C senza guid"][0]
        self.assertEqual(item["id"], ID_C)

    def test_title_whitespace_normalized(self):
        self.assertEqual(rt.parse_feed(FEED)[1]["title"], "Notizia B")

    def test_html_escape(self):
        item = rt.parse_feed(FEED)[-1]
        self.assertEqual(
            rt.format_message(item),
            '<b>Notizia D: Potenza &amp; Matera &lt;oggi&gt; "speciale"</b>\n'
            "https://www.ansa.it/basilicata/notizie/d.html?a=1&amp;b=2")

    def test_message_format(self):
        item = rt.parse_feed(FEED)[0]
        self.assertEqual(rt.format_message(item), "<b>Notizia A</b>\n" + ID_A)

    def test_invalid_xml(self):
        with self.assertRaises(rt.FeedError):
            rt.parse_feed(b"<rss><channel><item>")

    def test_not_rss(self):
        with self.assertRaises(rt.FeedError):
            rt.parse_feed(b"<html><body/></html>")

    def test_items_without_date_do_not_crash(self):
        xml = (b"<rss version='2.0'><channel><item><title>X</title><link>http://x</link></item>"
               b"<item><title>Y</title><link>http://y</link><pubDate>bad</pubDate></item></channel></rss>")
        self.assertEqual([i["id"] for i in rt.parse_feed(xml)], ["http://y", "http://x"])


class FirstRunTests(BaseCase):
    def test_first_run_marks_all_without_sending(self):
        sender = FakeSender()
        code, _ = self.run_once(sender)
        self.assertEqual(code, 0)
        self.assertEqual(sender.messages, [])
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])

    def test_first_run_with_bootstrap(self):
        sender = FakeSender()
        code, _ = self.run_once(sender, bootstrap=2)
        self.assertEqual(code, 0)
        self.assertEqual(sender.messages, [rt.format_message(i) for i in rt.parse_feed(FEED)[2:]])
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])
        self.assertEqual(self.sleeps, [rt.SEND_DELAY])

    def test_bootstrap_larger_than_feed(self):
        sender = FakeSender()
        self.run_once(sender, bootstrap=50)
        self.assertEqual(len(sender.messages), 4)
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])

    def test_bootstrap_ignored_when_state_exists(self):
        self.write_state([ID_A, ID_B, ID_C, ID_D])
        sender = FakeSender()
        code, out = self.run_once(sender, bootstrap=3)
        self.assertEqual((code, sender.messages), (0, []))
        self.assertIn("ignorato", out)

    def test_bootstrap_interrupted_is_resumed(self):
        sender = FakeSender(fail_on=1)
        code, _ = self.run_once(sender, bootstrap=3)
        self.assertEqual(code, 1)
        self.assertEqual(self.saved_ids(), [ID_A, ID_B])
        sender2 = FakeSender()
        self.assertEqual(self.run_once(sender2)[0], 0)
        self.assertEqual(sender2.messages, [rt.format_message(i) for i in rt.parse_feed(FEED)[2:]])


class DedupTests(BaseCase):
    def test_only_new_items_sent_in_order(self):
        self.write_state([ID_A, ID_B])
        sender = FakeSender()
        code, _ = self.run_once(sender)
        self.assertEqual(code, 0)
        self.assertEqual(sender.messages, [rt.format_message(i) for i in rt.parse_feed(FEED)[2:]])
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])

    def test_second_run_sends_nothing(self):
        self.write_state([ID_A])
        self.run_once(FakeSender())
        sender = FakeSender()
        self.run_once(sender)
        self.assertEqual(sender.messages, [])

    def test_duplicate_items_in_feed_sent_once(self):
        doubled = FEED.replace(b"</channel>", b"<item><title>Notizia A bis</title><link>%s</link></item></channel>"
                               % ID_A.encode())
        self.write_state([])
        sender = FakeSender()
        with contextlib.redirect_stdout(io.StringIO()):
            rt.run(self.config(), sender, self.sleeps.append, fetch=lambda url, sleep: doubled)
        self.assertEqual(len(sender.messages), 4)
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])


class ErrorAndResumeTests(BaseCase):
    def test_stop_on_error_then_resume_without_duplicates(self):
        self.write_state([])
        sender = FakeSender(fail_on=2)
        code, out = self.run_once(sender)
        self.assertEqual(code, 1)
        self.assertEqual(len(sender.messages), 2)
        self.assertEqual(self.saved_ids(), [ID_A, ID_B])
        self.assertIn("interrotto", out)

        sender2 = FakeSender()
        self.assertEqual(self.run_once(sender2)[0], 0)
        self.assertEqual(sender2.messages, [rt.format_message(i) for i in rt.parse_feed(FEED)[2:]])
        self.assertEqual(self.saved_ids(), [ID_A, ID_B, ID_C, ID_D])

    def test_pause_between_messages(self):
        self.write_state([])
        self.run_once(FakeSender())
        self.assertEqual(self.sleeps, [rt.SEND_DELAY] * 3)


class StateTests(BaseCase):
    def test_trim_to_500(self):
        old = ["old-%d" % i for i in range(600)]
        self.write_state(old)
        self.assertEqual(self.saved_ids(), old[100:])
        self.run_once(FakeSender())
        ids = self.saved_ids()
        self.assertEqual(len(ids), rt.MAX_STATE_IDS)
        self.assertEqual(ids[-4:], [ID_A, ID_B, ID_C, ID_D])
        self.assertEqual(ids[0], "old-104")

    def test_corrupted_state_is_an_error(self):
        os.makedirs(os.path.dirname(self.state))
        with open(self.state, "w") as handle:
            handle.write("{not json")
        with self.assertRaises(rt.StateError):
            self.run_once(FakeSender())

    def test_dry_run_does_not_send_nor_save(self):
        sender = FakeSender()
        code, out = self.run_once(sender, dry_run=True, bootstrap=2)
        self.assertEqual(code, 0)
        self.assertEqual(sender.calls, 0)
        self.assertFalse(os.path.exists(self.state))
        self.assertIn("<b>Notizia C senza guid</b>", out)

    def test_dry_run_first_run_shows_preview(self):
        code, out = self.run_once(FakeSender(), dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("anteprima", out)
        self.assertIn("&lt;oggi&gt;", out)


class TelegramClientTests(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.client = rt.TelegramClient(TOKEN, "@test", "https://api.telegram.org", self.sleeps.append)

    def send_with(self, effects):
        with mock.patch("urllib.request.urlopen", side_effect=effects) as patched, \
                contextlib.redirect_stdout(io.StringIO()):
            result = self.client.send_message("<b>x</b>")
        return result, patched

    def test_request_payload(self):
        _, patched = self.send_with([ok_response()])
        request = patched.call_args[0][0]
        body = request.data.decode()
        self.assertIn("parse_mode=HTML", body)
        self.assertIn("chat_id=%40test", body)

    def test_429_retry_after_zero(self):
        result, _ = self.send_with([http_error(429, {"ok": False, "parameters": {"retry_after": 0}}), ok_response()])
        self.assertEqual(result, {"message_id": 1})
        self.assertEqual(self.sleeps, [rt.DEFAULT_RETRY_AFTER])

    def test_429_retry_after_missing(self):
        self.send_with([http_error(429, {"ok": False}), http_error(429, None), ok_response()])
        self.assertEqual(self.sleeps, [rt.DEFAULT_RETRY_AFTER, rt.DEFAULT_RETRY_AFTER])

    def test_429_retry_after_value(self):
        self.send_with([http_error(429, {"ok": False, "parameters": {"retry_after": 7}}), ok_response()])
        self.assertEqual(self.sleeps, [7])

    def test_429_retry_after_too_long_stops(self):
        with self.assertRaises(rt.TelegramError):
            self.send_with([http_error(429, {"ok": False, "parameters": {"retry_after": 3600}})])
        self.assertEqual(self.sleeps, [])

    def test_5xx_retried(self):
        self.send_with([http_error(502, None), http_error(500, {"ok": False}), ok_response()])
        self.assertEqual(self.sleeps, [2, 4])

    def test_5xx_gives_up(self):
        with self.assertRaises(rt.TelegramError):
            self.send_with([http_error(503, None)] * rt.SEND_ATTEMPTS)
        self.assertEqual(len(self.sleeps), rt.SEND_ATTEMPTS - 1)

    def test_4xx_not_retried(self):
        with self.assertRaises(rt.TelegramError) as ctx:
            self.send_with([http_error(400, {"ok": False, "description": "Bad Request: can't parse entities"})])
        self.assertIn("can't parse entities", str(ctx.exception))
        self.assertEqual(self.sleeps, [])


class FetchTests(unittest.TestCase):
    def test_fetch_retries_then_succeeds(self):
        sleeps = []
        effects = [urllib.error.URLError("timeout"), http_error(503, None), FakeResponse(FEED)]
        with mock.patch("urllib.request.urlopen", side_effect=effects), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rt.fetch_feed("http://feed", sleeps.append), FEED)
        self.assertEqual(sleeps, [2, 4])

    def test_fetch_gives_up(self):
        sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(rt.FeedError):
                rt.fetch_feed("http://feed", sleeps.append)
        self.assertEqual(len(sleeps), rt.FEED_ATTEMPTS - 1)


class TokenSecrecyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"TELEGRAM_BOT_TOKEN": TOKEN, "STATE_FILE": os.path.join(self.tmp.name, "sent.json"),
                    "FEED_URL": "http://feed"}
        rt.save_state(self.env["STATE_FILE"], [])

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, urlopen_effect):
        def urlopen(request, timeout=None):
            if "sendMessage" in request.full_url:
                return urlopen_effect(request)
            return FakeResponse(FEED)
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rt.main(self.env, sleep=lambda s: None)
        return code, out.getvalue()

    def test_network_error_with_url_does_not_leak_token(self):
        def boom(request):
            raise urllib.error.URLError("cannot reach %s" % request.full_url)
        code, out = self.run_main(boom)
        self.assertEqual(code, 1)
        self.assertIn("***", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn("SECRET", out)

    def test_http_error_does_not_leak_token(self):
        def unauthorized(request):
            raise http_error(401, {"ok": False, "description": "Unauthorized %s" % TOKEN})
        code, out = self.run_main(unauthorized)
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", out)

    def test_unexpected_error_does_not_leak_token(self):
        def weird(request):
            raise ValueError("strange failure on %s" % request.full_url.replace(":", "%3A"))
        code, out = self.run_main(weird)
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", out)

    def test_redact_handles_url_encoded_token(self):
        rt._SECRETS.append(TOKEN)
        self.assertEqual(rt.redact("x 123456%3ASECRET-token_value y"), "x *** y")

    def test_state_file_never_contains_token(self):
        self.run_main(lambda request: ok_response())
        with open(self.env["STATE_FILE"]) as handle:
            self.assertNotIn("SECRET", handle.read())


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = rt.load_config({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "", "BOOTSTRAP_SEND_LAST": ""})
        self.assertEqual(config["chat_id"], "@ansabasilicata")
        self.assertEqual(config["feed_url"], rt.DEFAULT_FEED_URL)
        self.assertEqual(config["state_file"], "state/sent.json")
        self.assertEqual(config["bootstrap"], 0)
        self.assertFalse(config["dry_run"])

    def test_bootstrap_number_formats(self):
        self.assertEqual(rt.load_config({"TELEGRAM_BOT_TOKEN": "t", "BOOTSTRAP_SEND_LAST": "3"})["bootstrap"], 3)
        self.assertEqual(rt.load_config({"TELEGRAM_BOT_TOKEN": "t", "BOOTSTRAP_SEND_LAST": "3.0"})["bootstrap"], 3)

    def test_bootstrap_invalid(self):
        for value in ("-1", "abc", "2.5"):
            with self.assertRaises(rt.ConfigError):
                rt.load_config({"TELEGRAM_BOT_TOKEN": "t", "BOOTSTRAP_SEND_LAST": value})

    def test_token_required_unless_dry_run(self):
        with self.assertRaises(rt.ConfigError):
            rt.load_config({})
        self.assertTrue(rt.load_config({"DRY_RUN": "1"})["dry_run"])


if __name__ == "__main__":
    unittest.main()
