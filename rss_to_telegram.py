#!/usr/bin/env python3
"""Pubblica su un canale Telegram le nuove notizie di un feed RSS 2.0.

Usa solo la libreria standard. Configurazione tramite variabili d'ambiente:

  TELEGRAM_BOT_TOKEN   token del bot (obbligatorio, tranne in dry run)
  TELEGRAM_CHAT_ID     canale di destinazione (default @ansabasilicata)
  FEED_URL             URL del feed RSS
  STATE_FILE           file JSON con gli ID gia' pubblicati (default state/sent.json)
  BOOTSTRAP_SEND_LAST  solo al primo avvio: pubblica subito gli ultimi N articoli
  DRY_RUN=1            stampa i messaggi senza inviarli e senza salvare lo stato
  DIGEST_HOUR          ora (0-23, ora italiana) del riepilogo serale; "off" lo disattiva (default 20)
  DIGEST_MAX           notizie massime nel riepilogo (default 10)
  DIGEST_FORCE=1       invia subito il riepilogo di oggi (vale come riepilogo del giorno)
  QUIET_HOURS          fascia oraria (ora italiana) con notifiche silenziose, es. "22-7";
                       "off" la disattiva (default 22-7)
  TELEGRAM_API_URL     base URL dell'API Telegram (solo per i test)

Ogni notizia riceve gli hashtag dei comuni lucani citati (vedi comuni.py). Una volta al
giorno, dal primo giro dopo DIGEST_HOUR, esce il riepilogo con i titoli della giornata.
"""

import html
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import comuni

DEFAULT_FEED_URL = "https://www.ansa.it/basilicata/notizie/basilicata_rss.xml"
DEFAULT_CHAT_ID = "@ansabasilicata"
DEFAULT_STATE_FILE = "state/sent.json"
DEFAULT_API_URL = "https://api.telegram.org"

MAX_STATE_IDS = 500
SEND_DELAY = 1.5           # pausa tra un messaggio e l'altro (secondi)
FEED_ATTEMPTS = 4          # tentativi di download del feed
SEND_ATTEMPTS = 5          # tentativi di invio di un singolo messaggio
DEFAULT_RETRY_AFTER = 5    # attesa sul 429 quando retry_after vale 0 o manca
MAX_RETRY_AFTER = 60       # oltre questa attesa ci si ferma e si riprende al giro dopo
HTTP_TIMEOUT = 30
DRY_RUN_PREVIEW = 3
USER_AGENT = "ansa-basilicata-telegram/1.0 (RSS to Telegram; Python urllib)"

LOCAL_TZ = ZoneInfo("Europe/Rome")
DEFAULT_DIGEST_HOUR = 20
DEFAULT_DIGEST_MAX = 10
RECENT_KEEP = timedelta(hours=48)   # quanto conservare titoli e link per il riepilogo
TELEGRAM_MAX_CHARS = 4096
DEFAULT_QUIET_HOURS = "22-7"
MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre")

_SECRETS = []


class FeedError(Exception):
    pass


class StateError(Exception):
    pass


class ConfigError(Exception):
    pass


class TelegramError(Exception):
    pass


def redact(text):
    """Rimuove dal testo ogni segreto registrato (anche in forma URL-encoded)."""
    text = str(text)
    for secret in _SECRETS:
        if not secret:
            continue
        text = text.replace(secret, "***")
        quoted = urllib.parse.quote(secret, safe="")
        if quoted != secret:
            text = text.replace(quoted, "***")
    return text


def log(message, stream=None):
    print(redact(message), file=stream or sys.stdout, flush=True)


# --------------------------------------------------------------------------- feed

def fetch_feed(url, sleep=time.sleep, attempts=FEED_ATTEMPTS):
    """Scarica il feed con retry e backoff esponenziale."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = "HTTP %s" % exc.code
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                break
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last_error = "%s: %s" % (type(exc).__name__, getattr(exc, "reason", exc))
        if attempt < attempts:
            wait = 2 ** attempt
            log("Download del feed fallito (%s), nuovo tentativo tra %ss" % (last_error, wait))
            sleep(wait)
    raise FeedError("download del feed fallito dopo %d tentativi: %s" % (attempt, last_error))


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_feed(data):
    """Restituisce gli articoli del feed ordinati dal piu' vecchio al piu' recente."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedError("XML del feed non valido: %s" % exc)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError("il documento non e' un feed RSS 2.0")

    items = []
    for element in channel.findall("item"):
        title = " ".join((element.findtext("title") or "").split())
        link = (element.findtext("link") or "").strip()
        guid = (element.findtext("guid") or "").strip()
        item_id = guid or link
        if not item_id:
            log("Articolo senza guid e senza link ignorato: %r" % title)
            continue
        items.append({
            "id": item_id,
            "title": title,
            "link": link,
            "published": _timestamp(element.findtext("pubDate")),
            "summary": _plain_text(element.findtext("description")),
        })

    # I feed elencano di norma dal piu' recente: si inverte e poi si ordina in modo
    # stabile per data (gli articoli senza data restano nell'ordine inverso del feed).
    items.reverse()
    items.sort(key=lambda item: item["published"] if item["published"] is not None else float("-inf"))
    return items


