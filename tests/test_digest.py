"""Hashtag nei messaggi e riepilogo serale."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import rss_to_telegram as rt
from tests.test_e2e import TOKEN, FakeServer
from tests.test_rss_to_telegram import FakeSender, fake_fetch

# 4 novembre 2026 in ora italiana (CET = UTC+1)
MORNING = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)      # 10:00
EVENING = datetime(2026, 11, 4, 19, 5, tzinfo=timezone.utc)     # 20:05
LATER = EVENING + timedelta(minutes=15)                         # 20:20
OLD_STATE_IDS = ["https://www.ansa.it/basilicata/notizie/a.html"]


class DigestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "sent.json")

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **overrides):
        config = {"token": TOKEN, "chat_id": "@test", "feed_url": "http://feed", "state_file": self.state,
                  "api_url": "http://api", "bootstrap": 0, "dry_run": False,
                  "digest_hour": 20, "digest_max": 10, "digest_force": False}
        config.update(overrides)
        return config

    def write_old_state(self, ids):
        """Stato nel formato gia' in produzione: solo sent_ids."""
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as handle:
            json.dump({"sent_ids": ids}, handle)

    def saved(self):
        with open(self.state, encoding="utf-8") as handle:
            return json.load(handle)

    def run_at(self, now, sender, digest_sender=None, **overrides):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rt.run(self.config(**overrides), sender, lambda s: None, fetch=fake_fetch,
                          send_digest=digest_sender, now=now)
        return code, out.getvalue()


class UpgradeAndDigestTests(DigestCase):
    def test_upgrade_from_production_state_sends_only_new_items(self):
        self.write_old_state(OLD_STATE_IDS)
        sender = FakeSender()
        code, output = self.run_at(MORNING, sender)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(sender.messages), 3)  # B, C, D: A era gia' pubblicato
        self.assertTrue(sender.messages[-1].endswith("\n#Potenza #Matera"))
        data = self.saved()
        self.assertEqual(len(data["sent_ids"]), 4)
        self.assertEqual([e["title"] for e in data["recent"]],
                         ["Notizia B", "Notizia C senza guid", 'Notizia D: Potenza & Matera <oggi> "speciale"'])
        self.assertIsNone(data["last_digest"])

    def test_digest_once_after_hour(self):
        self.write_old_state(OLD_STATE_IDS)
        self.run_at(MORNING, FakeSender())
        digest = FakeSender()
        code, _ = self.run_at(EVENING, FakeSender(), digest)
        self.assertEqual(code, 0)
        self.assertEqual(len(digest.messages), 1)
        text = digest.messages[0]
        self.assertIn("4 novembre 2026", text)
        self.assertIn('<a href="https://www.ansa.it/basilicata/notizie/d.html?a=1&amp;b=2">'
                      'Notizia D: Potenza &amp; Matera &lt;oggi&gt; "speciale"</a>', text)
        self.assertEqual(self.saved()["last_digest"], "2026-11-04")
        again = FakeSender()
        self.run_at(LATER, FakeSender(), again)
        self.assertEqual(again.messages, [])

    def test_no_digest_before_hour_or_when_off(self):
        self.write_old_state(OLD_STATE_IDS)
        digest = FakeSender()
        self.run_at(MORNING, FakeSender(), digest)
        self.run_at(EVENING, FakeSender(), digest, digest_hour=None)
        self.assertEqual(digest.messages, [])

    def test_failed_digest_is_retried(self):
        self.write_old_state(OLD_STATE_IDS)
        self.run_at(MORNING, FakeSender())
        code, _ = self.run_at(EVENING, FakeSender(), FakeSender(fail_on=0))
        self.assertEqual(code, 1)
        self.assertIsNone(self.saved()["last_digest"])
        digest = FakeSender()
        self.assertEqual(self.run_at(LATER, FakeSender(), digest)[0], 0)
        self.assertEqual(len(digest.messages), 1)

    def test_old_items_pruned_and_previous_day_excluded(self):
        self.write_old_state(OLD_STATE_IDS)
        self.run_at(MORNING - timedelta(days=1), FakeSender())  # pubblicate ieri
        digest = FakeSender()
        self.run_at(EVENING, FakeSender(), digest)
        self.assertEqual(digest.messages, [])  # oggi nessuna notizia: niente riepilogo
        self.assertEqual(self.saved()["last_digest"], "2026-11-04")
        self.run_at(EVENING + timedelta(days=3), FakeSender())
        self.assertEqual(self.saved()["recent"], [])

    def test_force_and_dry_run_preview(self):
        self.write_old_state(OLD_STATE_IDS)
        self.run_at(MORNING, FakeSender())
        before = self.saved()
        code, output = self.run_at(MORNING, FakeSender(), FakeSender(), dry_run=True, digest_force=True)
        self.assertEqual(code, 0)
        self.assertIn("[DRY RUN] riepilogo serale", output)
        self.assertEqual(self.saved(), before)
        digest = FakeSender()
        self.run_at(MORNING, FakeSender(), digest, digest_force=True)
        self.assertEqual(len(digest.messages), 1)

    def test_empty_feed_dry_run_never_sends(self):
        self.write_old_state(OLD_STATE_IDS)
        self.run_at(MORNING, FakeSender())
        empty = b"<rss version='2.0'><channel><title>x</title></channel></rss>"
        digest = FakeSender()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rt.run(self.config(dry_run=True), FakeSender(), lambda s: None, fetch=lambda u, s: empty,
                   send_digest=digest, now=EVENING)
        self.assertEqual(digest.messages, [])
        self.assertIn("[DRY RUN] riepilogo serale", out.getvalue())
        digest = FakeSender()
        with contextlib.redirect_stdout(io.StringIO()):
            rt.run(self.config(), FakeSender(), lambda s: None, fetch=lambda u, s: empty,
                   send_digest=digest, now=EVENING)
        self.assertEqual(len(digest.messages), 1)

    def test_first_run_never_floods(self):
        sender, digest = FakeSender(), FakeSender()
        code, _ = self.run_at(EVENING, sender, digest)
        self.assertEqual((code, sender.messages, digest.messages), (0, [], []))
        self.assertEqual(len(self.saved()["sent_ids"]), 4)

    def test_malformed_extra_data_is_ignored(self):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as handle:
            json.dump({"sent_ids": OLD_STATE_IDS, "recent": [1, {"sent_at": "boh"}, {"sent_at": "2026-11-04T09:00:00"}],
                       "last_digest": 5}, handle)
        code, output = self.run_at(EVENING, FakeSender(), FakeSender())
        self.assertEqual(code, 0, output)


