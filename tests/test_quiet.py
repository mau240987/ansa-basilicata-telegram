"""Notifiche silenziose di notte e anteprima grande del link."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

import rss_to_telegram as rt
from tests.test_e2e import TOKEN, FakeServer
from tests.test_rss_to_telegram import ID_A


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class QuietConfigTests(unittest.TestCase):
    def test_default_and_values(self):
        base = {"DRY_RUN": "1"}
        self.assertEqual(rt.load_config(base)["quiet_hours"], (22, 7))
        self.assertEqual(rt.load_config(dict(base, QUIET_HOURS="23 - 6"))["quiet_hours"], (23, 6))
        self.assertEqual(rt.load_config(dict(base, QUIET_HOURS="1-6"))["quiet_hours"], (1, 6))
        self.assertIsNone(rt.load_config(dict(base, QUIET_HOURS="off"))["quiet_hours"])

    def test_invalid(self):
        for bad in ("22", "notte", "24-7", "7-7", "22-7-1"):
            with self.assertRaises(rt.ConfigError):
                rt.load_config({"DRY_RUN": "1", "QUIET_HOURS": bad})


class InQuietHoursTests(unittest.TestCase):
    def test_overnight_window_in_italian_time(self):
        # inverno (UTC+1)
        self.assertTrue(rt.in_quiet_hours(utc(2026, 12, 1, 21, 0), (22, 7)))     # 22:00
        self.assertFalse(rt.in_quiet_hours(utc(2026, 12, 1, 20, 59), (22, 7)))   # 21:59
        self.assertTrue(rt.in_quiet_hours(utc(2026, 12, 2, 5, 59), (22, 7)))     # 06:59
        self.assertFalse(rt.in_quiet_hours(utc(2026, 12, 2, 6, 0), (22, 7)))     # 07:00
        # estate (UTC+2)
        self.assertTrue(rt.in_quiet_hours(utc(2026, 7, 1, 20, 0), (22, 7)))      # 22:00
        self.assertFalse(rt.in_quiet_hours(utc(2026, 7, 1, 5, 0), (22, 7)))      # 07:00

    def test_same_day_window_and_off(self):
        self.assertTrue(rt.in_quiet_hours(utc(2026, 12, 1, 1, 0), (1, 6)))       # 02:00
        self.assertFalse(rt.in_quiet_hours(utc(2026, 12, 1, 22, 0), (1, 6)))     # 23:00
        self.assertFalse(rt.in_quiet_hours(utc(2026, 12, 1, 23, 0), None))


class EndToEndQuietTests(unittest.TestCase):
    def run_main_at(self, now, **extra_env):
        with tempfile.TemporaryDirectory() as tmp, FakeServer() as server:
            state = os.path.join(tmp, "state", "sent.json")
            rt.save_state(state, [ID_A])
            env = dict({"TELEGRAM_BOT_TOKEN": TOKEN, "FEED_URL": server.url + "/feed.xml", "STATE_FILE": state,
                        "TELEGRAM_API_URL": server.url, "DIGEST_HOUR": "off"}, **extra_env)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = rt.main(env, sleep=lambda s: None, now=now)
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn(TOKEN, out.getvalue())
            return server.received, out.getvalue()

    def test_night_messages_are_silent_with_large_preview(self):
        received, output = self.run_main_at(utc(2026, 12, 1, 23, 30))  # 00:30 in Italia
        self.assertEqual(len(received), 3)
        self.assertTrue(all(m.get("disable_notification") == "true" for m in received))
        self.assertTrue(all(json.loads(m["link_preview_options"]) == {"prefer_large_media": True}
                            for m in received))
        self.assertIn("Fascia notturna", output)

    def test_day_messages_ring(self):
        received, output = self.run_main_at(utc(2026, 12, 1, 10, 0))  # 11:00 in Italia
        self.assertEqual(len(received), 3)
        self.assertTrue(all("disable_notification" not in m for m in received))
        self.assertNotIn("Fascia notturna", output)

    def test_quiet_hours_off(self):
        received, _ = self.run_main_at(utc(2026, 12, 1, 23, 30), QUIET_HOURS="off")
        self.assertTrue(all("disable_notification" not in m for m in received))


if __name__ == "__main__":
    unittest.main()
