# FINN Alert — Technical Documentation

> Audience: engineers and AI coding agents who pick this repository up cold and must be able to understand, operate, debug and extend it without any prior context. `README.md` is the user/operator guide (installation, Telegram setup, pricing). This file explains **how the code works and why it is written the way it is**. Current version: see `VERSION` (1.4.2 at time of writing).

---

## 1. What the system does

FINN Alert is a single-process, long-running Python daemon that:

1. Periodically fetches a **FINN.no** (Norwegian marketplace) *search results* URL, over several pages.
2. Discovers listing IDs it has never seen, and stores them in a **SQLite durable queue**.
3. For each queued listing: fetches the detail page, extracts title / description / address / dates / map link / photos.
4. **Translates** the Norwegian text to English (default: fully offline, CPU-only Argos model via CTranslate2; optional Google web/Cloud).
5. Optionally computes a **straight-line distance** from a configured origin to the listing's postal code (offline GeoNames dataset).
6. Decides whether the listing is **priority/important** (keyword or postal-code match on the original Norwegian text).
7. Renders Telegram **HTML messages** + photo albums and sends them to one or two Telegram chats (main group / priority group), with notification sound only for important ones.
8. Checkpoints every message part in SQLite so a crash/restart resumes exactly where it stopped.

There is no web server, no framework, no ORM, no async. It is one thread of control (a polling loop) plus a `threading.Event` used as a cooperative stop flag / interruptible sleep.

---

## 2. Repository layout

