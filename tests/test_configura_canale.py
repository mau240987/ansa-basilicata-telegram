import contextlib
import io
import json
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import configura_canale as cc

TOKEN = "555:CONF-secret-token"
FULL_RIGHTS = {"status": "administrator", "can_post_messages": True, "can_edit_messages": True,
               "can_change_info": True}


class FakeTelegram:
    def __init__(self, member, description="vecchia"):
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                form = {k: v[0] for k, v in urllib.parse.parse_qs(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()).items()}
                outer.calls.append((method, form))
                if not self.path.startswith("/bot%s/" % TOKEN):
                    status, body = 401, {"ok": False, "description": "Unauthorized"}
                else:
                    status, body = 200, {"ok": True, "result": {
                        "getMe": {"id": 42, "username": "test_bot"},
                        "getChatMember": member,
                        "getChat": {"description": description},
                        "sendMessage": {"message_id": 99},
                    }.get(method, True)}
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def methods(self):
        return [m for m, _ in self.calls]


class ConfiguraCanaleTests(unittest.TestCase):
    def run_main(self, server, **extra):
        env = dict({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_API_URL": server.url}, **extra)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cc.main(env)
        self.assertNotIn("CONF-secret", out.getvalue())
        return code, out.getvalue()

    def test_full_configuration(self):
        server = FakeTelegram(FULL_RIGHTS)
        try:
            code, output = self.run_main(server)
        finally:
            server.close()
        self.assertEqual(code, 0, output)
        self.assertEqual(server.methods(), ["getMe", "getChatMember", "getChat", "setChatDescription",
                                            "sendMessage", "pinChatMessage"])
        calls = dict(server.calls)
        self.assertEqual(calls["setChatDescription"]["description"], cc.DESCRIPTION)
        self.assertEqual(calls["sendMessage"]["text"], cc.WELCOME)
        self.assertEqual(calls["sendMessage"]["disable_notification"], "true")
        self.assertEqual(calls["pinChatMessage"]["message_id"], "99")
        self.assertEqual(calls["getChatMember"]["chat_id"], "@ansabasilicata")
        self.assertLessEqual(len(cc.DESCRIPTION), 255)

    def test_missing_rights_changes_nothing(self):
        server = FakeTelegram({"status": "administrator", "can_post_messages": True})
        try:
            code, output = self.run_main(server)
        finally:
            server.close()
        self.assertEqual(code, 1)
        self.assertIn("Modifica info canale", output)
        self.assertEqual(server.methods(), ["getMe", "getChatMember"])

    def test_dry_run_only_checks(self):
        server = FakeTelegram(FULL_RIGHTS)
        try:
            code, output = self.run_main(server, DRY_RUN="1")
        finally:
            server.close()
        self.assertEqual(code, 0)
        self.assertIn("Benvenuto su Ansa Basilicata Unofficial", output)
        self.assertEqual(server.methods(), ["getMe", "getChatMember"])

    def test_description_already_set_is_skipped(self):
        server = FakeTelegram(FULL_RIGHTS, description=cc.DESCRIPTION)
        try:
            code, _ = self.run_main(server)
        finally:
            server.close()
        self.assertEqual(code, 0)
        self.assertNotIn("setChatDescription", server.methods())

    def test_wrong_token_does_not_leak(self):
        server = FakeTelegram(FULL_RIGHTS)
        try:
            code, output = self.run_main(server, TELEGRAM_BOT_TOKEN="777:other-CONF-secret")
        finally:
            server.close()
        self.assertEqual(code, 1)
        self.assertIn("Unauthorized", output)


if __name__ == "__main__":
    unittest.main()
