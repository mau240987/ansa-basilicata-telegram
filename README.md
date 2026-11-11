# ANSA Basilicata → Telegram

Pubblica sul canale Telegram [@ansabasilicata](https://t.me/ansabasilicata) le nuove notizie del feed
RSS <https://www.ansa.it/basilicata/notizie/basilicata_rss.xml>, ogni 5 minuti, con GitHub Actions.
Nessun server, nessuna dipendenza esterna: solo Python e la sua libreria standard.

## Come funziona

- `rss_to_telegram.py` scarica il feed, individua gli articoli non ancora pubblicati e li invia dal più
  vecchio al più recente, con una pausa di circa 1,5 s tra un messaggio e l'altro.
- Formato del messaggio (parse mode HTML, titolo e link escapati):

  ```
  <b>Titolo della notizia</b>
  https://www.ansa.it/basilicata/notizie/...
  #Matera #Policoro
  ```

- **Hashtag dei comuni**: `comuni.py` contiene i 131 comuni lucani (dati ISTAT) e aggiunge fino a 3
  hashtag per i comuni citati in titolo e sommario, nell'ordine in cui compaiono.
  - Le maiuscole contano: "Potenza" sì, "la potenza del vento" no; "Vietri di Potenza" non conta come Potenza.
  - Nomi che sono anche parole comuni o nomi di persona (Bella, Anzi, Tito, Rotonda…, elenco
    `AMBIGUOUS`) diventano hashtag solo con un contesto geografico: "a Tito", "comune di Anzi", "Tito Scalo".
  - Latronico è anche il cognome dell'assessore regionale alla Sanità (`SURNAMES`): serve "comune di
    Latronico", "sindaco di Latronico" o "Latronico Terme".
  - Forme brevi ("Rionero", "Scanzano") e località che non sono comuni (Metaponto) sono in `ALIASES` ed
    `EXTRA_PLACES`.
- **Riepilogo serale**: dal primo giro dopo `DIGEST_HOUR` (ora italiana, ora legale inclusa) il canale
  riceve i titoli cliccabili delle notizie pubblicate in giornata, al massimo `DIGEST_MAX`, senza
  anteprima del link. Parte una sola volta al giorno; se l'invio fallisce riprova al giro dopo; nei giorni
  senza notizie non parte. Titoli e link delle ultime 48 ore sono in `state/sent.json` (`recent`), insieme
  alla data dell'ultimo riepilogo (`last_digest`). Le notizie uscite dopo il riepilogo non vi rientrano.

- **Anteprima grande**: le notizie escono con l'anteprima del link in formato grande
  (`link_preview_options.prefer_large_media`), così la foto dell'articolo è in evidenza.
- **Notte silenziosa**: nella fascia `QUIET_HOURS` (default 22-7, ora italiana) i messaggi escono lo stesso
  ma senza suono di notifica (`disable_notification`). La fascia è decisa all'inizio di ogni giro.

- Gli ID già pubblicati (il `guid`, oppure il `link` se il guid manca) sono in `state/sent.json`, che
  conserva gli ultimi 500 ID e viene committato dal workflow solo quando cambia.
- **Primo avvio** (stato assente): tutto il feed viene segnato come già pubblicato, senza inviare nulla.
  Con `bootstrap_send_last = N` vengono pubblicati subito gli ultimi N articoli.
- **Errori di invio**: il giro si ferma, lo stato degli articoli già inviati è salvato (dopo ogni invio)
  e il run termina con exit code 1; al giro successivo si riprende senza doppioni.
- **Robustezza**: retry con backoff sul download del feed; sul `429` di Telegram si attende
  `retry_after` (5 s se vale 0 o manca; oltre 60 s ci si ferma e si riprende al giro dopo); retry sui `5xx`.
- Il token del bot non compare mai in log, errori o file: ogni messaggio passa da una funzione di
  oscuramento.

## Variabili d'ambiente

| Variabile | Default | Note |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Secret del repository. Obbligatorio tranne in dry run. |
| `TELEGRAM_CHAT_ID` | `@ansabasilicata` | Canale di destinazione. |
| `FEED_URL` | feed ANSA Basilicata | |
| `STATE_FILE` | `state/sent.json` | |
| `BOOTSTRAP_SEND_LAST` | `0` | Usato solo quando lo stato non esiste. |
| `DRY_RUN` | `0` | `1` stampa i messaggi senza inviarli e senza salvare lo stato. |
| `DIGEST_HOUR` | `20` | Ora (0-23, ora italiana) del riepilogo serale; `off` lo disattiva. Variabile di repository opzionale. |
| `DIGEST_MAX` | `10` | Notizie massime nel riepilogo. Variabile di repository opzionale. |
| `DIGEST_FORCE` | `0` | `1` invia subito il riepilogo di oggi (input `send_digest_now`); vale come riepilogo del giorno. |
| `QUIET_HOURS` | `22-7` | Fascia oraria (ora italiana) con notifiche silenziose, inizio incluso e fine esclusa; `off` la disattiva. Variabile di repository opzionale. |
| `TELEGRAM_API_URL` | `https://api.telegram.org` | Solo per i test end-to-end. |

## Workflow

`.github/workflows/rss-to-telegram.yml`:

- gira ogni 5 minuti (`*/5 * * * *`, il minimo consentito da GitHub) e a mano da **Actions → RSS to Telegram → Run workflow**, con gli
  input `dry_run`, `bootstrap_send_last` e `send_digest_now` (con `dry_run` mostra solo l'anteprima
  del riepilogo);
- `concurrency` senza cancellazione dei run in corso, `timeout-minutes: 10`;
- l'ultimo step (sempre eseguito, saltato in dry run) committa `state/sent.json` se è cambiato, con
  `[skip ci]` nel messaggio, poi fa `git pull --rebase` e `push`.

Comandi utili con la CLI `gh`:

```bash
gh secret set TELEGRAM_BOT_TOKEN --repo <owner>/ansa-basilicata-telegram   # chiede il token in modo interattivo
gh workflow run rss-to-telegram.yml -f dry_run=true                         # prova senza inviare
gh workflow run rss-to-telegram.yml -f bootstrap_send_last=0                # primo avvio reale
gh run watch && gh run view --log
```

## Sviluppo

```bash
python3 -m unittest discover -s tests -t . -v   # unit test + end-to-end con server locali
python3 -m pyflakes rss_to_telegram.py tests
DRY_RUN=1 python3 rss_to_telegram.py            # prova sul feed reale, senza inviare nulla
```

## Limiti noti

- Il cron di GitHub Actions non è puntuale: i run possono partire con minuti di ritardo o, nei momenti
  di carico, saltare.
- Nei repository pubblici GitHub disattiva i workflow programmati dopo 60 giorni senza attività nel
  repository; in quel caso basta riattivarli dalla scheda Actions.
- Se un invio va in timeout dopo che Telegram lo ha già accettato, il messaggio può essere ripetuto
  una volta (caso raro, inevitabile senza un'API idempotente).
