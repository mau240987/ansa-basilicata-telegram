"""Prova end-to-end: feed servito in locale e API Telegram simulata via HTTP."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rss_to_telegram as rt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "feed.xml")
TOKEN = "987654:E2E-secret-TOKEN"


class FakeServer:
    """Serve /feed.xml e simula /bot<TOKEN>/sendMessage."""

    def __init__(self):
        self.received = []
        self.responses = []  # coda di (status, body); None = risposta 200 standard
        self.feed_failures = 0
        with open(FIXTURE, "rb") as handle:
            feed = handle.read()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/feed.xml":
                    if outer.feed_failures:
                        outer.feed_failures -= 1
                        return self._reply(503, b"busy", "text/plain")
                    return self._reply(200, feed, "application/rss+xml")
                self._reply(404, {"ok": False})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode())
                if self.path != "/bot%s/sendMessage" % TOKEN:
                    return self._reply(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                queued = outer.responses.pop(0) if outer.responses else None
                if queued is not None:
                    return self._reply(*queued)
                outer.received.append({k: v[0] for k, v in form.items()})
                self._reply(200, {"ok": True, "result": {"message_id": len(outer.received)}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "sent.json")
        self.server = FakeServer().__enter__()
        self.env = {
            "TELEGRAM_BOT_TOKEN": TOKEN,
            "TELEGRAM_CHAT_ID": "@ansabasilicata",
            "FEED_URL": self.server.url + "/feed.xml",
            "STATE_FILE": self.state,
            "TELEGRAM_API_URL": self.server.url,
        }

    def tearDown(self):
        self.server.__exit__()
        self.tmp.cleanup()

    def main(self, **extra):
        env = dict(self.env, **extra)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rt.main(env, sleep=lambda seconds: None)
        output = out.getvalue()
        self.assertNotIn("E2E-secret", output)
        return code, output

    def sent_ids(self):
        with open(self.state) as handle:
            return json.load(handle)["sent_ids"]

    def test_full_lifecycle(self):
        # primo avvio con bootstrap: pubblica gli ultimi 2, con un 429 (retry_after=0) e un 502 lungo la strada
        self.server.responses = [(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 0}}),
                                 (502, {"ok": False})]
        self.server.feed_failures = 1
        code, output = self.main(BOOTSTRAP_SEND_LAST="2")
        self.assertEqual(code, 0, output)
        texts = [m["text"] for m in self.server.received]
        self.assertEqual(texts, [
            "<b>Notizia C senza guid</b>\nhttps://www.ansa.it/basilicata/notizie/c.html",
            '<b>Notizia D: Potenza &amp; Matera &lt;oggi&gt; "speciale"</b>\n'
            "https://www.ansa.it/basilicata/notizie/d.html?a=1&amp;b=2",
        ])
        self.assertTrue(all(m["parse_mode"] == "HTML" and m["chat_id"] == "@ansabasilicata"
                            for m in self.server.received))
        self.assertEqual(len(self.sent_ids()), 4)

        # giro successivo: nessun doppione
        code, _ = self.main(BOOTSTRAP_SEND_LAST="2")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.received), 2)

    def test_error_stops_and_next_run_resumes(self):
        rt.save_state(self.state, ["https://www.ansa.it/basilicata/notizie/a.html"])
        # B pubblicato, C fallisce con un 400: ci si ferma e D non viene tentato
        self.server.responses = [None, (400, {"ok": False, "description": "Bad Request: chat not found"})]
        code, output = self.main()
        self.assertEqual(code, 1)
        self.assertIn("chat not found", output)
        self.assertEqual([m["text"].split("\n")[0] for m in self.server.received], ["<b>Notizia B</b>"])
        self.assertEqual(self.sent_ids(), ["https://www.ansa.it/basilicata/notizie/a.html",
                                           "https://www.ansa.it/basilicata/notizie/b.html"])

        code, output = self.main()
        self.assertEqual(code, 0, output)
        self.assertEqual([m["text"].split("\n")[0] for m in self.server.received],
                         ["<b>Notizia B</b>", "<b>Notizia C senza guid</b>",
                          '<b>Notizia D: Potenza &amp; Matera &lt;oggi&gt; "speciale"</b>'])
        self.assertEqual(len(self.sent_ids()), 4)

    def test_wrong_token_reports_error_without_leaking(self):
        rt.save_state(self.state, [])
        code, output = self.main(TELEGRAM_BOT_TOKEN="111:other-secret-value")
        self.assertEqual(code, 1)
        self.assertIn("Unauthorized", output)
        self.assertNotIn("other-secret-value", output)

    def test_dry_run_via_subprocess(self):
        env = dict(os.environ, **self.env)
        env.update(DRY_RUN="1", BOOTSTRAP_SEND_LAST="2", PYTHONPATH=ROOT)
        result = subprocess.run([sys.executable, os.path.join(ROOT, "rss_to_telegram.py")], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<b>Notizia C senza guid</b>", result.stdout)
        self.assertNotIn("E2E-secret", result.stdout + result.stderr)
        self.assertEqual(self.server.received, [])
        self.assertFalse(os.path.exists(self.state))

    def test_real_run_via_subprocess(self):
        rt.save_state(self.state, ["https://www.ansa.it/basilicata/notizie/a.html",
                                   "https://www.ansa.it/basilicata/notizie/b.html"])
        env = dict(os.environ, **self.env)
        result = subprocess.run([sys.executable, os.path.join(ROOT, "rss_to_telegram.py")], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.server.received), 2)
        self.assertNotIn("E2E-secret", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