| Path | Role |
|---|---|
| `finn_alert.py` | **The whole application** (~755 lines): config loading, HTTP, parsing, translation dispatch, message rendering, Telegram sender, queue/state machine, main loop. |
| `offline_translation.py` | `OfflineTranslator`: loads CTranslate2 + SentencePiece model and translates nb→en on CPU. Imported lazily. |
| `postal_distance.py` | Config validation for `[distance]` and Haversine distance from a Norwegian postal code. |
| `setup_translation.py` | One-shot downloader/extractor for the Argos nb→en model into `models/nb-en` (only inference files). |
| `check_connection.py` | Diagnostic CLI: checks Telegram token/chat, FINN search+detail parsing, and translation; `--send-test` sends one Telegram message. Never writes DB. |
| `local_trial.py` | Time-boxed local run with an isolated `trial-<ns>.sqlite3`; baselines everything but one sample listing. |
| `config.toml` | Live (user's) configuration. Safe to commit: no secrets. |
| `config.example.toml` | Template with neutral defaults (priority off-routing, distance disabled). |
| `.env` / `.env.example` | Secrets and chat IDs. `.env` is git-ignored; `.env.example` is the template. |
| `data/postal_codes_no.json` | `{ "5003": [lat, lon], ... }` derived from GeoNames NO.zip (CC BY 4.0). `data/GEONAMES-README.txt` is the attribution/source doc. |
| `finn-alert.service` | Hardened systemd unit (runs from `/opt/finn-alert`, user `finn-alert`). |
| `requirements.txt` | Runtime deps (see §13). |
| `VERSION` | Single-line semantic version string. Bumped by "Release x.y.z" commits. |
| `tests/` | `unittest` suites (no network, no real Telegram). |
| `.github/workflows/tests.yml` | CI: Python 3.12, `pip install -r requirements.txt`, `python -m unittest discover -s tests -v` on push to `main` and PRs. |
| `.gitignore` | Ignores `.venv/`, `__pycache__/`, `.env*` (except example), `*.log`, `*.sqlite3*`, sample HTML, `models/`. |

Runtime-generated, **never committed**: `.env`, `state.sqlite3` (+ `-journal`/`-wal`), `state.sqlite3.lock`, `models/nb-en/`, `*.log`.

---

## 3. Execution model

### 3.1 CLI (`finn_alert.py`)

```
python finn_alert.py [--config config.toml] [--once] [--preview]
```

| Mode | Behaviour | Exit code |
|---|---|---|
| default | infinite poll loop until SIGINT/SIGTERM | 0 on clean stop, 1 on startup failure |
| `--once` | one discover + deliver cycle, real Telegram delivery | 0 if everything OK, 1 if search or delivery failed |
| `--preview` | `Bot(config, db=None)`; fetches first listing of the search, prints the generated message JSON; **no Telegram, no DB, no error alerts** | 0 |

Signals: `SIGINT`/`SIGTERM` set the global `STOP` event. All sleeps (`STOP.wait(...)`) are interruptible, so shutdown is prompt. `finn-alert.service` sets `TimeoutStopSec=70` to allow an in-flight network request (timeout 10 s connect + 45 s read) to finish.

### 3.2 Main loop (`main()`)

```
load_config → require_telegram → open <db>.lock → (posix) flock(LOCK_EX|LOCK_NB)
db = database(path); bot = Bot(config, db)
loop until STOP:
    bot.set_config(watcher.reload(bot.c))      # hot reload
    try:  bot.enqueue(bot.discover())          # search → ads table
    except ServiceError: log, ok=False
    ok = bot.deliver() and ok                  # drain the queue
    if --once: return 0/1
    wait_for_next_poll(max(1, poll_interval - elapsed), watcher)
```

Key properties:

* Poll interval is measured **start-to-start** (elapsed time is subtracted), min 1 s.
* A search failure does **not** skip delivery: previously queued listings are still delivered.
* `wait_for_next_poll` sleeps in ≤10 s slices and returns early if `ConfigWatcher.changed()` — so config edits take effect within ~10 s.
* All top-level exceptions (`ServiceError, ValueError, KeyError, OSError`) are caught, logged **without** values (only `ValueError` text or exception class name, to avoid leaking tokens/URLs) and mapped to exit code 1.

### 3.3 Single-instance guard

`<database>.lock` is opened in append mode and `fcntl.flock(LOCK_EX|LOCK_NB)` is taken on POSIX. A second instance on the same DB raises `ServiceError("Another instance is using this database")`. **On Windows there is no lock** (`os.name != "posix"`): run only one instance manually.

---

## 4. Configuration system

### 4.1 Sources and precedence

* **`config.toml`** (default; parsed with `tomllib`, falls back to `tomli` on Python < 3.11). A `*.json` path is still parsed as JSON for backwards compatibility (`read_config_file`). File is read as `utf-8-sig`.
* **`.env`** located **next to the config file** (`path.parent / ".env"`), parsed with `python-dotenv` (`interpolate=False`, `utf-8-sig`).
* Secrets resolve via `setting(name)`: **process environment > `.env` > empty string**.
* `config.toml` holds behaviour; `.env` holds credentials/IDs. This split is deliberate: `config.toml` is committed to Git.

### 4.2 Keys

Top-level (`config.toml`):

| Key | Type / validation | Default (example) |
|---|---|---|
| `search_url` | must be `https://www.finn.no/...` (`finn_url`) | — (required) |
| `poll_interval_seconds` | number > 0 (bool rejected) | 300 |
| `max_pages` | **int** > 0 | 3 |
| `request_delay_seconds` | number > 0 | 2 |
| `error_alert_interval_seconds` | number > 0 | 1800 (`setdefault`) |
| `initial_mode` | `"send"` \| `"skip"` | send |
| `database` | path relative to config file | `state.sqlite3` |
| `send_photos` | bool (read with `.get("send_photos", True)`) | true |
| `[translation] provider` | `argos` \| `google_web` \| `google_cloud` | argos |
| `[translation] model_path` | relative to config file | `models/nb-en` |
| `[distance]` | `enabled`, `origin_latitude`, `origin_longitude` | disabled |
| `[telegram] silent_normal` | bool | true |
| `[priority]` | `enabled`, `send_to_group`, `also_send_to_main`, `keywords`, `postal_codes` | see §9 |

`.env` keys: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PRIORITY_CHAT_ID`, `GOOGLE_TRANSLATE_API_KEY`, `PRIORITY_KEYWORDS`, `PRIORITY_POSTAL_CODES` (comma- or newline-separated; **added to** the TOML lists, not overriding them).

### 4.3 `load_config()` post-processing

The returned dict is **not** the raw file; it is normalized:

* `c["telegram"]` is rebuilt as `{silent_normal, bot_token, chat_id, priority_chat_id}` (secrets injected from env/.env).
* `c["priority"]` is the output of `load_priority` (normalized, see §9).
* `c["translation"]["google_api_key"]` injected; `model_path` and `database` made absolute relative to the config directory.

### 4.4 `require_telegram()` (runtime-only validation)

Kept separate from `load_config` so `check_connection.py`/`--preview` can load configs without Telegram. It enforces: token matches `\d+:[A-Za-z0-9_-]+`; `chat_id` non-empty and not the placeholder `your_channel`; if `priority.enabled and priority.send_to_group` then `TELEGRAM_PRIORITY_CHAT_ID` must be set.

### 4.5 Hot reload (`ConfigWatcher`)

* Signature = `(st_mtime_ns, st_size)` of `config.toml` and `.env` (missing → `None`). Two `stat` calls per check.
* `reload(current)`: if signature unchanged → return `current`. Otherwise update `seen` **first** (so a broken file is not re-parsed every loop), then `load_config` + `require_telegram`. On `ValueError/KeyError/TypeError/OSError` → log `Configuration reload failed; keeping previous settings` and return the old config (a bad edit never kills the bot).
* `database` cannot change at runtime: the new value is overwritten with the current one and a warning logged.
* `Bot.set_config` drops the cached `offline_translator` if `model_path` changed.
* Applies to: search URL, intervals, priority rules, Telegram token/IDs, provider, distance, photos, silent flag. Takes effect at the **next poll iteration**.

---

## 5. Data model (SQLite, `database()`)

`sqlite3.connect(path)` — default journal mode, autocommit managed with `with self.db:` transactions. Schema (created idempotently; no migrations framework — new columns would need manual `ALTER`):

```sql
CREATE TABLE ads (
  id TEXT PRIMARY KEY,          -- FINN numeric item id
  url TEXT NOT NULL,            -- canonical https://www.finn.no/recommerce/forsale/item/<id>
  status TEXT NOT NULL,         -- pending | sent | skipped | unavailable
  messages TEXT,                -- JSON list of prepared Telegram payloads (NULL until prepared)
  next_part INTEGER NOT NULL DEFAULT 0   -- index of next message part to send (checkpoint)
);
CREATE TABLE meta (key TEXT PRIMARY KEY);                       -- "baseline:<sha256>" markers
CREATE TABLE error_alerts (key TEXT PRIMARY KEY, sent_at REAL NOT NULL);  -- error-alert cooldown
```

### 5.1 Listing state machine

```
              enqueue (INSERT OR IGNORE)
  (unseen) ───────────────────────────────► pending ──deliver ok──► sent
      │  first run & initial_mode=skip                │
      └──────────────────────────────────► skipped    ├─ 404/410 on detail ─► unavailable
                                                      └─ transient error  ─► stays pending (retried next poll)
```

* `INSERT OR IGNORE` on `id` = **deduplication**; a listing is processed once ever per database.
* `messages` NULL ⇒ not prepared yet. Once prepared (translation, metadata, routing decided) the JSON is stored; retries **never re-translate** and never re-decide priority routing ("already-queued listings keep their earlier routing").
* `next_part` is updated after **each** part (text parts first, then photo albums), so a restart resumes mid-listing.
* Messages are stored as dicts. A legacy plain `str` entry is also supported by `send()` (`{"text": ...}` wrapping) — old queue rows from earlier versions still deliver.

### 5.2 Baseline (`meta`, `enqueue`)

The search URL is **normalized** (sorted query params, `utm_*` and `page` removed, fragment dropped) and hashed: `baseline:<sha256>`. If that key is absent this is the first run for this search:

* `initial_mode="skip"` → all currently visible listings inserted as `skipped` (never sent), only later new ones go out.
* `initial_mode="send"` → inserted `pending` (everything is sent, oldest first).

Changing filters therefore starts a fresh baseline. Insertion iterates `reversed(items)` so the oldest of the page is inserted first and `ORDER BY rowid` in `deliver()` yields chronological delivery (FINN returns newest first with `sort=PUBLISHED_DESC`).

### 5.3 Error-alert cooldown

`error_alerts.key = sha256(stage + ":" + redacted_detail)`; `sent_at` is epoch seconds. Survives restarts.

---

## 6. FINN scraping

All HTTP goes through `request(session, method, url, **kw)`:

* `timeout=(10, 45)`.
* `requests.RequestException` → `ServiceError("Network request failed or timed out")` (the original exception is dropped with `from None` because URLs may contain tokens).
* Non-200 → `HttpError(status_code)`.
* `Bot.session` has UA `FINN-Alert/1.0 (personal search notifier)`.
* `Bot.fetch(url)`: first `STOP.wait(request_delay_seconds)` (politeness delay), then validates `finn_url()` (https + host `www.finn.no` only — an SSRF/safety guard), then GET.

### 6.1 Search pages (`discover` / `parse_search`)

* Loop up to `max_pages`; each page: collect every `<a href>` resolved against the page URL whose path matches `^/recommerce/forsale/item/(\d+)/?$` on `www.finn.no`. Canonicalized to `https://www.finn.no/recommerce/forsale/item/<id>`. Dict keeps first-seen order and dedups.
* **Zero links ⇒ `ServiceError`** ("empty search, blocked page, or changed FINN markup") so a CAPTCHA/challenge page or markup change can never be mistaken for a valid (empty) baseline.
* Pagination: `a[rel~="next"]` → next URL (validated by `finn_url`). If `max_pages` is exhausted while a next link still exists, a warning is logged (`for…else`): older results are not monitored.
* Errors in search → `report_fetch_error("Search page", ...)` then re-raised as `ServiceError`.

### 6.2 Detail pages (`listing_detail`, `parse_detail`, `parse_metadata`, `parse_photos`)

FINN's markup uses **declarative shadow DOM** (`<template>` elements). BeautifulSoup's `get_text()` excludes `TemplateString` by default, so every text extraction passes `types=(NavigableString, TemplateString)`. **Do not remove this** or the text will come back empty.

Selectors (all brittle by nature — these are the first things to fix if FINN changes markup):

| Datum | Selector / rule |
|---|---|
| title | `[data-testid="object-title"]` |
| description | `[data-testid="description"] .whitespace-pre-wrap` |
| address | `[data-testid="object-address"]` (starts with 4-digit postal code, e.g. `5003 Bergen`) |
| dates | regex on `[data-testid="object-info"]` text: `Sist endret : DD.MM.YYYY kl. HH:MM`, `Publisert : …` → `"YYYY-MM-DD HH:MM (FINN local time)"` |
| map link | `[data-testid="map-link"][href]`, only kept if https + `www.finn.no` |
| photos | see below |

Missing title/description or empty text ⇒ `ServiceError` ⇒ wrapped as `ListingParseError` (listing stays queued, error alert sent).

**Photos** (`parse_photos`):
1. Primary: `img[id^="image-"]` / `img[data-testid^="image-"]` where the id fully matches `image-\d+` (gallery images only, excludes thumbnails/related ads).
2. Fallback: `img[alt="Miniatyrgalleribilde"]`, then `meta[property="og:image"]`.
3. URL taken from `data-src` → `src` → first entry of `data-srcset`/`srcset`.
4. Keep only `https://images.finncdn.no/dynamic/<size>/<path>`; if `<path>` begins with `item/` it must be `item/<this listing id>/...` (drops related-listing images). Deduplicated by path; every URL is rewritten to `/dynamic/960w/<path>`.

### 6.3 Error classification (`listing_detail`)

| Condition | Result |
|---|---|
| HTTP 404 / 410 on detail | `ListingUnavailable` → status `unavailable`, silently skipped forever |
| Other `HttpError`/network | `report_fetch_error` + `ServiceError` → stays `pending`, delivery loop `break`s (back-off) |
| Parse failure | `report_fetch_error` + `ListingParseError` → stays `pending`, loop **continues** to next listing, `failed=True` |

---

## 7. Translation

`Bot.translate(text)` dispatches on `translation.provider`:

### 7.1 `argos` (default, offline)

* `offline_translation.OfflineTranslator` (lazy import inside `translate`, so users of Google never need `ctranslate2`).
* Loads `models/nb-en/sentencepiece.model` and CTranslate2 model dir `models/nb-en/model` with `device="cpu"`, `compute_type="int8"`, `inter_threads=1`, `intra_threads=1` (RAM-frugal; designed for ~1 GB VPS, ≈160–230 MiB RSS).
* Algorithm: split by `\n` (preserving blank lines) → split paragraph into sentences by `(?<=[.!?])\s+` → SentencePiece-encode → process in windows of **200 tokens** → `translate_batch` with `beam_size=4`, `replace_unknowns=True`, `max_decoding_length=1024` → decode, strip `▁`/`_` artefacts → rejoin.
* Raises `ValueError` on empty output or when output reaches the 1024 decoder limit (never silently truncates). `Bot.translate` converts **any** exception to `ServiceError("Offline translation unavailable…")`.
* The model is installed by `setup_translation.py`: downloads `https://argos-net.com/v1/translate-nb_en-1_9.argosmodel` (a zip), extracts **only** `model/*`, `sentencepiece.model`, `metadata.json`, `README.md` with zip-slip protection (rejects absolute or `..` paths), verifies `from_code=nb`, `to_code=en` and presence of `model/model.bin`. Supports `--destination`.
* Only Norwegian Bokmål → English; no language auto-detection. **Do not add `argostranslate`/Stanza/PyTorch** (memory blow-up, 1.6 GiB).

### 7.2 `google_web`

Unofficial: `GET https://translate.google.com/m?sl=auto&tl=en&q=<chunk>`, scrape `.result-container`. May hit 429/CAPTCHA/markup change → `ServiceError`.

### 7.3 `google_cloud`

`POST https://translation.googleapis.com/language/translate/v2` with header `X-Goog-Api-Key`, body `{q, target:"en", format:"text", model:"nmt"}`; reads `data.translations[0].translatedText`. Requires `GOOGLE_TRANSLATE_API_KEY`. No spend cap in code.

For both Google providers text is split by `chunks(text, 2500)` (prefers cutting at a space past half the limit), each chunk preceded by `STOP.wait(request_delay_seconds)`, results `html.unescape`d and joined with `\n`.

### 7.4 Failure policy

Translation failure **never blocks delivery**: `listing_messages` catches `ServiceError`, logs a warning, and sends the **original Norwegian text** with heading `FINN | Translation failed. Original text follows.` That fallback message is marked `sent` like any other and is not retranslated later.

---

## 8. Message construction

`Bot.listing_messages(url, title, description)` (called once per listing, result persisted):

1. `original = title + "\n\n" + description`.
2. `metadata = self.detail_metadata.pop(url, {})` — populated as a side-channel by `listing_detail` (keyed by URL; popped to avoid leaking memory).
3. Distance: `approximate_distance(address, cfg["distance"])`; `OSError/ValueError` (missing dataset) → warning, omitted.
4. Priority: `priority_reasons(...)` (§9).
5. Photos → `photo_messages(url, photos, prefix)`; prefix `"🚨 PRIORITY | "` when important.
6. Translate; build text messages via `rich_messages`; concatenate `text_messages + photo_messages`.
7. If important, every message dict gets `"_route": "priority"` (stored in DB so restarts keep routing).

### 8.1 `rich_messages` (Telegram HTML)

Header (all dynamic text passes through `html.escape`):

```
[🚨 PRIORITY LISTING 🚨 / <blockquote>🎯 Reason: …</blockquote>]   # only when reasons
<b>FINN | English</b>   (or the translation-failed heading)
<a href=url>Open FINN listing</a>
Published: …  | Not provided by FINN
Last updated: …           # if known
Approx. distance: 2.4 km  # if computed
Area: <a href=map_url>address</a>   (or plain text)
<a href=google maps search "address, Norway">Google Maps</a>
```

Body: bilingual mode pairs `chunks(original, 700)` with `chunks(translated, 700)` via `zip_longest`, producing `<b>Original text:</b><pre>…</pre>` and `<b>Translated to English:</b><pre>…</pre>` per message. Fallback/monolingual mode uses `chunks(body, 1500)` in a single `<pre>`. The 700-char slices exist so header + two escaped blocks stay below Telegram's **4096-char** limit even with HTML-escape expansion (tests cover long/emoji inputs).

Every text message carries an inline keyboard: one row `[Google Maps] [Open listing]` (Google Maps button omitted without address). Google Maps URL = `https://www.google.com/maps/search/?api=1&query=<address>, Norway` (no API key, never claims exact seller location).

### 8.2 `photo_messages`

Groups of ≤10 (Telegram album limit). Group of 1 → `sendPhoto`; else `sendMediaGroup` with the caption on the first item. Caption: `"{prefix}FINN | Photos a-b of N\n<url>"`. Payload dicts carry the internal key `_method`.

### 8.3 Internal payload keys

`_method` (`sendMessage` default | `sendPhoto` | `sendMediaGroup`) and `_route` (`"priority"`) are **stripped before the Telegram call** (`payload.pop`). They exist only inside the queue JSON. Unknown `_method` raises `ServiceError`.

---

## 9. Priority listings & routing

### 9.1 Importance (`load_priority`, `priority_reasons`)

* `[priority]` flags: `enabled` (default **true**), `send_to_group` (false), `also_send_to_main` (false) — all strictly `bool`.
* `keywords`: TOML list + `PRIORITY_KEYWORDS`; `postal_codes`: TOML list + `PRIORITY_POSTAL_CODES`. Merged, stripped, de-duplicated. Postal codes must match `^\d{4}$` else `ValueError`.
* Keywords are normalized with `normalize_text` = `NFC` + `casefold`. Matching is **substring** on the normalized *original Norwegian* title+description (so `sykkel` matches `terrengsykkel`). Matching happens **before** translation on purpose.
* Postal match: address `^\s*(\d{4})(?:\s|$)` ∈ `postal_codes`.
* Rule is **OR**: any keyword **or** postal code ⇒ important. Reasons are listed in the banner (`keyword “x”`, `postal code 5006`).
* `enabled=false` ⇒ no reasons ever.

### 9.2 Routing & notification matrix (`Bot.send` / `_send`)

`telegram.silent_normal` (default true) controls sound for normal listings.

| Message | Destination chat | `disable_notification` |
|---|---|---|
| Plain string (error alert, test) | main | **false** (always notifies) |
| Normal listing text | main | `silent_normal` |
| Priority text, `send_to_group=false` | main | false (notifies) |
| Priority text, `send_to_group=true` | **priority chat** | false (notifies) |
| …plus `also_send_to_main=true` | additionally a **copy** to main | true (silent copy) |
| Any photo / album (`_method != sendMessage`) | follows its `_route` | **always true** (silent) |

`uses_priority_chat(msg)` ⇔ msg has `_route=="priority"` ∧ `send_to_group` ∧ `priority_chat_id` set.

Resilience rules in `send()`:

* If sending to the priority chat fails with **HTTP 400** (`HttpError`/`TelegramMediaRejected`; e.g. bot kicked, group migrated to supergroup → `migrate_to_chat_id`), the message is re-sent to the **main** chat so one bad group can't block the whole queue. Non-400 errors propagate (retry later).
* If the main-group **copy** fails after the priority send succeeded, it is only logged — retrying the part would duplicate it in the priority group.

---

## 10. Telegram delivery (`_send`)

* Endpoint: `https://api.telegram.org/bot<token>/<method>`, JSON body. **No automatic POST retry**: a timeout may follow a successful send, and retrying would duplicate. At-least-once is accepted; exactly-once is explicitly *not* guaranteed.
* Success requires `r.json()["ok"] == True`; else `ServiceError("Telegram rejected the message")`.
* **Photo fallback**: if a `sendPhoto`/`sendMediaGroup` returns **HTTP 400** (Telegram couldn't fetch the CDN URL), the bot downloads each image itself (only from `images.finncdn.no` over https; streamed, hard cap **10 MiB**/image, held in memory) and re-posts as `multipart/form-data` (`attach://photoN` for albums; `disable_notification` JSON-encoded as the form requires). Rejections there (400/404/410 on download, too large, Telegram 400 on upload, non-FINN host) raise `TelegramMediaRejected`.
* `deliver()` treats `TelegramMediaRejected` as **skippable**: the part is logged, `next_part` is advanced, the listing continues (text already delivered is preserved). Other `ServiceError`s keep the listing queued.
* Network failure/429/5xx ⇒ part not checkpointed ⇒ retried next poll.

### 10.1 `deliver()` algorithm

```
rows = SELECT id,url,messages,next_part FROM ads WHERE status='pending' ORDER BY rowid
for each row:
    if STOP: break
    if messages is NULL: fetch detail → (ListingUnavailable → mark unavailable, continue)
                                       (ListingParseError   → failed=True, continue)
                         build messages; UPDATE ads SET messages=json
    for i in next_part..len-1:
        send(messages[i])            # TelegramMediaRejected → warn & skip
        UPDATE ads SET next_part=i+1 # checkpoint
        STOP.wait(max(3, request_delay))   # ≥3 s between parts (Telegram flood limits)
    UPDATE ads SET status='sent'
    on ServiceError: log, failed=True, break    # back off the whole queue
return not failed
```

The ≥3 s inter-part wait is a rate-limit safeguard (Telegram ≈ 20 msgs/min per group).

---

## 11. Error alerting (`report_fetch_error`)

Search/detail fetch or parse failures send a plain-text (notifying) message `FINN Alert | Fetch error` with UTC time, stage, error, and page path. Safeguards:

* **No-op when `db is None`** (preview / check_connection).
* **Redaction**: bot token and Google key are replaced with `[REDACTED]`, any `https?://\S+` → `[URL omitted]`, truncated to 500 chars; page shown as `https://www.finn.no<path>` only (no query).
* **Dedup/cooldown**: key = sha256(stage + redacted detail); skip if sent within `error_alert_interval_seconds`. Different errors alert immediately. Cooldown persisted in `error_alerts`.
* Delivery failure of the alert is logged only (no recursion); it will be retried if the failure recurs.
* 404/410 on detail pages never alert.

---

## 12. Distance (`postal_distance.py`)

* `validate_distance` (called inside `load_config`): `enabled` bool; if enabled, latitude/longitude finite numbers within ±90/±180 (bools rejected).
* `approximate_distance(address, cfg)`: returns `None` if disabled (dataset is **not even loaded**), no leading 4-digit code, or unknown code. Otherwise Haversine with `R = 6371.0088 km`, rounded to 0.1 km.
* `postal_coordinates()` is `lru_cache(maxsize=1)` reading `data/postal_codes_no.json` next to the module. Keys are **strings** (leading zeros preserved, e.g. `"0150"`).
* The message shows only the distance, never the origin coordinates.
* Dataset caveat: GeoNames dump dated 2015; newer postal codes are absent (distance silently omitted). Centroid ≠ seller location.

---

## 13. Dependencies and environment

`requirements.txt`: `requests`, `beautifulsoup4` (parser `html.parser`, no lxml), `python-dotenv`, `ctranslate2==4.8.2` (pinned), `sentencepiece`, `tomli` (only for Python < 3.11). Python ≥ 3.10 (3.12 in CI). Target: Ubuntu 22.04/24.04 x86_64, but runs on Windows (without flock).

`ctranslate2`/`sentencepiece` are imported lazily, so unit tests and Google-only setups don't need them to import `finn_alert`.

### Deployment options

1. **Manual**: venv + `python finn_alert.py` in a persistent terminal/tmux (no autostart).
2. **systemd** (`finn-alert.service`): `/opt/finn-alert`, dedicated `finn-alert` user, `Restart=on-failure` (30 s), hardening (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths=/opt/finn-alert`, `UMask=0077`). Config/.env edits need no restart; code upgrades do.
3. The original production box runs it in tmux with a memory/CPU-limited scope wrapper (`run-bot.sh`, 256 MiB / 50 % CPU) — see README "Existing server deployment". That wrapper lives on the server, **not** in this repo.

Moving to a new host: copy code + `config.toml` + `.env` (chmod 600) + `state.sqlite3` (to keep history; otherwise set `initial_mode="skip"` to avoid a flood), create venv, `pip install -r requirements.txt`, `python setup_translation.py`, run `python check_connection.py`.

---

## 14. Testing

```
python -m unittest discover -s tests -v
```

Fully offline; no real Telegram or FINN traffic. Tests use fake sessions/monkeypatching and temporary SQLite DBs.

| File | Covers |
|---|---|
| `tests/test_finn_alert.py` | link/pagination parsing, challenge-page rejection, description extraction, shadow-DOM text, baseline skip/send, resume & dedup, 404/410 handling, retry on transient errors, translation success/fallback, bilingual chunking fits 4096, emoji chunking, error alerts (cooldown, restart, redaction, no-recursion, preview silent), metadata dates, HTML escaping, legacy vs rich queue entries, Google Maps URL |
| `tests/test_photos.py` | gallery dedup / related-ad exclusion / unsafe hosts, thumbnail & og:image fallback, 10-per-album split, single photo, upload fallback on 400, rejected photo doesn't block next listing, album retry checkpointing, text-before-photos |
| `tests/test_priority.py` | keyword OR postal logic, `.env` list merge, invalid settings rejected, routing & `_route` tagging, both-group delivery with silent copy and silent photos, priority-group 400 fallback, hot reload (valid applied / invalid ignored), early wake on config change |
| `tests/test_postal_distance.py` | haversine geometry, leading-zero codes, disabled never loads data, invalid coordinates, real dataset |
| `tests/test_offline_translation.py` | long input not truncated, paragraph structure, safe failure, model path/env follow custom config |

CI (`.github/workflows/tests.yml`) runs the same command on Python 3.12. When adding behaviour, add a test in the matching file; keep tests network-free.

Manual verification tools: `check_connection.py [--send-test]`, `finn_alert.py --preview`, `finn_alert.py --once`, `local_trial.py --seconds N`.

---

## 15. Design principles / invariants (read before changing code)

1. **Never lose a listing, tolerate duplicates.** Prepare → persist → send part-by-part → checkpoint. No blind POST retries.
2. **Never let one bad item stall the bot**: unavailable listings are marked and skipped; rejected photos are skipped; a rejected priority group falls back to main; bad config edits are ignored.
3. **Never leak secrets**: no URLs/exception strings in logs or alerts; token/key redaction; `.env` outside Git.
4. **Fail loudly on suspicious input**: empty search results or missing selectors are errors (not empty baselines).
5. **Be a polite client**: delay before every FINN/Google request, ≥3 s between Telegram parts, single instance.
6. **Prepared messages are immutable**: format/routing changes only apply to newly prepared listings; keep the queue JSON format backward compatible (legacy `str` entries must still send). Bump `VERSION` in release commits.
7. **Low memory budget**: no heavyweight ML runtimes; int8 CPU, one thread, lazy imports.
8. **Domain allow-listing**: only `https://www.finn.no` for pages and `https://images.finncdn.no` for images are ever fetched.
9. **All user-visible program text is English**; the seller's text stays in its original language.

---

## 16. Common change recipes

| Task | Where |
|---|---|
| FINN changed markup (no listings/empty text) | `parse_search` regex/`rel=next`; `parse_detail` selectors; `parse_metadata`; `parse_photos`. Save a sample page and extend tests with the HTML. |
| Add a config option | Validate in `load_config`; keep hot-reload safe (must not raise `KeyError` for older configs — use `.get` with default); document in `config.example.toml`, README; add test. |
| New priority criterion | Extend `load_priority` (validation/normalization) and `priority_reasons` (returns human-readable reasons); tests in `test_priority.py`. |
| New translation provider | Add name to the allow-list in `load_config`, a branch in `Bot.translate` (must raise `ServiceError`, never leak URLs); respect `chunks` limits. |
| New Telegram method | Add to the allow-list in `_send`; consider the silent flag and the 400-fallback logic. |
| Change message layout | `rich_messages`/`photo_messages`; keep each message < 4096 chars after HTML escaping; update tests. |
| Support another country | Replace `data/postal_codes_no.json`, the postal regexes (`\d{4}`) in `priority_reasons`/`approximate_distance`, `finn_url` host. |

## 17. Known limitations

* Exactly-once delivery is impossible: crash between send and checkpoint, or a timeout after acceptance, can duplicate a part.
* Listings that disappear between polls or fall beyond `max_pages` (e.g. after downtime) can be missed; sponsored listings may disturb ordering.
* No CAPTCHA/login handling; HTTP 403/429 simply back off and alert.
* Long queues delay the next search (delivery is synchronous in the same loop).
* `flock` single-instance guard is POSIX-only.
* No schema migrations; `INSERT OR IGNORE` means deleting a row (or the DB) re-sends that listing.
* Offline translation is nb→en only, imperfect with slang/product names.
* Google web translation is unofficial and unreliable; Cloud has no built-in cost cap.
