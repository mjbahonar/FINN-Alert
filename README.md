# FINN Alert

FINN Alert is a program that takes a FINN search link, a checking interval, and a Telegram group or channel destination, then sends notifications for new listings matching that search. Notifications contain the listing link, original title and full description under `Original text:`, and their English translation under `Translated to English:`. Both sections use separate copyable code blocks. The original and translated text is sent first, followed by listing photos as Telegram albums. If translation fails, the original text is sent with this English notice:

> Translation failed. Original text follows.

Requires Python 3.10+. Ubuntu 22.04 / 24.04 on x86_64 supported. Translation defaults to offline Norwegian Bokmal (`nb`) to English using an Argos model with CPU-only CTranslate2 int8 inference; no API key, PyTorch, or Stanza is needed. Polling defaults to five minutes. SQLite preserves delivery state across restarts; long descriptions are split into messages.

## Quick setup with WinSCP and a terminal kept open

1. In WinSCP, create a `finn-alert` directory inside your server user's home directory. Upload `finn_alert.py`, `offline_translation.py`, `setup_translation.py`, `requirements.txt`, `config.json`, and your private `.env`. For connectivity checks also upload `check_connection.py`. Set the search link and polling interval in `config.json`; set the Telegram token and group/channel destination in `.env` (use `.env.example` as a template).
2. Open a terminal on the server and run the following one-time installation:

```bash
cd ~/finn-alert
sudo apt update
sudo apt install -y python3 python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --no-cache-dir -r requirements.txt
python3 setup_translation.py
chmod 600 .env
```

3. Start the bot directly:

```bash
python3 finn_alert.py
```

Keep this terminal/session running. The bot polls automatically at the interval in `config.json`; there is no need to rerun it for each poll. Press `Ctrl+C` to stop. This direct method does not automatically restart after the process exits or the server reboots.

For later runs from a new terminal:

```bash
cd ~/finn-alert
source .venv/bin/activate
python3 finn_alert.py
```

Before the first run, set `"initial_mode": "skip"` in `config.json` if you want to baseline existing listings without sending them. Use `"send"` to send existing listings too. On a translation error, the original listing is sent with an English warning. Preserve the generated `state.sqlite3` file to retain delivery history. Restart the program after changing configuration. Run only one instance.

## Install on Ubuntu with systemd (alternative)

Copy the project to `/opt/finn-alert`, then run:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
sudo useradd --system --home /opt/finn-alert --shell /usr/sbin/nologin finn-alert
sudo chown -R finn-alert:finn-alert /opt/finn-alert
cd /opt/finn-alert
sudo -u finn-alert python3 -m venv .venv
sudo -u finn-alert .venv/bin/pip install --no-cache-dir -r requirements.txt
sudo -u finn-alert .venv/bin/python setup_translation.py
sudo -u finn-alert cp .env.example .env
sudo chmod 600 .env
sudo nano .env
sudo nano config.json
```

Skip `useradd` if the account exists. On Windows, use `python` instead of `sudo -u finn-alert .venv/bin/python` in the commands below.

## Configuration

| Setting | Purpose |
|---|---|
| `search_url` | FINN URL with location and other filters |
| `poll_interval_seconds` | Interval between poll starts; default `300` |
| `error_alert_interval_seconds` | Minimum interval for repeated identical FINN error alerts; default `1800` (30 minutes) |
| `max_pages` | Maximum search pages per poll; default `3` |
| `request_delay_seconds` | Request delay; default `2` seconds |
| `initial_mode` | `send`: send existing results on first run; `skip`: baseline existing results and send only newly discovered listings |
| `database` | SQLite path relative to the configuration file |
| `send_photos` | Send gallery photos after text; defaults to `true`; set `false` for text only |
| `TELEGRAM_BOT_TOKEN` (`.env`) | Complete BotFather token |
| `TELEGRAM_CHAT_ID` (`.env`) | Numeric group/private-channel ID or public channel `@username` |
| `translation.provider` | `argos` (default, offline nb to en), `google_web`, or `google_cloud` |
| `translation.model_path` | Model directory relative to config.json; default `models/nb-en` |
| `GOOGLE_TRANSLATE_API_KEY` (`.env`) | API key for `google_cloud` |

The program automatically reads `.env` beside the selected configuration file, including with `--config`. Process environment variables override `.env` values. Keep credentials and the Telegram destination in `.env`; `config.json` is safe to share in Git. Copy `.env.example` to `.env` for a new installation. Restart after editing either file.

## Telegram setup and group ID

1. Create a bot with @BotFather and copy the complete token.
2. Add it to your group and allow messages. For channels, grant administrator permission to post.
3. Send `/start@YourBotUsername` inside the group, replacing the username.
4. Open this URL locally, replacing `YOUR_BOT_TOKEN`:

```text
https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
```

Find the `chat` object whose `title` matches your group:

```json
"chat": {
  "id": -1001234567890,
  "title": "My group",
  "type": "supergroup"
}
```

Use the actual `chat.id` with its minus sign, without `@`:

```dotenv
TELEGRAM_CHAT_ID=-1001234567890
```

Do not use `from.id`, which identifies the sender. If `result` is empty, send the group command again and refresh. An active webhook prevents `getUpdates`; another bot application may consume updates. This notifier only sends messages and does not consume updates.

To avoid placing the token in browser history, run this locally instead:

```bash
.venv/bin/python - <<'PY'
from getpass import getpass
import requests
token = getpass('Bot token: ')
data = requests.get(f'https://api.telegram.org/bot{token}/getUpdates', timeout=30).json()
for update in data.get('result', []):
    msg = update.get('message') or update.get('channel_post') or update.get('my_chat_member') or {}
    chat = msg.get('chat', {})
    if chat:
        print(chat.get('id'), chat.get('title', chat.get('username', '')))
