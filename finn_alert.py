"""FINN search -> durable SQLite queue -> offline/Google translation -> Telegram."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from itertools import zip_longest
import logging
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from dotenv import dotenv_values
from bs4 import BeautifulSoup
from bs4.element import NavigableString, TemplateString

LOG = logging.getLogger("finn-alert")
STOP = threading.Event()


class ServiceError(Exception):
    pass


class ListingParseError(ServiceError):
    pass


def finn_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != "www.finn.no":
        raise ValueError("Expected an https://www.finn.no URL")
    return url


def load_config(path):
    path = Path(path)
    env = dotenv_values(path.parent / ".env", encoding="utf-8-sig", interpolate=False)
    c = json.loads(path.read_text(encoding="utf-8-sig"))
    c.setdefault("error_alert_interval_seconds", 1800)
    finn_url(c["search_url"])
    for name in ("poll_interval_seconds", "max_pages", "request_delay_seconds", "error_alert_interval_seconds"):
        value = c[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{name} must be positive")
    if not isinstance(c["max_pages"], int):
        raise ValueError("max_pages must be an integer")
    if c["initial_mode"] not in ("send", "skip"):
        raise ValueError("initial_mode must be send or skip")
    if c["translation"]["provider"] not in ("argos", "google_web", "google_cloud"):
        raise ValueError("Unknown translation provider")
    c["telegram"] = {
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN") or "",
        "chat_id": os.getenv("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID") or "",
    }
    c["translation"]["google_api_key"] = (os.getenv("GOOGLE_TRANSLATE_API_KEY")
                                           or env.get("GOOGLE_TRANSLATE_API_KEY") or "")
    c["translation"]["model_path"] = str(path.parent / c["translation"].get("model_path", "models/nb-en"))
    c["database"] = str(path.parent / c.get("database", "state.sqlite3"))
    return c


def request(session, method, url, **kwargs):
    # Never expose request URLs or exception strings: URLs may contain tokens.
    try:
        response = session.request(method, url, timeout=(10, 45), **kwargs)
    except requests.RequestException:
        raise ServiceError("Network request failed or timed out") from None
    if response.status_code != 200:
        raise ServiceError(f"Remote service returned HTTP {response.status_code}")
    return response


def parse_search(source, url):
    soup = BeautifulSoup(source, "html.parser")
    items = {}
    for a in soup.select('a[href]'):
        href = urljoin(url, a["href"])
        p = urlsplit(href)
        match = re.fullmatch(r"/recommerce/forsale/item/(\d+)/?", p.path)
        if p.netloc == "www.finn.no" and match:
            ident = match.group(1)
            items[ident] = f"https://www.finn.no/recommerce/forsale/item/{ident}"
    if not items:
        # Do not mistake a challenge page / changed markup for a valid baseline.
        raise ServiceError("No listing links found; empty search, blocked page, or changed FINN markup")
    next_link = soup.select_one('a[rel~="next"]')
    next_url = finn_url(urljoin(url, next_link["href"])) if next_link else None
    return items, next_url


def parse_detail(source):
    soup = BeautifulSoup(source, "html.parser")
    title = soup.select_one('[data-testid="object-title"]')
    desc = soup.select_one('[data-testid="description"] .whitespace-pre-wrap')
    if title is None or desc is None:
        raise ServiceError("Listing title/description missing; removed listing or changed FINN markup")
    # FINN renders its page inside declarative shadow-DOM <template> elements.
    # BeautifulSoup excludes TemplateString from get_text() by default.
    types = (NavigableString, TemplateString)
    title_text = title.get_text(" ", strip=True, types=types)
    desc_text = desc.get_text("\n", strip=True, types=types)
    if not title_text or not desc_text:
        raise ServiceError("Listing title/description is empty")
    return title_text, desc_text


def chunks(text, limit=2500):
    # 2500 code points also stay below Google's web input limit.
    while text:
        cut = min(len(text), limit)
        if cut < len(text):
            space = text.rfind(" ", 0, cut)
            if space > limit // 2:
                cut = space + 1
        yield text[:cut]
        text = text[cut:]


def parse_metadata(source):
    soup = BeautifulSoup(source, "html.parser")
    types = (NavigableString, TemplateString)
    address = soup.select_one('[data-testid="object-address"]')
    info = soup.select_one('[data-testid="object-info"]')
    metadata = {"address": address.get_text(" ", strip=True, types=types) if address else ""}
    text = info.get_text(" ", strip=True, types=types) if info else ""
    for label, key in (("Sist endret", "updated"), ("Publisert", "published")):
        match = re.search(label + r"\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})\s+kl\.\s*(\d{1,2}:\d{2})", text)
        if match:
            try:
                stamp = datetime.strptime(" ".join(match.groups()), "%d.%m.%Y %H:%M")
            except ValueError:
                continue
            metadata[key] = stamp.strftime("%Y-%m-%d %H:%M") + " (FINN local time)"
    link = soup.select_one('[data-testid="map-link"][href]')
    if link:
        url = urljoin("https://www.finn.no", link["href"])
        if urlsplit(url).scheme == "https" and urlsplit(url).netloc == "www.finn.no":
            metadata["map_url"] = url
    return metadata


def parse_photos(source, listing_url):
    """Extract ordered gallery photos only, excluding thumbnails and related ads."""
    soup = BeautifulSoup(source, "html.parser")
    nodes = [node for node in soup.select('img[id^="image-"], img[data-testid^="image-"]')
             if re.fullmatch(r"image-\d+", node.get("id", "") or node.get("data-testid", ""))]
    if not nodes:
        nodes = soup.select('img[alt="Miniatyrgalleribilde"]')
    candidates = []
    for node in nodes:
        value = node.get("data-src") or node.get("src")
        if not value:
            value = (node.get("data-srcset") or node.get("srcset") or "").split(",")[0].strip().split(" ")[0]
        if value:
            candidates.append(value)
    if not candidates:
        candidates = [node.get("content", "") for node in soup.select('meta[property="og:image"]')]
    photos, seen = [], set()
    listing_id = urlsplit(listing_url).path.rstrip("/").split("/")[-1]
    for value in candidates:
        parsed = urlsplit(urljoin(listing_url, value))
        if parsed.scheme != "https" or parsed.netloc != "images.finncdn.no":
            continue
        match = re.fullmatch(r"/dynamic/[^/]+/(.+)", parsed.path)
        if not match:
            continue
        image_path = match.group(1).lstrip("/")
        if image_path.startswith("item/") and not image_path.startswith("item/" + listing_id + "/"):
            continue
        if image_path not in seen:
            seen.add(image_path)
            photos.append("https://images.finncdn.no/dynamic/960w/" + image_path)
    return photos


def photo_messages(url, photos):
    messages = []
    for offset in range(0, len(photos), 10):
        group = photos[offset:offset + 10]
        caption = f"FINN | Photos {offset + 1}-{offset + len(group)} of {len(photos)}\n{url}"
        if len(group) == 1:
            messages.append({"_method": "sendPhoto", "photo": group[0], "caption": caption})
        else:
            media = [{"type": "photo", "media": photo} for photo in group]
            media[0]["caption"] = caption
            messages.append({"_method": "sendMediaGroup", "media": media})
    return messages


def rich_messages(url, body, heading, metadata, original=None):
    header = (f"<b>{html.escape(heading)}</b>\n"
              f'<a href="{html.escape(url, quote=True)}">Open FINN listing</a>\n'
              f"Published: {html.escape(metadata.get('published', 'Not provided by FINN'))}\n")
    if metadata.get("updated"):
        header += "Last updated: " + html.escape(metadata["updated"]) + "\n"
    address = metadata.get("address", "")
    map_url = metadata.get("map_url")
    if address:
        label = html.escape(address[:300])
        header += (f'Area: <a href="{html.escape(map_url, quote=True)}">{label}</a>\n' if map_url else f"Area: {label}\n")
    buttons = [[{"text": "Copy listing link", "copy_text": {"text": url}}]]
    if address and len(address) <= 256:
        buttons[0].append({"text": "Copy address", "copy_text": {"text": address}})
    if map_url:
        buttons.append([{"text": "Open map", "url": map_url}])
    messages = []
    parts = (("", part) for part in chunks(body, 1500)) if original is None else zip_longest(
        chunks(original, 700), chunks(body, 700), fillvalue="")
    for original_part, part in parts:
        rows = [list(row) for row in buttons]
        if len(part) <= 256:
            rows.append([{"text": "Copy text", "copy_text": {"text": part}}])
        if original is None:
            content = "<pre>" + html.escape(part) + "</pre>"
        else:
            content = ""
            if original_part:
                content += "<b>Original text:</b>\n<pre>" + html.escape(original_part) + "</pre>"
            if part:
                content += "\n\n<b>Translated to English:</b>\n<pre>" + html.escape(part) + "</pre>"
        messages.append({"text": header + "\n" + content,
                         "parse_mode": "HTML", "reply_markup": {"inline_keyboard": rows}})
    return messages


class Bot:
    def __init__(self, config, db):
        self.c, self.db = config, db
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "FINN-Alert/1.0 (personal search notifier)"
        self.detail_metadata = {}
        self.offline_translator = None

    def fetch(self, url):
        STOP.wait(self.c["request_delay_seconds"])
        response = request(self.session, "GET", finn_url(url))
        return response.content

    def discover(self):
        url = self.c["search_url"]
        found = {}
        for _ in range(self.c["max_pages"]):
            try:
                items, next_url = parse_search(self.fetch(url), url)
            except (ServiceError, ValueError) as exc:
                self.report_fetch_error("Search page", url, exc)
                raise ServiceError(str(exc)) from None
            found.update(items)
            if not next_url:
                break
            url = next_url
        else:
            if next_url:
                LOG.warning("Scan reached max_pages; older results outside this window are not monitored")
        return found

    def report_fetch_error(self, stage, url, error):
        # Preview/connectivity checks have no database and must never send messages.
        if self.db is None:
            return
        detail = str(error) if isinstance(error, ServiceError) else type(error).__name__
        for secret in (self.c.get("telegram", {}).get("bot_token"),
                       self.c.get("translation", {}).get("google_api_key")):
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        detail = re.sub(r"https?://\S+", "[URL omitted]", detail)[:500]
        # Group identical errors across listing IDs to avoid one alert per failed ad.
        key = hashlib.sha256((stage + ":" + detail).encode()).hexdigest()
        now = time.time()
        row = self.db.execute("SELECT sent_at FROM error_alerts WHERE key=?", (key,)).fetchone()
        interval = self.c.get("error_alert_interval_seconds", 1800)
        if row and now - row[0] < interval:
            return
        p = urlsplit(url)
        link = f"https://www.finn.no{p.path}" if p.netloc == "www.finn.no" else "FINN"
        message = ("FINN Alert | Fetch error\n"
                   f"Time (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
                   f"Stage: {stage}\nError: {detail}\nPage: {link}\n\n"
                   "The failed fetch will be retried on a later poll. "
                   f"Identical alerts are limited to once every {interval:g} seconds.")
        try:
            self.send(message)
        except ServiceError:
            # Do not recursively alert on an alert-delivery failure.
            LOG.error("Could not send FINN fetch-error alert to Telegram; will retry if the fetch fails again")
            return
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO error_alerts(key,sent_at) VALUES(?,?)", (key, now))

    def listing_detail(self, url):
        try:
            source = self.fetch(url)
        except (ServiceError, ValueError) as exc:
            self.report_fetch_error("Listing details", url, exc)
            raise ServiceError(str(exc)) from None
        try:
            detail = parse_detail(source)
            self.detail_metadata[url] = parse_metadata(source)
            if self.c.get("send_photos", True):
                self.detail_metadata[url]["photos"] = parse_photos(source, url)
            return detail
        except ServiceError as exc:
            self.report_fetch_error("Listing details", url, exc)
            raise ListingParseError(str(exc)) from None

    def translate(self, text):
        if self.c["translation"]["provider"] == "argos":
            try:
                if self.offline_translator is None:
                    from offline_translation import OfflineTranslator
                    self.offline_translator = OfflineTranslator(self.c["translation"]["model_path"])
                value = self.offline_translator.translate(text)
                if not value.strip():
                    raise ValueError("Empty translation")
                return value
            except Exception:
                raise ServiceError("Offline translation unavailable; check installed model and dependencies") from None
        translated = []
        for part in chunks(text):
            STOP.wait(self.c["request_delay_seconds"])
            if self.c["translation"]["provider"] == "google_cloud":
                key = self.c["translation"]["google_api_key"]
                if not key:
                    raise ServiceError("google_cloud requires google_api_key")
                r = request(self.session, "POST", "https://translation.googleapis.com/language/translate/v2",
                            headers={"X-Goog-Api-Key": key},
                            json={"q": part, "target": "en", "format": "text", "model": "nmt"})
                try:
                    value = r.json()["data"]["translations"][0]["translatedText"]
                except (ValueError, KeyError, IndexError, TypeError):
                    raise ServiceError("Unexpected Google Cloud response") from None
            else:
                r = request(self.session, "GET", "https://translate.google.com/m",
                            params={"sl": "auto", "tl": "en", "q": part})
                node = BeautifulSoup(r.content, "html.parser").select_one(".result-container")
                if node is None:
                    raise ServiceError("Google web translation unavailable; retry later or use google_cloud")
                value = node.get_text(" ", strip=True)
            if not value:
                raise ServiceError("Translation was empty")
            translated.append(html.unescape(value))
        return "\n".join(translated)

    def send(self, text):
        token = self.c["telegram"]["bot_token"]
        # No automatic POST retry: a network timeout might follow a successful send.
        payload = dict(text) if isinstance(text, dict) else {"text": text}
        method = payload.pop("_method", "sendMessage")
        if method not in ("sendMessage", "sendPhoto", "sendMediaGroup"):
            raise ServiceError("Unknown Telegram delivery method")
        payload["chat_id"] = self.c["telegram"]["chat_id"]
        r = request(self.session, "POST", f"https://api.telegram.org/bot{token}/{method}", json=payload)
        try:
            ok = r.json().get("ok")
        except ValueError:
            ok = False
        if not ok:
            raise ServiceError("Telegram rejected the message")

    def listing_messages(self, url, title, description):
        original = title + "\n\n" + description
        metadata = self.detail_metadata.pop(url, {})
        photos = photo_messages(url, metadata.pop("photos", []))
        try:
            body = self.translate(original)
            heading = "FINN | English"
            return photos + rich_messages(url, body, heading, metadata, original=original)
        except ServiceError as exc:
            LOG.warning("Translation failed; sending original listing: %s", exc)
            body = original
            heading = "FINN | Translation failed. Original text follows."
        return photos + rich_messages(url, body, heading, metadata)

    def enqueue(self, items):
        # Baselines belong to a search, so changing filters starts a new baseline.
        p = urlsplit(self.c["search_url"])
        normalized = urlunsplit((p.scheme, p.netloc, p.path, urlencode(sorted(
            (k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k != "page")), ""))
        key = "baseline:" + hashlib.sha256(normalized.encode()).hexdigest()
        first = not self.db.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone()
        state = "skipped" if first and self.c["initial_mode"] == "skip" else "pending"
        with self.db:
            for ident, url in reversed(list(items.items())):
                self.db.execute("INSERT OR IGNORE INTO ads(id,url,status) VALUES(?,?,?)", (ident, url, state))
            self.db.execute("INSERT OR IGNORE INTO meta(key) VALUES(?)", (key,))
        LOG.info("Discovered %s listings; initial_mode=%s", len(items), self.c["initial_mode"])

    def deliver(self):
        rows = self.db.execute("SELECT id,url,messages,next_part FROM ads WHERE status='pending' ORDER BY rowid").fetchall()
        failed = False
        for ident, url, stored, next_part in rows:
            if STOP.is_set():
                break
            try:
                if stored is None:
                    try:
                        title, desc = self.listing_detail(url)
                    except ListingParseError:
                        LOG.error("Listing %s has no readable description; retained for a later poll", ident)
                        failed = True
                        continue
                    messages = self.listing_messages(url, title, desc)
                    with self.db:
                        self.db.execute("UPDATE ads SET messages=? WHERE id=?", (json.dumps(messages), ident))
                else:
                    messages = json.loads(stored)
                for i in range(next_part, len(messages)):
                    if STOP.is_set():
                        return False
                    self.send(messages[i])
                    with self.db:
                        self.db.execute("UPDATE ads SET next_part=? WHERE id=?", (i + 1, ident))
                    STOP.wait(max(3, self.c["request_delay_seconds"]))
                with self.db:
                    self.db.execute("UPDATE ads SET status='sent' WHERE id=?", (ident,))
                LOG.info("Sent listing %s", ident)
            except ServiceError as exc:
                LOG.error("Listing %s remains queued: %s", ident, exc)
                failed = True
                # Avoid hammering Telegram/Google during an outage or rate limit.
                break
        return not failed


def database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ads (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, status TEXT NOT NULL,
            messages TEXT, next_part INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS error_alerts (key TEXT PRIMARY KEY, sent_at REAL NOT NULL);
    """)
    return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--once", action="store_true", help="Run one poll, including delivery")
    parser.add_argument("--preview", action="store_true", help="Print one translated listing; no Telegram or database writes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        c = load_config(args.config.resolve())
        if args.preview:
            bot = Bot(c, None)
            items, _ = parse_search(bot.fetch(c["search_url"]), c["search_url"])
            url = next(iter(items.values()))
            title, desc = bot.listing_detail(url)
            print(json.dumps(bot.listing_messages(url, title, desc), ensure_ascii=False, indent=2))
            return 0
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", c["telegram"]["bot_token"]):
            raise ValueError("Set a valid TELEGRAM_BOT_TOKEN in .env or the process environment")
        if "your_channel" in c["telegram"]["chat_id"] or not c["telegram"]["chat_id"]:
            raise ValueError("Set TELEGRAM_CHAT_ID in .env or the process environment")
        # Linux flock prevents two processes from using the same delivery queue.
        with open(c["database"] + ".lock", "a") as lock:
            if os.name == "posix":
                import fcntl
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ServiceError("Another instance is using this database") from None
            db = database(c["database"])
            bot = Bot(c, db)
            try:
                while not STOP.is_set():
                    start = time.monotonic()
                    ok = True
                    try:
                        bot.enqueue(bot.discover())
                    except ServiceError as exc:
                        LOG.error("Search failed: %s", exc)
                        ok = False
                    ok = bot.deliver() and ok
                    if args.once:
                        return 0 if ok else 1
                    STOP.wait(max(1, c["poll_interval_seconds"] - (time.monotonic() - start)))
            finally:
                db.close()
        return 0
    except (ServiceError, ValueError, KeyError, OSError) as exc:
        # Avoid printing config values, credentials, or request URLs.
        LOG.error("Startup/run failed (%s). Check configuration, connectivity and file permissions.", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