def _plain_text(value):
    """La description puo' contenere HTML: teniamo solo il testo."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(text).split())


def format_message(item):
    title = html.escape(item["title"] or item["link"], quote=False)
    link = html.escape(item["link"], quote=False)
    message = "<b>%s</b>\n%s" % (title, link)
    tags = comuni.hashtags_for(item["title"], item.get("summary", ""))
    if tags:
        message += "\n" + " ".join(tags)
    return message


def format_digest(entries, day, max_items=DEFAULT_DIGEST_MAX):
    """Riepilogo con i titoli cliccabili. None se non c'e' nulla da riassumere."""
    if not entries:
        return None
    shown = entries[-max(max_items, 1):]
    header = "🗞 <b>Le notizie di oggi in Basilicata</b>\n%d %s %d\n" % (day.day, MESI[day.month - 1], day.year)

    def build(rows):
        lines = [header]
        for entry in rows:
            title = html.escape(entry.get("title") or entry.get("link") or "", quote=False)
            link = entry.get("link")
            if link:
                lines.append('• <a href="%s">%s</a>' % (html.escape(link, quote=True), title))
            else:
                lines.append("• %s" % title)
        hidden = len(entries) - len(rows)
        if hidden > 0:
            lines.append("\n…e altre %d sul canale" % hidden)
        return "\n".join(lines)

    text = build(shown)
    while len(text) > TELEGRAM_MAX_CHARS and len(shown) > 1:
        shown = shown[1:]  # si tolgono le piu' vecchie finche' il messaggio entra
        text = build(shown)
    return text


# --------------------------------------------------------------------------- stato

def load_state(path):
    """Restituisce la lista degli ID gia' pubblicati, oppure None se lo stato manca."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StateError("stato %s illeggibile: %s" % (path, exc))
    ids = data.get("sent_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise StateError("stato %s non valido: attesa la chiave 'sent_ids' con una lista di stringhe" % path)
    return ids


def load_extra(path):
    """Dati per il riepilogo serale ("recent", "last_digest"). Tolleranti: se mancano o sono
    malformati si riparte da vuoto, senza toccare gli ID gia' pubblicati."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    recent = [e for e in data.get("recent") or [] if isinstance(e, dict) and _sent_at(e)]
    last_digest = data.get("last_digest")
    return {"recent": recent, "last_digest": last_digest if isinstance(last_digest, str) else None}


def _sent_at(entry):
    try:
        moment = datetime.fromisoformat(entry["sent_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def save_state(path, ids, extra=None):
    """Salva in modo atomico gli ultimi MAX_STATE_IDS identificativi (e i dati del riepilogo)."""
    ids = list(ids)[-MAX_STATE_IDS:]
    payload = {"sent_ids": ids}
    if extra is not None:
        payload["recent"] = list(extra.get("recent") or [])[-MAX_STATE_IDS:]
        payload["last_digest"] = extra.get("last_digest")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)


# --------------------------------------------------------------------------- Telegram

def _json_body(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}
    return data if isinstance(data, dict) else {}


class TelegramClient:
    def __init__(self, token, chat_id, api_url=DEFAULT_API_URL, sleep=time.sleep, silent=False):
        self._url = "%s/bot%s/sendMessage" % (api_url.rstrip("/"), token)
        self.chat_id = chat_id
        self.sleep = sleep
        self.silent = silent

    def send_message(self, text, preview=True):
        params = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if preview:
            # anteprima del link con l'immagine grande: attira piu' clic e inoltri
            params["link_preview_options"] = json.dumps({"prefer_large_media": True})
        else:
            params["link_preview_options"] = json.dumps({"is_disabled": True})
        if self.silent:
            params["disable_notification"] = "true"
        body = urllib.parse.urlencode(params).encode("utf-8")
        last_error = None
        for attempt in range(1, SEND_ATTEMPTS + 1):
            wait = None
            try:
                request = urllib.request.Request(self._url, data=body, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                    payload = _json_body(response.read())
                if payload.get("ok"):
                    return payload.get("result")
                raise TelegramError("risposta inattesa da Telegram: %s" % payload.get("description", "ok=false"))
            except urllib.error.HTTPError as exc:
                payload = _json_body(exc.read())
                description = payload.get("description") or exc.reason
                last_error = "HTTP %s: %s" % (exc.code, description)
                if exc.code == 429:
                    retry_after = (payload.get("parameters") or {}).get("retry_after")
                    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and retry_after > 0:
                        wait = retry_after
                    else:
                        wait = DEFAULT_RETRY_AFTER
                    if wait > MAX_RETRY_AFTER:
                        raise TelegramError("limite di invio Telegram: retry_after=%ss, riprovo al prossimo giro" % wait)
                elif 500 <= exc.code < 600:
                    wait = 2 ** attempt
                else:
                    raise TelegramError(last_error)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                last_error = "%s: %s" % (type(exc).__name__, getattr(exc, "reason", exc))
                wait = 2 ** attempt
            if attempt < SEND_ATTEMPTS:
                log("Invio non riuscito (%s), nuovo tentativo tra %ss" % (last_error, wait))
                self.sleep(wait)
        raise TelegramError("invio fallito dopo %d tentativi: %s" % (SEND_ATTEMPTS, last_error))


# --------------------------------------------------------------------------- logica

def load_config(env):
    def get(name, default=""):
        value = (env.get(name) or "").strip()
        return value or default

    raw_bootstrap = get("BOOTSTRAP_SEND_LAST", "0")
    try:
        number = float(raw_bootstrap)
    except ValueError:
        raise ConfigError("BOOTSTRAP_SEND_LAST deve essere un intero >= 0, ricevuto %r" % raw_bootstrap)
    if number < 0 or number != int(number):
        raise ConfigError("BOOTSTRAP_SEND_LAST deve essere un intero >= 0, ricevuto %r" % raw_bootstrap)

    raw_hour = get("DIGEST_HOUR", str(DEFAULT_DIGEST_HOUR)).lower()
    if raw_hour in ("off", "no", "false", "-1"):
        digest_hour = None
    else:
        try:
            digest_hour = int(raw_hour)
        except ValueError:
            digest_hour = -1
        if not 0 <= digest_hour <= 23:
            raise ConfigError("DIGEST_HOUR deve essere un'ora tra 0 e 23 oppure 'off', ricevuto %r" % raw_hour)

    raw_max = get("DIGEST_MAX", str(DEFAULT_DIGEST_MAX))
    try:
        digest_max = int(raw_max)
    except ValueError:
        digest_max = 0
    if digest_max < 1:
        raise ConfigError("DIGEST_MAX deve essere un intero >= 1, ricevuto %r" % raw_max)

    raw_quiet = get("QUIET_HOURS", DEFAULT_QUIET_HOURS).lower()
    if raw_quiet in ("off", "no", "false"):
        quiet_hours = None
    else:
        match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", raw_quiet)
        quiet_hours = (int(match.group(1)), int(match.group(2))) if match else None
        if (quiet_hours is None or not all(0 <= h <= 23 for h in quiet_hours)
                or quiet_hours[0] == quiet_hours[1]):
            raise ConfigError("QUIET_HOURS deve essere del tipo '22-7' (ore 0-23, diverse) oppure 'off', "
                              "ricevuto %r" % raw_quiet)

    config = {
        "quiet_hours": quiet_hours,
        "digest_hour": digest_hour,
        "digest_max": digest_max,
        "digest_force": get("DIGEST_FORCE", "0").lower() in ("1", "true", "yes", "on"),
        "token": get("TELEGRAM_BOT_TOKEN"),
        "chat_id": get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID),
        "feed_url": get("FEED_URL", DEFAULT_FEED_URL),
        "state_file": get("STATE_FILE", DEFAULT_STATE_FILE),
        "api_url": get("TELEGRAM_API_URL", DEFAULT_API_URL),
        "bootstrap": int(number),
        "dry_run": get("DRY_RUN", "0").lower() in ("1", "true", "yes", "on"),
    }
    if not config["dry_run"] and not config["token"]:
        raise ConfigError("TELEGRAM_BOT_TOKEN non impostato")
    return config


def run(config, send, sleep=time.sleep, fetch=fetch_feed, send_digest=None, now=None):
    """Esegue un giro completo. Restituisce l'exit code.

    send_digest invia il riepilogo (default: send); now serve ai test.
    """
    now = now or datetime.now(timezone.utc)
    items = parse_feed(fetch(config["feed_url"], sleep))
    log("Feed letto: %d articoli" % len(items))

    state_path = config["state_file"]
    sent_ids = load_state(state_path)
    dry_run = config["dry_run"]
    extra = load_extra(state_path)
    extra["recent"] = [e for e in extra["recent"] if _sent_at(e) >= now - RECENT_KEEP]

    if not items:
        log("Il feed non contiene articoli: nulla da pubblicare")
        if sent_ids is None:
            return 0
        return run_digest(config, sent_ids, extra, None if dry_run else (send_digest or send), now)

    if sent_ids is None:
        count = min(config["bootstrap"], len(items))
        to_send = items[len(items) - count:] if count else []
        sent_ids = []
        for item in items[:len(items) - count]:
            if item["id"] not in sent_ids:
                sent_ids.append(item["id"])
        log("Primo avvio: %d articoli segnati come gia' pubblicati, %d da pubblicare ora"
            % (len(sent_ids), len(to_send)))
        if not dry_run:
            save_state(state_path, sent_ids, extra)
    else:
        if config["bootstrap"]:
            log("BOOTSTRAP_SEND_LAST ignorato: lo stato esiste gia'")
        to_send = items

    known = set(sent_ids)
    pending = []
    for item in to_send:
        if item["id"] not in known:
            known.add(item["id"])
            pending.append(item)
    log("Articoli nuovi da pubblicare: %d" % len(pending))

    if dry_run:
        for item in pending:
            log("[DRY RUN] messaggio per %s:\n%s\n" % (config["chat_id"], format_message(item)))
        if not pending:
            for item in items[-DRY_RUN_PREVIEW:]:
                log("[DRY RUN] anteprima formato (non verrebbe inviato):\n%s\n" % format_message(item))
        run_digest(config, sent_ids, extra, None, now)
        log("[DRY RUN] nessun messaggio inviato, stato non salvato")
        return 0

    for index, item in enumerate(pending):
        if index:
            sleep(SEND_DELAY)
        try:
            send(format_message(item))
        except TelegramError as exc:
            log("ERRORE: invio interrotto sull'articolo %s: %s" % (item["id"], exc), sys.stderr)
            log("Pubblicati %d di %d; i restanti verranno ripresi al prossimo giro"
                % (index, len(pending)), sys.stderr)
            return 1
        sent_ids.append(item["id"])
        extra["recent"].append({
            "id": item["id"],
            "title": item["title"],
            "link": item["link"],
            "sent_at": now.isoformat(timespec="seconds"),
        })
        save_state(state_path, sent_ids, extra)
        log("Pubblicato: %s" % item["title"])
    return run_digest(config, sent_ids, extra, send_digest or send, now)


def run_digest(config, sent_ids, extra, send, now):
    """Invia il riepilogo del giorno una sola volta, dopo DIGEST_HOUR (ora italiana).

    send=None significa dry run: stampa l'anteprima senza inviare e senza salvare.
    Se l'invio fallisce il giorno resta aperto e il giro successivo riprova.
    """
    local_now = now.astimezone(LOCAL_TZ)
    today = local_now.date()
    hour = config.get("digest_hour")
    if not config.get("digest_force"):
        if hour is None or local_now.hour < hour or extra.get("last_digest") == today.isoformat():
            return 0

    entries = [e for e in extra["recent"] if _sent_at(e).astimezone(LOCAL_TZ).date() == today]
    text = format_digest(entries, today, config.get("digest_max", DEFAULT_DIGEST_MAX))
    if send is None:
        log("[DRY RUN] riepilogo serale:\n%s\n" % (text or "(nessuna notizia oggi, non verrebbe inviato)"))
        return 0
    if text:
        try:
            send(text)
        except TelegramError as exc:
            log("ERRORE: riepilogo serale non inviato, riprovo al prossimo giro: %s" % exc, sys.stderr)
            return 1
        log("Riepilogo serale pubblicato con %d notizie" % len(entries))
    else:
        log("Nessuna notizia oggi: riepilogo serale saltato")
    extra["last_digest"] = today.isoformat()
    save_state(config["state_file"], sent_ids, extra)
    return 0


def in_quiet_hours(now, quiet_hours):
    """True se l'ora italiana di `now` cade nella fascia silenziosa (inizio incluso, fine esclusa)."""
    if not quiet_hours:
        return False
    start, end = quiet_hours
    hour = now.astimezone(LOCAL_TZ).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def main(env=None, sleep=time.sleep, now=None):
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token and token not in _SECRETS:
        _SECRETS.append(token)
    try:
        config = load_config(env)
        now = now or datetime.now(timezone.utc)
        silent = in_quiet_hours(now, config["quiet_hours"])
        if silent:
            log("Fascia notturna: i messaggi escono senza notifica sonora")
        client = TelegramClient(config["token"], config["chat_id"], config["api_url"], sleep, silent=silent)
        return run(config, client.send_message, sleep,
                   send_digest=lambda text: client.send_message(text, preview=False), now=now)
    except (ConfigError, FeedError, StateError, TelegramError) as exc:
        log("ERRORE: %s" % exc, sys.stderr)
        return 1
    except Exception as exc:  # nessun traceback: potrebbe contenere dati sensibili
        log("ERRORE inatteso: %s: %s" % (type(exc).__name__, exc), sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