PY
```

### Telegram 404 / token troubleshooting

For `{"ok":false,"error_code":404,"description":"Not Found"}`, check the URL. Keep the literal `bot` immediately before the complete token, including both parts separated by `:`. Do not add spaces, quotes, or angle brackets. Example with a fake token:

```text
https://api.telegram.org/bot123456789:ABCDEF_example/getMe
```

Replace the token. `getMe` returning `"ok":true` confirms authentication; then change `getMe` to `getUpdates`. HTTP 401 generally means an invalid/revoked token. Never share the token or a URL containing it.

References: [getUpdates](https://core.telegram.org/bots/api#getupdates), [getMe](https://core.telegram.org/bots/api#getme).

## Offline Norwegian to English translation (default)

Run `python setup_translation.py` once after installing requirements. It downloads the Argos nb-to-en model version 1.9 and installs only its inference files in `models/nb-en`. Setup needs internet; subsequent translation performs no network requests. FINN retrieval and Telegram delivery still need internet. Model files are ignored by Git.

```json
"translation": {
  "provider": "argos",
  "model_path": "models/nb-en"
}
```

The model path is relative to the selected configuration file. For a custom location, download with `python setup_translation.py --destination /path/to/model` and set `model_path` accordingly. Only Norwegian Bokmal to English is supported by this offline provider; text in other languages is not detected automatically. Quality is imperfect, especially product names and informal wording. Long text is processed in bounded token segments and message parts; source text is not silently truncated; reaching the output limit triggers the original-text fallback.

The runtime loads CTranslate2 and SentencePiece directly, uses CPU int8 inference and one inference thread, and reuses the model between listings. It does not import the full Argos, Stanza, or PyTorch runtime. No Google key is required. Missing models or translation errors send the original text with an English warning.

Original titles and descriptions and their English translations are sent inside separate Telegram HTML `<pre>` code blocks, with listing links, dates and map controls outside the blocks. This retains the full text and enables copying in supported clients.

Measured on the Ubuntu 24.04 test server with 961 MiB RAM: direct inference used about 163 MiB peak process RAM and took 0.8-3.9 seconds for three short listings under a 50% CPU quota. These are sample measurements, not limits for larger listings. The earlier complete Argos test installation occupied about 1.6 GiB and exceeded a 320 MiB memory limit; the deployed production environment measured about 230 MiB plus 77 MiB for the model. A live FINN-to-Telegram delivery test with the production code measured 184 MiB peak process RAM. Production uses the smaller inference dependencies. Do not install the full `argostranslate` package or GPU PyTorch for this application.

References: [Argos Translate](https://github.com/argosopentech/argos-translate), [model index](https://github.com/argosopentech/argospm-index), [CTranslate2](https://opennmt.net/CTranslate2/).

## Optional Google translation

### Website method

`google_web` uses the Google Translate website without a key. This unofficial method may encounter CAPTCHA, HTTP 429, or changed HTML. The local test encountered HTTP 429. Translation errors now trigger original-text delivery instead of blocking notifications.

### Official API setup

This program supports **Cloud Translation Basic v2, standard NMT**, with English as the target language.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. Link a billing account. Billing must be enabled even within the free allowance.
3. Under **APIs & Services > Library**, enable **Cloud Translation API**.
4. Under **APIs & Services > Credentials > Create credentials > API key**, create an API key for Basic v2.
5. Restrict the key to Cloud Translation API. If using IP restrictions, allow the public IP of the machine running this script, including the development machine during testing.
6. Set the translation provider in `config.json`:

```json
"translation": {
  "provider": "google_cloud"
}
```

Put the key in your private `.env`:

```dotenv
GOOGLE_TRANSLATE_API_KEY=YOUR_GOOGLE_CLOUD_API_KEY
```

Alternatively supply `GOOGLE_TRANSLATE_API_KEY` in the process environment. Never commit your real key.

7. Run `python check_connection.py` and look for `Translation (google_cloud): OK`. Restart the service after changing configuration.

Official references: [setup](https://docs.cloud.google.com/translate/docs/setup), [authentication](https://docs.cloud.google.com/translate/docs/authentication), [Basic v2 API](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate).

### Pricing

Checked October 6, 2026: standard NMT includes the first **500,000 input characters per month**, applied as a **$10 monthly credit**. Standard usage above that allowance costs **$20 per million characters** at the standard rate. Characters are not words or requests. The allowance renews monthly and is shared across Basic and Advanced usage; do not assume each key gives another allowance. Other models and document translation have different prices.

For example, one million NMT input characters in a month costs approximately $10 after the credit, excluding other usage and taxes. The application does not enforce a monthly character cap. Monitor usage and configure quotas and billing alerts; budget alerts alone do not stop spending.

Check [current pricing](https://cloud.google.com/products/translate/pricing) before enabling paid usage.

## Testing

Check connectivity and translation without sending:

```bash
sudo -u finn-alert .venv/bin/python check_connection.py
```

Send exactly one connectivity test message during those checks:

```bash
sudo -u finn-alert .venv/bin/python check_connection.py --send-test
```

Preview one listing without Telegram delivery or database changes:

```bash
sudo -u finn-alert .venv/bin/python finn_alert.py --preview
```

Preview uses the original-text fallback if translation fails. Use `check_connection.py` to verify translation independently.

Run one poll with **real listing delivery**:

```bash
sudo -u finn-alert .venv/bin/python finn_alert.py --once
```

### Bounded local trial

```bash
python local_trial.py --seconds 900
```

Runs for 15 minutes using `config.json` and an isolated `trial-*.sqlite3` database. Existing listings are baselined; only one existing sample and newly discovered listings are sent. The configured polling interval applies. It may stop slightly later if a network request is in progress. Logs report sent/pending counts. Fallback delivery confirms notifications, not successful translation.

## Ubuntu service

```bash
sudo cp finn-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finn-alert
sudo systemctl status finn-alert
sudo journalctl -u finn-alert -f
```

Restart after configuration changes: `sudo systemctl restart finn-alert`. Stop: `sudo systemctl stop finn-alert`.

## Listing photo albums

All gallery photos exposed in a listing page are extracted in page order. Duplicate thumbnail versions, profile images and unrelated recommended listings are excluded. Photos are sent after the original text and English translation. Tap a photo in Telegram to browse the album.

Each album contains up to 10 photos. Larger galleries are split across multiple albums; a remaining single photo uses `sendPhoto`. Every album or single photo includes the FINN link in its caption. Listings without extractable photos still send text normally. Set `"send_photos": false` in config.json to disable gallery delivery.

Telegram retrieves 960-pixel-wide image URLs directly from FINN's image CDN; the bot does not store photos on the server. Photo URL availability and Telegram's file limits still apply. If a photo or album request fails, its listing remains queued and retries on a later poll. Previously acknowledged albums are checkpointed and skipped on retry. Text is delivered before photos, so an album failure does not prevent the listing text from reaching the chat. The failed photo part remains queued and resumes on a later poll. A network timeout or crash after Telegram accepts an album but before checkpointing may duplicate that album.

Prepared queue entries from older versions retain their existing format and are not rewritten. Previously sent listings are not resent solely to add photos. New or not-yet-prepared listings use the new photo format. The SQLite schema and delivery history are preserved.

References: [Telegram albums](https://core.telegram.org/bots/api#sendmediagroup), [single photos](https://core.telegram.org/bots/api#sendphoto).

## Delivery behavior and limitations

Listing-detail HTTP 404 (not found) and 410 (gone) responses mark the listing as `unavailable` in SQLite and continue with the next queued listing. These entries are not retried or repeatedly reported to Telegram. Search-page errors and temporary listing failures such as HTTP 403/429/5xx or network timeouts still use the retry and error-alert behavior below. Preserve `state.sqlite3` when upgrading; no database migration is needed.

Listing messages include the publication date/time **when explicitly available**, and the separately labeled last-update time shown by FINN. Many listings expose only `Sist endret` (last updated); these show `Published: Not provided by FINN` rather than mislabeling the update time as publication. Times retain FINN's displayed local time. The visible address/postal area links to FINN's map and also has an `Open map` button. A postal-area location is not necessarily an exact street address.

Descriptions appear in a preformatted text block for easy copying in Telegram clients that offer a code-block copy control. `Copy listing link` and `Copy address` buttons copy those fields directly. Telegram's native copy buttons allow only 256 characters, so `Copy text` is provided only for short text parts. Longer text remains complete in the copyable block; the exact copy gesture depends on the Telegram client. Old plain-text queue entries still send normally; newly prepared listings use the new format. To update a simple WinSCP installation, stop the bot, replace `finn_alert.py`, `offline_translation.py`, `setup_translation.py`, and `requirements.txt`, install the updated requirements, run `python setup_translation.py` if the offline model is not installed, select `argos` in the translation configuration, and run it again; preserve `config.json`, `.env`, and `state.sqlite3`.

FINN search and listing-detail fetch/parsing failures send an English `FINN Alert | Fetch error` notification to the same configured Telegram destination. It includes UTC time, the failing stage, a safe error description (such as HTTP 403/429 or a network timeout), and the FINN page path. Query strings, tokens, and raw network exception details are omitted. Identical errors in the same stage, including errors affecting different listings, are grouped and reported at most once per 30 minutes by default. A different error is reported immediately. The cooldown is stored in SQLite and survives restarts. Existing configurations automatically use the default; add `error_alert_interval_seconds` to customize it.

Preview and connectivity checks do not send these alerts. If Telegram or the entire network is unavailable, the alert cannot be delivered: the failure is logged locally, and the alert is attempted again if the FINN failure recurs. Missed alerts are not separately queued. Translation failures continue to use the original-listing fallback below.

- All program-generated messages and documentation are English. When translation fails, the seller's original text remains in its original language, with an English warning.
- Successfully delivered fallback listings are marked sent and are not later resent as translations. Telegram failures retain prepared messages for retry without repeating translation.
- Message parts are checkpointed individually. A crash between Telegram delivery and SQLite recording it, or a timeout with an unknown outcome, can cause duplication. Exactly-once delivery is not guaranteed.
- Preserve the database across restarts; use a different database for a different Telegram destination.
- Empty/blocked pages do not establish a baseline. FINN markup changes and removed listings may prevent extraction. CAPTCHA bypass and account login are not implemented.
- Every configured search page is scanned. Listings removed between polls or pushed beyond `max_pages` can be missed, especially after downtime. Featured listings can affect chronological ordering.
- A long delivery queue can delay the next poll. Linux file locking prevents two production instances sharing the same database.

## Existing server deployment (October 7, 2026)

The updated bot runs in tmux session `FINN`, window `0`, from `/root/FINN`, using `.venv-offline/bin/python`. Its `run-bot.sh` wrapper limits the process to 256 MiB memory and 50% of one CPU with a transient systemd scope. Output is appended to `/root/FINN/finn-alert.log`.

```bash
tmux attach -t FINN
tail -n 50 /root/FINN/finn-alert.log
```

In tmux, use `Ctrl+B`, then `0` to select the bot window. Stop with `Ctrl+C`. From a shell in that window, restart with:

```bash
bash /root/FINN/run-bot.sh >> /root/FINN/finn-alert.log 2>&1
```

Credentials were migrated into `/root/FINN/.env`. Search filters, polling interval, initial mode and the SQLite queue were preserved. A private backup of the earlier code, configuration and database is in `/root/FINN/backup-20261007T075823Z`. Do not share that backup because the older configuration contains credentials. The `argos-run` tmux window and `/root/argos-test` hold the earlier isolated experiments; the running bot uses `/root/FINN/models/nb-en`.

This tmux deployment does not automatically start after reboot. Use the systemd installation above when automatic startup is wanted.

## Tests and Git

```bash
python -m unittest discover -s tests -v
git status
```

Tests run without network calls or real messages. Offline model inference has also been tested with socket connections and DNS blocked. Live Telegram authentication, group lookup, test delivery, and FINN extraction succeeded on October 6, 2026. Website translation returned HTTP 429; official API translation needs a real key for a live test.

The local Git repository ignores private `.env` files, databases, and logs. Share `config.json`, `config.example.json`, and `.env.example`; they contain no credentials. No GitHub remote is configured automatically.
