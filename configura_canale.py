#!/usr/bin/env python3
"""Configurazione una tantum del canale: descrizione e messaggio di benvenuto fissato.

Si lancia a mano dal workflow "Configura canale". Prima controlla che il bot abbia i
permessi necessari; se ne manca uno si ferma senza pubblicare nulla.

Variabili d'ambiente: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (default @ansabasilicata),
DRY_RUN=1 (controlla i permessi e stampa i testi senza modificare il canale),
TELEGRAM_API_URL (solo per i test).
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DESCRIPTION = (
    "Canale non ufficiale. Le notizie della Basilicata in tempo reale: Potenza, Matera, cronaca, "
    "politica, sanità, cultura. Titoli e link da ANSA Basilicata, aggiornati ogni pochi minuti."
)

WELCOME = """👋 Benvenuto su Ansa Basilicata Unofficial!

📰 Qui trovi in tempo reale le notizie della Basilicata, da Potenza a Matera: cronaca, politica, sanità, lavoro, cultura.
🔗 Ogni post è un titolo con il link all'articolo originale su ANSA.it.
🔕 Troppe notifiche? Puoi silenziare il canale e leggere quando vuoi.

📲 Conosci qualcuno della Basilicata? Inoltragli questo messaggio:
👉 t.me/ansabasilicata

ℹ️ Canale non ufficiale, non affiliato ad ANSA."""

# permessi di amministratore richiesti in un canale
REQUIRED_RIGHTS = {
    "can_post_messages": "Pubblica messaggi",
    "can_edit_messages": "Modifica messaggi altrui (serve per fissare i messaggi)",
    "can_change_info": "Modifica info canale",
}


class ApiError(Exception):
    pass


class Api:
    def __init__(self, token, api_url="https://api.telegram.org"):
        self._token = token
        self._base = "%s/bot%s/" % (api_url.rstrip("/"), token)

    def call(self, method, **params):
        data = urllib.parse.urlencode(
            {k: json.dumps(v) if isinstance(v, (dict, bool)) else v for k, v in params.items()}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self._base + method, data=data), timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode())
            except ValueError:
                payload = {}
            raise ApiError("%s: HTTP %s %s" % (method, exc.code, payload.get("description", "")))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError("%s: %s" % (method, getattr(exc, "reason", exc)))
        if not payload.get("ok"):
            raise ApiError("%s: %s" % (method, payload.get("description", "ok=false")))
        return payload["result"]

    def redact(self, text):
        text = str(text)
        for secret in (self._token, urllib.parse.quote(self._token, safe="")):
            if secret:
                text = text.replace(secret, "***")
        return text


def missing_rights(member):
    if member.get("status") == "creator":
        return []
    if member.get("status") != "administrator":
        return ["il bot non è amministratore del canale"]
    return [label for key, label in REQUIRED_RIGHTS.items() if not member.get(key)]


def configure(api, chat_id, dry_run, out=print):
    me = api.call("getMe")
    member = api.call("getChatMember", chat_id=chat_id, user_id=me["id"])
    missing = missing_rights(member)
    if missing:
        out("ERRORE: al bot @%s mancano questi permessi nel canale %s: %s"
            % (me.get("username"), chat_id, "; ".join(missing)))
        out("Nessuna modifica fatta. Concedili in Info canale -> Amministratori e rilancia.")
        return 1
    out("Permessi del bot verificati.")
    out("Descrizione (%d caratteri):\n%s\n" % (len(DESCRIPTION), DESCRIPTION))
    out("Messaggio di benvenuto:\n%s\n" % WELCOME)
    if dry_run:
        out("[DRY RUN] nessuna modifica al canale")
        return 0

    current = api.call("getChat", chat_id=chat_id).get("description") or ""
    if current == DESCRIPTION:
        out("Descrizione già aggiornata.")
    else:
        api.call("setChatDescription", chat_id=chat_id, description=DESCRIPTION)
        out("Descrizione aggiornata.")

    message = api.call("sendMessage", chat_id=chat_id, text=WELCOME, disable_notification=True,
                       link_preview_options={"is_disabled": True})
    out("Messaggio di benvenuto pubblicato (id %s)." % message["message_id"])
    api.call("pinChatMessage", chat_id=chat_id, message_id=message["message_id"], disable_notification=True)
    out("Messaggio di benvenuto fissato in alto.")
    return 0


def main(env=None):
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("ERRORE: TELEGRAM_BOT_TOKEN non impostato")
        return 1
    api = Api(token, (env.get("TELEGRAM_API_URL") or "").strip() or "https://api.telegram.org")
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip() or "@ansabasilicata"
    dry_run = (env.get("DRY_RUN") or "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        return configure(api, chat_id, dry_run, out=lambda text: print(api.redact(text), flush=True))
    except ApiError as exc:
        print("ERRORE: %s" % api.redact(exc), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