class FormatDigestTests(unittest.TestCase):
    def test_limits(self):
        entries = [{"title": "Notizia %d %s" % (i, "x" * 300), "link": "https://x/%d" % i} for i in range(30)]
        text = rt.format_digest(entries, date(2026, 11, 4), max_items=10)
        self.assertIn("Notizia 29", text)
        self.assertNotIn("Notizia 19 ", text)
        self.assertIn("altre 20", text)
        self.assertLessEqual(len(text), rt.TELEGRAM_MAX_CHARS)
        self.assertIsNone(rt.format_digest([], date(2026, 11, 4)))


class ConfigTests(unittest.TestCase):
    def test_digest_settings(self):
        base = {"DRY_RUN": "1"}
        self.assertEqual(rt.load_config(base)["digest_hour"], 20)
        self.assertIsNone(rt.load_config(dict(base, DIGEST_HOUR="off"))["digest_hour"])
        self.assertEqual(rt.load_config(dict(base, DIGEST_HOUR="21", DIGEST_MAX="5"))["digest_max"], 5)
        for bad in ({"DIGEST_HOUR": "25"}, {"DIGEST_HOUR": "sera"}, {"DIGEST_MAX": "0"}):
            with self.assertRaises(rt.ConfigError):
                rt.load_config(dict(base, **bad))


class EndToEndDigestTests(unittest.TestCase):
    def test_main_sends_digest_without_preview(self):
        with tempfile.TemporaryDirectory() as tmp, FakeServer() as server:
            state = os.path.join(tmp, "state", "sent.json")
            os.makedirs(os.path.dirname(state))
            with open(state, "w", encoding="utf-8") as handle:
                json.dump({"sent_ids": OLD_STATE_IDS}, handle)
            env = {"TELEGRAM_BOT_TOKEN": TOKEN, "FEED_URL": server.url + "/feed.xml", "STATE_FILE": state,
                   "TELEGRAM_API_URL": server.url, "DIGEST_HOUR": "20"}
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(rt.main(env, sleep=lambda s: None, now=EVENING), 0)
            self.assertNotIn(TOKEN, out.getvalue())
            news, digest = server.received[:-1], server.received[-1]
            self.assertEqual(len(news), 3)
            self.assertTrue(all("link_preview_options" not in m for m in news))
            self.assertIn("Le notizie di oggi", digest["text"])
            self.assertEqual(json.loads(digest["link_preview_options"]), {"is_disabled": True})


if __name__ == "__main__":
    unittest.main()
