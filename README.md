# FINN Alert

Monitor a FINN search and send new listings to a Telegram group or channel. Notifications contain the listing link and an English translation of the title and full description. If translation fails, the original text is sent with this English notice:

> Translation failed. Original text follows.

Requires Python 3.10+. Ubuntu 22.04 / 24.04 supported. Polling defaults to five minutes. SQLite preserves delivery state across restarts; long descriptions are split into messages.

## Quick setup with WinSCP and a terminal kept open

1. In WinSCP, create a `finn-alert` directory inside your server user's home directory. Upload just these three files: `finn_alert.py`, `requirements.txt`, and your configured `config.json` (containing your Telegram token and destination).
2. Open a terminal on the server and run the following one-time installation:

```bash
cd ~/finn-alert
sudo apt update
sudo apt install -y python3 python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod 600 config.json
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
sudo -u finn-alert .venv/bin/pip install -r requirements.txt
sudo -u finn-alert cp config.example.json config.json
sudo chmod 600 config.json
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
| `telegram.bot_token` | Complete BotFather token |
| `telegram.chat_id` | Numeric group/private-channel ID or public channel `@username` |
| `translation.provider` | `google_web` or `google_cloud` |
| `translation.google_api_key` | API key for `google_cloud` |

Environment variables `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `GOOGLE_TRANSLATE_API_KEY` override the corresponding values. Restart after editing the configuration.

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

```json
"chat_id": "-1001234567890"
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

## Google translation

### Website method

`google_web` uses the Google Translate website without a key. This unofficial method may encounter CAPTCHA, HTTP 429, or changed HTML. The local test encountered HTTP 429. Translation errors now trigger original-text delivery instead of blocking notifications.

### Official API setup

This program supports **Cloud Translation Basic v2, standard NMT**, with English as the target language.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. Link a billing account. Billing must be enabled even within the free allowance.
3. Under **APIs & Services > Library**, enable **Cloud Translation API**.
4. Under **APIs & Services > Credentials > Create credentials > API key**, create an API key for Basic v2.
5. Restrict the key to Cloud Translation API. If using IP restrictions, allow the public IP of the machine running this script, including the development machine during testing.
6. Replace the existing translation section in your private `config.json`:

```json
"translation": {
  "provider": "google_cloud",
  "google_api_key": "YOUR_GOOGLE_CLOUD_API_KEY"
}
```

Alternatively supply `GOOGLE_TRANSLATE_API_KEY` in the process environment. Never commit your real key.

7. Run `python check_connection.py` and look for `Google translation (google_cloud): OK`. Restart the service after changing configuration.

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

## Delivery behavior and limitations

FINN search and listing-detail fetch/parsing failures send an English `FINN Alert | Fetch error` notification to the same configured Telegram destination. It includes UTC time, the failing stage, a safe error description (such as HTTP 403/429 or a network timeout), and the FINN page path. Query strings, tokens, and raw network exception details are omitted. Identical errors in the same stage, including errors affecting different listings, are grouped and reported at most once per 30 minutes by default. A different error is reported immediately. The cooldown is stored in SQLite and survives restarts. Existing configurations automatically use the default; add `error_alert_interval_seconds` to customize it.

Preview and connectivity checks do not send these alerts. If Telegram or the entire network is unavailable, the alert cannot be delivered: the failure is logged locally, and the alert is attempted again if the FINN failure recurs. Missed alerts are not separately queued. Translation failures continue to use the original-listing fallback below.

- All program-generated messages and documentation are English. When translation fails, the seller's original text remains in its original language, with an English warning.
- Successfully delivered fallback listings are marked sent and are not later resent as translations. Telegram failures retain prepared messages for retry without repeating translation.
- Message parts are checkpointed individually. A crash between Telegram delivery and SQLite recording it, or a timeout with an unknown outcome, can cause duplication. Exactly-once delivery is not guaranteed.
- Preserve the database across restarts; use a different database for a different Telegram destination.
- Empty/blocked pages do not establish a baseline. FINN markup changes and removed listings may prevent extraction. CAPTCHA bypass and account login are not implemented.
- Every configured search page is scanned. Listings removed between polls or pushed beyond `max_pages` can be missed, especially after downtime. Featured listings can affect chronological ordering.
- A long delivery queue can delay the next poll. Linux file locking prevents two production instances sharing the same database.

## Tests and Git

```bash
python -m unittest discover -s tests -v
git status
```

Tests run without network calls or real messages. Live Telegram authentication, group lookup, test delivery, and FINN extraction succeeded on October 6, 2026. Website translation returned HTTP 429; official API translation needs a real key for a live test.

The local Git repository ignores `config.json`, `.env` files, databases, and logs. Share `config.example.json` only. No GitHub remote is configured automatically.
